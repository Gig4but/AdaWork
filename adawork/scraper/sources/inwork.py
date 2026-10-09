"""inwork.cz: ~32k ads, 40 per region listing page (`/prace/<kraj>/?start=N`); detail pages in
the shared "Fajn skupina" template (see _inzerce.py).

The sitemap is not used: it lags weeks behind (the newest ads are missing) and still lists
expired ones. Many ads are re-listed Úřad práce ads titled with CZ-ISCO names; dedup merges
them with the MPSV originals. fajn-brigady.cz ads (/brigady/…, same ids) are read by that
source.
"""

import re

from selectolax.lexbor import LexborHTMLParser

from adawork.core import ParsedJob
from adawork.geo.places import PlaceResolver
from adawork.scraper.crawl import (
    KRAJ_SLUGS,
    CrawlSource,
    Lead,
    ListingPage,
    node_value,
)
from adawork.scraper.sources import _inzerce

SOURCE_ID = "inwork"
BASE = "https://www.inwork.cz"
PAGE_SIZE = 40
AD_URL = re.compile(
    r"^https://www\.inwork\.cz/prace/([a-z-]+)/([a-z0-9-]+)/([a-z0-9-]+)/(\d+)-[^/]*/$"
)
SLUGS = {kraj: "vysocina" if slug == "kraj-vysocina" else slug for kraj, slug in KRAJ_SLUGS.items()}
REGIONS = {slug: kraj for kraj, slug in SLUGS.items()}


def url_place(okres: str, obec: str, places: PlaceResolver):
    """The town from the URL ("/kolin/kolin/"), so a card has a place before its page is read."""
    if obec == "cela-cr":
        return None
    return places.resolve(obec.replace("-", " "), district=okres.replace("-", " "))


def region_of(url: str) -> str | None:
    m = AD_URL.match(url)
    return REGIONS.get(m[1]) if m else None


class InWork(CrawlSource):
    id = SOURCE_ID
    label = "inwork.cz"
    region_slugs = SLUGS

    def listing_url(self, region_slug: str, page: int) -> str:
        start = (page - 1) * PAGE_SIZE
        return f"{BASE}/prace/{region_slug}/" + (f"?start={start}" if start else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = []
        for card in tree.css("a.item[id^='i']"):
            url = card.attributes.get("href") or ""
            m = AD_URL.match(url)
            if m is None:
                continue
            ad_id = m[4]
            job = ParsedJob(
                external_id=ad_id,
                title=node_value(card.css_first(".item__title")) or "",
                url=url,
                location=url_place(m[2], m[3], places),
            )
            leads.append(Lead(ad_id, url, url, job))
        # A pager without a next link ends the listing; no pager at all (a changed layout):
        # read on until an empty page.
        more = tree.css_first("a.pagination__next") is not None
        if not more and tree.css_first(".pagination") is None:
            more = bool(leads)
        return ListingPage(leads, has_next=more)

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedJob | None:
        return _inzerce.parse_detail(html, lead, places, region=region_of(lead.url))


SOURCE = InWork()
