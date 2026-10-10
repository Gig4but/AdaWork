"""Ad pictures: hashing, download, repost merging, clean-up and reading their text."""

import hashlib
import json
from contextlib import contextmanager
from dataclasses import asdict
from datetime import timedelta
from io import BytesIO

import httpx
import pytest
from conftest import make_ai
from PIL import Image, ImageDraw
from sqlalchemy import select

from adawork.ai.client import Reply, Timing
from adawork.config import Settings
from adawork.core import ParsedJob, utcnow
from adawork.db.models import Job, JobImage, JobSource
from adawork.db.session import make_engine, make_sessionmaker, migrate
from adawork.net.polite import PoliteFetcher
from adawork.scraper.images import ImageStore, dhash, distance, shrink
from adawork.scraper.store import JobStore
from adawork.system.resources import Priority


def poster(text: str = "HLEDÁME ŘIDIČE", size=(1600, 1200), fmt="PNG") -> bytes:
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle((100, 100, size[0] // 2, size[1] // 3), fill="navy")
    draw.text((150, size[1] // 2), text, fill="black")
    draw.ellipse((size[0] // 2, size[1] // 2, size[0] - 100, size[1] - 100), fill="red")
    out = BytesIO()
    img.save(out, format=fmt)
    return out.getvalue()


def copy_of(data: bytes, size=(900, 675)) -> bytes:
    """The same picture smaller and as a JPEG, as a repost often is."""
    with Image.open(BytesIO(data)) as img:
        out = BytesIO()
        img.convert("RGB").resize(size).save(out, format="JPEG", quality=70)
    return out.getvalue()


def other_picture() -> bytes:
    img = Image.new("RGB", (800, 800), "black")
    ImageDraw.Draw(img).rectangle((400, 0, 800, 400), fill="yellow")
    out = BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


@pytest.fixture
def sessions(tmp_path):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    yield make_sessionmaker(engine)
    engine.dispose()


def add(sessions, source_id, external_id, title, text, images):
    with sessions() as s:
        store = JobStore(s, source_id)
        store.add(ParsedJob(external_id, title, description=text, images=images))
        store.commit()


def fetcher_for(routes: dict[str, bytes]) -> PoliteFetcher:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        body = routes.get(str(request.url))
        return httpx.Response(200, content=body) if body else httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return PoliteFetcher(client, delay=0, sleep=lambda s: None)


def test_hash_survives_resizing_and_recompression_but_tells_pictures_apart():
    big = shrink(poster())
    small = shrink(copy_of(poster()))
    assert distance(big[3], small[3]) <= 4
    assert distance(big[3], shrink(other_picture())[3]) > 10
    jpeg, width, height, _ = shrink(poster(size=(4000, 3000)))
    assert (width, height) == (1280, 960) and jpeg[:2] == b"\xff\xd8"
    assert len(dhash(Image.new("RGB", (10, 10)))) == 16


def test_jobs_without_pictures_keep_their_content_hash():
    job = ParsedJob("1", "Skladník", description="Text")
    data = asdict(job)
    data.pop("external_id")
    data.pop("images")
    old = hashlib.sha256(
        json.dumps(data, sort_keys=True, default=str, ensure_ascii=False).encode()
    ).hexdigest()
    assert job.content_hash() == old
    assert ParsedJob("1", "Skladník", description="Text", images=["x"]).content_hash() != old


def test_pictures_are_downloaded_and_reposts_merged(sessions, tmp_path):
    text = "Přijmeme řidiče sk. C do Kolína, nástup ihned, mzda 42 000 Kč, volejte."
    add(sessions, "board_a", "1", "Řidič C – Kolín", text, ["https://a.cz/p1.png"])
    add(sessions, "board_b", "9", "Řidič C Kolín", text + " Díky.", ["https://b.cz/p9.jpg"])
    # The same employer logo on a different offer: not a repost.
    add(sessions, "board_b", "5", "Prodavačka Kolín", "Úplně jiná práce v obchodě.",
        ["https://b.cz/p5.jpg"])  # fmt: skip
    add(sessions, "board_b", "6", "Skladník", "Bez obrázku.", ["https://b.cz/gone.jpg"])
    store = ImageStore(sessions, tmp_path / "images")
    fetcher = fetcher_for({
        "https://a.cz/p1.png": poster(),
        "https://b.cz/p9.jpg": copy_of(poster()),
        "https://b.cz/p5.jpg": poster(),
    })  # fmt: skip
    assert store.download(fetcher) == 3
    with sessions() as s:
        statuses = dict(s.execute(select(JobImage.url, JobImage.status)).all())
        assert statuses["https://b.cz/gone.jpg"] == "failed"
        kept = s.scalars(select(JobImage).where(JobImage.file.is_not(None)))
        files = [store.path(i) for i in kept]
        assert all(p.exists() for p in files) and len(files) == 3

    assert store.merge_reposts() == 1
    with sessions() as s:
        titles = sorted(s.scalars(select(Job.title)))
        assert titles == ["Prodavačka Kolín", "Skladník", "Řidič C – Kolín"]
        driver = s.scalar(select(Job).where(Job.title == "Řidič C – Kolín"))
        assert sorted((src.source_id, src.is_primary) for src in driver.sources) == [
            ("board_a", True),
            ("board_b", False),
        ]
    assert store.merge_reposts() == 0

    # Pictures of jobs gone for a month are deleted with their files.
    with sessions() as s:
        job = s.scalar(select(Job).where(Job.title == "Prodavačka Kolín"))
        job.status, job.updated_at = "expired", utcnow() - timedelta(days=40)
        path = store.path(job.images[0])
        s.commit()
    assert store.clean_up() == 1 and not path.exists()
    with sessions() as s:
        assert s.scalar(select(JobSource.id).where(JobSource.external_id == "5")) is not None


def test_poster_text_is_added_to_a_short_ad_and_read_by_the_rules(sessions, tmp_path):
    add(sessions, "board_a", "1", "Řidič", "Info na plakátu.", ["https://a.cz/p1.png"])
    add(sessions, "board_a", "2", "Skladník", "x" * 700, ["https://a.cz/p2.png"])  # long ad
    images = ImageStore(sessions, tmp_path / "images")
    images.download(fetcher_for({"https://a.cz/p1.png": poster(), "https://a.cz/p2.png": poster()}))

    ai = make_ai(tmp_path, Settings.load(tmp_path / "config.json"), sessions)
    ai.enricher.images_dir = tmp_path / "images"
    calls = []

    class Vision:
        model = "fake-vl"

        def chat(self, messages, schema=None, max_tokens=0, temperature=0):
            calls.append(messages)
            return Reply("HLEDÁME ŘIDIČE\nMzda 42 000 Kč měsíčně", Timing(900, 3, 20, 2, 5))

    @contextmanager
    def session(vision=False, priority=Priority.AI):
        assert vision
        yield Vision()

    ai.manager.session = session
    assert ai.enricher.run_images() == 1
    assert len(calls) == 1  # the long ad's picture is not read
    with sessions() as s:
        job = s.scalar(select(Job).where(Job.title == "Řidič"))
        assert job.description.endswith("Text z obrázku:\nHLEDÁME ŘIDIČE\nMzda 42 000 Kč měsíčně")
        assert job.rules_hash is None and job.extra["origin"]["description"] == "image"
        assert job.images[0].text_model == "fake-vl"
    assert ai.enricher.run_images() == 0  # read once
    ai.enricher.index = lambda session: None  # salary needs no occupation index
    with sessions() as s:
        from adawork.ai.enrich import add_image_text

        assert not add_image_text(s.scalar(select(Job).where(Job.title == "Řidič")))


def test_a_photo_only_ad_takes_its_title_from_the_picture():
    from adawork.ai.enrich import add_image_text

    job = Job(title="Inzerát z obrázku", description="", extra={"title_from_picture": True})
    job.images = [JobImage(url="upload:a.png", text="\nHLEDÁME ŘIDIČE\nMzda 42 000 Kč")]
    assert add_image_text(job)
    assert job.title == "HLEDÁME ŘIDIČE" and "title_from_picture" not in job.extra


def test_the_same_text_on_two_boards_is_one_offer(sessions):
    from adawork.scraper.reposts import merge_text_reposts

    text = (
        "Přijmeme řidiče sk. C na rozvoz po Kolíně a okolí. Nástup ihned, mzda 42 000 Kč, "
        "služební telefon. Volejte 777 000 111."
    )
    add(sessions, "board_a", "1", "Řidič C Kolín", text, [])
    add(sessions, "board_b", "7", "Hledáme řidiče", text + " Díky.", [])
    other = text + " Také skladník."
    add(sessions, "board_b", "8", "Skladník", "Úplně jiná nabídka: skladník do Brna, " * 3, [])
    add(sessions, "portal", "9", "Řidič", other, [])  # a portal: left to the portal dedup
    with sessions() as s:
        assert merge_text_reposts(s, ["board_a", "board_b"]) == 1
        assert sorted(s.scalars(select(Job.title))) == ["Skladník", "Řidič", "Řidič C Kolín"]
        assert merge_text_reposts(s, ["board_a", "board_b"]) == 0


def test_vision_answers_lose_echoes_and_loops():
    from adawork.ai.vision import clean

    assert clean("Přepis: -") == ""
    assert clean("  Přepis:\nHLEDÁME ŘIDIČE") == "HLEDÁME ŘIDIČE"
    assert clean("\n".join(["Ceník", *["500"] * 400])) == "Ceník\n500\n500"
    assert clean("• Spolehlivost\n\n• Spolehlivost") == "• Spolehlivost\n\n• Spolehlivost"


def test_the_same_picture_on_many_ads_is_read_once(sessions, tmp_path):
    logo = poster("INDUS security agency")
    for n in range(3):
        add(sessions, "board_a", str(n), f"Ostraha {n}", "Krátký text.", [f"https://a.cz/{n}.png"])
    images = ImageStore(sessions, tmp_path / "images")
    images.download(fetcher_for({f"https://a.cz/{n}.png": logo for n in range(3)}))
    ai = make_ai(tmp_path, Settings.load(tmp_path / "config.json"), sessions)
    ai.enricher.images_dir = tmp_path / "images"
    calls = []

    class Vision:
        model = "fake-vl"

        def chat(self, messages, schema=None, max_tokens=0, temperature=0):
            calls.append(1)
            return Reply("INDUS\nsecurity agency", Timing(900, 3, 20, 2, 5))

    @contextmanager
    def session(vision=False, priority=Priority.AI):
        yield Vision()

    ai.manager.session = session
    assert ai.enricher.run_images() == 3 and len(calls) == 1


def test_a_refusing_picture_host_does_not_stop_the_others(sessions, tmp_path):
    add(sessions, "board_a", "1", "Řidič", "Text A.", ["https://a.cz/1.png", "https://a.cz/2.png"])
    add(sessions, "board_b", "2", "Kuchař", "Text B.", ["https://b.cz/1.png"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.host == "a.cz":
            return httpx.Response(429)
        return httpx.Response(200, content=poster())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    store = ImageStore(sessions, tmp_path / "images")
    assert store.download(PoliteFetcher(client, delay=0, sleep=lambda s: None)) == 1
    with sessions() as s:
        statuses = dict(s.execute(select(JobImage.url, JobImage.status)).all())
    assert statuses == {  # the refused ones are tried again on a later run
        "https://a.cz/1.png": "new",
        "https://a.cz/2.png": "new",
        "https://b.cz/1.png": "stored",
    }


def test_each_picture_host_gets_its_share(sessions, tmp_path):
    add(sessions, "board_a", "1", "Řidič", "Text A.", [f"https://a.cz/{n}.png" for n in range(3)])
    add(sessions, "board_b", "2", "Kuchař", "Text B.", ["https://b.cz/1.png"])
    add(sessions, "board_c", "3", "Zedník", "Text C.", [f"https://c.cz/{n}.png" for n in range(14)])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            # c.cz asks for 100 s between requests: 3 pictures in 5 minutes.
            slow = request.url.host == "c.cz"
            return httpx.Response(200, text="User-agent: *\nCrawl-delay: 100\n" if slow else "")
        return httpx.Response(200, content=poster())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetcher = PoliteFetcher(client, delay=0, sleep=lambda s: None)
    store = ImageStore(sessions, tmp_path / "images")
    # The newest jobs' pictures do not crowd out the older ones: a share per host.
    assert store.download(fetcher, per_host=2) == 2 + 1 + 2
    assert store.download(fetcher) == 1 + 3
    with sessions() as s:
        new = set(s.scalars(select(JobImage.url).where(JobImage.status == "new")))
    assert new == {f"https://c.cz/{n}.png" for n in range(5, 14)}  # next time


def bomb(side: int) -> bytes:
    """A PNG of a few kB that opens as side x side pixels."""
    out = BytesIO()
    Image.new("1", (side, side)).save(out, format="PNG")
    return out.getvalue()


def test_huge_pictures_are_refused_unopened(sessions, tmp_path):
    for side in (8000, 14000):  # over MAX_PIXELS; over Pillow's own limit
        data = bomb(side)
        assert len(data) < 100_000
        with pytest.raises(ValueError):
            shrink(data)
        with pytest.raises(ValueError, match="not a picture"):
            ImageStore(sessions, tmp_path / "images").keep(JobImage(url="upload:x.png"), data)


def test_a_bad_picture_fails_alone_and_big_downloads_stop_early(sessions, tmp_path, monkeypatch):
    monkeypatch.setattr("adawork.scraper.images.MAX_BYTES", 100_000)
    urls = [f"https://a.cz/{n}.png" for n in ("bomb", "odd", "endless", "ok")]
    add(sessions, "board_a", "1", "Řidič", "Text A.", urls)
    sent = []

    def endless():
        while True:
            sent.append(1)
            yield b"\0" * 10_000

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.strip("/")
        if name == "robots.txt":
            return httpx.Response(404)
        if name == "odd.png":
            raise RuntimeError("unexpected")
        if name == "endless.png":
            return httpx.Response(200, content=endless())
        return httpx.Response(200, content=bomb(14000) if name == "bomb.png" else poster())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    store = ImageStore(sessions, tmp_path / "images")
    assert store.download(PoliteFetcher(client, delay=0, sleep=lambda s: None)) == 1
    with sessions() as s:
        statuses = dict(s.execute(select(JobImage.url, JobImage.status)).all())
    assert statuses == dict(zip(urls, ["failed", "failed", "failed", "stored"], strict=True))
    assert len(sent) <= 12  # read up to the limit, not to the end
