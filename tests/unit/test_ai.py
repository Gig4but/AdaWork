import base64
import errno
import hashlib
import json
import subprocess
import sys
import threading
import time
from io import BytesIO

import httpx
import pytest
from conftest import FakeEmbedder, make_ai, make_hw
from PIL import Image
from sqlalchemy import select

from adawork.ai import advisor, vision
from adawork.ai.advisor import Speed
from adawork.ai.ask import to_filters
from adawork.ai.catalog import MODELS, TIERS
from adawork.ai.client import LlmClient, LlmError, Timing
from adawork.ai.download import DownloadError, RemoteFile, download
from adawork.ai.extract import schema, user_message, validate
from adawork.ai.grammar import compact_grammar
from adawork.ai.isco import Candidate, IscoCodebook, IscoIndex
from adawork.ai.runtime import Launch
from adawork.config import Settings
from adawork.core import ParsedJob
from adawork.db.models import Job, LlmCache
from adawork.db.session import make_engine, make_sessionmaker, migrate
from adawork.scraper.store import JobStore
from adawork.system.resources import Priority, ResourceManager

GIB = 1024**3


# --- model choice (PLAN §6.6) ------------------------------------------------------------


def icons(advice):
    return [f.icon for f in advice.fits]


def test_advisor_matches_the_expected_hardware_table():
    minimum = advisor.advise(make_hw(8, "AMD Radeon(TM) Graphics", 0.5, cores=6))
    assert icons(minimum) == ["green", "yellow", "yellow"]  # Good and Excellent: ~3.3 GB each
    assert minimum.pick.tier == "simple" and minimum.reason == "ai.reason.igpu"

    mid = advisor.advise(make_hw(16, "AMD Radeon(TM) Graphics", 0.5, cores=8))
    assert icons(mid) == ["green", "green", "yellow"]
    assert mid.pick.tier == "good"

    gaming = advisor.advise(make_hw(32, "NVIDIA GeForce RTX 4060", 8, cores=8))
    assert icons(gaming) == ["green", "green", "green"]
    assert gaming.pick.tier == "excellent" and gaming.reason == "ai.reason.dgpu"


def test_advisor_too_little_memory_or_disk():
    tiny = advisor.advise(make_hw(4))
    assert tiny.pick is None and tiny.reason == "ai.reason.none"

    full_disk = advisor.advise(make_hw(16, "Intel(R) Graphics", 0.1, disk_gb=3))
    assert all("ai.why.disk" in f.reasons for f in full_disk.fits[1:])
    downloaded = {MODELS[TIERS["good"]].id}
    assert (
        "ai.why.disk"
        not in advisor.advise(
            make_hw(16, "Intel(R) Graphics", 0.1, disk_gb=3), downloaded=downloaded
        )
        .fits[1]
        .reasons
    )


def test_measured_speed_replaces_the_estimate():
    hw = make_hw(16, "AMD Radeon(TM) Graphics", 0.5, cores=8)
    slow = {TIERS["good"]: Speed(prompt_tps=40, output_tps=3, measured=True)}
    advice = advisor.advise(hw, measured=slow)
    good = advice.fits[1]
    assert good.speed.measured and good.icon == "red" and "ai.why.slow" in good.reasons
    assert advice.pick.tier == "simple"


def test_launch_settings_follow_the_hardware(tmp_path):
    spec = MODELS[TIERS["simple"]]
    igpu = advisor.launch_for(spec, make_hw(8, "AMD Radeon(TM) Graphics", 0.5, cores=6), tmp_path)
    assert igpu.backend == "vulkan" and igpu.gpu_layers is None and igpu.threads == 5
    assert igpu.mmproj is None  # the vision part is loaded only for images
    cpu = advisor.launch_for(spec, make_hw(8, cores=2), tmp_path, vision=True)
    assert cpu.backend == "cpu" and cpu.threads == 2 and cpu.mmproj is not None
    args = cpu.args(8080)
    assert args[args.index("--device") + 1] == "none" and "--mmproj" in args
    assert cpu.ram_need > igpu.ram_need  # the vision projector needs memory too


# --- resources (PLAN §3.2) ---------------------------------------------------------------


def test_heavy_jobs_run_one_at_a_time_on_8_gb_and_by_priority():
    rm = ResourceManager(make_hw(8), available_ram=lambda: 6 * GIB, idle_seconds=lambda: 0)
    assert rm.limits.parallel_heavy == 1 and rm.limits.routing_radius_km == 150
    order, holding = [], []

    def take(priority):
        slot = rm.heavy(priority)
        slot.__enter__()
        holding.append(slot)  # kept: the slot stays taken
        order.append(priority)

    with rm.heavy(Priority.IMPORT):
        threads = [
            threading.Thread(target=take, args=(p,), daemon=True)
            for p in (Priority.IMPORT, Priority.AI, Priority.USER)
        ]
        for t in threads:
            t.start()
        deadline = time.monotonic() + 2
        while len(rm._waiting) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)  # all three are waiting before the slot frees up
    threads[2].join(2)  # USER gets the slot first even though it asked last
    assert order == [Priority.USER]
    holding[0].__exit__(None, None, None)
    threads[1].join(2)
    assert order == [Priority.USER, Priority.AI]
    holding[1].__exit__(None, None, None)
    threads[0].join(2)
    with pytest.raises(TimeoutError), rm.heavy(Priority.AI, timeout=0.05):
        pass
    assert ResourceManager(make_hw(32)).limits.parallel_heavy == 3


def test_busy_means_low_memory_while_the_user_works():
    free = {"ram": 1 * GIB}
    idle = {"s": 5.0}
    rm = ResourceManager(make_hw(8), lambda: free["ram"], lambda: idle["s"])
    assert rm.busy()
    idle["s"] = 600  # the user went away: background work may use the PC
    assert not rm.busy()
    idle["s"], free["ram"] = 5.0, 5 * GIB
    assert not rm.busy() and rm.busy(need=4 * GIB)


# --- downloads ---------------------------------------------------------------------------


