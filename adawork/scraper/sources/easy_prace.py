"""easy-prace.cz: ~10.5k own ads. Region listings with 40 cards per page (title, place,
employer, contract, pay); each region's listing ends with the ads for the whole country. No
structured data on detail pages, so the "Lokalita / Úvazek / Vzdělání / Vhodné pro" block and
the ad text are read.

The portal also re-lists Úřad práce ads (`/volne-misto/...`); those come from Úřad práce
directly and are not in the region listings. Pages show only a relative time ("Přidáno
včera", "Aktualizováno před pár hodinami"), so only new ads get a posted day.
"""

import re
from datetime import date, datetime, time, timedelta

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adawork.core import ParsedJob, ParsedLocation
from adawork.geo.places import PlaceResolver
from adawork.normalize.text import (
    clean_spaces,
    clean_title,
    contract_types,
    education_code,
    fold,
    node_text,
    suitable_for,
)
from adawork.scraper.crawl import (
    KRAJ_SLUGS,
    CrawlSource,
    Lead,
    ListingPage,
    node_value,
    set_salary,
)

SOURCE_ID = "easy_prace"
BASE = "https://www.easy-prace.cz"
SLUGS = {**KRAJ_SLUGS, "Kraj Vysočina": "vysocina-kraj"}
AD_PATH = re.compile(r"^/nabidka/[^/]*/(\d+)$")
_PAGES = re.compile(r"&quot;pocetStranek&quot;:(\d+)")  # in the results' data attribute
_WITH_DISTRICT = re.compile(r"^(.*?)\s*\((?:okres\s+)?([^)]+)\)$")


def resolve_place(place: str, places: PlaceResolver) -> ParsedLocation | None:
    """ "Pardubice (Pardubice)", "Valy (okres Pardubice)", "Pardubičky, Pardubice"."""
    m = _WITH_DISTRICT.match(place)
    if m:
        return places.resolve(m[1], district=m[2])
    if "," in place:
        part, town = (p.strip() for p in place.split(",", 1))
        named = part if fold(part).startswith(fold(town)) else f"{town}-{part}"
        return places.resolve(named) or places.resolve(town)
    return places.resolve(place)


def posted_day(status: str | None) -> datetime | None:
    """ "Přidáno před pár hodinami" (today), "Přidáno včera"; a changed ad tells nothing."""
    folded = fold(status or "")
    if not folded.startswith("pridano"):
        return None
    if "vcera" in folded:
        return datetime.combine(date.today() - timedelta(days=1), time())
    if "hodin" in folded or "minut" in folded or "dnes" in folded:
        return datetime.combine(date.today(), time())
    return None


def card_job(external_id: str, url: str, card: LexborNode, places: PlaceResolver) -> ParsedJob:
    job = ParsedJob(
        external_id=external_id,
        title=clean_title(node_value(card.css_first(".NabidkaItem-title"))),
        url=url,
        posted_at=posted_day(node_value(card.css_first(".NabidkaItem-status"))),
    )
    for item in card.css(".NabidkaItem-infoItem"):
        icon = item.css_first("svg")
        kind = (icon.attributes.get("class") or "") if icon is not None else ""
        text = clean_spaces(item.text())
        if "icon--map" in kind:  # "Jihlava", "Vysočina kraj, Vysočina kraj", "Česká republika"
            job.place_text = text
            job.location = places.resolve(text.split(",")[0])
        elif "icon--building" in kind:
            job.employer_name = text
        elif "icon--clock" in kind:
            job.contract_types = contract_types(text)
    for label in card.css(".NabidkaItem-label"):
        if label.css_first(".NabidkaItem-icon--banknote") is not None:
            set_salary(job, node_value(label.css_first(".NabidkaItem-labelValue")))
    return job


class EasyPrace(CrawlSource):
    id = SOURCE_ID
    label = "Easy-práce.cz"
    region_slugs = SLUGS

    def listing_url(self, region_slug: str, page: int) -> str:
        return f"{BASE}/{region_slug}" + (f"/strana/{page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = []
        for card in tree.css("a.NabidkaItem"):
            m = AD_PATH.match(card.attributes.get("href") or "")
            if m is None:
                continue
            url = BASE + m[0]
            leads.append(Lead(m[1], url, url, card_job(m[1], url, card, places)))
        more = tree.css_first("link[rel=next]") is not None
        # No pager link: the last page, or a changed layout (see dobraprace.py).
        if not more:
            found = _PAGES.search(html)
            more = page < int(found[1]) if found else bool(leads)
        return ListingPage(leads, has_next=more)

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedJob | None:
        tree = LexborHTMLParser(html)
        title = node_value(tree.css_first("h1.NabidkaDetail-title"))
        if not title:
            return None
        info: dict[str, str] = {}
        place_names: list[str] = []
        for item in tree.css(".NabidkaDetail-infoItem"):
            label = node_value(item.css_first(".NabidkaDetail-infoLabel"))
            value = item.css_first(".NabidkaDetail-infoValue")
            if not label or value is None:
                continue
            if fold(label) == "lokalita":
                place_names = [clean_spaces(a.text()) for a in value.css("a.map-link")]
            info[fold(label)] = clean_spaces(value.text())

        text = tree.css_first(".NabidkaDetail-text")
        if text is not None:
            for heading in text.css("h2"):
                if fold(node_value(heading) or "") == "popis pracovni nabidky":
                    heading.decompose()

        employer = node_value(tree.css_first(".NabidkaDetail-companyHeaderTitle"))
        job = ParsedJob(
            external_id=lead.external_id,
            title=clean_title(title),  # titles often start with an emoji
            url=lead.url,
            description=node_text(text) if text is not None else "",
            employer_name=employer,
            place_text="; ".join(place_names) or info.get("lokalita") or None,
            location=next((loc for p in place_names if (loc := resolve_place(p, places))), None),
            contract_types=contract_types(info.get("uvazek")),
            education=education_code(info.get("vzdelani")),
            suitable_for=suitable_for(info.get("vhodne pro")),
        )
        set_salary(job, node_value(tree.css_first(".NabidkaDetail-salary")))
        fields = [clean_spaces(n.text()) for n in tree.css("#filter-obory-content .filter-name")]
        if fields:
            job.extra["portal_fields"] = fields
        return job


SOURCE = EasyPrace()
