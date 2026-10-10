"""Portals read page by page: region listings → leads → detail pages (PLAN §4.1).

A source adapter only knows its own pages: listing URLs, how to read a listing card and a
detail page. `crawl` does the rest the same way for every portal:
- walks the listings of the regions in scope until the last page;
- jobs it already knows are only marked as still offered;
- reads detail pages of new jobs (at most `max_details` per run, the rest stay card-only
  and are completed on later runs);
- expires jobs the source no longer lists, but only in the regions whose listing it read to
  the end.
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, fields
from urllib.parse import urlsplit, urlunsplit

import httpx
from selectolax.lexbor import LexborHTMLParser, LexborNode

from adawork.core import ParsedJob, SourceKind
from adawork.geo.places import PlaceResolver
from adawork.net.polite import PoliteFetcher, SourceBlocked
from adawork.normalize.text import clean_spaces, parse_salary
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


@dataclass
class Lead:
    external_id: str
    url: str  # the page shown to the user
    fetch_url: str | None = None  # detail page to read; None = the card is all we get
    card: ParsedJob | None = None


@dataclass
class ListingPage:
    leads: list[Lead]
    has_next: bool


class CrawlSource:
    id: str
    label: str
    max_pages: int = 300  # per region; prace.cz Praha has ~150
    default_enabled: bool = True
    kind: SourceKind = "portal"
    needs_consent = False

    def listing_url(self, region_slug: str, page: int) -> str:
        raise NotImplementedError

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        raise NotImplementedError

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedJob | None:
        raise NotImplementedError


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
    for f in fields(ParsedJob):
        if f.name in ("external_id", "url", "salary_currency"):
            continue
        if getattr(job, f.name) in (None, "", [], {}) and getattr(card, f.name) not in (
            None,
            "",
            [],
            {},
        ):
            setattr(job, f.name, getattr(card, f.name))
            if f.name == "salary_min" or f.name == "salary_max":
                job.salary_currency = card.salary_currency
    return job


def set_salary(job: ParsedJob, text: str | None) -> None:
    """Salary fields from a portal's pay text ("28 000 – 35 000 Kč/měs.")."""
    salary = parse_salary(text)
    if salary:
        job.salary_min, job.salary_max = salary.min, salary.max
        job.salary_unit, job.salary_currency = salary.unit, salary.currency


def crawl(
    source: CrawlSource,
    fetcher: PoliteFetcher,
    store: JobStore,
    places: PlaceResolver,
    kraje: list[str],
    max_details: int = MAX_DETAILS,
    progress: Callable[[str, int], None] = lambda phase, n: None,
) -> CrawlResult:
    result = CrawlResult()
    seen: set[str] = set()
    new: list[Lead] = []
    incomplete: list[Lead] = []  # known, but only from a card so far
    done_kraje: list[str] = []  # regions whose listing was read to the end
    try:
        whole = True  # every listing was read to the end
        for kraj in kraje:
            slug = KRAJ_SLUGS[kraj]
            complete = True
            page = 1
            while True:
                if page > source.max_pages:
                    complete = False
                    break
                resp = fetcher.get(source.listing_url(slug, page))
                if resp is None:
                    complete = complete and page > 1  # robots.txt or gone
                    break
                if resp.history and other_page(str(resp.history[0].url), str(resp.url)):
                    complete = False  # the listing ends before its last page
                    break
                result.pages += 1
                listing = source.parse_listing(resp.text, page, places)
                for lead in listing.leads:
                    if lead.external_id in seen:
                        continue
                    seen.add(lead.external_id)
                    if store.knows(lead.external_id):
                        store.touch(lead.external_id)
                        if lead.fetch_url and not store.has_detail(lead.external_id):
                            incomplete.append(lead)
                    else:
                        new.append(lead)
                progress("listing", len(seen))
                if not listing.leads:
                    complete = False  # an error page or a new layout, not an empty region
                if not listing.has_next or not listing.leads:
                    break
                page += 1
            whole = whole and complete
            if complete:
                done_kraje.append(kraj)
        store.commit()

        queue = [lead for lead in new + incomplete if lead.fetch_url][:max_details]
        failed_in_row = 0
        for lead in queue:
            url = lead.fetch_url
            assert url is not None  # queued for its detail page
            try:
                resp = fetcher.get(url)
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
    if result.complete or done_kraje:
        # A region read to the end counts even when another one's listing broke off.
        store.expire_missing(kraje if result.complete else done_kraje)
    return result
