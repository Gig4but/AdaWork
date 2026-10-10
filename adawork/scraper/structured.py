"""schema.org JobPosting from any page: JSON-LD first, then microdata (PLAN §4.4 step 3).

`find_job_postings` returns plain dicts in JSON-LD shape whichever syntax the page used, and
`posting_to_job` maps one onto ParsedJob. Portals differ only in discovery and quirks.
"""

import json
import re
from datetime import datetime
from typing import Any

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adawork.core import ParsedJob, ParsedLocation
from adawork.geo.places import PlaceResolver
from adawork.normalize.text import clean_spaces, contract_types, fold, html_to_text

_HTML_VALUE_TAGS = {"div", "section", "article", "ul", "ol", "td"}


def _is_job_posting(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    kind = item.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return any(isinstance(k, str) and k.rsplit("/", 1)[-1] == "JobPosting" for k in kinds)


def _walk_json(data: Any):
    if isinstance(data, list):
        for item in data:
            yield from _walk_json(item)
    elif isinstance(data, dict):
        if _is_job_posting(data):
            yield data
        elif "@graph" in data:
            yield from _walk_json(data["@graph"])


def _load_json(text: str) -> Any:
    text = text.strip().removeprefix("<!--").removesuffix("-->").strip()
    try:
        return json.loads(text, strict=False)  # strict=False: raw newlines inside strings
    except json.JSONDecodeError:
        return None


def _microdata_value(node: LexborNode) -> Any:
    attrs = node.attributes
    if "itemscope" in attrs:
        return _microdata_item(node)
    for attr in ("content", "datetime"):
        if attrs.get(attr):
            return attrs[attr]
    if node.tag in ("a", "link") and attrs.get("href"):
        return attrs["href"]
    if node.tag in _HTML_VALUE_TAGS:
        return node.html  # descriptions keep their structure for html_to_text
    return clean_spaces(node.text())


def _microdata_item(scope: LexborNode) -> dict[str, Any]:
    item: dict[str, Any] = {}
    itemtype = scope.attributes.get("itemtype")
    if itemtype:
        item["@type"] = itemtype.rsplit("/", 1)[-1]

    def walk(node: LexborNode) -> None:
        for child in node.iter():
            prop = child.attributes.get("itemprop")
            if prop:
                value = _microdata_value(child)
                for name in prop.split():
                    if name in item:
                        old = item[name]
                        item[name] = [*old, value] if isinstance(old, list) else [old, value]
                    else:
                        item[name] = value
                if "itemscope" in child.attributes:
                    continue  # its children belong to the nested item
                if child.tag in _HTML_VALUE_TAGS:
                    continue
            elif "itemscope" in child.attributes:
                continue  # an unrelated item
            walk(child)

    walk(scope)
    return item


def find_job_postings(html: str | LexborHTMLParser) -> list[dict[str, Any]]:
    tree = LexborHTMLParser(html) if isinstance(html, str) else html
    found: list[dict[str, Any]] = []
    for script in tree.css('script[type="application/ld+json"]'):
        found.extend(_walk_json(_load_json(script.text())))
    if not found:
        for node in tree.css("[itemscope][itemtype]"):
            if (node.attributes.get("itemtype") or "").rstrip("/").endswith("JobPosting"):
                found.append(_microdata_item(node))
    return found


# --- mapping onto ParsedJob -----------------------------------------------------------------

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?")


def parse_date(value: Any) -> datetime | None:
    """Lenient: prace.cz sends "2026-11-01T23:59:59+01:00T23:59"."""
    if not isinstance(value, str):
        return None
    m = _DATE.search(value)
    if not m:
        return None
    try:
        parsed = datetime.fromisoformat(m[0].replace(" ", "T"))
    except ValueError:
        return None
    return parsed if parsed.year < 2100 else None  # "9999-12-31" means "until filled"


def _first(value: Any) -> Any:
    return value[0] if isinstance(value, list) and value else value


def _text(value: Any) -> str | None:
    value = _first(value)
    if isinstance(value, dict):
        value = value.get("name") or value.get("value")
    if value is None:
        return None
    text = clean_spaces(str(value))
    return text or None


def _number(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        return round(value) or None
    if isinstance(value, str):
        digits = re.sub(r"[^\d.,]", "", value)
        if re.fullmatch(r"\d{1,3}([.,])\d{3}(\1\d{3})*", digits):
            digits = re.sub(r"[.,]", "", digits)
        elif "," in digits and "." in digits:
            decimal = max(",.", key=digits.rfind)
            digits = digits.replace("." if decimal == "," else ",", "")
        digits = digits.replace(",", ".")
        try:
            return round(float(digits)) or None
        except ValueError:
            return None
    return None


# schema.org unitText -> (our unit, multiplier to that unit)
_SALARY_UNITS = {
    "HOUR": ("hour", 1),
    "DAY": ("month", 21.7),
    "WEEK": ("month", 4.33),
    "MONTH": ("month", 1),
    "YEAR": ("month", 1 / 12),
}


def salary_fields(base: Any) -> dict[str, Any]:
    base = _first(base)
    if not isinstance(base, dict):
        return {}
    value = base.get("value")
    unit_text = base.get("unitText")
    if isinstance(value, dict):
        unit_text = value.get("unitText") or unit_text
        low = _number(value.get("minValue")) or _number(value.get("value"))
        high = _number(value.get("maxValue"))
    else:
        low, high = _number(value), None
    if not low and not high:
        return {}
    unit, factor = _SALARY_UNITS.get(str(unit_text or "MONTH").upper(), ("month", 1))
    low = round(low * factor) if low else None
    high = round(high * factor) if high else None
    if low and high == low:
        high = None
    return {
        "salary_min": low,
        "salary_max": high,
        "salary_unit": unit,
        "salary_currency": str(base.get("currency") or "CZK").upper(),
    }


def addresses(posting: dict[str, Any]) -> list[dict[str, str] | str]:
    """jobLocation as a list of address dicts (or plain strings, as profesia sends)."""
    places = posting.get("jobLocation") or []
    if not isinstance(places, list):
        places = [places]
    result: list[dict[str, str] | str] = []
    for place in places:
        address = place.get("address") if isinstance(place, dict) else place
        if isinstance(address, list):
            address = address[0] if address else None
        if isinstance(address, dict):
            values = {k: _text(v) for k, v in address.items()}
            result.append({k: v for k, v in values.items() if v})
        elif isinstance(address, str) and address.strip():
            result.append(clean_spaces(address))
    return result


def resolve_address(address: dict[str, str] | str, places: PlaceResolver) -> ParsedLocation | None:
    if isinstance(address, str):
        return places.resolve(address.split(",")[0])
    country = fold(address.get("addressCountry", "cz"))
    if country not in ("cz", "cze", "czech republic", "ceska republika", "czechia"):
        return None
    return places.resolve(
        address.get("addressLocality"),
        street=address.get("streetAddress"),
        postal_code=address.get("postalCode"),
        region=address.get("addressRegion"),
    )


def place_text(address: dict[str, str] | str) -> str | None:
    if isinstance(address, str):
        return address
    return address.get("addressLocality") or address.get("addressRegion")


_EDUCATION_WORDS = (
    ("doctoral", ("doktor", "phd", "doctor")),
    ("master", ("magist", "master", "postgraduate")),
    ("bachelor", ("bakal", "bachelor")),
    ("higher_vocational", ("vyssi odborn", "associate")),
    ("maturita", ("maturit", "high school")),
    ("vocational", ("vyuc", "bez maturity", "professional certificate")),
    ("basic", ("zakladni",)),
)


def education_code(value: Any) -> str | None:
    texts = []
    for item in value if isinstance(value, list) else [value]:
        if isinstance(item, dict):
            item = item.get("credentialCategory") or item.get("name")
        if isinstance(item, str):
            texts.append(fold(item))
    joined = " ".join(texts)
    # The lowest level named is the requirement ("SŠ nebo VŠ" means SŠ is enough).
    for code, words in reversed(_EDUCATION_WORDS):
        if any(w in joined for w in words):
            return code
    return None


def posting_to_job(
    posting: dict[str, Any],
    external_id: str,
    url: str | None,
    places: PlaceResolver,
) -> ParsedJob:
    org = posting.get("hiringOrganization")
    all_addresses = addresses(posting)
    location = next((loc for a in all_addresses if (loc := resolve_address(a, places))), None)
    names = [p for a in all_addresses if (p := place_text(a))]
    benefits = _text(posting.get("jobBenefits"))
    job = ParsedJob(
        external_id=external_id,
        title=_text(posting.get("title")) or "",
        url=url,
        description=html_to_text(_text_or_html(posting.get("description"))),
        employer_name=_text(org),
        place_text=", ".join(dict.fromkeys(names)) or None,
        location=location,
        contract_types=contract_types(*_strings(posting.get("employmentType"))),
        education=education_code(posting.get("educationRequirements")),
        posted_at=parse_date(_first(posting.get("datePosted"))),
        expires_at=parse_date(_first(posting.get("validThrough"))),
        **salary_fields(posting.get("baseSalary")),
    )
    if benefits:
        job.extra["benefits_text"] = benefits
    if posting.get("jobLocationType") == "TELECOMMUTE":
        job.extra["remote"] = True
    return job


def _strings(value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value]
    return [str(v) for v in items if v]


def _text_or_html(value: Any) -> str | None:
    value = _first(value)
    return value if isinstance(value, str) else None
