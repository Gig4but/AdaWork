"""Employer module (PLAN §4.5, M9): names, registers, resolver, reviews, mentions, summary,
the background service and the employer filters."""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select, text

from adawork.ai.client import Reply, Timing
from adawork.ai.grammar import compact_grammar
from adawork.config import EmployerSettings
from adawork.core import ParsedJob, ParsedLocation
from adawork.db.models import (
    Employer,
    EmployerMention,
    EmployerName,
    EmployerReview,
    EmployerSummary,
    Job,
)
from adawork.db.session import make_engine, make_sessionmaker, migrate
from adawork.employers import atmoskop, downloads, google, mentions, official, resolver, summary
from adawork.employers.names import (
    client_in,
    core_words,
    known_inside,
    looks_like_agency,
    name_key,
    short_name,
    slug,
    variants,
)
from adawork.employers.service import CARD_RETRY, EmployerService, card_links
from adawork.net.polite import PoliteFetcher
from adawork.normalize.text import valid_ico
from adawork.scraper.store import JobStore
from adawork.search.service import Filters, filters_from, filters_to_query, search_jobs

FIXTURES = Path(__file__).parents[1] / "fixtures" / "employers"
NOW = datetime(2026, 10, 14, 12, 0)


# --- names ------------------------------------------------------------------------------------


def test_name_keys_ignore_case_punctuation_and_legal_form():
    assert name_key("ADECCO spol.s r.o.") == name_key("ADECCO spol. s r.o.") == "adecco"
    assert name_key("Penny Market, s. r. o.") == "penny market"
    assert name_key("Manuvia, a. s., organizační složka") == "manuvia"
    assert name_key("Dopravní podnik hl. m. Prahy, akciová společnost") == (
        "dopravni podnik hl m prahy"
    )
    assert name_key(None) == ""
    assert core_words("Foxconn Czech Republic") == core_words("FOXCONN CZ s.r.o.") == ["foxconn"]
    assert core_words("mBank S.A., organizační složka") == ["mbank"]
    assert core_words("Česká pošta, s.p.") == ["ceska", "posta"]  # the first word stays


def test_search_variants_for_ares():
    assert variants("Lidl ČR") == ["Lidl ČR", "Lidl Česká republika", "Lidl"]
    assert variants("Hospoda U Lípy") == ["Hospoda U Lípy", "U Lípy"]
    assert variants("SNOP Zápy", frozenset({"zapy"})) == ["SNOP Zápy", "SNOP"]
    assert variants("AAA Auto (AURES Holdings a.s.)") == [
        "AAA Auto (AURES Holdings a.s.)", "AURES Holdings a.s", "AAA Auto",
    ]  # fmt: skip
    assert variants("McDonald's") == ["McDonald's"]  # ARES writes "McDonald`s ČR"


def test_known_employer_inside_a_longer_name():
    known = {("penta", "hospitals"): 1, ("js", "trans"): 2, ("advantage", "consulting"): 3}
    assert known_inside("Ambulance Penta Hospitals CZ", known) == 1
    assert known_inside("JS Trans logistic s.r.o.", known) is None  # another company
    assert known_inside("Advantage Consulting, s.r.o. Brno", known, frozenset({"brno"})) == 3
    assert known_inside("Penta Hospitals", known) is None  # the same name: not "inside"


def test_agency_wording_but_not_its_denial():
    assert looks_like_agency("Pro našeho klienta, výrobce aut, hledáme operátory.")
    assert looks_like_agency("Jedná se o agenturní zaměstnávání.")
    assert not looks_like_agency("Nejedná se o agenturní zaměstnávání, jsme přímý zaměstnavatel.")
    assert not looks_like_agency(None)
    assert slug("Penny Market s.r.o.") == "penny-market-s-r-o"
    assert short_name("Penny Market s.r.o.") == "Penny Market"
    assert short_name("ČEZ, a. s.") == "ČEZ"
    assert short_name("Manuvia, a. s., organizační složka") == "Manuvia"
    assert short_name("Kofola a.s") == "Kofola" and short_name("Baz GmbH") == "Baz"


def test_client_named_in_an_agency_ad():
    assert client_in("Pro společnost Foxconn CZ s.r.o. hledáme operátory.") == "Foxconn CZ s.r.o."
    assert client_in("pro našeho klienta HELLA AUTOTECHNIK NOVA, s.r.o. v Mohelnici") == (
        "HELLA AUTOTECHNIK NOVA, s.r.o."
    )
    assert client_in("Pro našeho klienta, výrobce automobilů v Kolíně, hledáme") is None


# --- registers ----------------------------------------------------------------------------------


def record(ico, name, form="112", band="340", nace="47110", ir="NEEXISTUJICI", seat=554782,
           ended=None, dic=None):  # fmt: skip
    return {
        "ico": ico,
        "obchodniJmeno": name,
        "pravniForma": form,
        "datumVzniku": "2001-02-03",
        "datumZaniku": ended,
        "dic": dic if dic is not None else f"CZ{ico}",
        "sidlo": {"textovaAdresa": "Ulice 1, Praha", "nazevObce": "Praha", "kodObce": seat},
        "seznamRegistraci": {"stavZdrojeIr": ir},
        "_band": band,
        "_nace": nace,
    }


