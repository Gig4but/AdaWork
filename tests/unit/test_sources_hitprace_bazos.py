"""hitprace.cz and prace.bazos.cz adapters against saved pages (no network)."""

from datetime import datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from test_sources_m6 import fixture, run_crawl

from adawork.core import ParsedJob, ScrapeQuery
from adawork.db.models import JobSource
from adawork.db.session import make_engine, make_sessionmaker, migrate
from adawork.geo.places import PlaceResolver
from adawork.scraper.crawl import Lead
from adawork.scraper.sources import bazos, hitprace

STREDOCESKY = ScrapeQuery(kraje=["Středočeský kraj"], home="Kolín", radius_km=50)


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


def html(path: str) -> str:
    return fixture(path).decode("utf-8")


# --- hitprace.cz ------------------------------------------------------------------------------


def test_hitprace_searches_near_home_with_the_nearest_radius():
    query = ScrapeQuery(kraje=["Středočeský kraj"], home="Kolín", radius_km=12, keywords=["řidič"])
    ((_, listing),) = hitprace.SOURCE.listings(query)
    params = parse_qs(urlsplit(listing(2)).query, keep_blank_values=True)
    assert params == {
        "lokalita_textbox": ["Kolín"],
        "obor_textbox": ["řidič"],
        "form_geolocation_okruh": ["15"],
        "strana": ["2"],
    }
    assert "strana" not in listing(1)
    assert hitprace.radius(80) == 40 and hitprace.radius(0) == 3

    # No home: around each region's capital.
    query = ScrapeQuery(kraje=["Kraj Vysočina", "Zlínský kraj"])
    towns = [
        parse_qs(urlsplit(u(1)).query)["lokalita_textbox"][0]
        for _, u in hitprace.SOURCE.listings(query)
    ]
    assert towns == ["Jihlava", "Zlín"]


def test_hitprace_listing_cards(places):
    page = hitprace.SOURCE.parse_listing(html("hitprace/listing_brno.html"), 1, places)
    assert len(page.leads) == 10 and page.has_next
    lead = page.leads[0]
    assert lead.external_id == "ru6551487"
    assert lead.url == lead.fetch_url
    assert lead.url == (
        "https://www.hitprace.cz/prace-detail/"
        "vedouci-smeny-ve-vyrobe-bez-nocnich-a-vikendu-nastup-ihned-a14721-brno-ru6551487/"
    )
    card = lead.card
    assert card.title.startswith("Vedoucí směny ve výrobě")  # without the leading ⭐
    assert card.employer_name == "HOFMANN WIZARD s.r.o."
    assert (card.salary_min, card.salary_max, card.salary_unit) == (35000, 40000, "month")
    assert card.place_text == "Brno" and card.location.municipality == "Brno"
    assert page.leads[3].card.salary_min is None  # no pay shown

    # Later pages: no data-job-id, the ID ends the detail URL; badges are not the employer.
    page = hitprace.SOURCE.parse_listing(html("hitprace/search_kolin_p2.html"), 2, places)
    assert len(page.leads) == 10 and page.has_next
    first = page.leads[0]
    assert first.external_id == "HIT82253"
    assert first.card.employer_name == "Agentura Top Talent, s. r. o."
    assert first.card.place_text == "Chlumec nad Cidlinou"
    kolin = next(lead.card for lead in page.leads if lead.card.place_text == "Kolín")
    assert kolin.location.kraj == "Středočeský kraj"
    hourly = next(lead.card for lead in page.leads if lead.external_id == "fb6555234")
    assert (hourly.salary_min, hourly.salary_unit) == (190, "hour")


