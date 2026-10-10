import time
from pathlib import Path

import httpx
import pytest
from selectolax.lexbor import LexborHTMLParser
from sqlalchemy import func, select

from adawork.config import Profile
from adawork.db.models import Job, JobSource, Location, Run, SourceState
from adawork.db.session import make_engine, make_sessionmaker, migrate
from adawork.net.polite import PoliteFetcher
from adawork.scraper.crawl import has_page_link
from adawork.scraper.runner import ScrapeRunner
from adawork.scraper.sources import jenprace, prace_cz, profesia

SOURCES = Path(__file__).parents[1] / "fixtures" / "sources"
LAST_PAGE = """<html><body>
<article id="advert-extra-1"><h2 data-testid="job-card-title">
<a data-testid="advert-link" href="/nabidka/extra-1/?rps=1">Skladník</a></h2>
<ul><li><span class="accessibility-hidden">Lokalita:</span><span>Praha</span></li>
<li><span class="accessibility-hidden">Název firmy:</span><span>ACME s.r.o.</span></li></ul>
</article></body></html>"""
OTHER_LAST_PAGE = LAST_PAGE.replace("extra-1", "extra-2")
PRAHA = Profile(home_address="Praha", max_minutes=10, travel_mode="walk")


class Site:
    """prace.cz as a mock: listing page 1 from the fixture, a small page 2, detail pages."""

    def __init__(self) -> None:
        self.first_page = (SOURCES / "prace_cz/listing_praha.html").read_bytes()
        self.last_page = LAST_PAGE
        self.status = 200
        self.detail_status = 200
        self.requests: list[str] = []
        self.answers: dict[str, httpx.Response] = {}  # by URL: other regions, redirects

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        if str(request.url) in self.answers:
            return self.answers[str(request.url)]
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, content=(SOURCES / "prace_cz/robots.txt").read_bytes())
        if self.status != 200:
            return httpx.Response(self.status)
        if path == "/nabidky/praha/":
            if request.url.params.get("page") == "2":
                return httpx.Response(200, text=self.last_page)
            return httpx.Response(200, content=self.first_page)
        if path.startswith("/nabidka/") or "/nabidka/" in path:
            if self.detail_status != 200:
                return httpx.Response(self.detail_status)
            return httpx.Response(200, content=(SOURCES / "prace_cz/detail_1.html").read_bytes())
        return httpx.Response(404)

    def details(self) -> int:
        return sum("/nabidka/" in url for url in self.requests)


@pytest.fixture
def sessions(tmp_path, municipalities):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    sessions = make_sessionmaker(engine)
    with sessions() as s:
        municipalities(s)
    yield sessions
    engine.dispose()


def run(
    sessions, tmp_path, site: Site, max_details: int = 5, home: Profile = PRAHA
) -> ScrapeRunner:
    transport = httpx.MockTransport(site)
    runner = ScrapeRunner(
        sessions,
        tmp_path / "cache",
        lambda: httpx.Client(transport=transport),
        geocode=False,
        profile=lambda: home,
        sources=[prace_cz.SOURCE],
        fetcher_factory=lambda client: PoliteFetcher(client, delay=0, sleep=lambda s: None),
        max_details=max_details,
    )
    site.requests.clear()
    runner.start()
    runner.wait(60)
    return runner


def test_crawl_reads_cards_then_details_then_expires(sessions, tmp_path):
    site = Site()
    runner = run(sessions, tmp_path, site)
    p = runner.progress
    assert p.phase == "done", (p.message, p.errors)
    assert p.results["prace_cz"]["new"] == 41  # 40 cards on page 1, one on page 2
    assert site.details() == 5  # the per-run cap
    assert not any("jobs.cz" in url for url in site.requests)  # career pages: card only
    with sessions() as s:
        sources = s.scalars(select(JobSource)).all()
        assert sum(src.detail_at is not None for src in sources) == 5
        mbank = s.scalar(select(Job).join(JobSource).where(JobSource.url.like("%mbank.jobs.cz%")))
        assert mbank.employer_name == "mBank" and mbank.location.key == "part:554782:karlin"
        assert mbank.sources[0].url == "https://mbank.jobs.cz/pd/2001441216"
        paid = s.scalar(select(Job).where(Job.employer_name == "LAMPS, a.s."))  # card only
        assert (paid.salary_min, paid.salary_max, paid.salary_unit) == (35000, 40000, "month")
        detailed = s.scalar(select(Job).join(JobSource).where(JobSource.detail_at.is_not(None)))
        assert detailed.description.startswith("Hledáme")
        assert detailed.location.key == "addr:554782:sulicka:"  # from the detail page
        assert s.scalar(select(Run.status)) == "ok"

    # Next run: nothing new, the next five card-only jobs get their details.
    runner = run(sessions, tmp_path, site)
    assert runner.progress.results["prace_cz"] | {"merged": 0} == {
        "new": 0,
        "merged": 0,
        "updated": 5,
        "removed": 0,
    }
    assert site.details() == 5
    with sessions() as s:
        assert s.scalar(select(func.count()).where(JobSource.detail_at.is_not(None))) == 10

    # The ad on page 2 is gone, another one took its place.
    site.last_page = OTHER_LAST_PAGE
    runner = run(sessions, tmp_path, site, max_details=0)
    assert runner.progress.results["prace_cz"]["removed"] == 1
    with sessions() as s:
        gone = s.scalar(select(Job).join(JobSource).where(JobSource.external_id == "extra-1"))
        assert gone.status == "expired"


