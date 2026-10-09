"""inwork.cz and fajn-brigady.cz: one template, read from saved pages (no network)."""

from datetime import date, datetime

import pytest
from test_sources_m6 import fixture, run_crawl

from adawork.core import ScrapeQuery
from adawork.db.models import JobSource
from adawork.db.session import make_engine, make_sessionmaker, migrate
from adawork.geo.places import PlaceResolver
from adawork.scraper.crawl import Lead, sitemap_entries
from adawork.scraper.sources import _inzerce, fajn_brigady, inwork

FB = "https://www.fajn-brigady.cz/brigady"
IW = "https://www.inwork.cz/prace"
FB_SITEMAP = "/static/sitemap/sitemap_fb/inzeraty_detail.xml"


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


def lead_for(source, url):
    if hasattr(source, "lead"):  # sitemap source
        return source.lead(url, None)
    ad_id = url.rstrip("/").rsplit("/", 1)[-1].split("-", 1)[0]
    return Lead(ad_id, url, url)


def parse(source, path, url, places):
    return source.parse_detail(fixture(path).decode("utf-8"), lead_for(source, url), places)


def test_fajn_brigady_detail(places):
    job = parse(
        fajn_brigady.SOURCE,
        "fajn_brigady/detail_1.html",
        f"{FB}/manualni/klasterec-nad-ohri/6516920-ostraha-objektu/",
        places,
    )
    assert job.external_id == "6516920" and job.title == "Ostraha objektu"
    assert job.employer_name == "1. Mostecká bezpečnostní s.r.o."
    assert (job.salary_min, job.salary_max, job.salary_unit) == (20000, 23000, "month")
    assert job.positions_count == 3
    assert job.suitable_for == ["OZP2"]  # "Důchodce, OZP"
    assert job.posted_at == datetime(2026, 9, 9)
    assert job.start_date is None and job.extra["start"] == "Ihned"
    assert job.place_text == "Klášterec nad Ohří (Ok. Chomutov)"
    assert job.description.startswith("Nabízím práci ostrahy")
    assert "Informace o pracovním místě" not in job.description

    job = parse(
        fajn_brigady.SOURCE,
        "fajn_brigady/detail_2.html",
        f"{FB}/manualni/ceske-budejovice/6520793-ostraha/",
        places,
    )
    assert job.title == "OSTRAHA" and job.employer_name == "OSFA s.r.o."
    assert (job.salary_min, job.salary_max, job.salary_unit) == (134, 134, "hour")
    # The workplace is cut short on the page ("1661..."), whole in its title.
    assert job.place_text == "České Budějovice, J. Boreckého 1661/34"
    assert "Požadujeme" in job.description and "Nabízíme" in job.description


def test_inwork_detail(places):
    job = parse(
        inwork.SOURCE,
        "inwork/detail_2.html",
        f"{IW}/stredocesky-kraj/melnik/melnik/6474409-technici-udrzby-serizovaci/",
        places,
    )
    assert job.external_id == "6474409" and job.title == "Technici údržby - seřizovači"
    assert job.employer_name == "RETAL Czech a.s."  # without "(Úřad práce)"
    assert job.extra["advertiser"] == "Úřad práce"
    assert job.extra["position"] == "Mechanik, opravář"
    assert (job.salary_min, job.salary_max, job.salary_unit) == (45000, 45000, "month")
    assert job.contract_types == ["full_time"]
    assert job.education == "vocational"  # "Vyučen"
    assert job.positions_count == 1
    assert job.start_date == date(2026, 8, 7)
    assert job.posted_at == datetime(2026, 8, 9)
    assert job.description.startswith("Náplň práce:")  # the emptied contact line is gone

    # An employer's branded ad page: the title sits in the content column.
    job = parse(
        inwork.SOURCE,
        "inwork/detail_3.html",
        f"{IW}/stredocesky-kraj/mlada-boleslav/mlada-boleslav/6554719-kontrolori-kvality/",
        places,
    )
    assert job.title.startswith("Kontroloři kvality")
    assert job.employer_name == "HOFMANN WIZARD s.r.o."
    assert (job.salary_min, job.salary_unit) == (170, "hour")
    assert job.suitable_for == ["absol"] and job.education == "basic"
    assert job.positions_count == 10 and job.extra["advertiser"] == "Agentura"
    assert job.posted_at == datetime(2026, 10, 5)


