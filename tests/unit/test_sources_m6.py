"""M6 portals: sitemap discovery and the adapters, against saved pages (no network)."""

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from adawork.core import ScrapeQuery
from adawork.db.models import Job, JobSource
from adawork.db.session import make_engine, make_sessionmaker, migrate
from adawork.geo.places import PlaceResolver
from adawork.net.polite import PoliteFetcher
from adawork.scraper.crawl import Lead, crawl, sitemap_entries
from adawork.scraper.sources import (
    bazos,
    dobraprace,
    easy_prace,
    inwork,
    jobs_cz,
    personalka,
    pracezarohem,
    startupjobs,
)
from adawork.scraper.store import JobStore

SOURCES = Path(__file__).parents[1] / "fixtures" / "sources"
AD = f"{startupjobs.BASE}/nabidka"
VYSOCINA = ScrapeQuery(kraje=["Kraj Vysočina"], home="Jihlava")


def fixture(path: str) -> bytes:
    return (SOURCES / path).read_bytes()


@pytest.fixture
def sessions(tmp_path, municipalities):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    sessions = make_sessionmaker(engine)
    with sessions() as s:
        municipalities(s)
    yield sessions
    engine.dispose()


def run_crawl(sessions, source, routes, query=None, max_details=5):
    """routes: path -> bytes of the mock site, "*" for any other page; returns the result
    and the requested URLs."""
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, content=routes.get("/robots.txt", b""))
        path = request.url.path
        query = request.url.query.decode()
        body = routes.get(f"{path}?{query}" if query else path, routes.get(path, routes.get("*")))
        return httpx.Response(200, content=body) if body is not None else httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetcher = PoliteFetcher(client, delay=0, sleep=lambda s: None)
    query = query or ScrapeQuery(kraje=["Středočeský kraj"], home="Kolín")
    with sessions() as session:
        places = PlaceResolver(session)
        store = JobStore(session, source.id)
        result = crawl(source, fetcher, store, places, query, max_details=max_details)
    return result, requested


def test_sitemap_entries_with_and_without_lastmod():
    xml = (
        "<urlset><url><loc>https://a.cz/1</loc><lastmod>2026-08-07T07:00:03+02:00</lastmod>"
        "</url><url><loc>https://a.cz/2?x=1&amp;y=2</loc></url>"
        "<url> <loc>https://a.cz/3</loc> <lastmod>2026-08-28</lastmod> </url></urlset>"
    )
    assert sitemap_entries(xml) == [
        ("https://a.cz/1", datetime(2026, 8, 7, 5, 0, 3)),
        ("https://a.cz/2?x=1&y=2", None),
        ("https://a.cz/3", datetime(2026, 8, 28)),
    ]


def test_near_home_matches_names_of_any_length():
    towns = ("Kolín", "Brandýs nad Labem-Stará Boleslav", "Plzeň")
    kraje = ("Středočeský kraj", "Středočeský kraj", "Plzeňský kraj")
    places = SimpleNamespace(
        municipalities=[SimpleNamespace(name=n, kraj=k) for n, k in zip(towns, kraje, strict=True)]
    )
    source = startupjobs.SOURCE
    source.prepare(ScrapeQuery(kraje=["Středočeský kraj"]), places)
    assert source.near_home(f"{AD}/1/skladnik-kolin")
    assert source.near_home(f"{AD}/2/ridic-brandys-nad-labem-stara-boleslav")
    assert not source.near_home(f"{AD}/3/ridic-brandys-nad-labem")
    assert not source.near_home(f"{AD}/4/kuchar-plzen")


