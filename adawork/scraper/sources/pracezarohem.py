"""pracezarohem.cz (Práce za rohem, Alma Career): ~21k ads, 50 per region listing page.

Listing pages carry the ads as JSON (`__NEXT_DATA__`) with GPS coordinates, salary, the
employer's IČO and up to three occupation names, so a card alone is a useful job. Detail
pages (`/dl/jd/<id>`) add the text, contract type and education; only the portal's own ads
(`PZR…`) are read there. Ads re-listed from profesia (`PROF…`) and jobs.cz (`G2…`) stay
card-only: their full text is on those portals.
"""

import json
import re
from datetime import datetime

from adawork.core import ParsedJob, ParsedLocation
from adawork.geo.places import PlaceResolver
from adawork.normalize.text import (
    contract_types,
    education_code,
    html_to_text,
)
from adawork.scraper.crawl import CrawlSource, Lead, ListingPage, set_salary

SOURCE_ID = "pracezarohem"
BASE = "https://www.pracezarohem.cz"
PAGE_SIZE = 50
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
KRAJ_SLUGS = {
    "Hlavní město Praha": "hlavni-mesto-praha",
    "Středočeský kraj": "stredocesky",
    "Jihočeský kraj": "jihocesky",
    "Plzeňský kraj": "plzensky",
    "Karlovarský kraj": "karlovarsky",
    "Ústecký kraj": "ustecky",
    "Liberecký kraj": "liberecky",
    "Královéhradecký kraj": "kralovehradecky",
    "Pardubický kraj": "pardubicky",
    "Kraj Vysočina": "vysocina",
    "Jihomoravský kraj": "jihomoravsky",
    "Olomoucký kraj": "olomoucky",
    "Moravskoslezský kraj": "moravskoslezsky",
    "Zlínský kraj": "zlinsky",
}
OWN_ADS = ("PZR-", "PZRG-")
ICO = re.compile(r"^CZ(\d{8})\b")


def next_data(html: str) -> dict:
    m = NEXT_DATA.search(html)
    return json.loads(m.group(1))["props"]["pageProps"] if m else {}


def location(place: str | None, coords, label: str | None, places: PlaceResolver):
    """The portal's own coordinates, with the municipality the place name resolves to (kept
    without it when the name is unknown, so the job still shows on the map)."""
    loc = places.resolve(place) if place else None
    try:
        lat, lon = (float(c) for c in coords or ())
    except (TypeError, ValueError):
        return loc
    if loc is not None and loc.municipality_code is None:
        return loc  # a district or region: the coordinates are no more precise
    # "Poděbradská, Jirny" = a street, "Jirny" = the town (cards never say which).
    precision = "street" if "," in (label or "") else "municipality"
    code = loc.municipality_code if loc else "?"
    return ParsedLocation(
        key=f"geo:{code}:{precision}:{lat:.5f},{lon:.5f}",
        precision=precision,
        address_text=label or place,
        municipality=loc.municipality if loc else place,
        municipality_code=loc.municipality_code if loc else None,
        okres=loc.okres if loc else None,
        kraj=loc.kraj if loc else None,
        lat=lat,
        lon=lon,
    )


class PraceZaRohem(CrawlSource):
    id = SOURCE_ID
    label = "Práce za rohem"
    region_slugs = KRAJ_SLUGS

    def listing_url(self, region_slug: str, page: int) -> str:
        return f"{BASE}/nabidky/{region_slug}" + (f"?page={page}" if page > 1 else "")

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        data = next_data(html).get("adverts") or {}
        leads = []
        for ad in data.get("adverts") or ():
            ad_id = ad.get("id")
            if not ad_id or not ad.get("valid", True):
                continue
            url = f"{BASE}/dl/jd/{ad_id}"
            ga = ad.get("ga") or {}
            loc = ad.get("location") or {}
            ico = ICO.match(ga.get("itemBrand") or "")
            job = ParsedJob(
                external_id=ad_id,
                title=(ad.get("title") or "").strip(),
                url=url,
                employer_name=ad.get("company"),
                employer_ico=ico.group(1) if ico else None,
                place_text=loc.get("placeDetail") or loc.get("place"),
                location=location(loc.get("place"), loc.get("coords"), None, places),
                posted_at=_epoch(ad.get("validStart")),
            )
            occupations = [ga[k] for k in ("itemCategory2", "itemCategory3", "itemCategory4")
                           if ga.get(k)]  # fmt: skip
            if occupations:
                job.extra["occupations"] = occupations
            set_salary(job, ad.get("salary"))
            own = ad_id.startswith(OWN_ADS)
            leads.append(Lead(ad_id, url, url if own else None, job))
        found = data.get("numFound")
        # Without the count (a changed layout) read on until an empty page.
        more = page * PAGE_SIZE < found if isinstance(found, int) else bool(leads)
        return ListingPage(leads, has_next=more)

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedJob | None:
        ad = next_data(html).get("advert") or {}
        if ad.get("result") != "success" or not ad.get("title"):
            return None
        fields = ad.get("descFields") or {}
        props = {p.get("label"): p.get("value") for p in fields.get("jobProperties") or ()}
        sections = []
        for part in fields.get("jobText") or ():
            text = html_to_text(part.get("value"))
            if text:
                sections.append(f"{part['label']}\n{text}" if part.get("label") else text)
        loc = ad.get("location") or {}
        label = loc.get("label")
        town = label.rsplit(",", 1)[-1].strip() if label else None
        card = lead.card
        job = ParsedJob(
            external_id=lead.external_id,
            title=ad["title"].strip(),
            url=lead.url,
            description="\n\n".join(sections),
            employer_name=ad.get("company"),
            employer_ico=card.employer_ico if card else None,
            place_text=label,
            location=location(town, loc.get("coords"), label, places),
            contract_types=contract_types(props.get("Úvazek"), props.get("Smlouva")),
            education=education_code(props.get("Vzdělání")),
        )
        if props.get("Zařazené"):
            job.extra["occupations"] = [s.strip() for s in props["Zařazené"].split(",")]
        set_salary(job, ad.get("salaryDetailed") or ad.get("salary"))
        return job


def _epoch(value) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value))
    except (TypeError, ValueError, OverflowError, OSError):
        return None


SOURCE = PraceZaRohem()
