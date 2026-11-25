"""The crawl on a generic portal: expiry only after a pass that can tell what is gone,
newest-first listings finished on a later run, jobs matched across sites."""

from datetime import timedelta
from functools import partial
from urllib.parse import urljoin

import httpx
import pytest
from selectolax.lexbor import LexborHTMLParser
from sqlalchemy import func, select

from adawork.config import Profile
from adawork.core import ParsedJob, ScrapeQuery
from adawork.db.models import Job, JobSource
from adawork.db.session import make_engine, make_sessionmaker, migrate
from adawork.geo.places import PlaceResolver
from adawork.net.polite import PoliteFetcher
from adawork.scraper.crawl import CrawlSource, Lead, ListingPage, crawl, has_page_link
from adawork.scraper.dedup import DedupIndex
from adawork.scraper.runner import ScrapeRunner
from adawork.scraper.store import JobStore

PRAHA = Profile(home_address="Praha", max_minutes=10, travel_mode="walk")
DETAIL = "<html><body><p>Hledáme skladníka na ranní směny.</p></body></html>"


class Portal(CrawlSource):
    """A job portal read by region."""

    label = "Portál"

    def __init__(self, id="portal", host="www.portal.test"):
        self.id, self.base = id, f"https://{host}"

    def listing_url(self, region_slug, page):
        return f"{self.base}/{region_slug}/" + (f"?page={page}" if page > 1 else "")

    def parse_listing(self, html, page, places):
        tree = LexborHTMLParser(html)
        leads = []
        for a in tree.css("a.ad"):
            url = urljoin(self.base, a.attributes["href"])
            card = ParsedJob(a.id, a.text(), url=url, place_text="Praha")
            leads.append(Lead(a.id, url, url, card))
        return ListingPage(leads, has_page_link(tree, "page", page + 1))

    def parse_detail(self, html, lead, places):
        text = LexborHTMLParser(html).css_first("p")
        return ParsedJob(lead.external_id, "", description=text.text()) if text else None


class Board(Portal):
    """Classifieds the user switches on: one nationwide list, newest first; an ad unseen
    for three weeks is gone."""

    default_enabled = False
    newest_first = True
    nationwide = True
    expire_after_days = 21

    def listings(self, query):
        return [(None, partial(self.listing_url, "prace"))]


class Dump(Board):
    newest_first = False
    expire_after_days = 3


class Whole(Board):
    newest_first = False
    expire_after_days = None


def card(ad):
    return f'<a class="ad" id="{ad}" href="/nabidka/{ad}">Skladník {ad}</a>'


def page(*cards, more=False):
    return "<html><body>" + "".join(cards) + ('<a href="?page=2">2</a>' if more else "")


class Site:
    """The portal as a mock: answers by URL, detail pages, everything else 404."""

    def __init__(self) -> None:
        self.status = 200
        self.requests: list[str] = []
        self.answers: dict[str, httpx.Response] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if self.status != 200:
            return httpx.Response(self.status)
        if answer := self.answers.get(str(request.url)):
            return httpx.Response(answer.status_code, content=answer.content)
        if request.url.path.startswith("/nabidka/"):
            return httpx.Response(200, text=DETAIL)
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


def run(sessions, tmp_path, site, sources, max_details=5, choices=None) -> ScrapeRunner:
    transport = httpx.MockTransport(site)
    runner = ScrapeRunner(
        sessions,
        tmp_path / "cache",
        lambda: httpx.Client(transport=transport),
        geocode=False,
        profile=lambda: PRAHA,
        sources=sources,
        fetcher_factory=lambda client: PoliteFetcher(client, delay=0, sleep=lambda s: None),
        max_details=max_details,
        source_choices=lambda: choices or {},
    )
    site.requests.clear()
    runner.start()
    runner.wait(60)
    return runner


