"""hitprace.cz: an aggregator (IDs `ru…`, `GIG…`, `GR…`, `HIT…`, hashes) re-listing ads of
other portals, so most of its jobs merge with ones we already have. JSON-LD on detail pages.

Read through its search around the user's home town with the nearest radius the form offers
(at most 40 km), one search per kind of work; without a home, around each region's capital.
Searches are ordered by paid placement, not by date, and a big city has thousands of hits, so
only the first pages are read and a job counts as gone when unseen for two weeks.
"""

from functools import partial
from urllib.parse import urlencode, urljoin

from selectolax.lexbor import LexborHTMLParser

from adawork.core import ParsedJob, ScrapeQuery
from adawork.geo.places import PlaceResolver
from adawork.normalize.text import clean_spaces, clean_title
from adawork.scraper.crawl import (
    CrawlSource,
    Lead,
    Listing,
    ListingPage,
    clean_url,
    has_page_link,
    node_value,
    set_salary,
)
from adawork.scraper.structured import find_job_postings, posting_to_job

SOURCE_ID = "hitprace"
BASE = "https://www.hitprace.cz"
RADII_KM = (3, 5, 10, 15, 20, 25, 30, 35, 40)  # the search form's options; 3 means "+0 km"
KRAJ_TOWNS = {
    "Hlavní město Praha": "Praha",
    "Středočeský kraj": "Kladno",
    "Jihočeský kraj": "České Budějovice",
    "Plzeňský kraj": "Plzeň",
    "Karlovarský kraj": "Karlovy Vary",
    "Ústecký kraj": "Ústí nad Labem",
    "Liberecký kraj": "Liberec",
    "Královéhradecký kraj": "Hradec Králové",
    "Pardubický kraj": "Pardubice",
    "Kraj Vysočina": "Jihlava",
    "Jihomoravský kraj": "Brno",
    "Olomoucký kraj": "Olomouc",
    "Moravskoslezský kraj": "Ostrava",
    "Zlínský kraj": "Zlín",
}


def radius(km: float) -> int:
    return next((r for r in RADII_KM if r >= km), RADII_KM[-1])


class HitPrace(CrawlSource):
    id = SOURCE_ID
    label = "HitPráce.cz"
    max_pages = 100  # 10 cards each
    expire_after_days = 14

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        towns = [query.home] if query.home else [KRAJ_TOWNS[k] for k in query.kraje]
        return [
            (None, partial(self.search_url, town, keywords, radius(query.radius_km)))
            for town in towns
            for keywords in (query.keywords or [""])
        ]

    @staticmethod
    def search_url(town: str, keywords: str, km: int, page: int) -> str:
        params = {
            "lokalita_textbox": town,
            "obor_textbox": keywords,
            "form_geolocation_okruh": km,
        }
        if page > 1:
            params["strana"] = page
        return f"{BASE}/prace/?{urlencode(params)}"

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = []
        for card in tree.css("div.jobs-item[data-detail-url]"):
            path = card.attributes.get("data-detail-url") or ""
            # Only the first page's cards carry data-job-id; the ID also ends the URL.
            external_id = card.attributes.get("data-job-id") or path.rstrip("/").rsplit("-", 1)[-1]
            if not path.startswith("/prace-detail/") or not external_id:
                continue
            url = clean_url(urljoin(BASE, path))
            place = node_value(card.css_first("a[data-click-target='location']"))
            job = ParsedJob(
                external_id=external_id,
                title=clean_title(node_value(card.css_first("a[data-click-target='title']"))),
                url=url,
                employer_name=_employer(card),
                place_text=place,
                location=places.resolve(place) if place else None,
            )
            set_salary(job, node_value(card.css_first(".job-pay")))
            leads.append(Lead(external_id, url, url, job))
        return ListingPage(leads, has_page_link(tree, "strana", page + 1))

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedJob | None:
        tree = LexborHTMLParser(html)
        postings = find_job_postings(tree)
        if not postings:
            return None
        posting = postings[0]
        _fix_locality(posting)
        job = posting_to_job(posting, lead.external_id, lead.url, places)
        job.title = clean_title(job.title)
        # validThrough is always a day or two after the page was built, not a real deadline.
        job.expires_at = None
        if job.salary_min is None and job.salary_max is None:
            set_salary(job, node_value(tree.css_first(".hp-ds-detail-meta__pay")))
        return job if job.title else None


def _employer(card) -> str | None:
    """The text after the 🏢 icon: "📍 Brno · 🏢 HD pro, s.r.o. ✓ Ověřený zaměstnavatel"."""
    meta = card.css_first(".hp-ds-job__meta")
    if meta is None or meta.css_first(".hp-ds-ico--org") is None:
        return None
    for node in meta.css(".hp-ds-ico, a, .job-meta-sep, .job-verified-inline"):
        node.decompose()
    return clean_spaces(meta.text()) or None


def _fix_locality(posting: dict) -> None:
    """ "Jihomoravský kraj, Brno": the town is the last part."""
    locations = posting.get("jobLocation")
    for place in locations if isinstance(locations, list) else [locations]:
        address = place.get("address") if isinstance(place, dict) else None
        locality = address.get("addressLocality") if isinstance(address, dict) else None
        if isinstance(address, dict) and isinstance(locality, str) and "," in locality:
            address["addressLocality"] = locality.rsplit(",", 1)[-1].strip()


SOURCE = HitPrace()
