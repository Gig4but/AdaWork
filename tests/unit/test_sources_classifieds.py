"""Classified boards (scraper/sources/classifieds.py) and the offer rules, against saved
pages (no network)."""

import re
from pathlib import Path

import pytest
from test_sources_m6 import run_crawl

from adawork.db.models import Job
from adawork.db.session import make_engine, make_sessionmaker, migrate
from adawork.geo.places import PlaceResolver
from adawork.normalize.offers import not_offer_reason, red_flags
from adawork.scraper.crawl import Lead
from adawork.scraper.sources import CRAWL_SOURCES, KINDS
from adawork.scraper.sources.classifieds import SOURCES, boards, label_values
from adawork.search.service import Filters, search_jobs

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "classifieds"
BY_ID = {s.id: s for s in SOURCES}


@pytest.fixture
def sessions(tmp_path, municipalities):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    sessions = make_sessionmaker(engine)
    with sessions() as s:
        municipalities(s)
    yield sessions
    engine.dispose()


@pytest.fixture
def places(sessions):
    with sessions() as session:
        yield PlaceResolver(session)


def page(name: str) -> str:
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


def detail(board_id: str, i: int, places):
    source = BY_ID[board_id]
    url = (FIXTURES / f"{board_id}.detail{i}.url").read_text(encoding="utf-8").strip()
    lead = Lead(source._ad_link.match(url)[1], url, url)
    return source.parse_detail(page(f"{board_id}.detail{i}"), lead, places)


# --- offer rules ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "reason"),
    [
        ("Hledám práci jako řidič", "seeker"),
        ("HLEDÁM PŘIVÝDĚLEK 100% REMOTE", "seeker"),
        ("Studentka hledá brigádu", "seeker"),
        ("Nabízím hlídání dětí", "service"),
        ("Naučím Vás práci s Excelem online", "service"),
        ("SOUKROMÉ PŮJČKY PRO CELOU ČR DO 24 HODIN", "loan"),
        ("Prodám pracovní boty", "sale"),
        ("Hledáme řidiče taxi", None),
        ("Hledám pomocnici do kuchyně", None),
        ("Nabízím práci na stavbě", None),
        ("Poptáváme managery i živnostníky", None),
    ],
)
def test_not_offer_reason(title, reason):
    assert not_offer_reason(title) == reason


def test_red_flags_fire_on_scams_but_not_on_ordinary_ads():
    scam = "Kompletace propisovaček doma, startovací balíček 300 Kč"
    assert red_flags("Práce na doma", scam) == ["fee", "home_assembly"]
    assert red_flags("Hledáme tipaře", "Až 10.000 Kč za 5 minut Vašeho času!") == [
        "easy_money",
        "referral",
    ]
    assert red_flags("40 000 Kč/měs. pro pohlednou ženu", "") == ["adult"]
    assert red_flags("Prodavačka", "Zkušební doba 3 měsíce, první den zaučení.") == []
    assert red_flags("Prodavačka", "První zkušební den je neplacený.") == ["unpaid_trial"]
    assert red_flags("Prodavačka", "Zkušební den bez nároku na mzdu.") == ["unpaid_trial"]
    # Real Úřad práce ads that looked suspicious to a first version of the rules.
    assert red_flags("Kuchař", "Pohovor: Žerotínova 1742/54, Praha 3") == []
    assert red_flags("Referent", "2x ročně společné aktivity zaměřené na budování týmu") == []
    assert red_flags("Finanční poradce", "Co nabízíme: - žádný multilevel") == []
    assert red_flags("Manažer IT", "znalost: platební systémy, kryptoměny") == []


# --- boards -----------------------------------------------------------------------------------


def test_every_board_is_a_switchable_source_with_a_readable_listing(places):
    assert [b.id for b in boards()] == list(BY_ID)
    for source in SOURCES:
        assert source in CRAWL_SOURCES and KINDS[source.id] == "classifieds"
        listing = source.parse_listing(page(source.id), 1, places)
        assert len(listing.leads) >= 15, source.id
        assert all(lead.fetch_url == lead.url for lead in listing.leads)
        assert len({lead.external_id for lead in listing.leads}) == len(listing.leads)
        assert listing.has_next == bool(source.board.page)
    assert BY_ID["cbazar_prace"].page_url(2) == "https://prace.cbazar.cz/1/"
    assert BY_ID["annonce_prace"].page_url(3).endswith("$18.html?nabidkovy=1&page=3")
    assert BY_ID["bazar_cz"].page_url(2) == BY_ID["bazar_cz"].board.section_url


