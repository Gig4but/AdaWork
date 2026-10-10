"""Public classified boards with a job section (PLAN §4.3 "Forums"): HyperInzerce, Annonce,
Bazar.cz, cBazar, Mimibazar. The boards are listed in classifieds.yaml; each one is a source
of its own (own switch, pause when blocked, expiry).

Every board is read the same way: listing pages newest first → links matching the board's
ad pattern → ad pages. An ad page gives a title, the ad text and a block of "label: value"
rows, read generically (table rows, label elements, "Label: value" lines), so a board needs
only a few selectors. Places come from those rows, else from the ad URL or the page title.

Ads are free text written by anyone: posts that are not job offers (people looking for work,
services, loans, sales) are kept as non-offers so they are not read again, and the red-flag
rules mark likely scams (normalize/offers.py).
"""

import re
from dataclasses import dataclass, fields
from datetime import datetime
from functools import cache
from pathlib import Path
from urllib.parse import urljoin

import yaml
from selectolax.lexbor import LexborHTMLParser

from adawork.core import ParsedJob, ParsedLocation, ScrapeQuery, SourceKind
from adawork.geo.places import PlaceResolver
from adawork.normalize.offers import not_offer_reason
from adawork.normalize.text import (
    clean_spaces,
    contract_types,
    czech_date,
    education_code,
    fold,
    node_text,
)
from adawork.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value
from adawork.scraper.crawl import set_salary as _set_salary

BOARDS_FILE = Path(__file__).with_suffix(".yaml")
# Pictures that are not the ad's own: site logos, placeholders, QR codes, counters.
NOT_AD_IMAGE = re.compile(
    r"logo|default\.(?:png|jpe?g)|qrcode|/img/|toplist|facebook\.png|no-?image|blank|placeholder",
    re.IGNORECASE,
)
_LABEL_LINE = re.compile(r"^([^\W\d][\w .]{1,30}?)\s*:\s*(\S.{0,150})$")
# Folded labels of the "label: value" rows, by meaning.
TOWN = ("lokalita", "misto", "mesto", "obec", "misto vykonu prace", "pracoviste")
STREET = ("ulice", "adresa")
PAY = ("odmena", "mzda", "plat", "cena")
DATE = ("datum", "vlozen", "vlozeno", "aktualizovano")


@dataclass(frozen=True)
class Board:
    id: str
    name: str
    section_url: str
    ad_link: str
    page: str | None = None
    skip_link: str | None = None
    title: str = "h1"
    text: str | None = None
    image: str | None = None
    place_url: str | None = None
    place_title: str | None = None
    default_enabled: bool = True


@cache
def boards(path: Path = BOARDS_FILE) -> tuple[Board, ...]:
    known = {f.name for f in fields(Board)}
    with path.open(encoding="utf-8") as fh:
        entries = yaml.safe_load(fh)
    return tuple(Board(**{k: v for k, v in b.items() if k in known}) for b in entries)


def label_values(tree: LexborHTMLParser) -> dict[str, str]:
    """Folded label -> value from the ad's info rows: "<th>Lokalita:</th><td>Praha</td>",
    "<div class="…label">Odměna v Kč/měs</div> 15200", "<dt>…</dt><dd>…</dd>", "Datum: 8. 10. 2026".
    """
    found: dict[str, str] = {}

    def keep(label: str | None, value: str | None) -> None:
        label = fold(clean_spaces(label or "")).rstrip(":").strip()
        value = clean_spaces(value or "")
        if label and value and len(label) <= 40:
            found.setdefault(label, value)

    for row in tree.css("tr"):
        cells = row.css("th, td")
        if len(cells) >= 2:
            keep(cells[0].text(), " ".join(c.text() for c in cells[1:]))
    for dt in tree.css("dt"):
        dd = dt.next
        while dd is not None and dd.tag != "dd":
            dd = dd.next
        if dd is not None:
            keep(dt.text(), dd.text())
    for node in tree.css("[class*='label']"):
        parent = node.parent
        if parent is not None:
            whole, label = clean_spaces(parent.text()), clean_spaces(node.text())
            if whole.startswith(label):
                keep(label, whole[len(label) :])
    for node in tree.css("div, span, li, p, strong"):
        if node.child is None or node.child.next is not None or node.child.tag != "-text":
            continue  # only elements with a single text inside
        if m := _LABEL_LINE.match(clean_spaces(node.text())):
            keep(m[1], m[2])
    return found


def _first(values: dict[str, str], names: tuple[str, ...]) -> tuple[str, str] | None:
    """The first row whose label starts with one of `names`: (label, value)."""
    for name in names:
        for label, value in values.items():
            if label == name or label.startswith(name + " "):
                return label, value
    return None


def _meta(tree: LexborHTMLParser, prop: str) -> str | None:
    node = tree.css_first(f"meta[property='{prop}']") or tree.css_first(f"meta[name='{prop}']")
    return clean_spaces(node.attributes.get("content") or "") or None if node else None


def _date(text: str | None) -> datetime | None:
    day = czech_date(text)
    return datetime(day.year, day.month, day.day) if day else None


