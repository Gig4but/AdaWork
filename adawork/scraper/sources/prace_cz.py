"""prace.cz (Alma Career): ~19.5k ads. Region listings, JSON-LD on detail pages.

About a third of the listing cards link to company career pages on jobs.cz
(`<company>.jobs.cz/pd/<id>`), which have free-form layouts: those jobs are stored from the
card (title, place, company, contract, salary) with a link to the career page.
"""

from urllib.parse import urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser

from adawork.core import ParsedJob
from adawork.geo.places import PlaceResolver
from adawork.normalize.text import clean_spaces, contract_types, split_places
from adawork.scraper.crawl import (
    CrawlSource,
    Lead,
    ListingPage,
    clean_url,
    has_page_link,
    set_salary,
)
from adawork.scraper.structured import find_job_postings, posting_to_job

SOURCE_ID = "prace_cz"
BASE = "https://www.prace.cz"


def card_job(external_id: str, url: str, title: str, info: dict[str, str], places) -> ParsedJob:
    names = split_places(info.get("Lokalita"))
    location = next((loc for n in names if (loc := places.resolve(n))), None)
    job = ParsedJob(
        external_id=external_id,
        title=title,
        url=url,
        employer_name=info.get("Název firmy"),
        place_text=", ".join(names) or None,
        location=location,
        contract_types=contract_types(info.get("Typ úvazku")),
    )
    set_salary(job, info.get("Plat"))
    return job


class PraceCz(CrawlSource):
    id = SOURCE_ID
    label = "prace.cz"

    def listing_url(self, region_slug: str, page: int) -> str:
        return f"{BASE}/nabidky/{region_slug}/" + (f"?page={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = []
        for card in tree.css("article[id^='advert-']"):
            link = card.css_first("a[data-testid='advert-link']") or card.css_first("h2 a")
            if link is None or not link.attributes.get("href"):
                continue
            external_id = (card.attributes["id"] or "").removeprefix("advert-")
            href = urljoin(BASE + "/", link.attributes["href"])
            url = clean_url(href)
            own_page = urlsplit(url).netloc in ("prace.cz", "www.prace.cz")
            info = {}
            for item in card.css("li"):
                label = item.css_first(".accessibility-hidden")
                if label is None:
                    continue
                name = clean_spaces(label.text()).rstrip(":").strip()
                value = clean_spaces(item.text()).removeprefix(clean_spaces(label.text()))
                info.setdefault(name, value.strip())
            title = clean_spaces(link.text())
            job = card_job(external_id, url, title, info, places)
            leads.append(Lead(external_id, url, url if own_page else None, job))
        return ListingPage(leads, has_page_link(tree, "page", page + 1))

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedJob | None:
        postings = find_job_postings(html)
        if not postings:
            return None
        return posting_to_job(postings[0], lead.external_id, lead.url, places)


SOURCE = PraceCz()