def test_annonce_ad_with_info_rows(places):
    job = detail("annonce_prace", 0, places)
    assert job.title == "Pracovník ostrahy/sportovní zboží - BRNO"
    assert job.location.municipality == "Brno"
    assert (job.salary_min, job.salary_unit) == (180, "hour")
    assert job.employer_name == "Samcor s.r.o."
    assert job.extra["occupations"] == ["Pracovník bezpečnostní agentury"]
    assert job.posted_at.date().isoformat() == "2026-09-30"
    assert "prevence krádeží" in job.description
    assert "not_offer" not in job.extra


def test_hyperinzerce_place_from_url_own_images_and_non_offers(places):
    job = detail("hyperinzerce_brigady", 2, places)  # "...-nabidka-plzen-1"
    assert job.location.municipality == "Plzeň"
    assert job.images and all("9682503183775321472%2Foriginal" in u for u in job.images)
    loan = detail("hyperinzerce_prace", 2, places)
    assert loan.extra["not_offer"] == "loan"


def test_mimibazar_place_from_the_page_title(places):
    job = detail("mimibazar_prace", 1, places)  # "..., Praha | Mimibazar.cz"
    assert job.location.municipality == "Praha"
    assert job.extra["not_offer"] == "service"


def test_label_values_reads_table_rows_label_elements_and_lines():
    from selectolax.lexbor import LexborHTMLParser

    tree = LexborHTMLParser(
        "<table><tr><th>Lokalita:</th><td>Praha</td></tr></table>"
        "<div><div class='x__label'>Odměna v Kč/měs</div> 15200</div>"
        "<dl><dt>Úvazek</dt><dd>plný</dd></dl><div class='date'>Datum: 8. 10. 2026</div>"
    )
    assert label_values(tree) == {
        "lokalita": "Praha",
        "odmena v kc/mes": "15200",
        "uvazek": "plný",
        "datum": "8. 10. 2026",
    }


def test_crawl_keeps_non_offers_unlisted_and_hides_flagged_offers(sessions):
    source = BY_ID["bazar_cz"]
    links = "".join(
        f"<a href='https://www.bazar.cz/xad{n}-ad-{n}.aspx'>{n}</a>" for n in (1, 2, 3)
    ).encode()
    routes = {
        "/privydelek-brigada-prace/": b"<html><body>" + links + b"</body></html>",
        "/xad1-ad-1.aspx": (FIXTURES / "bazar_cz.detail0.html").read_bytes(),  # tipař
        "/xad2-ad-2.aspx": (FIXTURES / "bazar_cz.detail1.html").read_bytes(),  # France
        "/xad3-ad-3.aspx": b"<h1>Hled\xc3\xa1m pr\xc3\xa1ci</h1><p class='text-justify'>"
        b"Jsem spolehliv\xc3\xa1, hled\xc3\xa1m pr\xc3\xa1ci v Praze.</p>",
    }
    result, _ = run_crawl(sessions, source, routes)
    assert result.details == 3
    with sessions() as s:
        jobs = {j.title: j for j in s.query(Job)}
        assert not jobs["Hledám práci"].is_offer
        assert jobs["Hledáme tipaře - vysoké provize za pár minut vašeho času"].red_flags == [
            "easy_money",
            "referral",
        ]
        shown = search_jobs(s, Filters(), facets=True)
        assert [j.title for j in shown.jobs] == ["Ucházejte se o pracovní nabídky ve Francii."]
        assert shown.flagged == 1
        everything = search_jobs(s, Filters(flagged=True))
        assert everything.total == 2  # the job seeker's post is never listed

    # A second run reads nothing again: the non-offer is known.
    result, requested = run_crawl(sessions, source, routes)
    assert result.details == 0 and not any("xad3" in u for u in requested)