def served(data: bytes, honour_range: bool = True):
    def handler(request: httpx.Request) -> httpx.Response:
        start = 0
        if honour_range and "range" in request.headers:
            start = int(request.headers["range"].split("=")[1].rstrip("-"))
            return httpx.Response(206, content=data[start:])
        return httpx.Response(200, content=data)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_resumes_and_checks_the_hash(tmp_path):
    data = bytes(range(256)) * 4000
    remote = RemoteFile(
        "https://x/model.gguf", "model.gguf", len(data), hashlib.sha256(data).hexdigest()
    )
    (tmp_path / "model.gguf.part").write_bytes(data[:300_000])  # an interrupted download
    seen = []
    path = download(remote, tmp_path, served(data), progress=lambda d, t: seen.append(d))
    assert path.read_bytes() == data and not (tmp_path / "model.gguf.part").exists()
    assert seen[0] > 300_000  # continued, not restarted

    (tmp_path / "other.gguf.part").write_bytes(b"x" * 10)
    other = RemoteFile("https://x/other.gguf", "other.gguf", len(data), "0" * 64)
    with pytest.raises(DownloadError):
        download(other, tmp_path, served(data, honour_range=False))
    assert not (tmp_path / "other.gguf").exists()


def fake_disk(monkeypatch, free: dict):
    from collections import namedtuple

    from adawork.ai import download as dl

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(dl.shutil, "disk_usage", lambda path: usage(0, 0, free["bytes"]))


def test_a_full_disk_stops_the_download_and_keeps_the_part_file(tmp_path, monkeypatch):
    from adawork.ai.download import SPACE_MARGIN, NoSpace

    data = bytes(range(256)) * 4000
    remote = RemoteFile(
        "https://x/model.gguf", "model.gguf", len(data), hashlib.sha256(data).hexdigest()
    )
    part = tmp_path / "model.gguf.part"
    part.write_bytes(data[:300_000])
    free = {"bytes": SPACE_MARGIN + 100_000}  # less than the rest of the file
    fake_disk(monkeypatch, free)
    requests = []
    refuse = httpx.Client(transport=httpx.MockTransport(requests.append))
    with pytest.raises(NoSpace, match="disk space"):
        download(remote, tmp_path, refuse)
    assert not requests and part.stat().st_size == 300_000  # nothing fetched, nothing lost

    # The disk fills up while writing: the part stays for a later resume.
    free["bytes"] = 10 * GIB
    real_open = type(part).open

    class FullDisk:
        def __init__(self, f):
            self.f = f

        def write(self, chunk):
            raise OSError(errno.ENOSPC, "disk full")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.f.close()

    def open_(self, mode="r", *args, **kwargs):
        f = real_open(self, mode, *args, **kwargs)
        return FullDisk(f) if "a" in mode else f

    monkeypatch.setattr(type(part), "open", open_)
    with pytest.raises(NoSpace):
        download(remote, tmp_path, served(data))
    monkeypatch.undo()
    assert part.stat().st_size == 300_000
    assert download(remote, tmp_path, served(data)).read_bytes() == data


def test_the_manager_does_not_start_a_download_that_cannot_fit(tmp_path, monkeypatch):
    from adawork.ai.manager import ModelManager
    from adawork.ai.runtime import LlamaServer

    fake_disk(monkeypatch, {"bytes": 2 * GIB})
    settings = Settings.load(tmp_path / "config.json")

    def no_http():
        raise AssertionError("nothing may be downloaded")

    manager = ModelManager(
        lambda: settings.ai, tmp_path / "state.json", tmp_path / "models",
        LlamaServer(tmp_path, tmp_path / "log"), probe=lambda: make_hw(16, "Intel(R) Graphics"),
        http_factory=no_http, is_metered=lambda: False,
    )  # fmt: skip
    assert manager.start_download()
    manager.wait(5)
    assert manager.download.state == "failed" and "disk space" in manager.download.error


# --- client, grammar, extraction -----------------------------------------------------------


def test_compact_grammar_has_no_whitespace_rules():
    grammar = compact_grammar(schema(["9333", "4321"]))
    root = grammar.splitlines()[0]
    assert root.startswith('root ::= "{" "\\"profese\\"" ":" v0 ","')
    assert 'v0 ::= "\\"9333\\"" | "\\"4321\\"" | "\\"jiná\\""' in grammar
    assert "space" not in grammar and '"null"' in grammar


def test_grammar_integers_have_no_leading_zeros():
    import re

    grammar = compact_grammar(schema([], fields=("mzda",)))
    rule = next(line for line in grammar.splitlines() if line.startswith("v0 ::= "))
    body = rule.removeprefix("v0 ::= ").removesuffix(' | "null"')
    # This rule's GBNF is a regex once the quotes and spaces are gone.
    pattern = re.compile(body.replace('"', "").replace(" ", ""))
    assert all(pattern.fullmatch(n) for n in ("0", "-7", "35000", "123456789"))
    assert not any(pattern.fullmatch(n) for n in ("007", "-01", "1234567890"))


def test_grammar_array_with_a_minimum_length():
    spec = {"type": "array", "items": {"enum": ["a", "b"]}, "minItems": 1, "maxItems": 3}
    grammar = compact_grammar({"properties": {"x": spec}})
    assert 'v0 ::= "[" v0-item ( "," v0-item ){0,2} "]"' in grammar


def fake_server(answer: dict, seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(answer)}, "finish_reason": "stop"}],
                "timings": {
                    "prompt_n": 800,
                    "prompt_ms": 2000,
                    "predicted_n": 60,
                    "predicted_ms": 4000,
                },
            },
        )

    return httpx.MockTransport(handler)


def test_client_sends_a_grammar_and_reports_speed():
    seen = []
    client = LlmClient("http://ai", transport=fake_server({"a": 1}, seen))
    reply = client.chat([{"role": "user", "content": "x"}], schema=schema(["9333"]))
    assert reply.data == {"a": 1} and "grammar" in seen[0] and "response_format" not in seen[0]
    assert reply.timing.prompt_tps == 400 and reply.timing.output_tps == 15
    ollama = LlmClient("http://ai", native=False, transport=fake_server({"a": 1}, seen))
    ollama.chat([{"role": "user", "content": "x"}], schema=schema(["9333"]))
    assert seen[1]["response_format"]["type"] == "json_schema"