def test_hitprace_detail_pages(places):
    lead = Lead("ru6551487", "https://www.hitprace.cz/prace-detail/x-ru6551487/")
    job = hitprace.SOURCE.parse_detail(html("hitprace/detail_1.html"), lead, places)
    assert job.title == "Vedoucí směny ve výrobě | bez nočních a víkendů | nástup ihned (A14721)"
    assert job.employer_name == "HOFMANN WIZARD s.r.o."
    assert job.location.municipality == "Brno"
    assert job.posted_at == datetime(2026, 10, 2)
    assert job.expires_at is None  # a rolling validThrough, not a deadline
    assert job.contract_types == ["full_time"]
    # No baseSalary in the JSON-LD: the pay shown on the page.
    assert (job.salary_min, job.salary_max, job.salary_unit) == (35000, 40000, "month")
    assert "Vedoucím směny" in job.description

    job = hitprace.SOURCE.parse_detail(html("hitprace/detail_2.html"), lead, places)
    assert job.title == "Servisní technik kogeneračních jednotek"
    assert job.place_text == "Brno"  # "Jihomoravský kraj, Brno"
    assert job.location.municipality_code == "582786"
    assert (job.salary_min, job.salary_max) == (45000, 52000)
    assert job.posted_at == datetime(2026, 8, 30)

    assert hitprace.SOURCE.parse_detail("<html></html>", lead, places) is None


def test_hitprace_crawl_round_trip(sessions):
    # One search page without further pages; every detail page is the same saved one.
    listing = fixture("hitprace/search_kolin_p2.html").replace(b"strana=", b"x=")
    routes = {
        "/robots.txt": fixture("hitprace/robots.txt"),
        "/prace/": listing,
        "*": fixture("hitprace/detail_2.html"),
    }
    result, requested = run_crawl(sessions, hitprace.SOURCE, routes, STREDOCESKY, max_details=3)
    assert "lokalita_textbox=Kol%C3%ADn" in requested[1]
    assert "form_geolocation_okruh=40" in requested[1]
    assert result.complete and result.details == 3
    assert len([u for u in requested if "/prace-detail/" in u]) == 3
    with sessions() as s:
        assert s.query(JobSource).filter_by(source_id="hitprace").count() == 10


# --- prace.bazos.cz ---------------------------------------------------------------------------

ROW = (
    '<div class="inzeraty inzeratyflex"><div class="inzeratynadpis">'
    '<h2 class=nadpis><a href="/inzerat/{id}/ridic.php">Řidič</a></h2>'
    "<span class=velikost10> - [6.10. 2026]</span><div class=popis>Text</div></div>"
    '<div class="inzeratycena"><b>Dohodou</b></div>'
    '<div class="inzeratylok">{area}<br>{psc}</div></div>'
)


def test_bazos_walks_every_category():
    urls = [listing(1) for _, listing in bazos.SOURCE.listings(STREDOCESKY)]
    assert len(urls) == 24 and "https://prace.bazos.cz/logistika/" in urls
    assert bazos.SOURCE.category_url("logistika", 3) == "https://prace.bazos.cz/logistika/40/"
    categories = {
        a.split("/")[1]
        for a in html("bazos/listing_home.html").split('href="')
        if a[:1] == "/" and a.count("/") >= 2 and a.split("/")[1] in bazos.CATEGORIES
    }
    assert categories == set(bazos.CATEGORIES)


def test_bazos_listing_rows(places):
    page = bazos.SOURCE.parse_listing(html("bazos/listing_logistika.html"), 2, places)
    assert len(page.leads) == 19 and page.has_next  # a link to /logistika/40/
    lead = page.leads[0]
    assert lead.external_id == "223687422"
    assert lead.url == "https://prace.bazos.cz/inzerat/223687422/stala-prace-rakovnik-naves.php"
    card = lead.card
    assert card.title == "Stálá práce RAKOVNÍK KOLEČKA - návěs"
    assert card.place_text == "Rakovník"
    assert card.posted_at == datetime(2026, 10, 6)
    assert card.description.startswith("Hledáme řidiče kamionu C+E")
    assert card.salary_min is None  # "2 500 Kč" without a unit: likely a day's pay
    kolin = next(lead.card for lead in page.leads if lead.external_id == "224576160")
    assert kolin.location.municipality == "Kolín"
    assert (kolin.salary_min, kolin.salary_unit) == (48000, "month")

    page = bazos.SOURCE.parse_listing(html("bazos/listing_pravo.html"), 1, places)
    assert [lead.external_id for lead in page.leads] == ["223125539"] and not page.has_next
    card = page.leads[0].card
    assert card.posted_at == datetime(2026, 8, 28)
    assert card.location.kraj == "Hlavní město Praha"  # "Praha 10"


