"""Portals read page by page: listings → leads → detail pages (PLAN §4.1).

A source adapter only knows its own pages: listing URLs, how to read a listing card and a
detail page. `crawl` does the rest the same way for every portal:
- walks each listing (a region, or a search near home) until the last page;
- jobs it already knows are only marked as still offered;
- reads detail pages of new jobs (at most `max_details` per run, the rest stay card-only
  and are completed on later runs);
- reads detail pages again when they are older than `refresh_after_days` (after new ones);
- expires jobs the source no longer lists: after a complete pass over a region's listing
  (only in that region; everywhere for a nationwide list such as a sitemap), or, for
  searches that show only the newest ads, when unseen for some days.

Sitemap sources (`SitemapSource`) list every ad of the portal with its last change: leads
outside the user's regions are skipped when the URL tells the place, changed ads are read
again, and ads whose URL mentions a place near home are read first.
"""

import logging
import re
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, fields
from datetime import UTC, datetime, timedelta
from functools import partial
from operator import attrgetter
from urllib.parse import urlsplit, urlunsplit

import httpx
from selectolax.lexbor import LexborHTMLParser, LexborNode

from adawork.core import ParsedJob, ScrapeQuery, SourceKind, utcnow
from adawork.geo.places import PlaceResolver
from adawork.net.polite import PoliteFetcher, SourceBlocked
from adawork.normalize.text import clean_spaces, parse_salary, slugify
from adawork.scraper.store import JobStore

log = logging.getLogger(__name__)

# Listing slugs shared by prace.cz, jenprace.cz and profesia.cz.
KRAJ_SLUGS = {
    "Hlavní město Praha": "praha",
    "Středočeský kraj": "stredocesky-kraj",
    "Jihočeský kraj": "jihocesky-kraj",
    "Plzeňský kraj": "plzensky-kraj",
    "Karlovarský kraj": "karlovarsky-kraj",
    "Ústecký kraj": "ustecky-kraj",
    "Liberecký kraj": "liberecky-kraj",
    "Královéhradecký kraj": "kralovehradecky-kraj",
    "Pardubický kraj": "pardubicky-kraj",
    "Kraj Vysočina": "kraj-vysocina",
    "Jihomoravský kraj": "jihomoravsky-kraj",
    "Olomoucký kraj": "olomoucky-kraj",
    "Moravskoslezský kraj": "moravskoslezsky-kraj",
    "Zlínský kraj": "zlinsky-kraj",
}
MAX_DETAILS = 300  # detail pages per source and run (~8 min at the polite pace)
MAX_FAILED_DETAILS = 10  # in a row: the page layout probably changed
REFRESH_DAYS = 30  # a listed ad's detail page is read again after this many days


# (region, page number -> URL); region None: the listing is not one region's.
Listing = tuple[str | None, Callable[[int], str]]


@dataclass
class Lead:
    external_id: str
    url: str  # the page shown to the user
    fetch_url: str | None = None  # detail page to read; None = the card is all we get
    card: ParsedJob | None = None
    modified: datetime | None = None  # sitemap lastmod: read again when newer than our copy
    priority: int = 0  # lower first when detail pages are read


@dataclass
class ListingPage:
    leads: list[Lead]
    has_next: bool


