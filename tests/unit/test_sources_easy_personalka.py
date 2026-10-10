"""easy-prace.cz and personalka.cz adapters against saved pages (no network)."""

from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import func, select
from test_sources_m6 import fixture, run_crawl

from adawork.core import ScrapeQuery, utcnow
from adawork.db.models import Job, JobSource
from adawork.db.session import make_engine, make_sessionmaker, migrate
from adawork.geo.places import PlaceResolver
from adawork.net.polite import PoliteFetcher
from adawork.scraper.crawl import MAX_RECHECKS, Lead, crawl
from adawork.scraper.sources import easy_prace, personalka
from adawork.scraper.store import JobStore


@pytest.fixture
def sessions(tmp_path, municipalities):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    sessions = make_sessionmaker(engine)
    with sessions() as s:
        municipalities(s)
    yield sessions
    engine.dispose()


def html(path: str) -> str:
    return fixture(path).decode("utf-8")


def test_easy_prace_detail_pages(sessions):
    with sessions() as session:
        places = PlaceResolver(session)
        url = "https://www.easy-prace.cz/nabidka/-odlevani-a-opracovani-dilu-/630059"
        lead = Lead("630059", url, url)
        job = easy_prace.SOURCE.parse_detail(html("easy_prace/detail_1.html"), lead, places)
        assert job.title.startswith("Odlévání a opracování dílů | 3směnný provoz")
        assert job.employer_name == "Manuvia DreamJob s.r.o."
        assert (job.salary_min, job.salary_max, job.salary_unit) == (32000, 35000, "month")
        assert job.contract_types == ["full_time"]
        assert job.education == "basic"  # "Základní, Vyučení, SŠ s maturitou, ..."
        assert job.suitable_for == ["absol"]
        assert job.place_text.startswith("Pardubice (Pardubice); Pardubičky, Pardubice;")
        assert job.location.municipality == "Pardubice"
        assert job.description.startswith("Hledáme nové kolegy do výroby!")
        assert "Popis pracovní nabídky" not in job.description
        assert "Zkušenosti z výrobního závodu" in job.description
        assert "Kontaktní osoba" not in job.description
        assert job.extra["portal_fields"] == ["Výroba a průmysl", "Strojírenství"]

        job = easy_prace.SOURCE.parse_detail(html("easy_prace/detail_2.html"), lead, places)
        assert job.title == "Elektrikář do oddělení údržby - Náborový příspěvek 50 000 Kč"
        assert job.employer_name == "HOFMANN WIZARD s.r.o."
        assert (job.salary_min, job.salary_max) == (42000, 46000)
        assert job.education == "higher_vocational"  # "Vyšší odborné, Bakalářské, Vysokoškolské"
        assert job.place_text == "Kopřivnice"


def test_easy_prace_place_forms(sessions):
    with sessions() as session:
        places = PlaceResolver(session)
        assert easy_prace.resolve_place("Kolín (Kolín)", places).municipality == "Kolín"
        assert easy_prace.resolve_place("Lhota (okres Kladno)", places).kraj == "Středočeský kraj"
        assert easy_prace.resolve_place("Lhota (okres Zlín)", places).kraj == "Zlínský kraj"


def test_easy_prace_region_listing(sessions):
    last = fixture("easy_prace/listing_vysocina_75.html").replace(
        b"&quot;pocetStranek&quot;:75", b"&quot;pocetStranek&quot;:2"
    )
    routes = {
        "/robots.txt": fixture("easy_prace/robots.txt"),
        "/vysocina-kraj": fixture("easy_prace/listing_vysocina.html"),
        "/vysocina-kraj/strana/2": last,
        "*": fixture("easy_prace/detail_1.html"),
    }
    query = ScrapeQuery(kraje=["Kraj Vysočina"], home="Jihlava")
    result, requested = run_crawl(sessions, easy_prace.SOURCE, routes, query, max_details=3)
    assert result.complete and result.pages == 2 and result.details == 3
    with sessions() as s:
        assert s.scalar(select(func.count()).select_from(JobSource)) == 70
        card = s.scalar(select(Job).join(JobSource).where(JobSource.external_id == "627580"))
        assert card.title == "Account Manager pro služby Google"
        assert card.employer_name == "STREEWVIEW CZECH s.r.o."
        assert (card.salary_min, card.salary_max) == (80000, 170000)
        assert card.location.kraj == "Kraj Vysočina"
        assert card.posted_at is None  # "Aktualizováno před pár hodinami"
        read = s.scalar(select(Job).join(JobSource).where(JobSource.external_id == "630933"))
        assert read.posted_at is not None  # "Přidáno před pár hodinami", from the card
        nationwide = s.scalars(select(Job).where(Job.place_text == "Česká republika")).all()
        assert nationwide and all(job.location is None for job in nationwide)


