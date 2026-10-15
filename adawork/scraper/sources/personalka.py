"""personalka.cz: ~40k ads (many older ones re-listed from Úřad práce). Region listings with
30 cards per page (title, place, salary, contract, employer), detail pages without
structured data: the "Shrnutí pracovní pozice" block, the ad text and the employer box.

A region listing covers ads within 30 km of the region, so neighbouring regions show up too.
The "Načíst další" button loads more cards by script; `?page=N` serves the same pages.
"""

import re
from urllib.parse import urlsplit

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adawork.core import ParsedJob, ParsedLocation
from adawork.geo.places import PlaceResolver
from adawork.normalize.text import (
    card_date,
    clean_spaces,
    contract_types,
    education_code,
    fold,
    node_text,
    suitable_for,
    valid_ico,
)
from adawork.scraper.crawl import (
    KRAJ_SLUGS,
    CrawlSource,
    Lead,
    ListingPage,
    node_value,
    set_salary,
)

SOURCE_ID = "personalka"
BASE = "https://personalka.cz"
PAGE_SIZE = 30
SLUGS = {**KRAJ_SLUGS, "Hlavní město Praha": "hlavni-mesto-praha"}
_REGION_SLUGS = frozenset(SLUGS.values())
_CONTRACT_LABELS = {"hpp", "ico", "dpp", "dpc", "brigada", "zkraceny uvazek", "plny uvazek", "staz"}
_HEADER_TAGS = {"doporucujeme", "top zamestnavatel"}
_WORKPLACE = re.compile(r"^(?:(?P<street>.*),\s*)?(?P<psc>\d{3}\s?\d{2})\s+(?P<town>.+)$")
_FOUND = re.compile(r"Našli jsme\s*<strong>\s*([\d\s]+?)\s*nabíd")  # "Našli jsme 4 554 nabídek"
_GONE = re.compile(r"Litujeme, ale pracovní inzerát.{0,500}?již není aktuální", re.S)
_BENEFITS = (
    ("premie", ("premie", "bonus")),
    ("strav", ("strav",)),
    ("ubyt", ("ubytovani",)),
    ("dovol", ("dovolen",)),
    ("jizdne", ("jizdn", "doprava do zamestnani")),
    ("predsk", ("skolk",)),
)


def _is_contracts(text: str) -> bool:
    return all(fold(item.strip()) in _CONTRACT_LABELS for item in text.split(","))


def card_job(external_id: str, url: str, card: LexborNode, places: PlaceResolver) -> ParsedJob:
    """The desktop card: place · salary · contracts · employer · date, the middle ones optional."""
    title = node_value(card.css_first("h3 a")) or ""
    job = ParsedJob(external_id=external_id, title=title, url=url)
    spans = card.css("div.text-grey-dark > span.inline")
    if spans:
        job.place_text = node_value(spans[0])
        job.location = places.resolve(job.place_text)
    for span in spans[1:]:
        text = node_value(span) or ""
        if "font-semibold" in (span.attributes.get("class") or ""):
            job.posted_at = card_date(text)  # older cards show no date
        elif re.search(r"\d", text) and "kc" in fold(text):
            set_salary(job, text)
        elif _is_contracts(text):
            job.contract_types = contract_types(text)
        else:
            job.employer_name = text
    return job


def summary_fields(tree: LexborHTMLParser) -> dict[str, str]:
    """Folded label without the colon -> value: {"mzda": "28 000–60 000 Kč", ...}."""
    found = {}
    for item in tree.css("div.mt-3"):
        label = item.css_first("strong")
        value = item.css_first("span")
        if label is None or value is None:
            continue
        text = clean_spaces(value.text())
        if text:
            found.setdefault(fold(clean_spaces(label.text())).rstrip(":"), text)
    return found


def place_links(tree: LexborHTMLParser) -> dict[str, str]:
    """The summary's place links: {"kraj": "Kraj Vysočina", "okres": "Jihlava", "obec": "Telč"}.
    Links of a profession ("/prace/prodavac/kraj-vysocina") are skipped."""
    found: dict[str, str] = {}
    for link in tree.css("div.mt-3 a[href*='/prace/']"):
        parts = urlsplit(link.attributes.get("href") or "").path.strip("/").split("/")[1:]
        if not parts or parts[0] not in _REGION_SLUGS:
            continue
        kind = ("kraj", "okres", "obec")[min(len(parts), 3) - 1]
        text = clean_spaces(link.text())
        found.setdefault(kind, text.removeprefix("okres ") if kind == "okres" else text)
    return found


def header_place(items: list[str]) -> str | None:
    """The place among the tags under the title ("Doporučujeme · Telč · Brigáda, DPP")."""
    for item in items:
        folded = fold(item)
        if folded in _HEADER_TAGS or _is_contracts(item) or contract_types(item):
            continue
        if folded.startswith(("nastup", "pro ")):
            continue
        return item
    return None