class CrawlSource:
    id: str
    label: str
    max_pages: int = 300  # per listing; prace.cz Praha has ~150
    region_slugs: dict[str, str] = KRAJ_SLUGS  # region -> its name in the listing URLs
    default_enabled: bool = True  # False: only after the user opts in
    obey_robots: bool = True  # False only for a source the user enabled despite robots.txt
    delay: float | None = None  # seconds between requests, when slower than the default
    max_leads: int | None = None  # cards per run
    max_details: int | None = None  # detail pages per run, when lower than the run's limit
    newest_first: bool = False  # stop a listing at the first page with nothing new
    empty_last_page: bool = False  # a "load more" listing ends with an empty page
    expire_after_days: int | None = None  # None: expire after a complete pass instead
    nationwide: bool = False  # the listings cover the whole country, not regions
    kind: SourceKind = "portal"
    needs_consent = False
    # Listed ads whose detail page is older are read again (text and pay change), after the
    # new ones. Not for leads with a lastmod: that tells when they changed.
    refresh_after_days: int | None = REFRESH_DAYS

    def detail_limit(self, max_details: int) -> int:
        return min(max_details, self.max_details or max_details)

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        """The listings to walk. Default: one per region in scope."""
        return [(kraj, partial(self.listing_url, self.region_slugs[kraj])) for kraj in query.kraje]

    def listing_url(self, region_slug: str, page: int) -> str:
        raise NotImplementedError

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        raise NotImplementedError

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedJob | None:
        raise NotImplementedError

    def wanted(self, lead: Lead, query: ScrapeQuery, places: PlaceResolver) -> bool:
        """False for a lead outside the user's regions (when its URL or card tells)."""
        return True

    def prepare(self, query: ScrapeQuery, places: PlaceResolver) -> None:
        """Called once before a run, e.g. to learn which towns are near home."""


SITEMAP_ENTRY = re.compile(
    r"<url>\s*<loc>\s*([^<\s]+)\s*</loc>(?:\s*<lastmod>\s*([^<\s]+)\s*</lastmod>)?", re.S
)


def sitemap_entries(xml: str) -> list[tuple[str, datetime | None]]:
    """(url, lastmod) pairs of a sitemap; lastmod "2026-08-07" or with time and zone."""
    entries = []
    for url, lastmod in SITEMAP_ENTRY.findall(xml):
        when = None
        if lastmod:
            with suppress(ValueError):
                when = datetime.fromisoformat(lastmod.replace("Z", "+00:00"))
            if when is not None:
                when = when.astimezone(UTC).replace(tzinfo=None) if when.tzinfo else when
        entries.append((url.replace("&amp;", "&"), when))
    return entries