def test_empty_pass_and_other_regions_do_not_expire(sessions, tmp_path):
    site = Site()
    run(sessions, tmp_path, site, max_details=0)
    with sessions() as s:
        job = s.scalar(select(Job).join(JobSource).where(JobSource.external_id == "extra-1"))
        job.location = Location(key="obec:554791", precision="municipality", kraj="Plzeňský kraj")
        s.commit()

    # Only Praha was crawled, so the job placed in Plzeň is not known to be gone.
    site.last_page = OTHER_LAST_PAGE
    runner = run(sessions, tmp_path, site, max_details=0)
    assert runner.progress.results["prace_cz"]["removed"] == 0

    # A listing without a single ad (changed layout) proves nothing either.
    site.first_page = b"<html><body></body></html>"
    runner = run(sessions, tmp_path, site, max_details=0)
    assert runner.progress.results["prace_cz"]["removed"] == 0
    with sessions() as s:
        assert s.scalar(select(func.count()).where(Job.status == "expired")) == 0
        assert s.scalar(select(Run.status).order_by(Run.id.desc()).limit(1)) == "partial"


def test_a_listing_that_breaks_off_expires_nothing(sessions, tmp_path):
    site = Site()
    run(sessions, tmp_path, site, max_details=0)

    # Page 1 promised a page 2, which came empty (an error page) or redirected to page 1.
    site.last_page = "<html><body></body></html>"
    run(sessions, tmp_path, site, max_details=0)
    page_2 = "https://www.prace.cz/nabidky/praha/?page=2"
    site.answers[page_2] = httpx.Response(302, headers={"Location": "/nabidky/praha/"})
    run(sessions, tmp_path, site, max_details=0)
    assert page_2 in site.requests and site.requests[-1] == "https://www.prace.cz/nabidky/praha/"
    with sessions() as s:
        assert s.scalar(select(func.count()).where(Job.status == "expired")) == 0
        assert s.scalars(select(Run.status).order_by(Run.id)).all() == ["ok", "partial", "partial"]


def test_an_empty_region_expires_nothing_there(sessions, tmp_path):
    site = Site()
    region = "https://www.prace.cz/nabidky/stredocesky-kraj/"
    site.answers[region] = httpx.Response(200, text=OTHER_LAST_PAGE.replace("Praha", "Kolín"))
    near_praha = Profile(home_address="Praha", max_minutes=30, travel_mode="car")
    run(sessions, tmp_path, site, max_details=0, home=near_praha)  # and Středočeský kraj
    assert region in site.requests

    # Středočeský kraj lists nothing; Praha, read to the end, lost one ad.
    site.answers[region] = httpx.Response(200, text="<html><body></body></html>")
    site.last_page = LAST_PAGE.replace("extra-1", "extra-3")
    runner = run(sessions, tmp_path, site, max_details=0, home=near_praha)
    assert runner.progress.results["prace_cz"]["removed"] == 1
    with sessions() as s:
        status = dict(
            s.execute(
                select(JobSource.external_id, JobSource.status).where(
                    JobSource.external_id.in_(["extra-1", "extra-2"])
                )
            ).all()
        )
        assert status == {"extra-1": "expired", "extra-2": "active"}
        assert s.scalar(select(Run.status).order_by(Run.id.desc()).limit(1)) == "partial"


