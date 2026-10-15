"""SQLite schema. Changes go through Alembic migrations in db/migrations/versions."""

from datetime import date, datetime
from typing import Any, cast

from sqlalchemy import JSON, ForeignKey, Index, String, Table, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from adawork.core import ImageStatus, JobStatus, Precision, RunStatus, SalaryUnit
from adawork.normalize.vocab import derived_values


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[str]: JSON, list[dict[str, Any]]: JSON}


def table(model: type[Base]) -> Table:
    """A model's table, for statements that bypass the ORM (bulk updates, fast inserts)."""
    return cast(Table, model.__table__)


class Location(Base):
    """A place shared by all jobs at the same spot, so travel times are computed once."""

    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    # "ruian:<address code>", "addr:<obec>:<street>:<number>", "part:<obec>:<part>",
    # "obec:<code>", "okres:<codes>", "kraj:<name>"
    key: Mapped[str] = mapped_column(String(200), unique=True)
    lat: Mapped[float | None]
    lon: Mapped[float | None]
    # address | street | part | municipality | district | region | unknown
    precision: Mapped[Precision] = mapped_column(String(20), default="unknown")
    address_text: Mapped[str | None]
    municipality: Mapped[str | None]
    municipality_code: Mapped[str | None] = mapped_column(String(12))
    okres: Mapped[str | None]
    kraj: Mapped[str | None]