REGISTRY = [
    record("64945880", "Penny Market s.r.o.", band="470"),
    record("01671243", "ZO UZO Penny Market Opava", form="707", band="000", nace="94200"),
    record("01493248", "Stavby Kohout s.r.o.", band="210", nace="41000", ir="AKTIVNI"),
    record("12345679", "Hostinec U Lípy, s.r.o.", band="120", nace="56100"),
    record("23456787", "U LÍPY-PRAHA, spol. s r.o.", band="120", nace="56100"),
    record("29813158", "AG MIRASTAFF s.r.o.", band="240", nace="78200"),
    record("44444443", "Pracovní Síla s.r.o.", band="130", nace="78100"),
    record("55555551", "Zaniklá Firma s.r.o.", ended="2020-01-01"),
    record("00121100", "Nespolehlivá s.r.o.", dic="CZ00121100"),
]


def fake_ares(registry=REGISTRY, calls=None):
    """ARES, RES and ISIR as a mock transport: search by IČO list or by all name words."""
    by_ico = {r["ico"]: r for r in registry}

    def public(r):
        return {k: v for k, v in r.items() if not k.startswith("_")}

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request.url.path)
        if request.url.host == "isir.justice.cz":
            ico = request.content.decode().split("<ic>")[1].split("</ic>")[0]
            name = "isir_01493248_debtor.xml" if ico == "01493248" else "isir_00177041_none.xml"
            return httpx.Response(200, content=(FIXTURES / "isir" / name).read_bytes())
        body = json.loads(request.content)
        if request.url.path.endswith("-res/vyhledat"):
            items = [
                {"icoId": i, "zaznamy": [{"ico": i, "primarniZaznam": True,
                 "statistickeUdaje": {"kategoriePoctuPracovniku": by_ico[i]["_band"]},
                 "czNacePrevazujici": by_ico[i]["_nace"]}]}
                for i in body["ico"] if i in by_ico
            ]  # fmt: skip
            return httpx.Response(
                200, json={"pocetCelkem": len(items), "ekonomickeSubjekty": items}
            )
        if "ico" in body:
            found = [public(by_ico[i]) for i in body["ico"] if i in by_ico]
        else:
            query = core_words(body["obchodniJmeno"]) or ["?"]
            found = [
                public(r) for r in registry if set(query) <= set(core_words(r["obchodniJmeno"]))
            ]
        if len(found) > 1000:
            return httpx.Response(400, json={"subKod": "VYSTUP_PRILIS_MNOHO_VYSLEDKU"})
        return httpx.Response(200, json={"pocetCelkem": len(found), "ekonomickeSubjekty": found})

    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def no_ares_pause(monkeypatch):
    monkeypatch.setattr(official, "ARES_GAP", 0.0)


def test_ares_batch_by_ico_with_res_statistics():
    with httpx.Client(transport=fake_ares()) as client:
        found = official.Ares(client).companies(["64945880", "01493248", "99999999"])
    penny = found["64945880"]
    assert penny.name == "Penny Market s.r.o." and penny.band == "470"
    assert penny.nace_main == "47110" and penny.active and not penny.insolvency_flag
    assert found["01493248"].insolvency_flag and "99999999" not in found


def test_real_ares_record_and_isir_answers():
    data = json.loads((FIXTURES / "ares" / "subjekt_00177041_skoda.json").read_text("utf-8"))
    skoda = official.company(data)
    assert (skoda.ico, skoda.dic, skoda.insolvency_flag) == ("00177041", "CZ00177041", False)
    assert (
        official.parse_insolvency((FIXTURES / "isir" / "isir_00177041_none.xml").read_bytes()) == []
    )
    found = official.parse_insolvency((FIXTURES / "isir" / "isir_01493248_debtor.xml").read_bytes())
    assert found[0]["state"] == "KONKURS" and found[0]["case"] == "INS 16223/2023"
    assert found[0]["since"] == "2023-11-14" and found[0]["url"].startswith("https://isir")