def test_transcribe_sends_a_small_jpeg_and_reads_no_text_as_empty():
    answers, seen = ["HLEDÁME ŘIDIČE\nMzda 42 000 Kč \n", "-"], []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        message = {"content": answers.pop(0)}
        return httpx.Response(200, json={"choices": [{"message": message}]})

    photo = BytesIO()
    Image.new("RGB", (4000, 3000), "white").save(photo, format="PNG")
    client = LlmClient("http://ai", transport=httpx.MockTransport(handler))
    assert vision.transcribe(client, photo.getvalue()).data == "HLEDÁME ŘIDIČE\nMzda 42 000 Kč"
    assert vision.transcribe(client, photo.getvalue()).data == ""
    url = seen[0]["messages"][1]["content"][0]["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    with Image.open(BytesIO(base64.b64decode(url.split(",", 1)[1]))) as sent:
        assert max(sent.size) == vision.MAX_IMAGE_SIDE


AD = """Skladník – Kolín
Logistika s.r.o.
Kolín

Mzda 32 000 – 38 000 Kč měsíčně, dvousměnný provoz, HPP. Vyučení výhodou."""


def test_validate_keeps_only_what_the_ad_supports():
    data = {
        "profese": "9333",
        "obec": "Kolín",
        "mzda_od": 32000,
        "mzda_do": 38000,
        "mzda_za": "měsíc",
        "smeny": ["dvousměnný"],
        "vzdelani": "vyučení",
    }
    ok = validate(data, AD, ["9333", "4321"])
    assert ok.isco_group == "9333" and ok.town == "Kolín"
    assert (ok.salary_min, ok.salary_max, ok.salary_unit) == (32000, 38000, "month")
    assert ok.shifts == ["dvouSm"] and ok.education == "vocational" and not ok.dropped

    invented = validate(
        data | {"profese": "1111", "obec": "Brno", "mzda_od": 45000, "mzda_do": None},
        AD,
        ["9333"],
    )
    assert invented.isco_group is None and invented.town is None
    assert invented.salary_min is None and len(invented.dropped) == 3

    assert validate({"obec": "Kolín"}, "Práce v Kolíně", []).town == "Kolín"
    assert validate({"obec": "Aš"}, "Pracujte v naší kanceláři", []).town is None

    thousands = validate({"mzda_od": 35000, "mzda_za": "měsíc"}, "plat od 35 tis. Kč", [])
    assert thousands.salary_min == 35000
    absurd = validate({"mzda_od": 120, "mzda_za": "měsíc"}, "120 Kč", [])
    assert absurd.salary_min is None


def test_schema_asks_only_for_missing_fields():
    full = schema(["9333"], untrusted=True)
    assert list(full["properties"])[0] == "nabidka" and "varovani" in full["properties"]
    small = schema([], fields=("smeny", "vzdelani"))
    assert list(small["properties"]) == ["smeny", "vzdelani"]
    message = user_message(AD, [Candidate("9333", "Pomocní pracovníci", 1.0, ("Skladník",))])
    assert message.startswith("Možné profese:\n9333 Pomocní pracovníci (např. Skladník)")


def test_ask_answer_becomes_filters():
    f = to_filters(
        {
            "slova": "sklad",
            "obor": ["Doprava", "Nesmysl"],
            "kraj": ["Středočeský kraj"],
            "uvazek": ["Plný úvazek"],
            "smeny": ["Jednosměnný provoz"],
            "mzda_od": 30000,
            "vzdelani": None,
            "dni": 7,
        }
    )
    assert (f.q, f.categories, f.kraj) == ("sklad", ["2"], ["Středočeský kraj"])
    assert f.contract_types == ["full_time"] and f.shifts == ["jednoSm"]
    assert f.salary_min == 30000 and f.posted_days == 7
    assert to_filters({"mzda_od": 3}).salary_min is None


def test_an_answer_of_the_wrong_shape_is_a_bad_answer():
    from adawork.ai.ask import ask
    from adawork.ai.client import BadAnswer, ServerDown

    client = LlmClient("http://ai", native=False, transport=fake_server([1, 2], []))
    with pytest.raises(BadAnswer, match="not a JSON object"):
        client.chat([{"role": "user", "content": "x"}], schema=schema(["9333"]))
    client = LlmClient("http://ai", native=False, transport=fake_server({"slova": 5}, []))
    with pytest.raises(BadAnswer):
        ask(client, "sklad")

    def down(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(ServerDown):
        LlmClient("http://ai", transport=httpx.MockTransport(down)).chat([], schema=schema([]))


# --- ISCO from similar titles --------------------------------------------------------------

CODEBOOK = IscoCodebook.load(
    __import__("pathlib").Path(__file__).parents[1] / "fixtures/sources/mpsv/codebooks/cz-isco.json"
)


def isco_index(tmp_path=None):
    index = IscoIndex(CODEBOOK, FakeEmbedder())
    examples = [("Skladník", "43210"), ("Skladník/skladnice", "43210"),
                ("Pomocný skladník", "93330"), ("Řidič nákladního automobilu", "83321"),
                ("Řidič autobusu", "83311"), ("Prodavač potravin", "52230")]  # fmt: skip
    index.build(examples, cache=tmp_path / "isco.npz" if tmp_path else None)
    return index


def test_isco_candidates_and_clear_winner(tmp_path):
    index = isco_index(tmp_path)
    found = index.lookup("Skladník / skladnice Kolín")
    assert found.candidates[0].code == "4321"
    assert found.clear_winner() and found.detail_code("4321") == "43210"
    assert "Úředníci" in found.candidates[0].name  # names come from the codebook
    assert CODEBOOK.categories("43210")  # every code maps to Úřad práce categories
    # A second build reuses the stored vectors.
    again = IscoIndex(CODEBOOK, None)
    again.build([("Skladník", "43210")], cache=tmp_path / "isco.npz")
    assert len(again) == 1


# --- enrichment ----------------------------------------------------------------------------


@pytest.fixture
def ai_setup(tmp_path):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    sessions = make_sessionmaker(engine)
    settings = Settings.load(tmp_path / "config.json")
    ai = make_ai(tmp_path, settings, sessions)
    ai.enricher._index = isco_index()
    ai.enricher.index = lambda session: ai.enricher._index
    return sessions, settings, ai


def add_portal_job(sessions, external_id, title, description, **fields):
    with sessions() as s:
        store = JobStore(s, "prace_cz")
        store.add(
            ParsedJob(external_id=external_id, title=title, description=description, **fields)
        )
        store.commit()
        return s.scalar(select(Job.id).order_by(Job.id.desc()))


def test_rules_give_portal_jobs_an_occupation(ai_setup):
    sessions, _, ai = ai_setup
    job_id = add_portal_job(sessions, "1", "Skladník", "")
    vague = add_portal_job(sessions, "2", "Kouzelník", "Nabízíme práci na plný i zkrácený úvazek.")
    assert ai.enricher.run_rules() == 1
    with sessions() as s:
        job = s.get(Job, job_id)
        assert job.isco_code == "43210" and job.categories
        assert job.extra["origin"]["isco_code"] == "knn"
        assert s.get(Job, vague).isco_code is None and s.get(Job, vague).rules_hash
        assert s.get(Job, vague).categories == ["0"]  # "Nezařazeno" until the AI finds one
        assert s.get(Job, vague).contract_types == ["full_time", "part_time"]  # from the text
        assert s.get(Job, vague).extra["origin"]["contract_types"] == "rules"
    assert ai.enricher.run_rules() == 0  # nothing is processed twice


class FakeSession:
    def __init__(self, answer):
        self.answer, self.calls = answer, 0
        self.model = "fake"

    def chat(self, messages, schema=None, max_tokens=0, temperature=0):
        from adawork.ai.client import Reply

        self.calls += 1
        data = {k: v for k, v in self.answer.items() if k in schema["properties"]}
        return Reply(data, Timing(800, 2.0, 60, 4.0, 6.0))


def test_ai_fills_only_missing_fields_and_caches(ai_setup, municipalities, monkeypatch):
    sessions, _, ai = ai_setup
    with sessions() as s:
        municipalities(s)
    text = "Hledáme skladníka do Kolína. " + AD + " " * 200
    job_id = add_portal_job(sessions, "1", "Skladník", text, contract_types=["part_time"])
    fake = FakeSession({
        "profese": "4321", "obec": "Kolín", "mzda_od": 32000,
        "mzda_do": 38000, "mzda_za": "měsíc", "smeny": ["dvousměnný"], "vzdelani": "vyučení",
    })  # fmt: skip
    from contextlib import contextmanager

    @contextmanager
    def session(vision=False, priority=Priority.AI):
        yield fake

    monkeypatch.setattr(ai.manager, "session", session)
    monkeypatch.setattr(type(ai.manager), "ready", property(lambda self: True))
    assert ai.enricher.run_ai() == 1
    with sessions() as s:
        job = s.get(Job, job_id)
        assert job.contract_types == ["part_time"]  # the source said so: kept
        assert job.location.municipality == "Kolín" and job.salary_month_min == 32000
        assert job.shifts == ["dvouSm"] and job.education == "vocational"
        assert job.isco_code == "43210" and job.ai_hash == job.content_hash
        assert job.extra["origin"]["shifts"] == "ai"
        assert s.scalar(select(LlmCache.key)) is not None
    assert ai.enricher.queue(sessions()) == []  # done until the ad changes

    # The same ad again (re-listed under a new id): answered from the cache.
    add_portal_job(sessions, "2", "Skladník", text, contract_types=["part_time"])
    assert ai.enricher.run_ai() == 1
    assert fake.calls == 1


def test_profile_matches_come_first(ai_setup, municipalities):
    sessions, settings, ai = ai_setup
    with sessions() as s:
        municipalities(s)
    settings.profile.kinds_of_work = ["řidič"]
    long = "x" * 300
    other = add_portal_job(sessions, "1", "Prodavač", long)
    driver = add_portal_job(sessions, "2", "Řidič autobusu", long)
    with sessions() as s:
        assert ai.enricher.queue(s) == [driver, other]
        ai.manager.state.speeds[TIERS["good"]] = {}
    # On a slow PC only the jobs matching the profile are processed.
    ai.manager.measured = lambda: {"x": Speed(10, 2, True)}
    ai.manager.active_model = lambda: type("M", (), {"id": "x"})()
    with sessions() as s:
        assert ai.enricher.queue(s) == [driver]


def test_vague_kinds_of_work_match_by_occupation(ai_setup, monkeypatch):
    from contextlib import contextmanager

    from adawork.search import kinds

    sessions, settings, ai = ai_setup
    settings.profile.kinds_of_work = ["ve skladu"]  # no word in common with any title
    long = "x" * 300
    seller = add_portal_job(sessions, "1", "Prodavač", long, isco_code="52230")
    store = add_portal_job(sessions, "2", "Manipulace se zbožím", long, isco_code="43210")
    # The model may only pick listed groups; a pick from another major group is dropped.
    fake = FakeSession({"profese": ["4321", "9333", "5223"]})

    @contextmanager
    def session(vision=False, priority=Priority.AI):
        yield fake

    monkeypatch.setattr(ai.manager, "session", session)
    monkeypatch.setattr(type(ai.manager), "ready", property(lambda self: True))
    ai.enricher.resolve_kinds()
    ai.enricher.resolve_kinds()  # mapped once
    assert fake.calls == 1
    with sessions() as s:
        assert kinds.load(s)["ve skladu"]["codes"] == ["4321"]
        assert kinds.searches(s, ["ve skladu"]) == ["Skladník"]
        assert ai.enricher.queue(s) == [store, seller]
        # Searched for, the phrase finds its occupation's ads without a word in common.
        from adawork.search.service import search_jobs

        assert [j.id for j in search_jobs(s, "Ve skladu").jobs] == [store]
        assert {j.id for j in search_jobs(s, "prodavač, ve skladu").jobs} == {store, seller}


def test_kinds_of_work_without_a_model():
    from adawork.ai import wanted
    from adawork.search.kinds import Wanted

    index = isco_index()
    assert wanted.resolve(index, "Prodavač potravin") == {
        "codes": ["5223"],
        "search": "",
        "ai": True,
    }
    assert wanted.resolve(index, "kouzelník")["codes"] == []  # no dominant group: words only
    assert (
        wanted.plain_title("Vychovatelka/vychovatel školní družiny")
        == "Vychovatelka školní družiny"
    )
    assert wanted.plain_title("UČITEL - UČITELKA MŠ") == "UČITEL"
    assert wanted.plain_title("skladník/ce (m+ž)") == "skladník"
    want = Wanted((("ridic",), ("it",)), ("832",))
    assert want.matches("Řidič/ka sk. B", None) and want.matches("Kurýr", "83221")
    assert want.matches("IT technik", None) and not want.matches("Kontrolor kvality", None)
    assert not want.matches("Kurýr", "96210") and Wanted().matches("cokoli", None)


# --- the model manager ---------------------------------------------------------------------


def fake_download(manager, *model_ids):
    for model_id in model_ids:
        for f in MODELS[model_id].files():
            path = manager.models_dir / model_id / f.name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")


def test_auto_upgrades_after_a_ram_upgrade_and_keeps_the_old_model_until_then(tmp_path):
    from adawork.ai.manager import ModelManager
    from adawork.ai.runtime import LlamaServer

    settings = Settings.load(tmp_path / "config.json")
    hw = {"now": make_hw(8, "AMD Radeon(TM) Graphics", 0.5)}
    manager = ModelManager(
        lambda: settings.ai, tmp_path / "state.json", tmp_path / "models",
        LlamaServer(tmp_path, tmp_path / "log"), probe=lambda: hw["now"],
        is_metered=lambda: False,
    )  # fmt: skip
    simple, good = TIERS["simple"], TIERS["good"]
    assert manager.target().id == simple and manager.active_model() is None
    fake_download(manager, simple)
    manager.run_benchmark = lambda spec: Speed(300, 15, True)
    manager._try_switch(MODELS[simple])
    assert manager.active_model().id == simple

    hw["now"] = make_hw(16, "AMD Radeon(TM) Graphics", 0.5, cores=8)  # RAM upgrade
    assert manager.refresh_hardware()
    assert manager.target().id == good and not manager.tier_still_right()
    assert manager.active_model().id == simple  # the old model works until the new is ready
    fake_download(manager, good)
    manager._try_switch(MODELS[good])
    assert manager.active_model().id == good
    assert (tmp_path / "models" / simple).exists()  # kept: only the user deletes models
    assert not manager.delete_model(good)  # the model in use cannot be deleted
    assert manager.delete_model(simple) and not (tmp_path / "models" / simple).exists()
    assert manager.take_notice() == ("ai.notice.upgraded", {"model": MODELS[good].name})
    assert manager.take_notice() is None

    # Real work turns out too slow on this PC: Auto moves down a tier.
    for _ in range(40):
        manager.record(Timing(800, 20.0, 60, 30.0, 50.0))
    assert manager.target().id == simple and not manager.tier_still_right()


def test_auto_rejects_a_model_that_benchmarks_too_slow(tmp_path):
    from adawork.ai.manager import ModelManager
    from adawork.ai.runtime import LlamaServer

    settings = Settings.load(tmp_path / "config.json")
    manager = ModelManager(
        lambda: settings.ai, tmp_path / "state.json", tmp_path / "models",
        LlamaServer(tmp_path, tmp_path / "log"),
        probe=lambda: make_hw(16, "AMD Radeon(TM) Graphics", 0.5, cores=8),
        is_metered=lambda: True,
    )  # fmt: skip
    assert manager.start_download()  # the metered check runs in the download thread
    manager.wait(5)
    assert manager.download.state == "waiting_metered"
    good = TIERS["good"]
    fake_download(manager, good)
    manager.run_benchmark = lambda spec: Speed(20, 1.5, True)  # far too slow
    manager._try_switch(MODELS[good])
    assert manager.state.active is None and good in manager.state.rejected
    assert manager.target() is None
    settings.ai.choice = "off"
    assert manager.active_model() is None and manager.target() is None


def test_session_waits_when_memory_is_low(tmp_path):
    from adawork.ai.manager import Busy, ModelManager
    from adawork.ai.runtime import LlamaServer

    settings = Settings.load(tmp_path / "config.json")
    manager = ModelManager(
        lambda: settings.ai, tmp_path / "state.json", tmp_path / "models",
        LlamaServer(tmp_path, tmp_path / "log", available_ram=lambda: GIB),
        probe=lambda: make_hw(8, "AMD Radeon(TM) Graphics", 0.5),
        is_metered=lambda: False,
    )  # fmt: skip
    with pytest.raises(Busy, match="no_model"), manager.session():
        pass
    fake_download(manager, TIERS["simple"])
    manager.state.active = TIERS["simple"]
    manager.resources.available_ram = lambda: GIB
    manager.resources.idle_seconds = lambda: 0  # the user is working
    with pytest.raises(Busy, match="pc_busy"), manager.session():
        pass
    manager.resources.idle_seconds = lambda: 900  # away, but still not enough RAM to load
    with pytest.raises(Busy, match="memory"), manager.session():
        pass

    def no_slot(priority, timeout=None):
        raise TimeoutError("no free slot")

    manager.resources.heavy = no_slot
    with pytest.raises(Busy, match="pc_busy"), manager.session(priority=Priority.USER):
        pass


def test_the_metered_check_does_not_block_and_is_cached(tmp_path):
    from adawork.ai.manager import ModelManager
    from adawork.ai.runtime import LlamaServer

    asked, answer = [], threading.Event()

    def is_metered():  # PowerShell takes seconds
        asked.append(True)
        answer.wait(5)
        return True

    settings = Settings.load(tmp_path / "config.json")
    manager = ModelManager(
        lambda: settings.ai, tmp_path / "state.json", tmp_path / "models",
        LlamaServer(tmp_path, tmp_path / "log"), probe=lambda: make_hw(16, "Intel(R) Graphics"),
        is_metered=is_metered,
    )  # fmt: skip
    started = time.perf_counter()
    assert manager.start_download()  # e.g. from the app's start-up, on the event loop
    assert time.perf_counter() - started < 1
    answer.set()
    manager.wait(5)
    assert manager.download.state == "waiting_metered"
    assert manager.start_download()
    manager.wait(5)
    assert manager.download.state == "waiting_metered" and len(asked) == 1


@pytest.mark.parametrize(
    ("result", "metered"),
    [("Fixed", True), ("Variable", True), ("Unrestricted", False), ("Unknown", False),
     (subprocess.TimeoutExpired("powershell", 10), False), (OSError("no powershell"), False)],
)  # fmt: skip
def test_is_metered_reads_the_connection_cost(monkeypatch, result, metered):
    from adawork.ai import download as dl

    def run(args, **kwargs):
        if isinstance(result, Exception):
            raise result
        return subprocess.CompletedProcess(args, 0, stdout=result + "\r\n")

    monkeypatch.setattr(dl.sys, "platform", "win32")
    monkeypatch.setattr(dl.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(dl.subprocess, "run", run)
    assert dl.is_metered() is metered


def test_real_work_changes_the_tier_only_after_enough_calls(tmp_path):
    from adawork.ai.manager import MIN_SAMPLES, ModelManager
    from adawork.ai.runtime import LlamaServer

    settings = Settings.load(tmp_path / "config.json")
    manager = ModelManager(
        lambda: settings.ai, tmp_path / "state.json", tmp_path / "models",
        LlamaServer(tmp_path, tmp_path / "log"),
        probe=lambda: make_hw(8, "AMD Radeon(TM) Graphics", 0.5), is_metered=lambda: False,
    )  # fmt: skip
    fake_download(manager, TIERS["simple"])
    manager.state.active = TIERS["simple"]
    for _ in range(MIN_SAMPLES - 1):
        manager.record(Timing(800, 20.0, 60, 30.0, 50.0))
    assert manager.measured() == {}  # a few slow calls are not a verdict yet
    manager.record(Timing(800, 20.0, 60, 30.0, 50.0))
    assert TIERS["simple"] in manager.measured()


def test_amd_integrated_graphics_with_a_large_reservation_are_not_a_graphics_card():
    from dataclasses import replace

    from adawork.system.hardware import Gpu

    def gpu(name, vram_gb):
        return Gpu(name, "AMD", int(vram_gb * GIB), 8 * GIB)

    for name in ("AMD Radeon(TM) Graphics", "AMD Radeon 780M Graphics", "AMD Radeon(TM) 890M",
                 "AMD Radeon(TM) 8060S Graphics", "AMD Radeon(TM) Vega 8 Graphics"):  # fmt: skip
        assert not gpu(name, 8).is_dedicated, name
    for name in ("AMD Radeon RX 7600M XT", "AMD Radeon RX 6600", "NVIDIA GeForce RTX 4060"):
        assert gpu(name, 8).is_dedicated, name
    hw = make_hw(32, "AMD Radeon 780M Graphics", 16, cores=8)
    assert hw.dedicated_gpu is None and advisor.speed_class(hw).name.startswith("AMD Radeon 760M")
    both = replace(hw, gpus=(*hw.gpus, gpu("NVIDIA GeForce RTX 4060", 8)))
    assert both.dedicated_gpu.name == "NVIDIA GeForce RTX 4060"


def test_launch_is_hashable_for_restart_checks(tmp_path):
    a = Launch(tmp_path / "m.gguf", None, "vulkan", None, 4, 4096, GIB)
    assert a == Launch(tmp_path / "m.gguf", None, "vulkan", None, 4, 4096, GIB)
    with pytest.raises(LlmError):
        raise LlmError("x")


def test_hardware_probe_is_quick_and_sane():
    from adawork.system.hardware import probe

    started = time.perf_counter()
    hw = probe()
    assert time.perf_counter() - started < 2
    assert hw.total_ram > GIB and hw.physical_cores >= 1 and hw.free_disk > 0
    assert hw.fingerprint() == probe().fingerprint()  # free memory does not change it


def test_scrape_runner_starts_enrichment_after_a_run(tmp_path):
    from adawork.scraper.runner import RunHooks, ScrapeRunner

    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    started = []
    runner = ScrapeRunner(
        make_sessionmaker(engine), tmp_path, sources=(),
        geocode=False, hooks=RunHooks(after_run=lambda: started.append(True)),
    )  # fmt: skip
    runner.start()
    runner.wait(10)
    assert runner.progress.phase == "done" and started == [True]


def test_listing_cards_only_ask_for_the_occupation(ai_setup):
    from adawork.ai.enrich import missing_fields

    sessions, _, ai = ai_setup
    card = add_portal_job(sessions, "1", "Kouzelník", "")
    full = add_portal_job(sessions, "2", "Kouzelník na akce", "Hledáme kouzelníka. " * 20)
    with sessions() as s:
        assert missing_fields(s.get(Job, card)) == ("profese",)
        assert "smeny" in missing_fields(s.get(Job, full))
        assert ai.enricher.queue(s) == [full, card]  # full ads first


def fake_ai_session(ai, monkeypatch, client):
    from contextlib import contextmanager

    @contextmanager
    def session(vision=False, priority=Priority.AI):
        yield client

    monkeypatch.setattr(ai.manager, "session", session)
    monkeypatch.setattr(type(ai.manager), "ready", property(lambda self: True))


def test_an_unusable_answer_skips_only_that_ad(ai_setup, monkeypatch):
    from adawork.ai.client import BadAnswer

    sessions, _, ai = ai_setup
    long = " Hledáme posilu do skladu." * 20
    first = add_portal_job(sessions, "1", "Skladník", "A" + long)
    second = add_portal_job(sessions, "2", "Skladník", "B" + long)
    fake = FakeSession({"profese": "4321"})
    answer = fake.chat

    def chat(messages, **kw):
        if messages[1]["content"].split("Inzerát:\n")[1].split("\n\n")[1].startswith("A"):
            raise BadAnswer("answer was cut off (max_tokens)")
        return answer(messages, **kw)

    fake.chat = chat
    fake_ai_session(ai, monkeypatch, fake)
    assert ai.enricher.run_ai() == 2
    with sessions() as s:
        assert s.get(Job, first).ai_hash == s.get(Job, first).content_hash
        assert s.get(Job, first).isco_code is None
        assert s.get(Job, second).isco_code == "43210"


def test_a_cached_answer_keeps_the_occupation_when_names_are_offered(ai_setup, monkeypatch):
    from dataclasses import replace

    from adawork.ai.catalog import PromptStyle

    sessions, _, ai = ai_setup
    by_name = replace(MODELS[TIERS["simple"]], prompt=PromptStyle(by_name=True))
    monkeypatch.setattr(ai.manager, "active_model", lambda: by_name)
    text = "Hledáme skladníka. " * 20

    class ByName(FakeSession):
        def chat(self, messages, schema=None, max_tokens=0, temperature=0):
            self.answer = {"profese": schema["properties"]["profese"]["enum"][0]}
            return super().chat(messages, schema, max_tokens, temperature)

    fake = ByName({})
    fake_ai_session(ai, monkeypatch, fake)
    first = add_portal_job(sessions, "1", "Skladník dnes", text)
    assert ai.enricher.run_ai() == 1
    again = add_portal_job(sessions, "2", "Skladník dnes", text)
    assert ai.enricher.run_ai() == 1 and fake.calls == 1
    with sessions() as s:
        assert s.get(Job, again).isco_code == s.get(Job, first).isco_code is not None


def test_another_model_waits_until_the_running_one_is_released(tmp_path):
    from adawork.ai.runtime import LlamaServer

    class Proc:
        def __init__(self, launch):
            self.launch, self.ended = launch, False

        def poll(self):
            return 0 if self.ended else None

        def terminate(self):
            self.ended = True
            events.append(f"stop {self.launch.weights.name}")

        def wait(self, timeout=None):
            return 0

    events = []
    server = LlamaServer(tmp_path, tmp_path / "log", available_ram=lambda: 64 * GIB)
    server._start = lambda launch: setattr(server, "_proc", Proc(launch))
    a = Launch(tmp_path / "a.gguf", None, "cpu", 0, 4, 4096, GIB)
    b = Launch(tmp_path / "b.gguf", None, "cpu", 0, 4, 4096, GIB)
    inside = threading.Event()

    def use_a():
        with server.use(a):
            inside.set()
            time.sleep(0.3)
            events.append("a done")

    worker = threading.Thread(target=use_a)
    worker.start()
    inside.wait(5)
    with server.use(b):
        events.append("b running")
    worker.join(5)
    assert events == ["a done", "stop a.gguf", "b running"]


def test_a_custom_server_does_not_change_the_local_model_speed(tmp_path):
    from adawork.ai.manager import ModelManager
    from adawork.ai.runtime import LlamaServer

    settings = Settings.load(tmp_path / "config.json")
    manager = ModelManager(
        lambda: settings.ai, tmp_path / "state.json", tmp_path / "models",
        LlamaServer(tmp_path, tmp_path / "log"),
        probe=lambda: make_hw(8, "AMD Radeon(TM) Graphics", 0.5), is_metered=lambda: False,
    )  # fmt: skip
    fake_download(manager, TIERS["simple"])
    manager.state.active = TIERS["simple"]
    settings.ai.server_url = "http://127.0.0.1:11434"
    manager.record(Timing(800, 1.0, 60, 1.0, 2.0))
    assert manager.state.speeds == {}
    settings.ai.server_url = ""
    manager.record(Timing(800, 1.0, 60, 1.0, 2.0))
    assert TIERS["simple"] in manager.state.speeds


def test_the_isco_index_is_rebuilt_when_titles_change(tmp_path):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    sessions = make_sessionmaker(engine)
    ai = make_ai(tmp_path, Settings.load(tmp_path / "config.json"), sessions)
    with sessions() as s:
        store = JobStore(s, "mpsv")
        store.add(ParsedJob(external_id="1", title="Skladník", isco_code="43210"))
        store.commit()
        first = ai.enricher.index(s)
        assert "Skladník" in first.titles
        s.execute(Job.__table__.update().values(title="Řidič autobusu", isco_code="83311"))
        s.commit()
        second = ai.enricher.index(s)
        assert second is not first and "Řidič autobusu" in second.titles
        assert ai.enricher.index(s) is second


# --- llama-server ----------------------------------------------------------------------------


def fake_llama(tmp_path, monkeypatch, runs: list, free_ram: dict):
    """A LlamaServer whose llama-server.exe is faked. Each start takes the next of `runs`:
    "ok", "slow" (never ready) or the text the process logs before it exits."""
    from adawork.ai import runtime

    for backend in ("vulkan", "cpu"):
        exe = tmp_path / "llama.cpp" / backend / "llama-server.exe"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.touch()
    started = []

    class Proc:
        pid = 1

        def __init__(self, args, stdout, **kwargs):
            self.args, self.run, self.code = args, runs.pop(0), None
            started.append(self)
            if self.run not in ("ok", "slow"):
                stdout.write(self.run.encode())
                self.code = 1

        def poll(self):
            return self.code

        def terminate(self):
            self.code = 0

        kill = terminate

        def wait(self, timeout=None):
            return self.code

    def health(request):
        return httpx.Response(200 if started[-1].run == "ok" else 503)

    real_client = httpx.Client
    monkeypatch.setattr(runtime.subprocess, "Popen", Proc)
    monkeypatch.setattr(
        runtime.httpx, "Client", lambda timeout: real_client(transport=httpx.MockTransport(health))
    )
    monkeypatch.setattr(runtime.winjob, "bind_to_app", lambda pid: True)
    monkeypatch.setattr(runtime, "START_TIMEOUT", 0.5)
    server = runtime.LlamaServer(
        tmp_path, tmp_path / "logs" / "llama-server.log", available_ram=lambda: free_ram["bytes"]
    )
    return server, started


PORT_TAKEN = "error: couldn't bind HTTP server socket"


def test_llama_server_start_ready_exited_or_timed_out(tmp_path, monkeypatch):
    from adawork.ai.runtime import ServerFailed

    runs = ["ok", PORT_TAKEN, "ggml_vulkan: device lost", "slow"]
    server, started = fake_llama(tmp_path, monkeypatch, runs, {"bytes": 64 * GIB})
    launch = Launch(tmp_path / "m.gguf", None, "vulkan", None, 4, 4096, GIB)
    server._start(launch)
    assert server.running and str(server._port) in started[0].args
    server.stop()
    with pytest.raises(ServerFailed, match="exited") as port_taken:
        server._start(launch)
    assert port_taken.value.transient  # another program took the port: worth a retry
    with pytest.raises(ServerFailed, match="exited") as crashed:
        server._start(launch)
    assert not crashed.value.transient  # only this start's log lines count
    with pytest.raises(ServerFailed, match="ready") as slow:
        server._start(launch)
    assert slow.value.transient and not server.running and started[-1].code == 0


def test_vulkan_is_given_up_only_when_it_crashes(tmp_path, monkeypatch):
    from adawork.ai.runtime import NotEnoughMemory, ServerFailed

    runs = [PORT_TAKEN, PORT_TAKEN, PORT_TAKEN, "ok", "ggml_vulkan: device lost"]
    free = {"bytes": 4 * GIB}
    server, started = fake_llama(tmp_path, monkeypatch, runs, free)
    # 2 GB in RAM next to a graphics card; 6 GB with the whole model in RAM.
    gpu = Launch(tmp_path / "m.gguf", None, "vulkan", None, 4, 4096, 2 * GIB, 6 * GIB)
    with pytest.raises(ServerFailed):
        server.ensure(gpu)
    assert len(started) == 2 and not server.vulkan_broken  # tried twice, Vulkan not blamed
    server.ensure(gpu)
    assert len(started) == 4 and not server.vulkan_broken  # the port race was retried
    server.stop()
    with pytest.raises(NotEnoughMemory):  # the CPU build would need more than is free
        server.ensure(gpu)
    assert server.vulkan_broken and len(started) == 5
    free["bytes"], runs[:] = 64 * GIB, ["ok"]
    server.ensure(gpu)
    assert "cpu" in started[-1].args[0] and "--device" in started[-1].args
    server.stop()

    spec = MODELS[TIERS["good"]]
    launch = advisor.launch_for(spec, make_hw(16, "NVIDIA GeForce RTX 3050", 4), tmp_path)
    assert launch.ram_need < launch.cpu_ram_need == advisor.ram_need(spec)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows job objects")
def test_llama_server_ends_with_the_app():
    import psutil

    app = (
        "import subprocess, sys\n"
        "from adawork.system import winjob\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(child.pid, winjob.bind_to_app(child.pid))\n"
    )  # binds a child, then ends like a crashed app: without stopping it
    out = subprocess.run([sys.executable, "-c", app], capture_output=True, text=True, timeout=60)
    pid, bound = out.stdout.split()
    try:
        assert bound == "True"
        deadline = time.monotonic() + 10
        while psutil.pid_exists(int(pid)) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not psutil.pid_exists(int(pid))
    finally:
        if psutil.pid_exists(int(pid)):
            psutil.Process(int(pid)).kill()


# --- enrichment: failures and facets ---------------------------------------------------------


def test_a_failing_ad_is_skipped_and_a_dead_server_stops_the_run(ai_setup, monkeypatch):
    from adawork.ai.client import LlmError, ServerDown

    sessions, _, ai = ai_setup
    long = " Hledáme posilu do skladu." * 20
    broken = add_portal_job(sessions, "1", "Skladník", "ERROR" + long)
    odd = add_portal_job(sessions, "2", "Skladník", "ODD" + long)
    fine = add_portal_job(sessions, "3", "Skladník", "FINE" + long)
    fake = FakeSession({"profese": "4321"})
    answer = fake.chat

    def chat(messages, **kw):
        if "ERROR" in messages[1]["content"]:
            raise LlmError("AI server answered 500: context too long")
        if "ODD" in messages[1]["content"]:
            raise KeyError("something unexpected")
        return answer(messages, **kw)

    fake.chat = chat
    fake_ai_session(ai, monkeypatch, fake)
    assert ai.enricher.run_ai() == 3
    with sessions() as s:
        for job_id in (broken, odd):  # not first in the queue again at the next run
            assert s.get(Job, job_id).ai_hash == s.get(Job, job_id).content_hash
        assert s.get(Job, fine).isco_code == "43210"

    down = add_portal_job(sessions, "4", "Skladník", "DOWN" + long)

    def unreachable(messages, **kw):
        raise ServerDown("AI server unreachable")

    fake.chat = unreachable
    with pytest.raises(ServerDown):
        ai.enricher.run_ai()
    with sessions() as s:
        assert s.get(Job, down).ai_hash is None  # tried again when the server is back


def test_quitting_mid_answer_is_not_an_error(ai_setup, monkeypatch, caplog):
    from adawork.ai.client import ServerDown

    sessions, _, ai = ai_setup
    add_portal_job(sessions, "1", "Skladník", "Hledáme posilu do skladu." * 20)
    fake = FakeSession({})

    def quitting(messages, **kw):  # the app stops the AI server while it answers
        ai.enricher.stop()
        raise ServerDown("AI server unreachable")

    fake.chat = quitting
    fake_ai_session(ai, monkeypatch, fake)
    monkeypatch.setattr(type(ai.manager), "ready", property(lambda self: True))
    ai.enricher._run()
    assert ai.enricher.progress.phase == "done" and not ai.enricher.progress.running
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


def test_no_model_call_without_occupations_to_offer(ai_setup, monkeypatch):
    sessions, _, ai = ai_setup
    ai.enricher._index = None  # e.g. no Úřad práce import yet
    card = add_portal_job(sessions, "1", "Kouzelník", "")
    fake = FakeSession({})
    fake_ai_session(ai, monkeypatch, fake)
    assert ai.enricher.run_ai() == 1 and fake.calls == 0
    with sessions() as s:
        assert s.get(Job, card).ai_hash == s.get(Job, card).content_hash


def test_facet_counts_follow_the_rules(ai_setup, monkeypatch):
    from datetime import datetime, timedelta
    from itertools import count

    from adawork.ai import enrich
    from adawork.search.service import search_jobs

    # Windows' clock moves in ~16 ms steps: a change right after the insert could get the same
    # time, so the rules get a clock that always moves on.
    ticks = count(1)
    monkeypatch.setattr(enrich, "utcnow", lambda: datetime(2030, 1, 1) + timedelta(next(ticks)))
    sessions, _, ai = ai_setup
    typed = add_portal_job(sessions, "1", "Kouzelník", "Práce na DPP, " * 5, categories=["5"])
    with sessions() as s:
        assert search_jobs(s, facets=True).facets["contract_types"] == []  # now cached
    ai.enricher.run_rules()  # contract types from the text
    with sessions() as s:
        found = s.get(Job, typed).contract_types
        assert found and search_jobs(s, facets=True).facets["contract_types"] == [(found[0], 1)]

    vague = add_portal_job(sessions, "2", "Kouzelník", "")
    with sessions() as s:
        assert search_jobs(s, facets=True).facets["categories"] == [("5", 1)]
    ai.enricher.run_rules()  # "Nezařazeno"
    with sessions() as s:
        assert ("0", 1) in search_jobs(s, facets=True).facets["categories"]
        assert s.get(Job, vague).updated_at > s.get(Job, vague).first_seen_at