def test_sitemap_ads_ending_with_a_town_elsewhere_are_skipped():
    towns = ("Kolín", "Plzeň", "Práce", "Vysočina")
    kraje = ("Středočeský kraj", "Plzeňský kraj", "Jihomoravský kraj", "Pardubický kraj")
    places = SimpleNamespace(
        municipalities=[SimpleNamespace(name=n, kraj=k) for n, k in zip(towns, kraje, strict=True)]
    )
    source = startupjobs.SOURCE
    source.prepare(ScrapeQuery(kraje=["Středočeský kraj"]), places)

    def wanted(url):
        return source.wanted(source.lead(url, None), ScrapeQuery(kraje=[]), places)

    for slug, expected in [
        ("1/skladnik-kolin", True),
        ("2/delnik-do-vyroby-plzen", False),
        ("3/operator-vyroby", True),  # no place: kept
        ("4/ridic-kolin-nebo-plzen", True),  # a town near home anywhere in the URL
        ("5/plzen-zavod-sklad", True),  # only the end of the URL is the place
        ("6/brigada-dokoncovaci-prace", True),  # "Práce" is a village, but here a word
    ]:
        assert wanted(f"{AD}/{slug}") is expected, slug

    # The region's name is not the village of the same name elsewhere.
    source.prepare(ScrapeQuery(kraje=["Kraj Vysočina"]), places)
    assert wanted(f"{AD}/7/mzdova-ucetni-vysocina")


@pytest.mark.parametrize(
    ("source", "path", "page", "edits", "has_next"),
    [
        # The pager or the ad count tells whether more pages follow.
        (personalka.SOURCE, "personalka/listing_kraj.html", 1, {"loadMore": "x"}, True),
        (personalka.SOURCE, "personalka/listing_kraj.html", 200, {"loadMore": "x"}, False),
        (inwork.SOURCE, "inwork/listing_kraj.html", 1, {"pagination__next": "x"}, False),
        (jobs_cz.SOURCE, "jobs_cz/listing_kraj.html", 1, {"page=": "p="}, False),
        (bazos.SOURCE, "bazos/listing_logistika.html", 2, {"/logistika/40/": "/x/"}, True),
        (dobraprace.SOURCE, "dobraprace/listing_vysocina_12.html", 11, {}, True),
        (dobraprace.SOURCE, "dobraprace/listing_vysocina_12.html", 12, {}, False),
        (easy_prace.SOURCE, "easy_prace/listing_vysocina_75.html", 74, {}, True),
        (easy_prace.SOURCE, "easy_prace/listing_vysocina_75.html", 75, {}, False),
        # Neither (a changed layout): a page of ads is not taken for the last one.
        (
            personalka.SOURCE,
            "personalka/listing_kraj.html",
            1,
            {"loadMore": "x", "Našli jsme": ""},
            True,
        ),
        (
            pracezarohem.SOURCE,
            "pracezarohem/listing_kraj.html",
            1,
            {'"numFound":3094': '"x":3094'},
            True,
        ),
        (inwork.SOURCE, "inwork/listing_kraj.html", 1, {"pagination": "x"}, True),
        (jobs_cz.SOURCE, "jobs_cz/listing_kraj.html", 1, {"page=": "p=", "Pagination": "X"}, True),
        (
            bazos.SOURCE,
            "bazos/listing_logistika.html",
            2,
            {"/logistika/40/": "/x/", "Zobrazeno": ""},
            True,
        ),
        (dobraprace.SOURCE, "dobraprace/listing_vysocina_12.html", 12, {"nabídek": "x"}, True),
        (easy_prace.SOURCE, "easy_prace/listing_vysocina_75.html", 75, {"pocetStr": "x"}, True),
    ],
)
def test_a_missing_pager_is_not_the_last_page(sessions, source, path, page, edits, has_next):
    html = fixture(path).decode("utf-8")
    for old, new in edits.items():
        assert old in html
        html = html.replace(old, new)
    with sessions() as s:
        listing = source.parse_listing(html, page, PlaceResolver(s))
    assert listing.leads and listing.has_next is has_next