def test_personalka_listing_cards(sessions):
    with sessions() as session:
        places = PlaceResolver(session)
    page = personalka.SOURCE.parse_listing(html("personalka/listing_kraj.html"), 1, places)
    assert len(page.leads) == 30 and page.has_next
    lead = next(lead for lead in page.leads if lead.external_id == "1063084")
    assert lead.url == lead.fetch_url == "https://personalka.cz/1063084"
    card = lead.card
    assert card.title == "ROZPOČTÁŘ / PŘÍPRAVÁŘ STAVEB (ž/m)"
    assert card.place_text == "Kolín" and card.location.municipality == "Kolín"
    assert (card.salary_min, card.salary_max, card.salary_unit) == (55000, 75000, "month")
    assert card.contract_types == ["full_time", "contractor"]  # "IČO, HPP"
    assert card.employer_name == "rbe Koláčný s r.o."
    assert card.posted_at.date() == date.today()  # "Dnes"
    first = page.leads[0].card
    assert (first.title, first.employer_name, first.salary_min) == (
        "Technolog programátor",
        "ALUMETALL CZ s.r.o.",
        None,
    )

    page = personalka.SOURCE.parse_listing(html("personalka/listing_kraj_150.html"), 150, places)
    assert len(page.leads) == 28 and page.has_next
    card = page.leads[0].card
    assert (card.title, card.place_text, card.salary_min, card.salary_max) == (
        "Skladníci, obsluha manipulačních vozíků",
        "Čelákovice",
        21480,
        22000,
    )
    assert card.employer_name == "FERMATA, a. s." and card.posted_at is None

    page = personalka.SOURCE.parse_listing(html("personalka/listing_kraj_end.html"), 400, places)
    assert page.leads == [] and not page.has_next


def test_personalka_listing_urls():
    query = ScrapeQuery(kraje=["Hlavní město Praha", "Středočeský kraj"])
    (praha, first), (stredocesky, second) = personalka.SOURCE.listings(query)
    assert (praha, stredocesky) == ("Hlavní město Praha", "Středočeský kraj")
    assert first(1) == "https://personalka.cz/prace/hlavni-mesto-praha"
    assert second(3) == "https://personalka.cz/prace/stredocesky-kraj?page=3"


def test_personalka_detail_pages(sessions):
    with sessions() as session:
        places = PlaceResolver(session)
        lead = Lead("1075419", "https://personalka.cz/1075419", "https://personalka.cz/1075419")
        job = personalka.SOURCE.parse_detail(html("personalka/detail_1.html"), lead, places)
        assert job.title == "Obchodní zástupce pro zemědělskou techniku Domažlicko"
        assert job.employer_name == "Z - TEC SERVICE s.r.o."
        assert job.employer_ico == "47548312"
        assert (job.salary_min, job.salary_max, job.salary_unit) == (28000, 60000, "month")
        assert job.place_text == "Domažlice"
        assert job.location.kraj == "Plzeňský kraj"  # Domažlice is not in the test table
        assert job.description.startswith("Zemědělství není jen obor.")
        assert "Kontaktní osoba" not in job.description

        job = personalka.SOURCE.parse_detail(html("personalka/detail_2.html"), lead, places)
        assert job.title == "Obsluha v kukuřičném bludišti Telč"
        assert (job.employer_name, job.employer_ico) == ("RAJAN EKO s.r.o.", "28817991")
        assert job.place_text == "Za Stínadly, 58856 Telč"
        assert job.location.okres == "Jihlava"  # the "okres Jihlava" link
        assert job.contract_types == ["dpp"]  # "Brigáda, DPP"
        assert job.suitable_for == ["absol"]
        assert job.benefits == ["premie"] and job.shifts == ["pruznaPd"]
        assert job.extra["languages"] == ["Čeština (aktivní)"]
        assert "Základní mzda 140,- Kč na hodinu" in job.description