def detail_location(
    workplace: str | None, header: str | None, links: dict[str, str], places: PlaceResolver
) -> ParsedLocation | None:
    okres = links.get("okres")
    m = _WORKPLACE.match(workplace or "")
    if m:
        loc = places.resolve(m["town"], street=m["street"], postal_code=m["psc"], district=okres)
        if loc:
            return loc
    for name in (links.get("obec"), header):
        loc = places.resolve(name, district=okres) if name else None
        # A namesake village in another district is worse than the district itself.
        if loc and (okres is None or fold(loc.okres or "") == fold(okres)):
            return loc
    if okres and (loc := places.resolve(f"okres {okres}")):
        return loc
    return places.resolve(links.get("kraj"))


def section_items(tree: LexborHTMLParser, heading: str) -> list[str]:
    for section in tree.css("section"):
        if fold(node_value(section.css_first("h2")) or "") == heading:
            return [clean_spaces(li.text()) for li in section.css("li")]
    return []


class Personalka(CrawlSource):
    id = SOURCE_ID
    label = "Personálka.cz"
    # Region listings run to 150–400 pages, newest first: after the first full pass a run
    # stops at the first page with nothing new. Older ads stay listed for months without
    # being seen again: a complete pass, or the ad's own page, tells which are gone.
    max_pages = 400
    newest_first = True
    empty_last_page = True
    recheck_after_days = 30
    region_slugs = SLUGS

    def listing_url(self, region_slug: str, page: int) -> str:
        return f"{BASE}/prace/{region_slug}" + (f"?page={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = []
        for row in tree.css(r"#rows div[wire\:key^='offer-']"):
            external_id = (row.attributes["wire:key"] or "").removeprefix("offer-")
            card = row.css_first("div.flex-1")
            if not external_id.isdigit() or card is None:
                continue
            url = f"{BASE}/{external_id}"
            leads.append(Lead(external_id, url, url, card_job(external_id, url, card, places)))
        more = tree.css_first(r"button[wire\:click='loadMore']") is not None
        # No button: the last page, or a changed layout. The ad count tells which; without
        # it, reading on until an empty page beats taking every page after this as gone.
        if not more:
            found = _FOUND.search(html)
            more = page * PAGE_SIZE < int(re.sub(r"\D", "", found[1])) if found else bool(leads)
        return ListingPage(leads, has_next=more)

    def gone(self, html: str) -> bool:
        """An ended ad keeps its page: "Litujeme, ale pracovní inzerát … již není aktuální."."""
        return _GONE.search(html) is not None

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedJob | None:
        tree = LexborHTMLParser(html)
        title = node_value(tree.css_first("h1"))
        if not title:
            return None
        fields = summary_fields(tree)
        header = [
            clean_spaces(li.text())
            for li in tree.css("ul.list-reset.text-grey-dark.font-heading > li")
        ]
        header = [re.sub(r"^·\s*", "", item) for item in header if item]
        links = place_links(tree)
        workplace = fields.get("pracoviste")
        place = header_place(header)

        description = ""
        for section in tree.css("section"):
            if fold(node_value(section.css_first("h2")) or "") == "popis":
                body = section.css_first(".tp")
                description = node_text(body) if body is not None else ""
                break

        employer = tree.css_first("a[href*='/firma/']")
        # The employer page is "/firma/<IČO>" (checksum-valid in the samples).
        ico = (
            urlsplit(employer.attributes.get("href") or "").path.rsplit("/", 1)[-1]
            if employer
            else ""
        )
        suitable = ", ".join(filter(None, [fields.get("vhodne pro"), *header]))
        benefits = section_items(tree, "benefity")
        folded_benefits = " ".join(fold(b) for b in benefits)
        job = ParsedJob(
            external_id=lead.external_id,
            title=title,
            url=lead.url,
            description=description,
            employer_name=node_value(employer),
            employer_ico=ico if valid_ico(ico) else None,
            place_text=workplace or place or links.get("obec") or links.get("okres"),
            location=detail_location(workplace, place, links, places),
            contract_types=contract_types(fields.get("typ uvazku")),
            education=education_code(fields.get("vzdelani")),
            suitable_for=suitable_for(suitable),
            benefits=[c for c, words in _BENEFITS if any(w in folded_benefits for w in words)],
            shifts=["pruznaPd"] if "pruzna pracovni doba" in folded_benefits else [],
        )
        set_salary(job, fields.get("mzda"))
        languages = section_items(tree, "pozadovane jazyky")
        if languages:
            job.extra["languages"] = languages
        if benefits:
            job.extra["benefits_text"] = ", ".join(benefits)
        return job


SOURCE = Personalka()
