"""jobs.cz (Alma Career): ~16.5k ads, 30 per region listing page, server-rendered HTML.

There is no structured data; cards and detail pages use stable `data-test` hooks. Detail
pages are `/rpd/<id>/` (some redirect to a company-branded `/fp/…` page with the same hooks;
big employers' career sites, `<company>.jobs.cz/detail-pozice`, load the ad with JavaScript,
so those jobs keep what their card says).
The analytics `dataLayer` names the occupations, which helps the occupation lookup.
jobs.cz shares many ads with prace.cz (same company); dedup merges them.
"""

import json
import re
from copy import copy

from selectolax.lexbor import LexborHTMLParser

from adawork.core import ParsedJob
from adawork.geo.places import PlaceResolver
from adawork.normalize.text import (
    clean_spaces,
    contract_types,
    education_code,
    node_text,
)
from adawork.scraper.crawl import (
    CrawlSource,
    Lead,
    ListingPage,
    has_page_link,
    node_value,
    set_salary,
)

SOURCE_ID = "jobs_cz"
BASE = "https://www.jobs.cz"
MORE_PLACES = re.compile(r"\s*\+\s*\d+\s+dalš\w+\s+lokalit\w*$")
DATA_LAYER = re.compile(r"dataLayer\.push\((\{.*?\})\);", re.S)
STREET_NUMBER = re.compile(r"\d")
CAREER_SITE = re.compile(r"<title>[^<]*Detail pozice \|")


def place(text: str | None, places: PlaceResolver, street: str | None = None):
    """ "Kolín – Kolín V", "Kolín + 8 dalších lokalit", "okres Beroun", "Česká republika"."""
    text = MORE_PLACES.sub("", clean_spaces(text or ""))
    if not text:
        return None
    town, _, part = (p.strip() for p in text.partition(" – "))
    if part:
        return places.resolve(part, street=street) or places.resolve(town, street=street)
    return places.resolve(town, street=street)


def occupations(html: str) -> list[str]:
    """dataLayer category "JD/<fields>/<occupation>-<occupation>", "*" standing for "/"."""
    m = DATA_LAYER.search(html)
    if not m:
        return []
    try:
        products = json.loads(m.group(1))["ecommerce"]["detail"]["products"]
    except (ValueError, KeyError, TypeError):
        return []
    category = (products[0].get("category") or "") if products else ""
    parts = category.split("/")
    if len(parts) < 3:
        return []
    return [o.replace("*", "/").strip() for o in parts[2].split("-") if o.strip()]


class JobsCz(CrawlSource):
    id = SOURCE_ID
    label = "Jobs.cz"

    def listing_url(self, region_slug: str, page: int) -> str:
        return f"{BASE}/prace/{region_slug}/" + (f"?page={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = []
        for card in tree.css("article.SearchResultCard"):
            link = card.css_first("a[data-jobad-id]")
            ad_id = link.attributes.get("data-jobad-id") if link is not None else None
            if link is None or not ad_id:
                continue
            url = f"{BASE}/rpd/{ad_id}/"
            where = node_value(card.css_first("[data-test='serp-locality']"))
            job = ParsedJob(
                external_id=ad_id,
                title=clean_spaces(link.text()),
                url=url,
                employer_name=node_value(card.css_first(".SearchResultCard__footerItem span")),
                place_text=where,
                location=place(where, places),
            )
            set_salary(job, node_value(card.css_first(".Tag--success")))
            leads.append(Lead(ad_id, url, url, job))
        # A pager without a link to the next page ends the listing; no pager at all (a changed
        # layout): read on until an empty page.
        more = has_page_link(tree, "page", page + 1)
        if not more and tree.css_first(".Pagination") is None:
            more = bool(leads)
        return ListingPage(leads, more)

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedJob | None:
        tree = LexborHTMLParser(html)
        body = tree.css_first("[data-test='jd-body-richtext']")
        title = node_value(tree.css_first("h1"))
        if body is None and lead.card is not None and CAREER_SITE.search(html):
            return copy(lead.card)  # read, but nothing more than the card to take
        if body is None or not title:
            return None
        info: dict[str, str] = {}  # "Požadované vzdělání" -> "Základní", …
        for item in tree.css("[data-test='jd-info-item']"):
            label = node_value(item.css_first(".accessibility-hidden"))
            value = item.css_first("p")
            if label and value is not None:
                info[label] = clean_spaces(value.text(separator=" "))
        address = node_value(tree.css_first("[data-test='jd-info-location']"))
        street, town = None, address
        if address and "," in address:
            first, rest = (p.strip() for p in address.split(",", 1))
            if STREET_NUMBER.search(first):  # "Řecká 1107, Nehvizdy"
                street, town = first, rest
        description = node_text(body)
        description = re.sub(r"^Pracovní nabídka\s*", "", description)
        job = ParsedJob(
            external_id=lead.external_id,
            title=title,
            url=lead.url,
            description=description,
            employer_name=info.get("Společnost"),
            place_text=address,
            location=place(town, places, street),
            contract_types=contract_types(
                info.get("Typ pracovního poměru"), info.get("Typ smluvního vztahu")
            ),
            education=education_code(info.get("Požadované vzdělání")),
        )
        benefits = tree.css_first("[data-test='jd-benefits']")
        if benefits is not None and (text := clean_spaces(benefits.text(separator=", "))):
            job.extra["benefits_text"] = text
        if "agentura" in (info.get("Zadavatel") or "").lower():
            job.extra["agency"] = True
        if found := occupations(html):
            job.extra["occupations"] = found
        salary = tree.css_first("[data-test='jd-salary']")
        set_salary(job, clean_spaces(salary.text(separator=" ")) if salary else None)
        return job


SOURCE = JobsCz()