def test_personalka_ico_check():
    assert personalka.valid_ico("47548312")
    assert not personalka.valid_ico("47548313")
    assert not personalka.valid_ico("1234")


PERSONALKA_PAGES = {
    "/prace/stredocesky-kraj": "personalka/listing_kraj.html",
    "/prace/stredocesky-kraj?page=2": "personalka/listing_kraj_150.html",
    "/prace/stredocesky-kraj?page=3": "personalka/listing_kraj_end.html",
}


def crawl_personalka(sessions, pages=PERSONALKA_PAGES, detail="personalka/detail_2.html"):
    """pages: path -> fixture (None: 404); any other page is `detail`. Returns the result
    and the requested URLs."""
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested.append(url)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, content=fixture("personalka/robots.txt"))
        path = pages.get(url.removeprefix("https://personalka.cz"), detail)
        return httpx.Response(200, content=fixture(path)) if path else httpx.Response(404)

    fetcher = PoliteFetcher(
        httpx.Client(transport=httpx.MockTransport(handler)), delay=0, sleep=lambda s: None
    )
    query = ScrapeQuery(kraje=["Středočeský kraj"], home="Kolín")
    with sessions() as session:
        store = JobStore(session, personalka.SOURCE_ID)
        result = crawl(
            personalka.SOURCE, fetcher, store, PlaceResolver(session), query, max_details=3
        )
    return result, requested


def test_personalka_empty_first_page_is_no_complete_pass(sessions):
    # The empty page after "load more" ends a listing; an empty first page is a broken one.
    pages = {"/prace/stredocesky-kraj": "personalka/listing_kraj_end.html"}
    assert not crawl_personalka(sessions, pages)[0].complete


def test_personalka_crawl_walks_pages(sessions):
    result, requested = crawl_personalka(sessions)
    assert result.complete and result.pages == 3 and result.details == 3
    assert len([u for u in requested if "/prace/" not in u and "robots" not in u]) == 3
    with sessions() as s:
        sources = s.scalars(select(JobSource).where(JobSource.source_id == "personalka")).all()
        assert len(sources) == 58
        assert sum(src.detail_at is not None for src in sources) == 3
        card_only = next(src for src in sources if src.external_id == "448241")
        assert card_only.detail_at is None and card_only.job.salary_min == 21480
        read = next(src for src in sources if src.external_id == "1063084")
        # The detail page wins; the card fills what it lacks (salary, posting date).
        assert read.detail_at is not None and read.job.employer_ico == "28817991"
        assert read.job.contract_types == ["dpp"] and read.job.salary_min == 55000
        assert read.job.posted_at.date() == date.today()


def test_personalka_keeps_ads_a_short_pass_did_not_reach(sessions):
    crawl_personalka(sessions)
    with sessions() as s:  # a month and more since the full pass
        for row in s.scalars(select(JobSource)):
            row.last_seen_at -= timedelta(days=40)
        s.commit()

    # Nothing new on page 1: the run stops there and does not see the older ads.
    result, requested = crawl_personalka(sessions)
    assert not result.complete and result.pages == 1
    with sessions() as s:
        statuses = s.scalars(select(JobSource.status)).all()
        assert len(statuses) == 58 and set(statuses) == {"active"}


def test_personalka_reads_long_unseen_ads_again(sessions):
    assert personalka.SOURCE.gone(html("personalka/detail_gone.html"))
    assert not personalka.SOURCE.gone(html("personalka/detail_2.html"))

    crawl_personalka(sessions)
    older = ["448241", "443595"]  # on page 2, which the next run does not reach
    with sessions() as s:
        for row in s.scalars(select(JobSource)):
            row.last_seen_at -= timedelta(days=60 if row.external_id in older else 40)
        s.commit()

    # The ads unseen longest are read first: one page says the ad is over, one is gone.
    pages = {**PERSONALKA_PAGES, "/448241": "personalka/detail_gone.html", "/443595": None}
    result, _ = crawl_personalka(sessions, pages)
    assert not result.complete and result.pages == 1
    with sessions() as s:
        rows = {r.external_id: r for r in s.scalars(select(JobSource))}
        assert {i for i, r in rows.items() if r.status == "expired"} == set(older)
        assert rows["448241"].job.status == "expired"
        # The others still online count as seen: read again only after another month.
        recent = utcnow() - timedelta(days=1)
        assert sum(r.last_seen_at > recent for r in rows.values()) == 30 + MAX_RECHECKS - 2