@pytest.mark.parametrize(
    ("area", "psc", "wanted"),
    [
        ("Kladno", "273 01", True),  # the PSČ names Lhota (okres Kladno)
        ("Zlín", "763 23", False),  # Lhota near Zlín
        ("Brno", "602 00", False),
        ("Kolín", "282 01", True),  # unknown PSČ: the place name
        ("Zahraničí", "987 66", False),
        ("Neznámo", "999 99", True),  # unknown: the AI decides later
    ],
)
def test_bazos_skips_ads_outside_the_regions(places, area, psc, wanted):
    page = bazos.SOURCE.parse_listing(ROW.format(id=1, area=area, psc=psc), 1, places)
    (lead,) = page.leads
    assert bazos.SOURCE.wanted(lead, STREDOCESKY, places) is wanted


def test_bazos_salary_only_when_plausible():
    def salary(text):
        job = ParsedJob("1", "x")
        bazos.set_salary(job, text)
        return job.salary_min, job.salary_unit

    assert salary("35 000 Kč") == (35000, "month")
    assert salary("180 Kč") == (180, "hour")
    assert salary("2 500 Kč") == (None, None)
    assert salary("2 500 000 Kč") == (None, None)
    assert salary("1 Kč") == (None, None)
    assert salary("Dohodou") == salary("V textu") == (None, None)


def test_bazos_detail_pages(places):
    lead = Lead("224343656", "https://prace.bazos.cz/inzerat/224343656/x.php")
    page = html("bazos/detail_1.html")
    job = bazos.SOURCE.parse_detail(page, lead, places)
    assert job.title == "FOODORA – Rozvoz jídla ve SLANÉM"
    assert job.posted_at == datetime(2026, 10, 6)
    assert job.place_text == "Kladno"
    assert job.location.okres == "Kladno"  # Slaný is not in the test data
    assert job.salary_min is None  # "V textu"
    assert job.description.startswith("🚗 Hledáme nové zaměstnance")
    assert "Firemní auto zdarma" in job.description
    assert job.employer_name is None and job.contact == {}

    # The town in the place link, with the PSČ, picks the municipality.
    page = page.replace("slan%C3%BD-1/27401", "lhota-1/27301").replace(">274 01<", ">273 01<")
    page = page.replace("V textu", "35 000 Kč")
    job = bazos.SOURCE.parse_detail(page, lead, places)
    assert job.location.municipality_code == "549240"
    assert (job.salary_min, job.salary_unit) == (35000, "month")

    job = bazos.SOURCE.parse_detail(html("bazos/detail_2.html"), lead, places)
    assert job.title == "ŘIDIČ MKD - PRAVIDELNÁ LINKA"
    assert job.location.okres == "Praha-východ"
    assert bazos.SOURCE.parse_detail("<html></html>", lead, places) is None


def test_bazos_crawl_round_trip(sessions, places):
    listing = html("bazos/listing_logistika.html")
    leads = bazos.SOURCE.parse_listing(listing, 1, places).leads
    wanted = [lead for lead in leads if bazos.SOURCE.wanted(lead, STREDOCESKY, places)]
    assert 0 < len(wanted) < len(leads)
    routes = {"/robots.txt": fixture("bazos/robots.txt"), "/logistika/": listing.encode()}
    for lead in wanted:
        routes[urlsplit(lead.url).path] = fixture("bazos/detail_2.html")

    result, requested = run_crawl(sessions, bazos.SOURCE, routes, STREDOCESKY, max_details=50)
    details = [u for u in requested if "/inzerat/" in u]
    assert sorted(details) == sorted(lead.url for lead in wanted)
    assert not any("224268799" in u for u in details)  # Brno
    assert not any("223939147" in u for u in details)  # Praha 8
    assert result.details == len(wanted) and not result.complete  # other categories are 404
    assert any(u.endswith("/logistika/20/") for u in requested)
    with sessions() as s:
        stored = s.query(JobSource).filter_by(source_id="bazos").count()
    assert stored == len(wanted)
