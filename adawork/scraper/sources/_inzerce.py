"""Detail pages of the "Fajn skupina" portals (inwork.cz, fajn-brigady.cz): the same template
with the ad text and a "label: value" block (Mzda, Počet míst, Úvazek, Lokalita, Firma, ...).
"""

import re
from datetime import datetime

from selectolax.lexbor import LexborHTMLParser

from adawork.core import ParsedJob
from adawork.geo.places import PlaceResolver
from adawork.normalize.text import (
    clean_spaces,
    contract_types,
    czech_date,
    education_code,
    fold,
    node_text,
    suitable_for,
)
from adawork.scraper.crawl import Lead, node_value, set_salary

_DISTRICT = re.compile(r"\((?:Ok\.|okres)\s*([^)]+)\)", re.I)
_UP_SUFFIX = " (Úřad práce)"
# Re-listed Úřad práce ads keep an emptied contact line: "Kontakt:;, e-mail:".
_EMPTY_CONTACT = re.compile(r"^(?:kontakt|e-mail|telefon|[:;,\s])*$", re.I)


def info_fields(tree: LexborHTMLParser) -> dict[str, str]:
    """Folded label without the colon -> value: {"mzda": "20 000 - 23 000 Kč/měs.", ...}."""
    found = {}
    for item in tree.css(".ad-detail__info__item"):
        # The label's class differs between page variants (__tabulka_nadpisek, green-text):
        # it is the item's first span, the value is everything after it.
        classed = item.css_first(".__tabulka_nadpisek")
        label_node = classed or item.css_first("span")
        label = node_value(label_node)
        if label_node is None or not label or (classed is None and not label.endswith(":")):
            continue
        value_node = item.css_first(".__tabulka_text")
        if value_node is None:
            value_node = label_node.next
            while value_node is not None and not clean_spaces(value_node.text() or ""):
                value_node = value_node.next
        if value_node is None:
            continue
        value = clean_spaces(value_node.text())
        titled = value_node.css_first("[title]")
        if value.endswith("...") and titled is not None:
            value = titled.attributes.get("title") or value  # a long address cut short
        if value:
            found[fold(label).rstrip(":").strip()] = clean_spaces(value)
    for item in tree.css(".ad-detail__info__bottom div"):
        text = clean_spaces(item.text())
        if ":" in text:
            label, value = text.split(":", 1)
            found[fold(label).strip()] = value.strip()
    return found


def _contracts(fields: dict[str, str]) -> list[str]:
    uvazek = fields.get("uvazek")
    # The block says just "Plný" / "Zkrácený".
    return contract_types(f"{uvazek} úvazek" if uvazek else None, fields.get("vhodne i pro"))


def parse_detail(
    html: str, lead: Lead, places: PlaceResolver, region: str | None = None
) -> ParsedJob | None:
    """None for an ad that is no longer offered or a page without the ad template."""
    tree = LexborHTMLParser(html)
    if tree.css_first(".no-active-offer") is not None:
        return None
    # Employers with a branded ad page have the title in the content column.
    heading = tree.css_first(".ad-detail__headline h1") or tree.css_first(".ad-detail__content h1")
    title = node_value(heading)
    if not title:
        return None
    fields = info_fields(tree)

    texts = []
    for block in tree.css(".ad-detail__content__left-side .rich-text-content"):
        for h2 in block.css("h2"):
            if fold(node_value(h2) or "").startswith("informace o pracovnim"):
                h2.decompose()
        texts.append(node_text(block))

    workplace = fields.get("pracoviste")
    town = fields.get("lokalita")
    district = _DISTRICT.search(workplace or "")
    street = None
    if workplace and "," in workplace:
        street = workplace.split(",", 1)[1].strip()
    if not town and workplace:
        town = _DISTRICT.sub("", workplace.split(",", 1)[0]).strip()
    location = places.resolve(
        town, street=street, region=region, district=district[1].strip() if district else None
    )

    employer = fields.get("firma")
    job = ParsedJob(
        external_id=lead.external_id,
        title=title,
        url=lead.url,
        description="\n".join(
            line for t in texts for line in t.split("\n") if not _EMPTY_CONTACT.match(line)
        ),
        employer_name=employer.removesuffix(_UP_SUFFIX) if employer else None,
        place_text=workplace or town,
        location=location,
        contract_types=_contracts(fields),
        education=education_code(fields.get("min. vzdelani")),
        suitable_for=suitable_for(fields.get("vhodne i pro")),
    )
    set_salary(job, fields.get("mzda"))
    count = re.sub(r"\D", "", fields.get("pocet mist", ""))
    job.positions_count = int(count) if count else None
    start = fields.get("datum nastupu") or fields.get("termin")
    job.start_date = czech_date(start)
    if start and job.start_date is None:
        job.extra["start"] = start  # "Ihned", "Dle domluvy"
    posted = czech_date(fields.get("vlozeno"))
    job.posted_at = datetime(posted.year, posted.month, posted.day) if posted else None
    advertiser = fields.get("inzerent") or (
        "Úřad práce" if employer and employer.endswith(_UP_SUFFIX) else None
    )
    if advertiser:
        job.extra["advertiser"] = advertiser  # Firma / Agentura / Soukromá osoba / Úřad práce
    if fields.get("pozice"):
        job.extra["position"] = fields["pozice"]
    if fields.get("jazyk"):
        job.extra["languages"] = fields["jazyk"]
    return job