def _url_words(url: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", urlsplit(url).path.lower())


# Names of villages that end job titles as plain words ("…-dokoncovaci-prace",
# "…-pracovni-misto", "…-sluzby-cechy", "…-pracovnik-ostrahy-strazny").
TITLE_WORDS = frozenset(
    {"prace", "dobra", "misto", "cechy", "strazny", "strazna", "kridla", "ceska", "cista", "patek"}
)


class SitemapSource(CrawlSource):
    """A portal whose sitemap lists every ad. Subclasses give the sitemap URL and turn an ad
    URL into a lead.

    Ads whose URL names a municipality in the user's regions are read first; ads whose URL
    ends with the name of a town elsewhere are skipped."""

    nationwide = True
    _near: frozenset[str] = frozenset()
    _near_words = 0
    _elsewhere: frozenset[str] = frozenset()

    def prepare(self, query: ScrapeQuery, places: PlaceResolver) -> None:
        kraje = set(query.kraje)
        kraje_of: dict[str, set[str]] = {}
        for m in places.municipalities:
            if m.kraj and len(m.name) > 3:
                kraje_of.setdefault(slugify(m.name), set()).add(m.kraj)
        near = {slug for slug, of in kraje_of.items() if of & kraje}
        # The region's own name: "…-kraj-vysocina", "…-vysocina".
        for slug in map(slugify, kraje):
            near |= {slug, "-".join(w for w in slug.split("-") if w != "kraj")}
        self._near = frozenset(near - TITLE_WORDS)  # "Práce v Brně" names no village
        self._near_words = max((slug.count("-") + 1 for slug in self._near), default=0)
        self._elsewhere = frozenset(kraje_of.keys() - near - TITLE_WORDS)

    def near_home(self, url: str) -> bool:
        words = _url_words(url)
        # Names of several words: "brandys-nad-labem-stara-boleslav" is matched as a whole.
        return any(
            "-".join(words[i : i + n]) in self._near
            for i in range(len(words))
            for n in range(1, self._near_words + 1)
        )

    def wanted(self, lead: Lead, query: ScrapeQuery, places: PlaceResolver) -> bool:
        """Only the end of the URL counts as the place ("…-delnik-do-vyroby-plzen.html"):
        titles are full of words that are also names of villages."""
        words = [w for w in _url_words(lead.url) if not w.isdigit() and w != "html"]
        far = any("-".join(words[-n:]) in self._elsewhere for n in range(1, len(words) + 1))
        return not far or self.near_home(lead.url)

    def listings(self, query: ScrapeQuery) -> list[tuple[str | None, Callable[[int], str]]]:
        return [(None, self.sitemap_url)]

    def sitemap_url(self, page: int) -> str:
        raise NotImplementedError

    def lead(self, url: str, modified: datetime | None) -> Lead | None:
        raise NotImplementedError

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        leads = []
        for url, modified in sitemap_entries(html):
            lead = self.lead(url, modified)
            if lead is not None:
                lead.priority = 0 if self.near_home(url) else 1
                leads.append(lead)
        return ListingPage(leads, has_next=False)


@dataclass
class CrawlResult:
    complete: bool = False
    pages: int = 0
    details: int = 0
    failed_details: int = 0
    blocked: str | None = None


def clean_url(url: str) -> str:
    """Without tracking parameters (?rps=, ?search_id=) and fragments."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def other_page(asked: str, got: str) -> bool:
    """A redirect led away from the page asked for (page 87 to page 1, to the home page), not
    just to another spelling of it (https, www, a trailing slash)."""
    a, b = urlsplit(asked), urlsplit(got)
    return (a.path.rstrip("/"), a.query) != (b.path.rstrip("/"), b.query)


def node_value(node: LexborNode | None) -> str | None:
    """First text of a node; cards often repeat a value for the mobile and desktop layouts."""
    if node is None:
        return None
    for piece in node.text(separator="\n").split("\n"):
        if piece := clean_spaces(piece):
            return piece
    return None


def has_page_link(tree: LexborHTMLParser, param: str, page: int) -> bool:
    link = re.compile(rf"[?&]{re.escape(param)}={page}(?![0-9])")
    return any(
        link.search((a.attributes.get("href") or "").replace("&amp;", "&"))
        for a in tree.css("a[href]")
    )


def merge_card(job: ParsedJob, card: ParsedJob | None) -> ParsedJob:
    """Detail page values win; the card fills what the detail page lacks (e.g. salary)."""
    if card is None:
        return job
    empty = (None, "", [], {})
    for f in fields(ParsedJob):
        if f.name in ("external_id", "url", "salary_currency"):
            continue
        if getattr(job, f.name) in empty and getattr(card, f.name) not in empty:
            setattr(job, f.name, getattr(card, f.name))
            if f.name in ("salary_min", "salary_max"):
                job.salary_currency = card.salary_currency
    return job


def set_salary(job: ParsedJob, text: str | None) -> None:
    """Salary fields from a portal's pay text ("28 000 – 35 000 Kč/měs.")."""
    salary = parse_salary(text)
    if salary:
        job.salary_min, job.salary_max = salary.min, salary.max
        job.salary_unit, job.salary_currency = salary.unit, salary.currency


_PRIORITY = attrgetter("priority")


def _full(source: CrawlSource, seen: set[str]) -> bool:
    return source.max_leads is not None and len(seen) >= source.max_leads


def crawl(
    source: CrawlSource,
    fetcher: PoliteFetcher,
    store: JobStore,
    places: PlaceResolver,
    query: ScrapeQuery,
    max_details: int = MAX_DETAILS,
    progress: Callable[[str, int], None] = lambda phase, n: None,
) -> CrawlResult:
    result = CrawlResult()
    seen: set[str] = set()
    new: list[Lead] = []
    incomplete: list[Lead] = []  # known, but only from a card so far
    changed: list[Lead] = []
    stale: list[Lead] = []  # known, with a detail page read long ago
    refresh_before = (
        utcnow() - timedelta(days=source.refresh_after_days)
        if source.refresh_after_days is not None
        else None
    )
    done_kraje: list[str] = []  # regions whose listing was read to the end
    get = partial(fetcher.get, obey_robots=source.obey_robots, min_delay=source.delay)
    source.prepare(query, places)
    try:
        whole = True  # every listing was read to the end
        for kraj, listing_url in source.listings(query):
            complete = True
            page = 1
            while True:
                if page > source.max_pages or _full(source, seen):
                    complete = False
                    break
                resp = get(listing_url(page))
                if resp is None:
                    complete = complete and page > 1  # robots.txt or gone
                    break
                if resp.history and other_page(str(resp.history[0].url), str(resp.url)):
                    complete = False  # the listing ends before its last page
                    break
                result.pages += 1
                listing = source.parse_listing(resp.text, page, places)
                fresh = 0
                for lead in listing.leads:
                    if lead.external_id in seen or _full(source, seen):
                        continue
                    seen.add(lead.external_id)
                    if store.knows(lead.external_id):
                        store.touch(lead.external_id)
                        if lead.fetch_url and not store.has_detail(lead.external_id):
                            incomplete.append(lead)
                        elif (
                            lead.fetch_url
                            and lead.modified
                            and store.detail_older_than(lead.external_id, lead.modified)
                        ):
                            changed.append(lead)
                        elif (
                            lead.fetch_url
                            and not lead.modified
                            and refresh_before
                            and store.detail_older_than(lead.external_id, refresh_before)
                        ):
                            stale.append(lead)
                    elif not source.wanted(lead, query, places):
                        continue
                    else:
                        new.append(lead)
                        fresh += 1
                progress("listing", len(seen))
                if (
                    not listing.leads
                    and source.expire_after_days is None
                    and not (source.empty_last_page and page > 1)
                ):
                    complete = False  # an error page or a new layout, not an empty region
                if not listing.has_next or not listing.leads:
                    break
                if source.newest_first and not fresh:
                    complete = False  # the rest is known already
                    break
                page += 1
            whole = whole and complete
            if complete and kraj is not None:
                done_kraje.append(kraj)
        store.commit()

        limit = min(max_details, source.max_details or max_details)
        unread = sorted((lead for lead in new + incomplete if lead.fetch_url), key=_PRIORITY)
        stale.sort(key=lambda lead: store.detail_read_at(lead.external_id))
        queue = (unread + sorted(changed, key=_PRIORITY) + stale)[:limit]
        failed_in_row = 0
        for lead in queue:
            url = lead.fetch_url
            assert url is not None  # queued for its detail page
            try:
                resp = get(url)
                job = source.parse_detail(resp.text, lead, places) if resp else None
            except (SourceBlocked, httpx.TransportError):
                raise
            except Exception:
                log.exception("%s: cannot read %s", source.id, url)
                job = None
            if job is None:
                result.failed_details += 1
                failed_in_row += 1
                if failed_in_row >= MAX_FAILED_DETAILS:
                    log.error("%s: %d detail pages in a row unreadable", source.id, failed_in_row)
                    break
                continue
            failed_in_row = 0
            job.external_id, job.url = lead.external_id, lead.url
            store.add(merge_card(job, lead.card), detail=True)
            result.details += 1
            progress("details", result.details)
        result.complete = whole and bool(seen)
    except SourceBlocked as exc:
        result.blocked = str(exc)
        log.warning("%s blocked: %s", source.id, exc)
    finally:
        # New jobs without a detail page yet still appear, from their card.
        for lead in new:
            if lead.card is not None and not store.knows(lead.external_id):
                store.add(lead.card, detail=False)
        store.commit()
    if source.expire_after_days is not None:
        store.expire_unseen(source.expire_after_days)
    elif result.complete:
        store.expire_missing(None if source.nationwide else query.kraje)
    elif done_kraje:  # a region read to the end counts even when another one's broke off
        store.expire_missing(done_kraje)
    return result