def test_runner_reads_several_boards_at_once(sessions, tmp_path):
    import threading
    import time

    import httpx

    from adawork.config import Profile
    from adawork.db.models import Run
    from adawork.net.polite import PoliteFetcher
    from adawork.scraper.runner import ScrapeRunner
    from adawork.scraper.sources.classifieds import Board, Classifieds

    hosts = ("a.test", "b.test", "c.test")
    threads = set()

    def site(request: httpx.Request) -> httpx.Response:
        threads.add(threading.current_thread().name)
        time.sleep(0.01)  # like a network: the other boards write meanwhile
        host, path = request.url.host, request.url.path
        if path == "/robots.txt":
            return httpx.Response(404)
        if path == "/":
            links = "".join(f"<a href='/ad{n}'>{n}</a>" for n in range(1, 21))
            return httpx.Response(200, text=f"<html><body>{links}</body></html>")
        title = f"Skladník {host} {path}"
        return httpx.Response(200, text=f"<h1>{title}</h1><p class='t'>Práce ve skladu.</p>")

    boards = [
        Classifieds(
            Board(
                h,
                h,
                f"https://{h}/",
                re.escape(f"https://{h}/ad") + r"(\d+)",
                text="p.t",
            )
        )
        for h in hosts
    ]
    transport = httpx.MockTransport(site)
    runner = ScrapeRunner(
        sessions,
        tmp_path / "cache",
        lambda: httpx.Client(transport=transport),
        geocode=False,
        sources=boards,
        profile=lambda: Profile(home_address="Kolín"),
        fetcher_factory=lambda client: PoliteFetcher(client, delay=0, sleep=lambda s: None),
    )
    runner.start()
    runner.wait(60)
    assert runner.progress.phase == "done" and not runner.progress.errors
    assert {name.rsplit("_", 1)[0] for name in threads} >= {"crawl"}
    with sessions() as s:
        assert s.query(Job).count() == 60
        assert {r.source_id for r in s.query(Run)} == set(hosts)


def test_a_board_stops_listing_once_it_has_enough_ads_for_the_run(sessions):
    source = BY_ID["hyperinzerce_prace"]
    listing = page("hyperinzerce_prace").encode()
    routes = {"/": listing, "*": (FIXTURES / "hyperinzerce_prace.detail1.html").read_bytes()}
    result, requested = run_crawl(sessions, source, routes, max_details=5)
    assert result.pages == 1 and result.details == 5  # 18 links on page 1 are enough
    assert not any("page=2" in u for u in requested)


def test_a_fee_that_is_denied_is_no_red_flag():
    assert red_flags("Přivýdělek", "Neplatíte žádné vstupní poplatky.") == []
    assert red_flags("Přivýdělek", "Nulové vstupní poplatky a žádné odběry.") == []
    assert red_flags("Přivýdělek", "Zaplatíte jen vstupní poplatek 990 Kč.") == ["fee"]


def test_no_transaction_stays_open_while_a_page_downloads(sessions):
    """SQLite has one writer: a source that kept its transaction open across downloads made
    the other parallel sources fail with "database is locked" in the first full run."""
    import httpx

    from adawork.core import ScrapeQuery
    from adawork.net.polite import PoliteFetcher
    from adawork.scraper.crawl import crawl
    from adawork.scraper.store import JobStore

    source = BY_ID["hyperinzerce_prace"]
    listing = page("hyperinzerce_prace").encode()
    detail = (FIXTURES / "hyperinzerce_prace.detail1.html").read_bytes()
    with sessions() as session:
        store = JobStore(session, source.id)
        session.commit()
        open_during_download = []

        def site(request: httpx.Request) -> httpx.Response:
            open_during_download.append(session.in_transaction())
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(200, content=listing if request.url.path == "/" else detail)

        fetcher = PoliteFetcher(
            httpx.Client(transport=httpx.MockTransport(site)), delay=0, sleep=lambda s: None
        )
        places = PlaceResolver(session)
        session.commit()
        result = crawl(source, fetcher, store, places, ScrapeQuery(kraje=[]), max_details=5)
    assert result.details == 5 and len(open_during_download) > 5
    assert not any(open_during_download)


def test_two_sources_adding_the_same_new_place(sessions):
    from adawork.core import ParsedJob, ParsedLocation
    from adawork.scraper.store import JobStore

    place = ParsedLocation("obec:533165", "municipality", municipality="Kolín", kraj="x")
    with sessions() as a, sessions() as b:
        first, second = JobStore(a, "board_a"), JobStore(b, "board_b")  # both load no places
        a.commit()
        b.commit()
        first.add(ParsedJob("1", "Skladník", location=place))
        first.commit()
        second.add(ParsedJob("2", "Řidič", location=place))  # the place exists by now
        second.commit()
        assert {j.location_id for j in b.query(Job)} == {first._locations["obec:533165"]}
