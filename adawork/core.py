"""What every source produces (a ParsedJob, independent of the source's own format), and the
values of the coded text columns of db/models.py."""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal, Protocol

# How exactly a location is known, from the house down to the region.
Precision = Literal["address", "street", "part", "municipality", "district", "region", "unknown"]
SalaryUnit = Literal["month", "hour", "piece"]
JobStatus = Literal["active", "expired"]  # also of a job's source
RunStatus = Literal["running", "ok", "partial", "failed", "blocked", "skipped"]
SourceKind = Literal["portal", "classifieds", "api", "social"]  # groups of the Sources page


def utcnow() -> datetime:
    """Naive UTC timestamp, the form SQLite stores."""
    return datetime.now(UTC).replace(tzinfo=None)


class Source(Protocol):
    """What every job source tells about itself; scraper/sources/__init__.py lists them all.
    When and how a source is read depends on its kind (scraper/runner.py)."""

    id: str
    label: str
    kind: SourceKind
    default_enabled: bool  # on until the user switches it off
    needs_consent: bool  # off until the user accepts a warning about the site's terms


@dataclass
class ParsedLocation:
    key: str  # stable dedup key, e.g. "ruian:18329012", "obec:537683", "okres:3210"
    precision: Precision
    address_text: str | None = None
    municipality: str | None = None
    municipality_code: str | None = None
    okres: str | None = None
    kraj: str | None = None
    lat: float | None = None
    lon: float | None = None


@dataclass
class ParsedJob:
    external_id: str
    title: str
    url: str | None = None
    description: str = ""
    employer_name: str | None = None
    employer_ico: str | None = None
    place_text: str | None = None
    location: ParsedLocation | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_unit: SalaryUnit | None = None
    salary_currency: str = "CZK"
    # Codes from adawork.normalize.vocab
    contract_types: list[str] = field(default_factory=list)
    shifts: list[str] = field(default_factory=list)
    education: str | None = None
    categories: list[str] = field(default_factory=list)
    suitable_for: list[str] = field(default_factory=list)
    benefits: list[str] = field(default_factory=list)
    isco_code: str | None = None
    positions_count: int | None = None
    start_date: date | None = None
    posted_at: datetime | None = None
    expires_at: datetime | None = None
    contact: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def content_hash(self) -> str:
        data = asdict(self)
        data.pop("external_id")
        raw = json.dumps(data, sort_keys=True, default=str, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class ScrapeQuery:
    """What to look for, from the wizard profile (see geo/scope.py)."""

    kraje: list[str]
    home: str | None = None  # municipality name
    radius_km: float = 50
    keywords: list[str] = field(default_factory=list)  # empty = every kind of work
