"""profesia.cz: ~1.4k ads. Microdata on the standard detail template; branded employer
templates (e.g. ManpowerGroup) have none, so the card supplies the fields and the page text
the description.
"""

from urllib.parse import urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser

from adawork.core import ParsedJob
from adawork.geo.places import PlaceResolver
from adawork.normalize.text import node_text, split_places
from adawork.scraper.crawl import (
    CrawlSource,
    Lead,
    ListingPage,
    clean_url,
    has_page_link,
    node_value,
    set_salary,
)
from adawork.scraper.structured import find_job_postings, parse_date, posting_to_job

SOURCE_ID = "profesia"
BASE = "https://www.profesia.cz"


class Profesia(CrawlSource):
    id = SOURCE_ID
    label = "profesia.cz"

    def listing_url(self, region_slug: str, page: int) -> str:
        return f"{BASE}/prace/{region_slug}/" + (f"?page_num={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = []
        for row in tree.css("li.list-row"):
            link = row.css_first("h2 a[id^='offer']")
            if link is None or not link.attributes.get("href"):
                continue
            external_id = (link.attributes["id"] or "").removeprefix("offer")
            url = clean_url(urljoin(BASE + "/", link.attributes["href"]))
            names = split_places(node_value(row.css_first(".job-location")))
            job = ParsedJob(
                external_id=external_id,
                title=node_value(link) or "",
                url=url,
                employer_name=node_value(row.css_first(".employer")),
                place_text=", ".join(names) or None,
                location=next((loc for n in names if (loc := places.resolve(n))), None),
            )
            set_salary(job, node_value(row.css_first(".label-group .label")))
            own_page = urlsplit(url).netloc in ("profesia.cz", "www.profesia.cz")
            leads.append(Lead(external_id, url, url if own_page else None, job))
        return ListingPage(leads, has_page_link(tree, "page_num", page + 1))

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedJob | None:
        tree = LexborHTMLParser(html)
        salary = node_value(tree.css_first(".salary-range"))
        postings = find_job_postings(tree)
        if postings:
            job = posting_to_job(postings[0], lead.external_id, lead.url, places)
        else:
            main = tree.css_first(".details-section") or tree.css_first("main#detail")
            if main is None:
                return None
            for node in main.css("h1, .button-bar, .btn-group, script, style"):
                node.decompose()
            card = lead.card
            job = ParsedJob(
                external_id=lead.external_id,
                title=card.title if card else "",
                url=lead.url,
                description=node_text(main),
            )
            posted = tree.css_first("[itemprop='datePosted']")
            if posted is not None:
                job.posted_at = parse_date(node_value(posted))
        if job.salary_min is None and job.salary_max is None:
            set_salary(job, salary)
        return job if job.title or lead.card else None


SOURCE = Profesia()
