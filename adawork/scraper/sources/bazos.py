"""prace.bazos.cz: ~6k classified job ads in 24 categories, HTML only.

robots.txt disallows the search and locality filters, so every category is walked
(`/<category>/<offset>/`, 20 per page, ~300 pages in all). Each row shows the place with its
PSČ, which tells the region before the ad is opened: ads outside the user's regions are
skipped. Categories are sorted newest first, but a page of only far-away ads would look like
"nothing new", so every run walks them to the end; that complete pass then expires the ads
that are gone.

Ads are free text written by anyone: we keep the title, place, date, the salary when it is a
plausible monthly or hourly amount, and the text; the AI reads the rest from the text. Posts
that are not job offers (people looking for work, services) are kept but not listed.
"""

import re
from datetime import datetime
from functools import partial
from urllib.parse import unquote, urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adawork.core import ParsedJob, ParsedLocation, ScrapeQuery
from adawork.geo.places import PlaceResolver
from adawork.normalize.offers import not_offer_reason
from adawork.normalize.text import clean_spaces, clean_title, fold, node_text, parse_salary
from adawork.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value

SOURCE_ID = "bazos"
BASE = "https://prace.bazos.cz"
PAGE_SIZE = 20
CATEGORIES = (
    "administrativa", "bezpecnost", "brigada", "domacnost", "finance", "it", "logistika",
    "management", "marketing", "obchod", "ostatni", "personalistika", "potravinarstvi",
    "pravo", "prumysl", "remeslo", "sluzby", "stavebnictvi", "technika", "tisk", "ubytovani",
    "vyzkum", "zdravotnictvi", "zemedelstvi",
)  # fmt: skip
AD_PATH = re.compile(r"^/inzerat/(\d+)/[^/]+\.php$")
DATE = re.compile(r"\[(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})\]")
PSC = re.compile(r"^\d{3}\s?\d{2}$")
ABROAD = "zahranici"
SHOWN = re.compile(r"Zobrazeno\s+\d+-(\d+)\s+inzerát\w*\s+z\s+(\d{1,3}(?:\s\d{3})*)")  # "z 1 798"


def _int(text: str) -> int:
    return int(re.sub(r"\D", "", text))