def test_dobraprace_region_listing(sessions):
    last = fixture("dobraprace/listing_vysocina_12.html").replace(b" 455 ", b" 55 ")
    routes = {
        "/robots.txt": fixture("dobraprace/robots.txt"),
        "/nabidka-prace/vysocina/": fixture("dobraprace/listing_vysocina.html"),
        "/nabidka-prace/vysocina/?list=2": last,  # 55 ads: two pages
        "*": fixture("dobraprace/detail_1.html"),
    }
    result, requested = run_crawl(sessions, dobraprace.SOURCE, routes, VYSOCINA, max_details=3)
    assert requested[1:3] == [
        f"{dobraprace.BASE}/nabidka-prace/vysocina/",
        f"{dobraprace.BASE}/nabidka-prace/vysocina/?list=2",
    ]
    assert result.complete and result.pages == 2 and result.details == 3
    with sessions() as s:
        assert s.query(JobSource).filter_by(source_id="dobraprace").count() == 55
        card = s.scalar(select(Job).join(JobSource).where(JobSource.external_id == "2018962"))
        assert card.title.startswith("Brigáda co ti nezabere celý den")
        assert card.employer_name == "První novinová společnost a.s."
        assert (card.salary_min, card.salary_max, card.salary_unit) == (3600, 4000, "month")
        assert card.place_text == "Budkov (okres Třebíč)" and card.posted_at is not None
        jihlava = s.scalars(select(Job).where(Job.place_text.like("Třešť (okres Jihlava)%")))
        assert {job.location.okres for job in jihlava} == {"Jihlava"}  # the district


def test_dobraprace_and_startupjobs_detail_pages(sessions):
    with sessions() as session:
        places = PlaceResolver(session)
        for source, path in [
            (dobraprace.SOURCE, "dobraprace/detail_1.html"),
            (startupjobs.SOURCE, "startupjobs/detail_1.html"),
        ]:
            url = f"{dobraprace.BASE}/123-x.html" if source is dobraprace.SOURCE else f"{AD}/123/x"
            lead = Lead("123", url, url)
            job = source.parse_detail(fixture(path).decode("utf-8"), lead, places)
            assert job is not None and job.title and job.description and job.employer_name


def test_changed_ads_are_read_again(sessions):
    def sitemap(day):
        return (
            "<urlset><url><loc>https://www.startupjobs.cz/nabidka/1/a</loc>"
            f"<lastmod>2026-10-{day:02d}</lastmod></url></urlset>"
        ).encode()

    routes = {"/sitemap/offers.xml": sitemap(1), "*": fixture("startupjobs/detail_1.html")}
    result, requested = run_crawl(sessions, startupjobs.SOURCE, routes)
    assert result.details == 1
    _, requested = run_crawl(sessions, startupjobs.SOURCE, routes)
    assert not any("/nabidka/" in u for u in requested)  # same lastmod: not read again
    routes["/sitemap/offers.xml"] = sitemap(30)  # the portal changed the ad
    _, requested = run_crawl(sessions, startupjobs.SOURCE, routes)
    assert any("/nabidka/1/a" in u for u in requested)

    # Removed from the portal (another ad remains; an empty sitemap expires nothing).
    with sessions() as s:  # Windows clocks tick coarsely: the last sighting lies clearly before
        for row in s.scalars(select(JobSource)):
            row.last_seen_at -= timedelta(minutes=1)
        s.commit()
    routes["/sitemap/offers.xml"] = sitemap(30).replace(b"/nabidka/1/a", b"/nabidka/2/b")
    run_crawl(sessions, startupjobs.SOURCE, routes)
    with sessions() as s:
        source = s.scalar(select(JobSource).where(JobSource.external_id == "1"))
        assert source.status == "expired" and source.job.status == "expired"


