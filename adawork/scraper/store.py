"""Writes parsed jobs into the database: insert new, update changed, expire vanished."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import CursorResult, case, select, update
from sqlalchemy.orm import Session

from adawork.core import ParsedJob, ParsedLocation, utcnow
from adawork.db.models import Job, JobSource, Location, table
from adawork.normalize.vocab import EDUCATION_LEVEL, monthly_salary

_JOB_FIELDS = [
    "title",
    "description",
    "employer_name",
    "employer_ico",
    "place_text",
    "salary_min",
    "salary_max",
    "salary_unit",
    "salary_currency",
    "contract_types",
    "shifts",
    "education",
    "categories",
    "suitable_for",
    "benefits",
    "isco_code",
    "positions_count",
    "start_date",
    "posted_at",
    "expires_at",
    "contact",
    "extra",
]


def job_values(parsed: ParsedJob) -> dict:
    """Column values for a job: copied fields plus the derived filter fields."""
    values = {f: getattr(parsed, f) for f in _JOB_FIELDS}
    values["salary_month_min"] = monthly_salary(parsed.salary_min, parsed.salary_unit)
    values["salary_month_max"] = monthly_salary(parsed.salary_max, parsed.salary_unit)
    values["education_level"] = EDUCATION_LEVEL.get(parsed.education or "")
    return values


@dataclass
class StoreStats:
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    expired: int = 0

    def counts(self) -> dict[str, int]:
        """What the search progress shows per source."""
        return {"new": self.new, "updated": self.updated, "removed": self.expired}


class JobStore:
    """Upserts one source's jobs. Built for large batches (MPSV has ~40k records)."""

    def __init__(self, session: Session, source_id: str, batch_size: int = 2000) -> None:
        self.session = session
        self.source_id = source_id
        self.batch_size = batch_size
        self.stats = StoreStats()
        self.started_at = utcnow()
        rows = session.execute(
            select(JobSource.external_id, JobSource.job_id, Job.content_hash)
            .join(Job, Job.id == JobSource.job_id)
            .where(JobSource.source_id == source_id)
        )
        self._known = {ext: (job_id, h) for ext, job_id, h in rows}
        self._locations = dict(session.execute(select(Location.key, Location.id)).all())
        self._pending = 0
        self._unchanged_job_ids: list[int] = []
        self._unchanged_external_ids: list[str] = []

    def add_all(self, jobs: Iterable[ParsedJob]) -> StoreStats:
        for job in jobs:
            self.add(job)
        self.commit()
        return self.stats

    def commit(self) -> None:
        if self._unchanged_job_ids:
            now = utcnow()
            self.session.execute(
                update(Job)
                .where(Job.id.in_(self._unchanged_job_ids))
                .values(
                    last_seen_at=now,
                    status="active",
                    updated_at=case((Job.status == "active", Job.updated_at), else_=now),
                )
                .execution_options(synchronize_session=False)
            )
            self.session.execute(
                update(JobSource)
                .where(
                    JobSource.source_id == self.source_id,
                    JobSource.external_id.in_(self._unchanged_external_ids),
                )
                .values(last_seen_at=now)
                .execution_options(synchronize_session=False)
            )
            self._unchanged_job_ids.clear()
            self._unchanged_external_ids.clear()
        self.session.commit()
        self._pending = 0

    def add(self, parsed: ParsedJob) -> None:
        now = utcnow()
        digest = parsed.content_hash()
        known = self._known.get(parsed.external_id)
        location_id = self._location_id(parsed.location)

        if known is None:
            job = Job(
                **job_values(parsed),
                location_id=location_id,
                content_hash=digest,
                status="active",
                first_seen_at=now,
                last_seen_at=now,
                updated_at=now,
            )
            job.sources.append(
                JobSource(
                    source_id=self.source_id,
                    external_id=parsed.external_id,
                    url=parsed.url,
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
            self.session.add(job)
            self._known[parsed.external_id] = (-1, digest)  # guards duplicates within a run
            self.stats.new += 1
        else:
            job_id, old_digest = known
            if digest == old_digest:
                self.stats.unchanged += 1
                if job_id > 0:
                    self._unchanged_job_ids.append(job_id)
                    self._unchanged_external_ids.append(parsed.external_id)
            else:
                self.stats.updated += 1
                if job_id > 0:
                    self._update(job_id, parsed, digest, location_id, now)

        self._pending += 1
        if self._pending >= self.batch_size:
            self.commit()

    def _update(
        self, job_id: int, parsed: ParsedJob, digest: str, location_id: int | None, now: datetime
    ) -> None:
        values = job_values(parsed) | {
            "location_id": location_id,
            "content_hash": digest,
            "status": "active",
            "last_seen_at": now,
            "updated_at": now,
        }
        self.session.execute(update(Job).where(Job.id == job_id).values(**values))
        self.session.execute(
            update(JobSource)
            .where(
                JobSource.source_id == self.source_id,
                JobSource.external_id == parsed.external_id,
            )
            .values(last_seen_at=now, url=parsed.url)
        )

    def expire_missing(self) -> int:
        """For full-dump sources: jobs not seen in this run are no longer offered."""
        self.commit()
        stale = (
            select(JobSource.job_id)
            .where(JobSource.source_id == self.source_id)
            .where(JobSource.last_seen_at < self.started_at)
        )
        result = self.session.execute(
            update(Job)
            .where(Job.id.in_(stale), Job.status == "active")
            .values(status="expired", updated_at=utcnow())
        )
        assert isinstance(result, CursorResult)
        self.session.commit()
        self.stats.expired = result.rowcount or 0
        return self.stats.expired

    def _location_id(self, loc: ParsedLocation | None) -> int | None:
        if loc is None:
            return None
        if loc.key not in self._locations:
            row = Location(
                key=loc.key,
                precision=loc.precision,
                address_text=loc.address_text,
                municipality=loc.municipality,
                municipality_code=loc.municipality_code,
                okres=loc.okres,
                kraj=loc.kraj,
                lat=loc.lat,
                lon=loc.lon,
            )
            locations = table(Location)
            values = {c.key: getattr(row, c.key) for c in locations.c if c.key != "id"}
            row.id = self.session.connection().execute(locations.insert(), values).lastrowid
            self._locations[loc.key] = row.id
        return self._locations[loc.key]

    def mostly_unseen(self) -> bool:
        """Fewer than half of the jobs the source offered before this pass were seen in it.

        Then the dump or the pages are rather broken than so many jobs gone at once, so the
        caller expires nothing. A source with fewer than 20 jobs may lose them all.
        """
        self.commit()
        last_seen = self.session.scalars(
            select(JobSource.last_seen_at)
            .join(Job, Job.id == JobSource.job_id)
            .where(
                JobSource.source_id == self.source_id,
                JobSource.first_seen_at < self.started_at,
                Job.status == "active",
            )
        ).all()
        unseen = sum(when < self.started_at for when in last_seen)
        return len(last_seen) >= 20 and unseen * 2 > len(last_seen)