def crawl_dump(sessions, site: Site, dump: Board) -> set[str]:
    """One crawl alone, after the stored ads went unseen for 5 days; the ads' statuses."""
    fetcher = PoliteFetcher(
        httpx.Client(transport=httpx.MockTransport(site)), delay=0, sleep=lambda s: None
    )
    with sessions() as s:
        for ad in s.scalars(select(JobSource)):
            ad.last_seen_at -= timedelta(days=5)
        s.commit()
        crawl(dump, fetcher, JobStore(s, dump.id), PlaceResolver(s), ScrapeQuery(kraje=[]), 0)
        return set(s.scalars(select(JobSource.status)))


class Signed(Portal):
    """Cards that name the employer: enough to compare with other sites' jobs."""

    def parse_listing(self, html, page, places):
        listing = super().parse_listing(html, page, places)
        for lead in listing.leads:
            assert lead.card is not None
            lead.card.employer_name = "ACME s.r.o."
        return listing


def test_a_listing_broken_off_expires_nothing_unseen(sessions, tmp_path):
    site, dump = Site(), Dump("dump")
    site.answers["https://www.portal.test/prace/"] = httpx.Response(
        200, text=page(*(card(f"b{i}") for i in range(10)), more=True)
    )
    site.answers["https://www.portal.test/prace/?page=2"] = httpx.Response(
        200, text=page(*(card(f"c{i}") for i in range(10)))
    )
    run(sessions, tmp_path, site, [dump], max_details=20, choices={"dump": True})
    site.answers["https://www.portal.test/prace/?page=2"] = httpx.Response(503)
    assert crawl_dump(sessions, site, dump) == {"active"}


def test_an_error_page_expires_nothing_unseen(sessions, tmp_path):
    site, dump = Site(), Dump("dump")
    site.answers["https://www.portal.test/prace/"] = httpx.Response(
        200, text=page(*(card(f"b{i}") for i in range(10)))
    )
    run(sessions, tmp_path, site, [dump], max_details=20, choices={"dump": True})
    for answer in (httpx.Response(200, text="<html>údržba</html>"), httpx.Response(404)):
        site.answers["https://www.portal.test/prace/"] = answer
        assert crawl_dump(sessions, site, dump) == {"active"}
    site.answers["https://www.portal.test/prace/"] = httpx.Response(200, text="<html></html>")
    dump.parse_listing = lambda html, page, places: ListingPage([], False, none_listed=True)
    assert crawl_dump(sessions, site, dump) == {"expired"}  # the site says: none left


def test_a_narrowed_search_still_expires_its_old_ads(sessions, tmp_path):
    site, board = Site(), Board("board")
    site.answers["https://www.portal.test/prace/"] = httpx.Response(
        200, text=page(*(card(f"b{i}") for i in range(20)))
    )
    run(sessions, tmp_path, site, [board], max_details=30, choices={"board": True})
    with sessions() as s:
        for ad in s.scalars(select(JobSource)):
            ad.last_seen_at -= timedelta(days=22)
        s.commit()
    site.answers["https://www.portal.test/prace/"] = httpx.Response(
        200, text=page(card("n1"), card("n2"))
    )
    run(sessions, tmp_path, site, [board], choices={"board": True})
    with sessions() as s:
        statuses = {ad.external_id: ad.status for ad in s.scalars(select(JobSource))}
    assert statuses.pop("n1") == statuses.pop("n2") == "active"
    assert set(statuses.values()) == {"expired"} and len(statuses) == 20


def test_a_listing_with_none_left_expires_its_last_ads(sessions, tmp_path):
    site, whole = Site(), Whole("whole")
    site.answers["https://www.portal.test/prace/"] = httpx.Response(
        200, text=page(*(card(f"b{i}") for i in range(3)))
    )
    run(sessions, tmp_path, site, [whole], max_details=20, choices={"whole": True})
    whole.parse_listing = lambda html, page, places: ListingPage([], False, none_listed=True)
    assert crawl_dump(sessions, site, whole) == {"expired"}