def test_place_is_resolved(sessions):
    html = fixture("inwork/detail_2.html").decode("utf-8").replace("Mělník", "Kolín")
    with sessions() as session:
        places = PlaceResolver(session)
        url = f"{IW}/stredocesky-kraj/kolin/kolin/6474409-x/"
        job = inwork.SOURCE.parse_detail(html, lead_for(inwork.SOURCE, url), places)
    assert job.location.municipality == "Kolín" and job.location.kraj == "Středočeský kraj"


def test_emptied_contact_lines():
    assert _inzerce._EMPTY_CONTACT.match("Kontakt:;, e-mail:")
    assert not _inzerce._EMPTY_CONTACT.match("Kontakt: Jana, telefon 777 000 000")
    assert not _inzerce._EMPTY_CONTACT.match(", " * 200 + "x")


def test_expired_ad_is_not_read(places):
    job = parse(
        inwork.SOURCE,
        "inwork/detail_1.html",
        f"{IW}/stredocesky-kraj/melnik/neratovice/6471498-izolateri/",
        places,
    )
    assert job is None


def test_inwork_region_listing(places):
    page = inwork.SOURCE.parse_listing(fixture("inwork/listing_kraj.html").decode(), 1, places)
    assert len(page.leads) == 40 and page.has_next
    first = page.leads[0]
    assert first.external_id == "6558461" and first.card.title.startswith("ZÁSTUPCE/KYNĚ")
    assert inwork.SOURCE.listing_url("stredocesky-kraj", 3).endswith("/stredocesky-kraj/?start=80")
    query = ScrapeQuery(kraje=["Kraj Vysočina"])
    assert inwork.SOURCE.listings(query)[0][1](1) == f"{IW}/vysocina/"


def test_fajn_brigady_leads_and_regions(places):
    source = fajn_brigady.SOURCE
    query = ScrapeQuery(kraje=["Středočeský kraj"], home="Kolín")
    source.prepare(query, places)

    def wanted(town):
        lead = source.lead(f"{FB}/manualni/{town}/123-x/", None)
        return source.wanted(lead, query, places)

    assert wanted("kolin") and wanted("brandys-nad-labem-stara-boleslav")
    assert wanted("lhota")  # one Lhota is in the region
    assert not wanted("praha-4") and not wanted("jihlava") and not wanted("plzen")
    assert wanted("cela-cr") and wanted("tynec-nad-labem")  # unknown: kept
    assert source.lead("https://www.fajn-brigady.cz/brigady-v-zahranici/1-x/", None) is None

    xml = fixture("fajn_brigady/sitemap_inzeraty_detail.xml").decode()
    entries = sitemap_entries(xml)
    leads = [lead for url, modified in entries if (lead := source.lead(url, modified))]
    assert len(entries) == 2112 and len(leads) == len(entries) - 6  # 6 ads abroad


def test_inwork_crawl(sessions, monkeypatch):
    monkeypatch.setattr(inwork.SOURCE, "max_pages", 1)  # the mock serves page 1 for any start
    routes = {
        "/robots.txt": fixture("inwork/robots.txt"),
        "/prace/stredocesky-kraj/": fixture("inwork/listing_kraj.html"),
        "*": fixture("inwork/detail_2.html"),
    }
    result, requested = run_crawl(sessions, inwork.SOURCE, routes, max_details=4)
    assert result.details == 4
    with sessions() as s:
        assert s.query(JobSource).filter_by(source_id="inwork").count() == 40  # cards too


def test_fajn_brigady_crawl(sessions):
    routes = {
        "/robots.txt": fixture("fajn_brigady/robots.txt"),
        FB_SITEMAP: fixture("fajn_brigady/sitemap_inzeraty_detail.xml"),
        "*": fixture("fajn_brigady/detail_1.html"),
    }
    result, requested = run_crawl(sessions, fajn_brigady.SOURCE, routes, max_details=3)
    details = [u for u in requested if "/brigady/" in u]
    assert result.complete and result.details == 3
    assert not any("/praha-" in u or "/jihlava/" in u for u in details)
    assert "/kolin/" in details[0] or "/brandys-nad-labem" in details[0]


def test_fajn_brigady_page_variant_with_other_label_class(places):
    job = parse(
        fajn_brigady.SOURCE,
        "fajn_brigady/detail_variant.html",
        f"{FB}/manualni/horice/6536050-ostraha-arealu-horice/",
        places,
    )
    assert job.employer_name == "ALEX FULL s.r.o." and job.positions_count == 2
    assert job.place_text.startswith("Hořice")