def write_lists(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    vat = {"generated": "2026-10-06", "payers": {"00121100": "2017-03-16"}}
    (directory / downloads.VAT_FILE).write_text(json.dumps(vat), encoding="utf-8")
    sample = (FIXTURES / "mpsv_agencies" / "agentury_prace_sample.json").read_text("utf-8")
    (directory / downloads.AGENCIES_FILE).write_text(sample, encoding="utf-8")


def test_local_lists(tmp_path):
    write_lists(tmp_path)
    lists = official.Lists(tmp_path).fresh()
    assert lists.unreliable_since("CZ00121100") == "2017-03-16"
    assert lists.unreliable_since("CZ64945880") is None and lists.unreliable_since(None) is None
    mirastaff = lists.agencies["29813158"]
    assert mirastaff.temp_staffing and mirastaff.until is None  # one permit has no end date
    assert lists.agency_names["ag mirastaff"] == "29813158"


# --- resolver ------------------------------------------------------------------------------------


def resolve(name, ctx=None):
    with httpx.Client(transport=fake_ares()) as client:
        return resolver.resolve(official.Ares(client), name, ctx or resolver.AdHints())


def test_resolver_skips_the_trade_union_and_takes_the_company():
    found = resolve("Penny Market")
    assert found.ico == "64945880" and found.method == "ares"
    assert all(c.company.legal_form != "707" for c in found.candidates[:1])


def test_resolver_shows_candidates_when_two_fit():
    found = resolve("Hospoda U Lípy")
    assert found.ico is None and found.method == "uncertain"
    assert {c.company.ico for c in found.candidates} == {"12345679", "23456787"}
    assert resolve("Úplně Neznámá Firma").method == "none"


# --- Atmoskop -----------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["penny_market", "manpowergroup"])
def test_atmoskop_reviews_page(name):
    html = (FIXTURES / "atmoskop" / f"{name}_hodnoceni.html").read_text("utf-8")
    expected = json.loads(
        (FIXTURES / "atmoskop" / f"{name}_hodnoceni.expected.json").read_text("utf-8")
    )
    got = atmoskop.parse(html, "https://www.atmoskop.cz/x")
    assert (got.employer_pct, got.colleagues_pct, got.boss_pct) == (
        expected["employer_pct"], expected["colleagues_pct"], expected["boss_pct"],
    )  # fmt: skip
    assert (got.ratings, got.comments) == (expected["count_reviews"], expected["count_comments"])
    texts = [r for r in expected["reviews"] if r["positive"] or r["negative"]]
    assert len(got.texts) == len(texts)
    assert " ," not in got.texts[0]["plus"] and got.texts[0]["date"] == texts[0]["date"]


def test_atmoskop_profile_found_by_ares_name(tmp_path):
    sitemap = tmp_path / downloads.ATMOSKOP_FILE
    sitemap.write_text((FIXTURES / "atmoskop" / "sitemap_sample.xml").read_text("utf-8"), "utf-8")
    index = atmoskop.SlugIndex(sitemap).fresh()
    url = "https://www.atmoskop.cz/nazory-na-zamestnavatele/2938439-albert-ceska-republika-s-r-o"
    assert index.find("Albert Česká republika, s.r.o.") == url
    assert index.find("ALBERT Česká republika s. r. o.") == url
    assert index.find("Neznámá Firma s.r.o.") is None


# --- mentions -----------------------------------------------------------------------------------


def test_mentions_keep_pages_that_name_the_employer():
    results = json.loads((FIXTURES / "ddgs" / "text_results.json").read_text("utf-8"))
    first = next(iter(results.values()))
    calls = []

    def ddgs_text(query, **kw):
        calls.append(query)
        if len(calls) == 1:
            raise RuntimeError("No results found")  # ddgs fails now and then: retried
        return first

    found = mentions.search("Penny Market", ddgs_text, sleep=lambda s: None)
    hosts = {mentions._host(m.url) for m in found.mentions}
    assert "atmoskop.cz" not in hosts and "jobinsider.cz" in hosts  # Atmoskop: own source
    assert found.indeed_url == "https://cz.indeed.com/cmp/Penny-9/reviews"
    assert calls[0] == calls[1] and len(calls) == 4  # one retry, then the other 2 queries


def test_facebook_link_only_from_a_page_that_names_the_employer():
    def ddgs_text(query, **kw):
        return [
            {"href": "https://www.facebook.com/JinaFirma", "title": "Jiná firma", "body": ""},
            {"href": "https://www.facebook.com/profile.php?id=1", "title": "Penny", "body": ""},
            {"href": "https://www.facebook.com/PennyCZ/", "title": "Penny Market", "body": ""},
        ]

    found = mentions.search("Penny", ddgs_text, sleep=lambda s: None)
    assert found.facebook_url == "https://www.facebook.com/PennyCZ"
    assert mentions.facebook_page("https://www.facebook.com/p/Penny-100064/") is None


def test_passages_about_working_there():
    html = """<html><body><nav>Menu práce</nav><article>
      <p>Penny Market: výplata chodila vždy včas, kolektiv na prodejně super.</p>
      <p>Recept na bábovku a pečení o víkendu bez práce.</p>
      <p>Šéf nám ve Penny Market nevyplatil přesčasy, mzda byla krácená.</p>
      <p>Krátké.</p></article><footer>Kontakt práce</footer></body></html>"""
    found = mentions.passages(html, "Penny Market")
    assert len(found) == 2 and "nevyplatil" in found[0]  # warnings first


# --- summary ------------------------------------------------------------------------------------


ITEMS = [
    summary.Item("atmoskop", "https://a/1", "Dobrý kolektiv a férový vedoucí.", "plus"),
    summary.Item("atmoskop", "https://a/1", "Nízká mzda.", "minus"),
    summary.Item("atmoskop", "https://a/2", "Kolektiv drží spolu.", "plus"),
    summary.Item("atmoskop", "https://a/2", "Časté přesčasy, změna pracovní smlouvy.", "minus"),
    summary.Item("web", "https://w/5", "Výplata chodila pozdě, mzda nebyla vyplacena celá."),
]  # fmt: skip


def test_summary_points_must_cite_texts_that_say_it():
    data = {
        "praise": [
            "Dobrý kolektiv [1,3]",
            "Skvělé benefity a auto [1]",  # not in the text
            "Bez zdroje",
            "Nízká mzda [2]",  # a complaint is not praise
            "Kolektiv [1] a vedoucí [3]",  # citations inside the point
        ],
        "criticism": ["nízká mzda [2]", "Přesčasy [9]"],
        "warnings": [
            "Mzda nebyla vyplacena celá [5]",
            "Práce bez smlouvy [4]",  # the source does not say so
            "Časté přesčasy [4]",  # a complaint, not a warning
        ],
    }
    s = summary.check(data, ITEMS)
    assert s.praise == [{"text": "Dobrý kolektiv.", "sources": [1, 3]}]
    assert s.criticism == [{"text": "Nízká mzda.", "sources": [2]}]
    assert s.warnings == [{"text": "Mzda nebyla vyplacena celá.", "sources": [5]}]
    assert s.dropped == 7


def test_summary_grammar_ends_points_with_their_sources():
    grammar = compact_grammar(summary.schema())
    assert '" [" [1-9] [0-9]? ( "," [1-9] [0-9]? ){0,3} "]"' in grammar


class FakeClient:
    model = "fake-model"

    def __init__(self, answer):
        self.answer = answer
        self.messages = None

    def chat(self, messages, schema=None, max_tokens=0, temperature=0.0):
        self.messages = messages
        answer = self.answer(messages) if callable(self.answer) else self.answer
        return Reply(answer, Timing(10, 0.1, 10, 0.1, 0.2))


def test_summarize_sends_numbered_texts():
    client = FakeClient({"praise": ["Dobrý kolektiv [2]"], "criticism": [], "warnings": []})
    items = [ITEMS[0], ITEMS[1], ITEMS[4]]  # praise, criticism, the web
    s = summary.summarize(client, "Penny", items)
    sent = client.messages[1]["content"]
    assert "Co zaměstnanci chválí:\n[1] Dobrý kolektiv" in sent and "[3] Výplata" in sent
    assert s.praise == [] and items[1].kind == "minus"  # [2] is criticism
    s = summary.summarize(FakeClient({"praise": ["Dobrý kolektiv [1]"], "criticism": [],
                                      "warnings": []}), "Penny", items)  # fmt: skip
    assert s.praise == [{"text": "Dobrý kolektiv.", "sources": [1]}]
    assert summary.inputs_key(ITEMS) != summary.inputs_key(ITEMS[:2])


# --- Google -------------------------------------------------------------------------------------


def test_google_places_stays_in_the_free_quota():
    def handler(request):
        if request.method == "POST":
            assert request.headers["X-Goog-FieldMask"] == "places.id"
            return httpx.Response(200, json={"places": [{"id": "P1"}]})
        return httpx.Response(200, json={"displayName": {"text": "Penny Kolín"}, "rating": 4.1,
                              "userRatingCount": 812, "googleMapsUri": "https://maps.google.com/?cid=1",
                              "reviews": [{"rating": 5, "text": {"text": "Milá obsluha"},
                                           "relativePublishTimeDescription": "dnes"}]})  # fmt: skip

    quota = google.Quota.load({"month": "2026-10", "details": google.FREE_DETAILS - 1}, NOW)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert google.find_place(client, "k", "Penny Market Kolín") == "P1"
        place = google.details(client, "k", "P1", quota)
        assert (place.rating, place.count, place.reviews[0]["text"]) == (4.1, 812, "Milá obsluha")
        with pytest.raises(google.QuotaUsed):
            google.details(client, "k", "P1", quota)
    assert google.Quota.load(quota.dump(), NOW + timedelta(days=30)).details == 0  # new month


# --- the service --------------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path, municipalities):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    sessions = make_sessionmaker(engine)
    with sessions() as s:
        municipalities(s)
    yield sessions
    engine.dispose()


def add_jobs(sessions, jobs) -> None:
    """jobs: (external id, employer name, IČO or None, description)."""
    with sessions() as s:
        store = JobStore(s, "x")
        for ext, name, ico, text in jobs:
            location = ParsedLocation(key="obec:533165", precision="municipality",
                                      municipality="Kolín", municipality_code="533165",
                                      lat=50.03, lon=15.2)  # fmt: skip
            job = ParsedJob(external_id=ext, title=f"Prodavač {ext}", description=text,
                            employer_name=name, employer_ico=ico, location=location)  # fmt: skip
            store.add(job)
        store.commit()


def make_service(sessions, tmp_path, profile=None, **kw):
    write_lists(tmp_path / "lists")
    registry = kw.pop("registry", REGISTRY)
    return EmployerService(
        sessions,
        tmp_path / "lists",
        kw.pop("settings", lambda: EmployerSettings(auto_check=True)),
        client_factory=lambda: httpx.Client(transport=fake_ares(registry)),
        profile_jobs=profile or (lambda s: set()),
        fetcher_factory=lambda c: PoliteFetcher(c, delay=0, sleep=lambda s: None),
        clock=lambda: NOW,
        **kw,
    )


def employer_of(sessions, ext):
    with sessions() as s:
        job = s.scalar(select(Job).where(Job.title == f"Prodavač {ext}"))
        return s.get(Employer, job.employer_id) if job.employer_id else None


def test_jobs_link_by_ico_learned_names_brands_and_agencies(db, tmp_path):
    add_jobs(db, [
        ("a", "Penny Market s.r.o.", "64945880", ""),  # IČO in the ad
        ("b", "PENNY MARKET, s. r. o.", None, ""),  # the same name, learned from "a"
        ("c", "Albert", None, ""),  # curated brand
        ("d", "AG MIRASTAFF, s.r.o.", None, ""),  # agency register
        ("e", "Hospoda U Lípy", None, ""),  # waits for ARES
    ])  # fmt: skip
    service = make_service(db, tmp_path)
    assert service.linking.link() == 4
    assert employer_of(db, "a").id == employer_of(db, "b").id
    assert employer_of(db, "c").ico == "44012373" and employer_of(db, "c").match == "brand"
    assert employer_of(db, "d").ico == "29813158" and employer_of(db, "d").match == "agency"
    assert employer_of(db, "e") is None
    with db() as s:
        assert [key for key, _, _ in service.linking.pending(s)] == ["hospoda u lipy"]
        assert s.get(EmployerName, "penny market").source == "ad"
    assert service.linking.link() == 0  # nothing new


def test_names_resolved_through_ares_and_jobs_linked(db, tmp_path):
    add_jobs(db, [("a", "Penny Market", None, ""), ("b", "Hospoda U Lípy", None, ""),
                  ("c", "Nikdo Takový", None, "")])  # fmt: skip
    service = make_service(db, tmp_path)
    service.linking.link()
    assert service.linking.resolve_names(10) == 3
    service.linking.link()
    penny, pub, nobody = (employer_of(db, x) for x in "abc")
    assert penny.ico == "64945880" and penny.match == "ares" and penny.employee_band == "470"
    assert pub.ico is None and pub.match == "uncertain" and len(pub.candidates) == 2
    assert nobody.match == "none"
    with db() as s:
        assert s.get(EmployerName, "nikdo takovy").employer_id == nobody.id  # asked once only


def test_without_automatic_checks_a_pass_only_links(db, tmp_path):
    add_jobs(db, [("a", "Penny Market s.r.o.", "64945880", ""), ("b", "Hospoda U Lípy", None, "")])

    def offline() -> httpx.Client:
        raise AssertionError("nothing is fetched")

    service = make_service(db, tmp_path, settings=EmployerSettings)  # auto_check off
    service.ctx.client_factory = offline
    assert service.run_pass() is False
    penny = employer_of(db, "a")
    assert penny.ico == "64945880" and penny.checked_at is None and penny.reviews_at is None
    assert employer_of(db, "b") is None  # the name waits for a check
    assert service.progress.phase == "done"


def test_official_checks_and_warnings(db, tmp_path):
    add_jobs(db, [
        ("a", "Stavby Kohout s.r.o.", "01493248", ""),
        ("b", "Zaniklá Firma s.r.o.", "55555551", ""),
        ("c", "Nespolehlivá s.r.o.", "00121100", ""),
        ("d", "Pracovní Síla s.r.o.", "44444443", "Pro našeho klienta hledáme skladníky."),
        ("e", "AG MIRASTAFF s.r.o.", "29813158", "Agenturní zaměstnávání, dlouhodobě."),
        ("f", "Penny Market s.r.o.", "64945880", "Nejedná se o agenturní zaměstnávání."),
        ("g", "Překlep s.r.o.", "27082440", ""),  # a valid IČO that ARES does not know
    ])  # fmt: skip
    service = make_service(db, tmp_path)
    service.linking.link()
    assert service.checks.check_due(100) == 7
    kohout, ended, vat, unlicensed, agency, penny = (employer_of(db, x) for x in "abcdef")
    assert employer_of(db, "g").warnings == ["not_found"]
    assert kohout.warnings == ["insolvency"] and kohout.insolvency[0]["state"] == "KONKURS"
    assert ended.warnings == ["ended"]
    assert vat.warnings == ["vat"] and vat.vat_unreliable == "2017-03-16"
    assert unlicensed.warnings == ["unlicensed"] and unlicensed.looks_agency
    assert agency.warnings == [] and agency.agency == {"temp": True, "until": None}
    assert penny.warnings == [] and not penny.looks_agency and penny.checked_at == NOW
    assert service.checks.check_due(100) == 0  # checked: again in 30 days
    links = card_links(kohout, "Kolín")
    assert links["ares"].endswith("01493248") and links["isir"].startswith("https://isir")


def test_uncertain_match_fixed_by_the_user(db, tmp_path):
    add_jobs(
        db, [("a", "Hospoda U Lípy", None, ""), ("b", "Hostinec U Lípy, s.r.o.", "12345679", "")]
    )
    service = make_service(db, tmp_path)
    service.linking.link()
    service.linking.resolve_names(10)
    service.linking.link()
    stub = employer_of(db, "a")
    assert stub.match == "uncertain"
    target = service.correct(stub.id, "12345679")
    assert employer_of(db, "a").id == employer_of(db, "b").id == target
    with db() as s:
        assert s.get(Employer, stub.id) is None  # merged into the company
        assert s.get(EmployerName, "hospoda u lipy").source == "manual"


class FakeManager:
    ready = True

    def __init__(self, answer):
        self.client = FakeClient(answer)

    def session(self, priority=None):
        from contextlib import nullcontext

        return nullcontext(self.client)

    def record(self, timing):
        pass


def test_ai_picks_among_close_candidates(db, tmp_path):
    add_jobs(db, [("a", "Hospoda U Lípy", None, "Hledáme číšníka do hostince v Praze.")])
    manager = FakeManager({"choice": 2})
    service = make_service(db, tmp_path, manager=manager)
    service.linking.link()
    service.linking.resolve_names(10)
    service.linking.link()
    stub = employer_of(db, "a")
    second = stub.candidates[1]["ico"]
    assert service.linking.ask_ai(profile_only=False) == 1
    assert employer_of(db, "a").ico == second and employer_of(db, "a").match == "ai"
    assert "Hledáme číšníka" in manager.client.messages[1]["content"]
    assert service.linking.ask_ai(profile_only=False) == 0  # asked once


def atmoskop_site(request: httpx.Request) -> httpx.Response | None:
    if request.url.host != "www.atmoskop.cz":
        return None
    if request.url.path == "/robots.txt":
        return httpx.Response(200, text=(FIXTURES / "atmoskop" / "robots.txt").read_text("utf-8"))
    html = (FIXTURES / "atmoskop" / "penny_market_hodnoceni.html").read_text("utf-8")
    return httpx.Response(200, text=html, headers={"content-type": "text/html"})


def test_reviews_mentions_and_summary_for_profile_employers(db, tmp_path):
    add_jobs(db, [("a", "Penny Market s.r.o.", "64945880", "")])
    sitemap = tmp_path / "lists" / downloads.ATMOSKOP_FILE
    sitemap.parent.mkdir(parents=True, exist_ok=True)
    sitemap.write_text(
        '<urlset><url><loc>https://www.atmoskop.cz/nazory-na-zamestnavatele/2790622-penny-market-s-r-o</loc></url></urlset>',
        encoding="utf-8",
    )  # fmt: skip
    ares = fake_ares()

    def handler(request):
        if request.url.host == "www.atmoskop.cz":
            return atmoskop_site(request)
        if request.url.host == "forum.example":
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            body = "<p>V Penny Market mi šéf nevyplatil přesčasy, mzda chodila pozdě.</p>"
            return httpx.Response(200, text=body, headers={"content-type": "text/html"})
        return ares.handle_request(request)

    def ddgs_text(query, **kw):
        return [{"href": "https://forum.example/penny", "title": "Penny Market zkušenosti",
                 "body": "Práce v Penny Market, výplata"}]  # fmt: skip

    def answer(messages):  # cites the forum passage by the number it was given
        n = re.search(r"\[(\d+)\] V Penny Market mi", messages[1]["content"])[1]
        return {"praise": [], "criticism": [], "warnings": [f"Šéf nevyplatil přesčasy [{n}]"]}

    manager = FakeManager(answer)

    service = make_service(db, tmp_path, profile=lambda s: set(s.scalars(select(Job.id))),
                           manager=manager, ddgs_text=ddgs_text)  # fmt: skip
    service.ctx.client_factory = lambda: httpx.Client(transport=httpx.MockTransport(handler))
    service.reviews.atmoskop_index = atmoskop.SlugIndex(sitemap)
    assert service.run_pass() is False
    penny = employer_of(db, "a")
    assert penny.satisfaction == 62 and penny.reviews_at == NOW and penny.mentions_at == NOW
    with db() as s:
        review = s.scalar(select(EmployerReview).where(EmployerReview.employer_id == penny.id))
        assert (review.rating, review.count, review.scale) == (62, 861, "percent")
        mention = s.scalar(select(EmployerMention))
        assert "nevyplatil" in mention.passages[0]
        written = s.get(EmployerSummary, penny.id)
        n = written.summary["warnings"][0]["sources"][0]
        assert written.sources[n - 1]["url"] == "https://forum.example/penny"
    assert service.ctx.state()["used"] == {"atmoskop": 1, "mentions": 1}  # daily limits


def test_employer_filters_and_sort(db, tmp_path):
    add_jobs(db, [
        ("a", "Penny Market s.r.o.", "64945880", ""),
        ("b", "Stavby Kohout s.r.o.", "01493248", ""),
        ("c", "Pracovní Síla s.r.o.", "44444443", "Pro našeho klienta hledáme."),
        ("d", "Neznámá", None, ""),
    ])  # fmt: skip
    service = make_service(db, tmp_path)
    service.linking.link()
    service.checks.check_due(100)
    with db() as s:
        penny = s.scalar(select(Employer).where(Employer.ico == "64945880"))
        penny.satisfaction = 62
        s.commit()

    def titles(**kw):
        with db() as s:
            return sorted(j.title[-1] for j in search_jobs(s, Filters(**kw), limit=50).jobs)

    assert titles() == ["a", "b", "c", "d"]
    assert titles(emp_min=60) == ["a"]
    assert titles(emp_ok=True) == ["a", "d"]  # no employer: nothing to warn about
    assert titles(emp_licensed=True) == ["a", "b", "d"]
    assert titles(emp_verified=True) == ["a", "b", "c"]
    assert titles(employer=penny.id) == ["a"]
    with db() as s:
        best = search_jobs(s, Filters(sort="employer"), limit=1).jobs[0]
        assert best.title.endswith("a") and best.employer.satisfaction == 62
    f = filters_from(_QP({"emp_min": "70", "emp_ok": "1", "employer": "5", "sort": "employer"}))
    assert (f.emp_min, f.emp_ok, f.employer, f.sort) == (70, True, 5, "employer")
    assert "emp_min=70" in filters_to_query(f) and "emp_ok=1" in filters_to_query(f)
    assert filters_from(_QP({"emp_min": "65"})).emp_min is None  # not one of the steps


class _QP(dict):
    def getlist(self, name):
        return [self[name]] if name in self else []


def test_a_name_learned_with_the_group_ico_goes_to_the_named_company(db, tmp_path):
    registry = [*REGISTRY, record("26185610", "AGROFERT, a.s."), record("26124459", "IMOBA, a.s.")]
    add_jobs(db, [("a", "IMOBA, a.s.", "26185610", ""), ("b", "IMOBA a.s.", None, "")])
    service = make_service(db, tmp_path, registry=registry)
    service.linking.link()
    assert employer_of(db, "b").ico == "26185610"  # learned from the ad with the group's IČO
    service.checks.check_due(100)
    assert service.linking.recheck_learned(10) == 1
    service.linking.link()
    assert employer_of(db, "b").ico == "26124459" and employer_of(db, "a").ico == "26185610"
    assert service.linking.recheck_learned(10) == 0  # asked once


def test_summary_keeps_only_the_parts_of_a_list_its_sources_say():
    data = {"praise": ["Dobrý kolektiv, vysoké prémie, férový vedoucí [1,2]"],
            "criticism": [], "warnings": []}  # fmt: skip
    s = summary.check(data, ITEMS)
    # "vysoké prémie" is in no source; [2] is a complaint and cannot back praise
    assert s.praise == [{"text": "Dobrý kolektiv, férový vedoucí.", "sources": [1]}]


def test_summary_words_must_stand_together_in_one_source():
    items = [summary.Item("web", "https://w/1", "Mzda pod průměrem pozice, neplacené přesčasy.")]
    data = {"praise": ["Nic [1]"], "criticism": [],
            "warnings": ["Neplacená mzda, neplacené přesčasy [1]"]}  # fmt: skip
    s = summary.check(data, items)
    assert s.warnings == [{"text": "Neplacené přesčasy.", "sources": [1]}]
    assert s.praise == []  # "Nic" says nothing
    source = [summary.Item("w", "u", "Neplacené přesčasy, mzda pod průměrem.", "minus")]
    data = {"praise": [], "criticism": ["Neplacené přesčasy, mzda po [1]"], "warnings": []}
    cut = summary.check(data, source)
    assert cut.criticism == [{"text": "Neplacené přesčasy.", "sources": [1]}]  # "mzda po" cut off
    assert not mentions.WORK_WORDS.search("spoluprace s traktory")  # not about working


def test_web_passages_back_only_warnings():
    items = [summary.Item("web", "https://w/1", "Pozdní placení, výmluvy, nevyplacená mzda.")]
    data = {"praise": ["Pozdní placení [1]"], "criticism": ["Výmluvy [1]"],
            "warnings": ["Chválí: nevyplacená mzda [1]"]}  # fmt: skip
    s = summary.check(data, items)
    assert (s.praise, s.criticism) == ([], [])
    assert s.warnings == [{"text": "Nevyplacená mzda.", "sources": [1]}]


def test_a_card_is_not_refreshed_for_switched_off_sources(db, tmp_path):
    cfg = EmployerSettings(mentions=False)
    service = make_service(db, tmp_path)
    service.ctx.settings = lambda: cfg
    e = Employer(ico="64945880", name="Penny", checked_at=NOW, reviews_at=NOW, updated_at=NOW)
    assert not service.needs_refresh(e)  # no mentions: not wanted, not missing
    cfg.mentions = True
    assert service.needs_refresh(e)


def test_a_card_that_stays_incomplete_is_not_refreshed_on_every_look(db, tmp_path):
    service = make_service(db, tmp_path)
    service._refresh = lambda eid, force: None  # done, but a source failed: still incomplete
    assert service.refresh(1)
    service.refreshing.clear()
    assert not service.refresh(1)  # the card polls again: no new round of requests
    assert service.refresh(1, force=True)  # "Aktualizovat"
    service.refreshing.clear()
    service.ctx.clock = lambda: NOW + CARD_RETRY
    assert service.refresh(1)


def test_the_employers_migration_goes_back_cleanly(tmp_path):
    from alembic import command
    from alembic.config import Config

    from adawork.db.session import MIGRATIONS_DIR

    engine = make_engine(tmp_path / "m.db")
    migrate(engine)
    with make_sessionmaker(engine)() as s:
        JobStore(s, "x").add(ParsedJob(external_id="a", title="Skladník"))
        s.commit()
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "1592b84d5c9c")
    with engine.begin() as conn:  # jobs are written as before
        conn.execute(text("UPDATE jobs SET title = 'Řidič'"))
        conn.execute(text("DELETE FROM jobs"))
    migrate(engine)  # and up again
    engine.dispose()