def test_a_refused_run_expires_nothing(sessions, tmp_path):
    site, board = Site(), Board("board")
    site.answers["https://www.portal.test/prace/"] = httpx.Response(
        200, text=page(*(card(f"b{i}") for i in range(10)))
    )
    run(sessions, tmp_path, site, [board], choices={"board": True})
    with sessions() as s:
        for ad in s.scalars(select(JobSource)):
            ad.last_seen_at -= timedelta(days=22)
        s.commit()
    site.answers["https://www.portal.test/prace/"] = httpx.Response(
        200, text=page(card("n1"), more=True)
    )
    site.answers["https://www.portal.test/prace/?page=2"] = httpx.Response(429)
    run(sessions, tmp_path, site, [board], choices={"board": True})
    with sessions() as s:
        assert set(s.scalars(select(JobSource.status))) == {"active"}


@pytest.mark.parametrize("named_first", [True, False])
def test_a_job_whose_page_names_its_employer_is_found_on_another_site(sessions, named_first):
    class Named(Portal):
        def parse_detail(self, html, lead, places):
            job = super().parse_detail(html, lead, places)
            if job is not None:
                job.employer_name = "ACME s.r.o."
            return job

    site = Site()
    site.answers["https://www.portal.test/praha/"] = httpx.Response(200, text=page(card("a1")))
    same = card("a1").replace('id="a1" href="/nabidka/a1"', 'id="x9" href="/nabidka/x9"')
    site.answers["https://www.other.test/praha/"] = httpx.Response(200, text=page(same))
    fetcher = PoliteFetcher(
        httpx.Client(transport=httpx.MockTransport(site)), delay=0, sleep=lambda s: None
    )
    query = ScrapeQuery(kraje=["Hlavní město Praha"])
    with sessions() as s:
        places, dedup = PlaceResolver(s), DedupIndex.load(s)
        sources = [(Named(), 5), (Signed("other", "www.other.test"), 0)]
        for source, details in sources if named_first else sources[::-1]:
            store = JobStore(s, source.id, dedup=dedup)
            crawl(source, fetcher, store, places, query, details)
        assert s.scalar(select(func.count()).select_from(Job)) == 1
        assert s.scalar(select(func.count()).select_from(JobSource)) == 2


class Unlisted(Board):
    """A board whose listing gives links only: what a job is needs its page."""

    def parse_listing(self, html, page, places):
        listing = super().parse_listing(html, page, places)
        for lead in listing.leads:
            lead.card = None
        return listing


def test_a_walk_stopped_by_the_detail_limit_goes_on_next_time(sessions, tmp_path):
    site, board = Site(), Unlisted("board")
    site.answers["https://www.portal.test/prace/"] = httpx.Response(
        200, text=page(*(card(f"b{i}") for i in range(10)), more=True)
    )
    site.answers["https://www.portal.test/prace/?page=2"] = httpx.Response(
        200, text=page(*(card(f"c{i}") for i in range(10)))
    )
    for _ in range(3):
        run(sessions, tmp_path, site, [board], max_details=5, choices={"board": True})
    assert "https://www.portal.test/prace/?page=2" in site.requests


def test_a_newest_first_listing_cut_short_is_read_on_next_time(sessions, tmp_path):
    site, board = Site(), Board("board")
    site.answers["https://www.portal.test/prace/"] = httpx.Response(
        200, text=page(*(card(f"b{i}") for i in range(10)), more=True)
    )
    older = httpx.Response(200, text=page(*(card(f"c{i}") for i in range(10))))
    site.answers["https://www.portal.test/prace/?page=2"] = httpx.Response(503)
    run(sessions, tmp_path, site, [board], choices={"board": True})
    site.answers["https://www.portal.test/prace/?page=2"] = older
    # Page 1 holds nothing new now, yet the walk goes on to the older ads it never read.
    runner = run(sessions, tmp_path, site, [board], choices={"board": True})
    assert runner.progress.results["board"]["new"] == 10
    assert sum("/prace/" in u for u in site.requests) == 2
    run(sessions, tmp_path, site, [board], choices={"board": True})
    assert sum("/prace/" in u for u in site.requests) == 1  # read to the end: newest only
