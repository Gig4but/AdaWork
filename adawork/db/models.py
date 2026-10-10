"""SQLite schema. Changes go through Alembic migrations in db/migrations/versions."""

from datetime import date, datetime
from typing import Any, cast

from sqlalchemy import JSON, ForeignKey, Index, String, Table, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from adawork.core import JobStatus, Precision, RunStatus, SalaryUnit
from adawork.normalize.vocab import derived_values


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[str]: JSON}


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


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    description: Mapped[str] = mapped_column(default="")
    employer_name: Mapped[str | None]
    employer_ico: Mapped[str | None] = mapped_column(String(8), index=True)
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

    location: Mapped[Location | None] = relationship()
    sources: Mapped[list["JobSource"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
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