def test_a_name_found_only_shortened_needs_the_seat_in_the_ads_town():
    registry = [record("12345679", "U Lípy s.r.o.", nace="56100")]

    def resolve_in(ctx):
        with httpx.Client(transport=fake_ares(registry)) as client:
            return resolver.resolve(official.Ares(client), "Hospoda U Lípy", ctx)

    assert resolve_in(resolver.AdHints()).method == "uncertain"  # another town's "U Lípy"
    assert resolve_in(resolver.AdHints(municipality_code=554782)).ico == "12345679"


def test_an_ad_with_the_ico_settles_an_uncertain_name(db, tmp_path):
    add_jobs(db, [("a", "Hospoda U Lípy", None, "")])
    service = make_service(db, tmp_path)
    service.linking.link()
    service.linking.resolve_names(10)
    service.linking.link()
    assert employer_of(db, "a").match == "uncertain"
    add_jobs(db, [("b", "Hospoda U Lípy", "12345679", "")])
    service.linking.link()
    assert employer_of(db, "a").ico == "12345679" == employer_of(db, "b").ico


def test_a_brand_is_not_used_for_a_name_with_its_legal_form(db, tmp_path):
    add_jobs(db, [("a", "Albert", None, ""), ("c", "ALBERT, a.s.", None, "")])
    service = make_service(db, tmp_path)
    service.linking.link()
    assert employer_of(db, "a").match == "brand"
    assert employer_of(db, "c") is None  # a registered name: asked in ARES, on its own
    with db() as s:
        assert [key for key, _, _ in service.linking.pending(s)] == ["albert a s"]
    assert valid_ico("64945880") and not valid_ico("64945881") and not valid_ico(None)