def test_new_ads_are_read_before_changed_ones(sessions):
    near = "https://www.startupjobs.cz/nabidka/1/skladnik-kolin"
    far = "https://www.startupjobs.cz/nabidka/2/skladnik"

    def sitemap(*ads):
        urls = "".join(
            f"<url><loc>{url}</loc><lastmod>2026-10-{day:02d}</lastmod></url>" for url, day in ads
        )
        return f"<urlset>{urls}</urlset>".encode()

    routes = {"/sitemap/offers.xml": sitemap((near, 1)), "*": fixture("startupjobs/detail_1.html")}
    run_crawl(sessions, startupjobs.SOURCE, routes)
    routes["/sitemap/offers.xml"] = sitemap((near, 30), (far, 1))
    _, requested = run_crawl(sessions, startupjobs.SOURCE, routes, max_details=1)
    assert [u for u in requested if "/nabidka/" in u] == [far]
    _, requested = run_crawl(sessions, startupjobs.SOURCE, routes, max_details=1)
    assert [u for u in requested if "/nabidka/" in u] == [near]


def test_pracezarohem_cards_and_own_detail_pages(sessions):
    from adawork.scraper.sources import pracezarohem

    routes = {
        "/robots.txt": fixture("pracezarohem/robots.txt"),
        "/nabidky/stredocesky": fixture("pracezarohem/listing_kraj.html"),
        "/dl/jd/PZR-378705": fixture("pracezarohem/detail_1.html"),
        "/dl/jd/PZR-378674": fixture("pracezarohem/detail_2.html"),
    }
    with sessions() as s:
        page = pracezarohem.SOURCE.parse_listing(
            routes["/nabidky/stredocesky"].decode(), 1, PlaceResolver(s)
        )
    assert len(page.leads) == 50 and page.has_next  # 3,094 ads in the region
    first = page.leads[0].card
    assert (first.title, first.employer_ico, first.salary_min, first.salary_max) == (
        "Předák", "10894209", 45000, 55000,
    )  # fmt: skip
    assert first.location.lat == pytest.approx(50.126, abs=1e-3)
    assert first.extra["occupations"][0] == "Skladník"
    # Ads re-listed from other portals are kept as cards, without reading their page.
    assert all(lead.fetch_url for lead in page.leads if lead.external_id.startswith("PZR"))
    assert not any(lead.fetch_url for lead in page.leads if lead.external_id.startswith("G2"))

    query = ScrapeQuery(kraje=["Středočeský kraj"], home="Kolín")
    result, requested = run_crawl(sessions, pracezarohem.SOURCE, routes, query, max_details=2)
    assert result.details == 2
    with sessions() as s:
        job = s.scalar(select(Job).where(Job.title == "Předák"))
        assert job.contract_types == ["full_time"] and job.education == "vocational"
        assert job.location.precision == "street" and "SAP" in job.description
        assert s.query(JobSource).filter_by(source_id="pracezarohem").count() == 50


def test_jobs_cz_cards_and_detail_pages(sessions):
    from adawork.scraper.crawl import Lead
    from adawork.scraper.sources import jobs_cz

    with sessions() as s:
        places = PlaceResolver(s)
        page = jobs_cz.SOURCE.parse_listing(
            fixture("jobs_cz/listing_kraj.html").decode(), 1, places
        )
        assert len(page.leads) == 30 and page.has_next
        cards = {lead.card.title: lead.card for lead in page.leads}
        seller = cards["Prodejce optického internetu (m/ž)"]
        assert seller.employer_name == "O2 Czech Republic a.s."
        assert seller.location.municipality == "Kolín"  # "Kolín + 8 dalších lokalit"
        assert (seller.salary_min, seller.salary_max) == (80000, 120000)
        assert all(lead.fetch_url.endswith(f"/rpd/{lead.external_id}/") for lead in page.leads)

        job = jobs_cz.SOURCE.parse_detail(
            fixture("jobs_cz/detail_1.html").decode(), Lead("1", "u", "u"), places
        )
    assert job.title == "SKLADNÍK/SKLADNICE" and job.employer_name == "GTL, spol. s r.o."
    assert (job.salary_min, job.salary_max) == (31000, 45000)
    assert job.contract_types == ["full_time"] and job.education == "basic"
    assert job.place_text == "Řecká 1107, Nehvizdy"
    assert job.extra["occupations"] == ["Skladník/manipulant"]
    assert job.description.startswith("SKLADNÍK / SKLADNICE – NEHVIZDY")
