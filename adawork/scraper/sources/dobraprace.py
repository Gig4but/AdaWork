"""dobraprace.cz: ~7.7k ads. Region listings with 40 cards per page (title, place, employer,
pay, date), JSON-LD on detail pages (its description is only a summary: the ad text comes
from the page).

jobsik.cz is the same operator with the same ads, so it is not read separately.
"""

import re

from selectolax.lexbor import LexborHTMLParser

from adawork.core import ParsedJob, ParsedLocation
from adawork.geo.places import PlaceResolver
from adawork.normalize.text import card_date, fold, node_text
from adawork.scraper.crawl import (
    KRAJ_SLUGS,
    CrawlSource,
    Lead,
    ListingPage,
    node_value,
    set_salary,
)
from adawork.scraper.structured import find_job_postings, posting_to_job

SOURCE_ID = "dobraprace"
BASE = "https://www.dobraprace.cz"
PAGE_SIZE = 40
SLUGS = {**KRAJ_SLUGS, "Středočeský kraj": "stredni-cechy", "Kraj Vysočina": "vysocina"}
AD_URL = re.compile(r"^https://www\.dobraprace\.cz/(\d+)-[^/]+\.html$")
# "ID: 1771305 / Aktualizováno / dnes"
PAGE_HEADER = re.compile(r"^ID: \d+\s*\nAktualizováno\s*\n[^\n]*\n")
_IN_DISTRICT = re.compile(r"^(?P<name>.*?)\s*\(okres (?P<okres>[^)]+)\)$")
_FOUND = re.compile(r"(\d[\d\s]*?)\s+nabídek práce")  # "455  nabídek práce"


def card_place(text: str | None, places: PlaceResolver) -> ParsedLocation | None:
    """ "Budkov (okres Třebíč)", "Vysočina (okres Jihlava)" (somewhere in the district),
    "Jihlava", "Jihlava, Havlíčkův Brod, Pelhřimov, …" (several: the first)."""
    m = _IN_DISTRICT.match(text or "")
    name, okres = (m["name"], m["okres"]) if m else (text or "", None)
    name = name.split(",")[0].strip()
    loc = places.resolve(name, district=okres) if name else None
    # A namesake village (there is one called Vysočina) is worse than the district itself.
    if loc and (okres is None or fold(loc.okres or "") == fold(okres)):
        return loc
    return places.resolve(f"okres {okres}") if okres else loc


class DobraPrace(CrawlSource):
    id = SOURCE_ID
    label = "dobraprace.cz"
    region_slugs = SLUGS

    def listing_url(self, region_slug: str, page: int) -> str:
        return f"{BASE}/nabidka-prace/{region_slug}/" + (f"?list={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = []
        for card in tree.css("div.jobOffer[id^='job']"):
            link = card.css_first("a.joboffer__title")
            m = AD_URL.match(link.attributes.get("href") or "") if link is not None else None
            if m is None:
                continue
            place = node_value(card.css_first(".joboffer__region"))
            job = ParsedJob(
                external_id=m[1],
                title=node_value(link) or "",
                url=m[0],
                employer_name=node_value(card.css_first(".joboffer__zadavatel")),
                place_text=place,
                location=card_place(place, places),
                posted_at=card_date(node_value(card.css_first(".joboffer__datum"))),
            )
            set_salary(job, node_value(card.css_first(".joboffer__wage-wrap")))
            leads.append(Lead(m[1], m[0], m[0], job))
        more = tree.css_first("a[rel=next]") is not None
        # No pager link: the last page, or a changed layout. The ad count tells which; without
        # it, reading on until an empty page beats taking every page after this as gone.
        if not more:
            found = _FOUND.search(html)
            more = page * PAGE_SIZE < int(re.sub(r"\D", "", found[1])) if found else bool(leads)
        return ListingPage(leads, has_next=more)

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedJob | None:
        postings = find_job_postings(html)
        if not postings:
            return None
        job = posting_to_job(postings[0], lead.external_id, lead.url, places)
        # The JSON-LD description is only a one-line summary; the page has the whole ad.
        body = LexborHTMLParser(html).css_first(".job-detail-text")
        text = PAGE_HEADER.sub("", node_text(body)) if body is not None else ""
        if len(text) > len(job.description):
            job.description = text
        return job


SOURCE = DobraPrace()