def with_hosts(registry, **hosts):
    """fake_ares, with some hosts (or ARES names) answered by `hosts`' handlers."""
    ares = fake_ares(registry)

    def handler(request):
        for host, answer in hosts.items():
            if request.url.host == host.replace("_", "."):
                return answer(request)
        if b"Chyba" in request.content:
            return httpx.Response(400, json={"kod": "CHYBA_VSTUPU"})
        return ares.handle_request(request)

    return lambda: httpx.Client(transport=httpx.MockTransport(handler))


def test_a_name_ares_refuses_does_not_stop_the_others(db, tmp_path):
    add_jobs(db, [("a", "Chyba Firma", None, ""), ("b", "Penny Market", None, "")])
    service = make_service(db, tmp_path)
    service.ctx.client_factory = with_hosts(REGISTRY)
    service.linking.link()
    assert service.linking.resolve_names(10) == 2
    service.linking.link()
    assert employer_of(db, "a").match == "none" and employer_of(db, "b").ico == "64945880"


def test_an_isir_failure_warns_and_is_checked_again(db, tmp_path):
    add_jobs(db, [("a", "Stavby Kohout s.r.o.", "01493248", "")])
    service = make_service(db, tmp_path)
    service.ctx.client_factory = with_hosts(REGISTRY, isir_justice_cz=lambda r: httpx.Response(503))
    service.linking.link()
    service.checks.check_due(100)
    kohout = employer_of(db, "a")
    assert kohout.warnings == ["insolvency"] and kohout.checked_at is None  # next pass again
    service.ctx.client_factory = with_hosts(REGISTRY)
    service.checks.check_due(100)
    kohout = employer_of(db, "a")
    assert kohout.insolvency[0]["state"] == "KONKURS" and kohout.checked_at == NOW