def set_pay(job: ParsedJob, label: str, value: str) -> None:
    """ "Odměna v Kč/měs" + "15200", "Mzda" + "25 000 Kč", "Cena" + "Dohodou". A board's
    price field is often a placeholder (0, 1 Kč), so only plausible amounts are kept."""
    unit = re.search(r"kc\s*/\s*(\w+)", label)
    text = f"{value} Kč/{unit[1]}" if unit and re.fullmatch(r"[\d\s]+", value) else value
    _set_salary(job, text)
    amount = job.salary_max or job.salary_min
    plausible = (80, 3000) if job.salary_unit == "hour" else (8000, 500_000)
    if amount is not None and not plausible[0] <= amount <= plausible[1]:
        job.salary_min = job.salary_max = job.salary_unit = None


class Classifieds(CrawlSource):
    newest_first = True  # boards list the newest ads first
    max_pages = 15
    max_details = 60
    expire_after_days = 30  # an ad not seen for a month is gone (old ads are not re-walked)
    nationwide = True
    kind: SourceKind = "classifieds"

    def __init__(self, board: Board) -> None:
        self.board = board
        self.id = board.id
        self.label = board.name
        self.default_enabled = board.default_enabled
        self._ad_link = re.compile(board.ad_link)
        self._skip = re.compile(board.skip_link) if board.skip_link else None

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        return [(None, self.page_url)]

    def page_url(self, page: int) -> str:
        if page == 1 or not self.board.page:
            return self.board.section_url
        return self.board.page.format(n=page, p=page - 1)

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads: dict[str, Lead] = {}
        base = self.page_url(page)
        for a in tree.css("a[href]"):
            url = urljoin(base, a.attributes.get("href") or "")
            m = self._ad_link.match(url)
            if m is None or m[1] in leads or (self._skip and self._skip.search(url)):
                continue
            leads[m[1]] = Lead(m[1], m[0], m[0])
        return ListingPage(list(leads.values()), has_next=bool(self.board.page) and bool(leads))

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedJob | None:
        tree = LexborHTMLParser(html)
        b = self.board
        title = node_value(tree.css_first(b.title)) or _meta(tree, "og:title")
        node = tree.css_first(b.text) if b.text else None
        text = node_text(node) if node is not None else _meta(tree, "og:description")
        if not title or not text:
            return None
        values = label_values(tree)
        job = ParsedJob(external_id=lead.external_id, title=title, url=lead.url, description=text)
        kind = fold(values.get("typ inzeratu", ""))
        reason = "seeker" if kind.startswith("poptav") else not_offer_reason(title, text)
        if reason:
            job.extra["not_offer"] = reason

        job.place_text, job.location = self._place(tree, values, lead.url, places)
        if pay := _first(values, PAY):
            set_pay(job, *pay)
        if employer := _first(values, ("zamestnavatel", "firma", "spolecnost")):
            job.employer_name = employer[1]
        if education := _first(values, ("vzdelani", "pozadovane vzdelani")):
            job.education = education_code(education[1])
        contract = _first(values, ("druh uvazku", "uvazek", "typ uvazku", "typ brigady"))
        job.contract_types = contract_types(contract[1] if contract else None, title)
        if occupation := _first(values, ("konkretni profese", "profese", "pozice")):
            job.extra["occupations"] = [occupation[1]]
        posted = _first(values, DATE)
        job.posted_at = _date(posted[1]) if posted else None
        job.images = self._images(tree, lead)
        return job

    def _place(
        self, tree: LexborHTMLParser, values: dict[str, str], url: str, places: PlaceResolver
    ) -> tuple[str | None, ParsedLocation | None]:
        town = _first(values, TOWN)
        street = _first(values, STREET)
        okres, kraj = values.get("okres"), values.get("kraj")
        names = [town[1] if town else None]
        if self.board.place_url and (m := re.search(self.board.place_url, url)):
            slug = m[1].replace("-", " ")
            names += [slug, re.sub(r"\s+\d+$", "", slug)]  # "plzen 1" -> "plzen"
        title = _meta(tree, "og:title") or ""
        if self.board.place_title and (m := re.search(self.board.place_title, title)):
            names.append(m[1])
        for name in filter(None, names):
            for part in re.split(r"\s*[,;]\s*", name):  # "Letňany, Praha"
                loc = places.resolve(part, street=street[1] if street else None, district=okres)
                if loc is not None:
                    return name, loc
        for area in (okres, kraj):
            if area and (loc := places.resolve(area)):
                return area, loc
        return next(filter(None, names), None), None

    def _images(self, tree: LexborHTMLParser, lead: Lead) -> list[str]:
        """The ad's own pictures (not logos or other ads' thumbnails)."""
        if self.board.image:
            own = re.compile(self.board.image.replace("{id}", re.escape(lead.external_id)))
            images = tree.css("img")
            sources = [i.attributes.get("src") or i.attributes.get("data-src") for i in images]
            sources += [a.attributes.get("href") for a in tree.css("a[href]")]
            found = [urljoin(lead.url, s) for s in sources if s and own.search(s)]
        else:
            found = [urljoin(lead.url, og)] if (og := _meta(tree, "og:image")) else []
        return [u for u in dict.fromkeys(found) if not NOT_AD_IMAGE.search(u)][:6]


SOURCES = tuple(Classifieds(board) for board in boards())