def posted(node: LexborNode | None) -> datetime | None:
    """ "- TOP - [6.10. 2026]"."""
    m = DATE.search(node.text() if node is not None else "")
    if not m:
        return None
    try:
        return datetime(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        return None


def set_salary(job: ParsedJob, text: str | None) -> None:
    """The price field has no unit: "2 500 Kč" may be a day's pay, "2 500 000 Kč" a joke,
    "1 Kč" a placeholder."""
    salary = parse_salary(text)
    amount = salary and (salary.min or salary.max)
    if not salary or not amount:
        return
    if 50 <= amount < 1000:
        unit = "hour"
    elif 12_000 <= amount <= 300_000:
        unit = "month"
    else:
        return
    job.salary_min, job.salary_unit, job.salary_currency = amount, unit, salary.currency


def location(
    places: PlaceResolver, area: str | None, psc: str | None, town: str | None = None
) -> ParsedLocation | None:
    """`area` is a district or city part ("Kolín", "Praha - východ", "Praha 8"), `psc` picks
    the municipality, `town` is the municipality named on the detail page."""
    psc = psc if psc and PSC.match(psc) else None
    by_psc = places.resolve(None, postal_code=psc) if psc else None
    if town:
        by_name = places.resolve(town, postal_code=psc)
        if by_name is not None and (by_psc is None or by_name.kraj == by_psc.kraj):
            return by_name
    if by_psc is not None:
        return by_psc
    return places.resolve(re.sub(r"\s+-\s+", "-", area)) if area else None


def _lines(node: LexborNode | None) -> list[str]:
    if node is None:
        return []
    return [line for p in node.text(separator="\n").split("\n") if (line := clean_spaces(p))]


def mark_not_offer(job: ParsedJob) -> None:
    """ "Hledám práci řidiče", "Nabízím hlídání": kept, but not listed as a job."""
    if reason := not_offer_reason(job.title, job.description):
        job.extra["not_offer"] = reason


class Bazos(CrawlSource):
    id = SOURCE_ID
    label = "Bazoš – práce"

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, partial(self.category_url, category)) for category in CATEGORIES]

    @staticmethod
    def category_url(category: str, page: int) -> str:
        offset = (page - 1) * PAGE_SIZE
        return f"{BASE}/{category}/" + (f"{offset}/" if offset else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = []
        for row in tree.css("div.inzeraty"):
            link = row.css_first("h2.nadpis a")
            m = AD_PATH.match(link.attributes.get("href") or "") if link is not None else None
            if m is None:
                continue
            url = urljoin(BASE, m[0])
            area, psc = (_lines(row.css_first(".inzeratylok")) + [None, None])[:2]
            snippet = row.css_first(".popis")
            job = ParsedJob(
                external_id=m[1],
                title=clean_title(node_value(link)),
                url=url,
                description=node_text(snippet) if snippet is not None else "",
                place_text=area,
                location=location(places, area, psc),
                posted_at=posted(row.css_first(".velikost10")),
            )
            set_salary(job, node_value(row.css_first(".inzeratycena")))
            mark_not_offer(job)
            leads.append(Lead(m[1], url, url, job))
        next_page = re.compile(rf"^/[a-z]+/{page * PAGE_SIZE}/$")
        has_next = any(next_page.match(a.attributes.get("href") or "") for a in tree.css("a"))
        # No link: the last page, or a changed layout. The ad count tells which; without it,
        # reading on until an empty page beats taking every page after this as gone.
        if not has_next:
            shown = SHOWN.search(html)
            has_next = _int(shown[1]) < _int(shown[2]) if shown else bool(leads)
        return ListingPage(leads, has_next)

    def wanted(self, lead: Lead, query: ScrapeQuery, places: PlaceResolver) -> bool:
        card = lead.card
        if card is None:
            return True
        if card.location is not None and card.location.kraj:
            return card.location.kraj in query.kraje
        return fold(card.place_text or "") != ABROAD

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedJob | None:
        tree = LexborHTMLParser(html)
        title = clean_title(node_value(tree.css_first("h1.nadpisdetail")))
        text = tree.css_first("div.popisdetail")
        if not title or text is None:
            return None
        info: dict[str, LexborNode] = {}
        for row in tree.css("td.listadvlevo tr"):
            cells = row.css("td")
            if len(cells) >= 2:
                label = clean_spaces(cells[0].text()).rstrip(":")
                info.setdefault(label, cells[-1])
        job = ParsedJob(
            external_id=lead.external_id,
            title=title,
            url=lead.url,
            description=node_text(text),
            posted_at=posted(tree.css_first(".inzeratydetnadpis .velikost10")),
        )
        place = info.get("Lokalita")
        if place is not None:
            # "<a>274 01</a> <a href="/inzeraty/slaný-1/27401/">Kladno</a>": PSČ, town, district.
            links = place.css("a")
            psc = node_value(links[0]) if links else None
            area = node_value(links[-1]) if len(links) > 1 else None
            href = unquote(links[-1].attributes.get("href") or "") if len(links) > 1 else ""
            m = re.search(r"/inzeraty/([^/]+?)(?:-\d+)?/\d{5}/", href)
            town = m[1].replace("-", " ") if m else None
            job.place_text = area
            job.location = location(places, area, psc, town)
        set_salary(job, node_value(info.get("Mzda/plat")))
        mark_not_offer(job)
        photos = (
            img.attributes.get("data-flickity-lazyload") or img.attributes.get("src")
            for img in tree.css("img.carousel-cell-image")
        )
        job.images = list(dict.fromkeys(p for p in photos if p))[:6]
        return job


SOURCE = Bazos()