def reviews_after(db, tmp_path, atmoskop_answer, used=0):
    add_jobs(db, [("a", "Penny Market s.r.o.", "64945880", "")])
    service = make_service(db, tmp_path)
    service.linking.link()
    with db() as s:
        e = s.get(Employer, employer_of(db, "a").id)
        e.links = {"atmoskop": "https://www.atmoskop.cz/nazory-na-zamestnavatele/1-penny"}
        s.commit()
    service.ctx.save_state({"day": f"{NOW:%Y-%m-%d}", "used": {"atmoskop": used}})
    client = with_hosts(REGISTRY, www_atmoskop_cz=atmoskop_answer)()
    fetcher = PoliteFetcher(client, delay=0, sleep=lambda s: None)
    service.reviews.fetch_reviews(e.id, client, fetcher, background=True)
    return service, employer_of(db, "a")


def test_reviews_wait_when_todays_limit_is_used_up(db, tmp_path):
    from adawork.employers.common import PER_DAY

    service, penny = reviews_after(db, tmp_path, atmoskop_site, used=PER_DAY["atmoskop"])
    assert penny.reviews_at is None  # asked in another pass, not in 30 days


def test_a_refusing_review_site_is_left_alone(db, tmp_path):
    def refuse(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(429)

    service, penny = reviews_after(db, tmp_path, refuse)
    assert penny.reviews_at is None and service.reviews._paused["atmoskop"] > NOW
    assert not service.reviews._may_ask("atmoskop", background=False)  # not even for a card


def test_a_failed_web_search_is_not_taken_for_no_results():
    def offline(query, **kw):
        raise RuntimeError("https://duckduckgo.com/ 202 Ratelimit")

    def nothing(query, **kw):
        raise RuntimeError("No results found.")

    assert mentions.search("Penny Market", offline, sleep=lambda s: None).failed
    assert not mentions.search("Penny Market", nothing, sleep=lambda s: None).failed
    assert mentions.names_it("Práce v Lidlu, Kolín", "Lidl Česká republika v.o.s.")


def test_cards_and_the_background_take_turns_at_each_site(db, tmp_path):
    service = make_service(db, tmp_path)
    with httpx.Client() as one, httpx.Client() as two:
        a, b = service.reviews.polite(one), service.reviews.polite(two)
    assert a.client is one and b.client is two
    assert a._last is b._last and a._turns is b._turns and a._robots is b._robots