def test_failing_detail_pages_keep_the_cards(sessions, tmp_path):
    site = Site()
    site.detail_status = 500
    runner = run(sessions, tmp_path, site)
    assert runner.progress.phase == "done", runner.progress.errors
    assert runner.progress.results["prace_cz"]["new"] == 41
    with sessions() as s:
        assert s.scalar(select(Run.status)) == "ok"


def test_next_page_link_is_exact():
    tree = LexborHTMLParser('<a href="/nabidky/?page=20">20</a><a href="?x=1&amp;page=3">3</a>')
    assert not has_page_link(tree, "page", 2)
    assert has_page_link(tree, "page", 3) and has_page_link(tree, "page", 20)


def test_blocked_source_is_paused(sessions, tmp_path):
    site = Site()
    site.status = 429
    runner = run(sessions, tmp_path, site)
    assert runner.progress.phase == "failed" and "prace_cz" in runner.progress.errors
    with sessions() as s:
        assert s.scalar(select(Run.status)) == "blocked"
        state = s.scalar(select(SourceState).where(SourceState.source_id == "prace_cz"))
        assert "blocked_until" in state.state

    site.status = 200
    run(sessions, tmp_path, site)
    assert site.requests == []  # still paused


def test_stop_ends_a_running_search_at_once(sessions, tmp_path):
    site = Site()
    transport = httpx.MockTransport(site)
    runner = ScrapeRunner(
        sessions,
        tmp_path / "cache",
        lambda: httpx.Client(transport=transport),
        geocode=False,
        profile=lambda: PRAHA,
        sources=[prace_cz.SOURCE],
        fetcher_factory=lambda client: PoliteFetcher(client, delay=30),  # real waits
    )
    runner.start()
    deadline = time.monotonic() + 10
    while not site.requests and time.monotonic() < deadline:
        time.sleep(0.05)
    started = time.monotonic()
    assert runner.stop(timeout=10)  # the next listing page was 30 s away
    assert time.monotonic() - started < 5
    assert not runner.progress.running


def listing(name: str, source, page: int = 1):
    html = (SOURCES / name).read_text(encoding="utf-8")
    return source.parse_listing(html, page, _NoPlaces())


class _NoPlaces:
    def resolve(self, *args, **kwargs):
        return None


def test_listings_of_every_portal():
    page = listing("prace_cz/listing_praha.html", prace_cz.SOURCE)
    assert len(page.leads) == 40 and page.has_next
    assert sum(lead.fetch_url is None for lead in page.leads) == 14  # jobs.cz career pages

    page = listing("jenprace/listing_praha.html", jenprace.SOURCE)
    assert len(page.leads) == 102 and page.has_next
    first = page.leads[0]
    assert first.external_id == "a60wwn" and first.fetch_url == first.url
    assert first.card.employer_name == "DELTA & PERSONAL s.r.o."
    assert (first.card.salary_min, first.card.salary_max) == (33500, 35000)

    page = listing("profesia/listing.html", profesia.SOURCE)
    assert len(page.leads) == 20 and page.has_next
    first = page.leads[0]
    assert first.url == "https://www.profesia.cz/prace/manpowergroup/O5370601"
    assert first.card.place_text == "Kraj Vysočina" and first.card.salary_min == 35000


def test_links_to_other_sites_are_card_only():
    jen = '<article data-cy="offer-slug-x"><a data-offer-pid="{}" href="{}">x</a></article>'
    pro = '<li class="list-row"><h2><a id="offer{}" href="{}">x</a></h2></li>'
    for source, card in ((jenprace.SOURCE, jen), (profesia.SOURCE, pro)):
        html = card.format("1", "/nabidka/1/x") + card.format("2", "https://firma.cz/kariera/2")
        own, other = source.parse_listing(html, 1, _NoPlaces()).leads
        assert own.fetch_url == own.url and own.url.endswith(".cz/nabidka/1/x")
        assert other.url == "https://firma.cz/kariera/2" and other.fetch_url is None


def test_branded_profesia_detail_uses_the_card():
    page = listing("profesia/listing.html", profesia.SOURCE)
    html = (SOURCES / "profesia/detail_1.html").read_text(encoding="utf-8")
    job = profesia.SOURCE.parse_detail(html, page.leads[0], _NoPlaces())
    assert job.title == "Finanční analytik - i pro absolventy bez praxe"
    assert job.description.startswith("Popis pozice") and "Odpovědět" not in job.description
    assert job.posted_at.date().isoformat() == "2026-10-06"
