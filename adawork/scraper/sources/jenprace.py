"""jenprace.cz: ~24k ads, 100 per listing page, JSON-LD (with salary) on detail pages.

`/nabidky` without a place redirects by Geo-IP, so listings always name a region.
"""

from urllib.parse import urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser

from adawork.core import ParsedJob
from adawork.geo.places import PlaceResolver
from adawork.normalize.text import contract_types, fold, split_places
from adawork.scraper.crawl import (
    CrawlSource,
    Lead,
    ListingPage,
    clean_url,
    has_page_link,
    node_value,
    set_salary,
)
from adawork.scraper.structured import find_job_postings, posting_to_job

SOURCE_ID = "jenprace"
BASE = "https://www.jenprace.cz"


def card_location(names: list[str], places: PlaceResolver):
    """Cards say "<place>, <district>": "Králíky, Ústí nad Orlicí", "Vinohrady, Praha"."""
    if len(names) == 2:
        place, district = names
        loc = places.resolve(place, district=district)
        if loc is not None and fold(loc.okres or "") == fold(district):
            return loc
        return (
            places.resolve(f"{district}-{place}")  # a part of a city
            or places.resolve(district)
        )
    return next((loc for n in names if (loc := places.resolve(n))), None)


class JenPrace(CrawlSource):
    id = SOURCE_ID
    label = "jenprace.cz"

    def listing_url(self, region_slug: str, page: int) -> str:
        return f"{BASE}/nabidky/{region_slug}" + (f"?page={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = []
        for card in tree.css("article[data-cy^='offer-slug-']"):
            link = card.css_first("a[data-offer-pid]")
            external_id = link.attributes.get("data-offer-pid") if link is not None else None
            if link is None or not external_id:
                continue
            url = clean_url(urljoin(BASE + "/", link.attributes.get("href") or ""))
            names = split_places(node_value(card.css_first("[data-cy='offer-locality']")))
            job = ParsedJob(
                external_id=external_id,
                title=node_value(card.css_first(".offer-link")) or "",
                url=url,
                employer_name=node_value(card.css_first("[data-cy='offer-ownership-company']")),
                place_text=", ".join(names) or None,
                location=card_location(names, places),
                contract_types=contract_types(
                    *(node_value(n) for n in card.css("[data-cy^='offer-label-employment']"))
                ),
            )
            set_salary(job, node_value(card.css_first("[data-cy='offer-label-reward']")))
            own_page = urlsplit(url).netloc in ("jenprace.cz", "www.jenprace.cz")
            leads.append(Lead(external_id, url, url if own_page else None, job))
        return ListingPage(leads, has_page_link(tree, "page", page + 1))

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedJob | None:
        postings = find_job_postings(html)
        if not postings:
            return None
        return posting_to_job(postings[0], lead.external_id, lead.url, places)


SOURCE = JenPrace()