class TravelSet(Base):
    """Travel times from one start point, leaving at one time, by one mode (or "any": the
    fastest); the times refer to it by a small number, which keeps the filter fast."""

    __tablename__ = "travel_sets"
    __table_args__ = (UniqueConstraint("origin", "depart", "mode"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    origin: Mapped[str] = mapped_column(String(24))  # "lat,lon", 4 decimals
    depart: Mapped[str] = mapped_column(String(5))  # "07:00" on a weekday
    mode: Mapped[str] = mapped_column(String(8))  # car | transit | walk | bike | any


class TravelTime(Base):
    """Minutes to a location (PLAN §4.6), computed up to two hours ahead, so moving the
    travel-time slider only filters. Stored by key only (WITHOUT ROWID): one lookup per job."""

    __tablename__ = "travel_times"
    __table_args__ = {"sqlite_with_rowid": False}

    set_id: Mapped[int] = mapped_column(ForeignKey("travel_sets.id"), primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"), primary_key=True)
    minutes: Mapped[int | None]  # None: not within two hours
    version: Mapped[int] = mapped_column(default=0)  # routing data version; 0 = an estimate


class Municipality(Base):
    """All Czech municipalities with a centre point (median of RÚIAN address points)."""

    __tablename__ = "municipalities"

    code: Mapped[str] = mapped_column(String(12), primary_key=True)
    name: Mapped[str]
    name_normalized: Mapped[str] = mapped_column(index=True)  # lowercase, no diacritics
    okres_code: Mapped[str | None] = mapped_column(String(8), index=True)
    okres: Mapped[str | None]
    kraj: Mapped[str | None]
    lat: Mapped[float]
    lon: Mapped[float]
    address_count: Mapped[int] = mapped_column(default=0)
    postal_codes: Mapped[str] = mapped_column(default="")  # space-separated PSČ, e.g. "14000 14200"


class Employer(Base):
    """A company behind ads (PLAN §4.5): one row per IČO, or per name that could not be
    resolved (no IČO; `candidates` lists the companies it may be)."""

    __tablename__ = "employers"

    id: Mapped[int] = mapped_column(primary_key=True)
    ico: Mapped[str | None] = mapped_column(String(8), unique=True)
    name: Mapped[str]
    # How the ads' name became this IČO: ad (the IČO was in an ad) | brand | agency | ares |
    # ai | manual; uncertain (several candidates) | none (nothing found)
    match: Mapped[str] = mapped_column(String(10), default="ad")
    confidence: Mapped[float | None]
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(default=list)  # {"ico", "name"...}
    # From ARES and RES
    dic: Mapped[str | None] = mapped_column(String(14))
    legal_form: Mapped[str | None] = mapped_column(String(3))
    founded: Mapped[date | None]
    ended: Mapped[date | None]
    address: Mapped[str | None]
    nace_main: Mapped[str | None] = mapped_column(String(6))
    employee_band: Mapped[str | None] = mapped_column(String(3))
    # Official checks (`warnings` lists the codes for the filter: ended, insolvency, vat,
    # unlicensed): proceedings from ISIR, unreliable VAT payer since, agency licence.
    insolvency: Mapped[list[dict[str, Any]]] = mapped_column(default=list)
    vat_unreliable: Mapped[str | None] = mapped_column(String(20))
    agency: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # {"temp", "until"}
    looks_agency: Mapped[bool] = mapped_column(default=False)  # its ads speak of agency work
    warnings: Mapped[list[str]] = mapped_column(default=list)
    checked_at: Mapped[datetime | None] = mapped_column(index=True)
    # Reviews (`satisfaction`: Atmoskop's employer %, for the filter and sorting)
    satisfaction: Mapped[int | None] = mapped_column(index=True)
    links: Mapped[dict[str, Any]] = mapped_column(default=dict)  # atmoskop, indeed, facebook...
    reviews_at: Mapped[datetime | None]
    mentions_at: Mapped[datetime | None]
    jobs_count: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime]


class EmployerName(Base):
    """An employer name as ads write it (names.name_key) -> the employer it was resolved to.
    The learned cache: each name goes to ARES only once."""

    __tablename__ = "employer_names"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    employer_id: Mapped[int | None] = mapped_column(
        ForeignKey("employers.id", ondelete="SET NULL"), index=True
    )
    source: Mapped[str] = mapped_column(String(10))  # ad | brand | agency | ares | ai | manual
    resolved_at: Mapped[datetime]


class EmployerReview(Base):
    """Ratings and review texts from one source (atmoskop: employees in %, google: customers
    in stars)."""

    __tablename__ = "employer_reviews"
    __table_args__ = (UniqueConstraint("employer_id", "source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    employer_id: Mapped[int] = mapped_column(ForeignKey("employers.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(10))
    rating: Mapped[float | None]
    scale: Mapped[str] = mapped_column(String(8))  # percent | stars5
    count: Mapped[int] = mapped_column(default=0)
    scores: Mapped[dict[str, Any]] = mapped_column(default=dict)
    texts: Mapped[list[dict[str, Any]]] = mapped_column(default=list)  # as the source gives
    url: Mapped[str | None]
    fetched_at: Mapped[datetime]


class EmployerMention(Base):
    """A web page that speaks about working for the employer (the Facebook page is only a
    link, `Employer.links`)."""

    __tablename__ = "employer_mentions"

    id: Mapped[int] = mapped_column(primary_key=True)
    employer_id: Mapped[int] = mapped_column(
        ForeignKey("employers.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(10), default="web")  # web: found by web search
    url: Mapped[str]
    title: Mapped[str] = mapped_column(default="")
    snippet: Mapped[str] = mapped_column(default="")
    passages: Mapped[list[str]] = mapped_column(default=list)
    fetched_at: Mapped[datetime]


class EmployerSummary(Base):
    """The AI summary of the reviews and mentions; written again when they change."""

    __tablename__ = "employer_summaries"

    employer_id: Mapped[int] = mapped_column(
        ForeignKey("employers.id", ondelete="CASCADE"), primary_key=True
    )
    summary: Mapped[dict[str, Any]] = mapped_column(default=dict)
    sources: Mapped[list[dict[str, Any]]] = mapped_column(default=list)  # {"source", "url"}
    inputs: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(40))
    prompt_version: Mapped[int]
    created_at: Mapped[datetime]


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    description: Mapped[str] = mapped_column(default="")
    employer_name: Mapped[str | None]
    employer_ico: Mapped[str | None] = mapped_column(String(8), index=True)
    employer_id: Mapped[int | None] = mapped_column(
        ForeignKey("employers.id", ondelete="SET NULL"), index=True
    )
    place_text: Mapped[str | None]
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"), index=True)

    salary_min: Mapped[int | None]
    salary_max: Mapped[int | None]
    salary_unit: Mapped[SalaryUnit | None] = mapped_column(String(10))
    salary_currency: Mapped[str] = mapped_column(String(3), default="CZK")
    # Monthly CZK equivalents, for filtering and sorting across hourly and monthly pay.
    salary_month_min: Mapped[int | None] = mapped_column(index=True)
    salary_month_max: Mapped[int | None]

    # Codes from adawork.normalize.vocab
    contract_types: Mapped[list[str]] = mapped_column(default=list)
    shifts: Mapped[list[str]] = mapped_column(default=list)
    education: Mapped[str | None]
    education_level: Mapped[int | None] = mapped_column(index=True)
    categories: Mapped[list[str]] = mapped_column(default=list)
    suitable_for: Mapped[list[str]] = mapped_column(default=list)
    benefits: Mapped[list[str]] = mapped_column(default=list)
    isco_code: Mapped[str | None] = mapped_column(String(6), index=True)
    positions_count: Mapped[int | None]
    start_date: Mapped[date | None]
    posted_at: Mapped[datetime | None] = mapped_column(index=True)
    expires_at: Mapped[datetime | None]
    contact: Mapped[dict[str, Any]] = mapped_column(default=dict)
    extra: Mapped[dict[str, Any]] = mapped_column(default=dict)  # source-specific leftovers

    status: Mapped[JobStatus] = mapped_column(String(10), default="active", index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    first_seen_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    # Enrichment (PLAN §6): the content_hash last processed by the rules (ISCO from similar
    # titles) and by the AI model. Different from content_hash = not processed yet.
    rules_hash: Mapped[str | None] = mapped_column(String(64))
    ai_hash: Mapped[str | None] = mapped_column(String(64))
    # Free-text posts (classifieds, social): False for people looking for work, services,
    # loans and sales (normalize/offers.py). Kept, so they are not read again, but never listed.
    is_offer: Mapped[bool] = mapped_column(default=True, index=True)
    red_flags: Mapped[list[str]] = mapped_column(default=list)  # vocab.RED_FLAGS codes
    hidden_at: Mapped[datetime | None] = mapped_column(index=True)  # hidden from the list

    location: Mapped[Location | None] = relationship()
    employer: Mapped[Employer | None] = relationship()
    sources: Mapped[list["JobSource"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    images: Mapped[list["JobImage"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobImage.position"
    )

    def update_derived(self) -> None:
        """Recomputes the monthly pay and the education level from the fields they come from."""
        values = {
            "salary_min": self.salary_min,
            "salary_max": self.salary_max,
            "salary_unit": self.salary_unit,
            "education": self.education,
        }
        for name, value in derived_values(values).items():
            setattr(self, name, value)


class JobSource(Base):
    """Where a job was seen. One job can come from several portals (see scraper/dedup.py).

    The primary source owns the job's fields; the others only fill fields it leaves empty.
    A job expires when none of its sources offers it any more.
    """

    __tablename__ = "job_sources"
    __table_args__ = (UniqueConstraint("source_id", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(200))
    url: Mapped[str | None]
    is_primary: Mapped[bool] = mapped_column(default=True)
    status: Mapped[JobStatus] = mapped_column(String(10), default="active")
    content_hash: Mapped[str | None] = mapped_column(String(64))  # of this source's version
    detail_at: Mapped[datetime | None]  # when the detail page was read; None = listing only
    first_seen_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]

    job: Mapped[Job] = relationship(back_populates="sources")


class JobImage(Base):
    """A picture attached to an ad (poster, photo of a notice, social post image), PLAN §6.8.

    The file is kept in the image cache; the vision model writes out its text, which then
    goes through the same extraction as any ad text. `phash` finds the same picture posted
    again elsewhere.
    """

    __tablename__ = "job_images"
    __table_args__ = (UniqueConstraint("job_id", "url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    url: Mapped[str]
    position: Mapped[int] = mapped_column(default=0)
    status: Mapped[ImageStatus] = mapped_column(String(10), default="new", index=True)
    file: Mapped[str | None]  # file name in the image cache
    phash: Mapped[str | None] = mapped_column(String(16), index=True)  # 64-bit dHash, hex
    width: Mapped[int | None]
    height: Mapped[int | None]
    text: Mapped[str | None]  # written out by the vision model; "" = no text in the image
    text_model: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime]

    job: Mapped[Job] = relationship(back_populates="images")


class SourceState(Base):
    __tablename__ = "sources"

    source_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    etag: Mapped[str | None]
    last_modified: Mapped[str | None]
    last_run_at: Mapped[datetime | None]
    state: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (Index("ix_runs_source_started", "source_id", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(String(40))
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]
    status: Mapped[RunStatus] = mapped_column(String(12), default="running")
    new_count: Mapped[int] = mapped_column(default=0)
    updated_count: Mapped[int] = mapped_column(default=0)
    removed_count: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None]


class SavedSearch(Base):
    """Filters the user saved by name; new matches are counted and can be notified."""

    __tablename__ = "saved_searches"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    query: Mapped[str]  # the search page's query string
    notify: Mapped[bool] = mapped_column(default=True)
    seen_up_to: Mapped[int] = mapped_column(default=0)  # jobs after this id are new to the user
    created_at: Mapped[datetime]


class LlmCache(Base):
    """Model answers by input, so an unchanged ad (or one seen on several portals) is read by
    the model only once. The key covers the model, the prompt version and the input."""

    __tablename__ = "llm_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    model: Mapped[str] = mapped_column(String(40))
    prompt_version: Mapped[int]
    result: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime]
