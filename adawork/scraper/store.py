"""Writes parsed jobs into the database: insert new, merge duplicates, update changed, expire.

One job can be offered by several sources (see scraper/dedup.py). The source that created
the job is its primary source and owns its fields; other sources only fill fields the job
lacks. A job expires when none of its sources offers it any more.
"""

from collections.abc import Collection, Iterable
from dataclasses import dataclass, fields
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, CursorResult, and_, case, exists, func, select, update
from sqlalchemy.orm import Session, aliased

from adawork.core import ParsedJob, ParsedLocation, utcnow
from adawork.db.models import Job, JobSource, Location, table
from adawork.normalize.vocab import derived_values
from adawork.scraper.dedup import DedupIndex, Entry

# Copied to the job's columns as they are; the rest goes to the source's row and the location.
_JOB_FIELDS = [
    f.name for f in fields(ParsedJob) if f.name not in ("external_id", "url", "location", "images")
]
# Filled from a secondary source when the job has no value. Salary fields move together.
_GAP_FIELDS = [
    "employer_ico",
    "contract_types",
    "shifts",
    "education",
    "categories",
    "suitable_for",
    "benefits",
    "isco_code",
    "positions_count",
    "start_date",
    "expires_at",
]
_SALARY_FIELDS = ["salary_min", "salary_max", "salary_unit", "salary_currency"]
SHORT_DESCRIPTION = 200  # a secondary source's longer description replaces a shorter one
PRECISION_RANK = {
    "address": 0,
    "street": 1,
    "part": 2,
    "municipality": 3,
    "district": 4,
    "region": 5,
    "unknown": 6,
}


def job_values(parsed: ParsedJob) -> dict:
    """Column values for a job: copied fields plus the derived filter fields."""
    values = {f: getattr(parsed, f) for f in _JOB_FIELDS}
    return values | derived_values(values)


def _empty(value) -> bool:
    return value is None or value == "" or value == [] or value == {}


@dataclass
class StoreStats:
    new: int = 0
    merged: int = 0  # new for this source, but already known from another one
    updated: int = 0
    unchanged: int = 0
    expired: int = 0

    def counts(self) -> dict[str, int]:
        """What the search progress shows per source."""
        return {
            "new": self.new,
            "merged": self.merged,
            "updated": self.updated,
            "removed": self.expired,
        }


@dataclass
class _Known:
    job_id: int  # -1: added in this run (its row id is not read back)
    digest: str | None
    is_primary: bool
    detail_at: datetime | None

    @property
    def has_detail(self) -> bool:
        return self.detail_at is not None


class JobStore:
    """Upserts one source's jobs. Built for large batches (MPSV has ~40k records)."""

    def __init__(
        self,
        session: Session,
        source_id: str,
        batch_size: int = 2000,
        dedup: DedupIndex | None = None,
    ) -> None:
        self.session = session
        self.source_id = source_id
        self.batch_size = batch_size
        self.stats = StoreStats()
        self.started_at = utcnow()
        rows = session.execute(
            select(
                JobSource.external_id,
                JobSource.job_id,
                JobSource.content_hash,
                JobSource.is_primary,
                JobSource.detail_at,
            ).where(JobSource.source_id == source_id)
        )
        self._known = {r[0]: _Known(r[1], r[2], r[3], r[4]) for r in rows}
        shared = select(JobSource.job_id).group_by(JobSource.job_id).having(func.count() > 1)
        self._shared_jobs = set(session.scalars(shared))  # jobs offered by several sources
        self.dedup = dedup if dedup is not None else DedupIndex.load(session)
        locations = session.execute(select(Location.key, Location.id, Location.precision))
        self._locations: dict[str, int] = {}
        self._precision: dict[int, str] = {}
        for key, loc_id, precision in locations:
            self._locations[key] = loc_id
            self._precision[loc_id] = precision
        self._pending = 0
        self._seen_external_ids: list[str] = []
        self._unchanged_ids: set[str] = set()
        self._new_entries: list[Entry] = []  # dedup entries whose job has no id yet

    # --- reading ----------------------------------------------------------------------------

    def knows(self, external_id: str) -> bool:
        return external_id in self._known

    def has_detail(self, external_id: str) -> bool:
        known = self._known.get(external_id)
        return bool(known and known.has_detail)

    def detail_older_than(self, external_id: str, when: datetime) -> bool:
        """The detail page was read before `when` (e.g. a sitemap's lastmod): read it again."""
        known = self._known.get(external_id)
        return bool(known and known.detail_at and known.detail_at < when)

    def detail_read_at(self, external_id: str) -> datetime:
        """When the detail page was last read (datetime.min: never)."""
        known = self._known.get(external_id)
        return (known.detail_at if known else None) or datetime.min

    # --- writing ----------------------------------------------------------------------------

    def add_all(self, jobs: Iterable[ParsedJob]) -> StoreStats:
        for job in jobs:
            self.add(job)
        self.commit()
        return self.stats

    def touch(self, external_id: str) -> None:
        """The source still offers this job, unchanged as far as we know."""
        if external_id not in self._unchanged_ids:
            self._unchanged_ids.add(external_id)
            self.stats.unchanged += 1
        self._seen_external_ids.append(external_id)
        self._count()

    def add(self, parsed: ParsedJob, detail: bool = True) -> None:
        """`detail`: the record is complete (False: only what a listing card shows)."""
        now = utcnow()
        digest = parsed.content_hash()
        known = self._known.get(parsed.external_id)

        if known is None:
            match = self.dedup.find(parsed, self.source_id)
            if match is not None:
                self._attach(match, parsed, digest, detail, now)
                self.stats.merged += 1
            else:
                self._create(parsed, digest, detail, now)
                self.stats.new += 1
            self._known[parsed.external_id] = _Known(
                -1, digest, match is None, now if detail else None
            )
        elif digest == known.digest:
            self.touch(parsed.external_id)
            if detail:  # read again (e.g. a newer sitemap lastmod) but unchanged
                known.detail_at = now
                self.session.execute(
                    update(JobSource)
                    .where(
                        JobSource.source_id == self.source_id,
                        JobSource.external_id == parsed.external_id,
                    )
                    .values(detail_at=now)
                )
            return
        elif known.job_id < 0:
            return  # a second, different copy in the same run (an API repeating an ad)
        else:
            self.stats.updated += 1
            if parsed.external_id in self._unchanged_ids:
                self._unchanged_ids.discard(parsed.external_id)
                self.stats.unchanged -= 1
            self._update(known, parsed, digest, detail, now)
            known.digest = digest
            if detail:
                known.detail_at = now
        self._count()

    def _count(self) -> None:
        self._pending += 1
        if self._pending >= self.batch_size:
            self.commit()

    def _source_row(self, parsed: ParsedJob, digest: str, detail: bool, now: datetime, primary):
        return JobSource(
            source_id=self.source_id,
            external_id=parsed.external_id,
            url=parsed.url,
            is_primary=primary,
            status="active",
            content_hash=digest,
            detail_at=now if detail else None,
            first_seen_at=now,
            last_seen_at=now,
        )

    def _create(self, parsed: ParsedJob, digest: str, detail: bool, now: datetime) -> None:
        job = Job(
            **job_values(parsed),
            location_id=self._location_id(parsed.location),
            content_hash=digest,
            status="active",
            first_seen_at=now,
            last_seen_at=now,
            updated_at=now,
        )
        job.sources.append(self._source_row(parsed, digest, detail, now, primary=True))
        self.session.add(job)
        entry = DedupIndex.entry_for(parsed, self.source_id)
        if entry is not None:
            entry.job = job
            self.dedup.add(entry)
            self._new_entries.append(entry)

    def _attach(
        self, match: Entry, parsed: ParsedJob, digest: str, detail: bool, now: datetime
    ) -> None:
        ref = match.ref
        row = self._source_row(parsed, digest, detail, now, primary=False)
        job = ref if isinstance(ref, Job) else self.session.get_one(Job, ref)
        job.sources.append(row)
        match.sources.add(self.source_id)
        if job.id is not None:
            self._shared_jobs.add(job.id)
        self._fill_gaps(job, parsed, now)

    def _fill_gaps(self, job: Job, parsed: ParsedJob, now: datetime) -> None:
        """A secondary source completes what the primary one left out."""
        changed = False
        for name in _GAP_FIELDS:
            new = getattr(parsed, name)
            if _empty(getattr(job, name)) and not _empty(new):
                setattr(job, name, new)
                changed = True
        no_salary = job.salary_min is None and job.salary_max is None
        if no_salary and (parsed.salary_min or parsed.salary_max):
            for name in _SALARY_FIELDS:
                setattr(job, name, getattr(parsed, name))
            changed = True
        if len(job.description or "") < SHORT_DESCRIPTION and len(parsed.description) > len(
            job.description or ""
        ):
            job.description = parsed.description
            changed = True
        if parsed.location is not None:
            current = (
                self._precision.get(job.location_id, "unknown") if job.location_id else "unknown"
            )
            if PRECISION_RANK.get(parsed.location.precision, 6) < PRECISION_RANK.get(current, 6):
                job.location_id = self._location_id(parsed.location)
                changed = True
        if changed:
            job.update_derived()
            job.updated_at = now
        job.status = "active"
        job.last_seen_at = now

    def _update(
        self, known: _Known, parsed: ParsedJob, digest: str, detail: bool, now: datetime
    ) -> None:
        source_values = {
            "last_seen_at": now,
            "url": parsed.url,
            "status": "active",
            "content_hash": digest,
        }
        if detail:
            source_values["detail_at"] = now
        self.session.execute(
            update(JobSource)
            .where(
                JobSource.source_id == self.source_id,
                JobSource.external_id == parsed.external_id,
            )
            .values(**source_values)
        )
        if not known.is_primary:
            job = self.session.get(Job, known.job_id)
            if job is not None:
                self._fill_gaps(job, parsed, now)
            return
        values = job_values(parsed)
        others = known.job_id in self._shared_jobs
        if others:  # keep what other sources filled in
            values = {k: v for k, v in values.items() if not _empty(v)}
        location_id = self._location_id(parsed.location)
        if location_id is not None or not others:
            values["location_id"] = location_id
        values |= {
            "content_hash": digest,
            "status": "active",
            "last_seen_at": now,
            "updated_at": now,
        }
        self.session.execute(update(Job).where(Job.id == known.job_id).values(**values))

    def commit(self) -> None:
        if self._seen_external_ids:
            now = utcnow()
            seen = select(JobSource.job_id).where(
                JobSource.source_id == self.source_id,
                JobSource.external_id.in_(self._seen_external_ids),
            )
            self.session.execute(
                update(Job)
                .where(Job.id.in_(seen))
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
                    JobSource.external_id.in_(self._seen_external_ids),
                )
                .values(last_seen_at=now, status="active")
                .execution_options(synchronize_session=False)
            )
            self._seen_external_ids.clear()
        self.session.flush()
        for entry in self._new_entries:
            _ = entry.ref  # swaps the flushed Job object for its id
        self._new_entries.clear()
        self.session.commit()
        self._pending = 0

    def expire_missing(self, kraje: Collection[str] | None = None) -> int:
        """After a complete pass over a source: what it no longer offers is gone from it.

        `kraje`: the pass covered only these regions; jobs placed in other regions stay.
        """
        return self._expire_seen_before(self.started_at, kraje)

    def expire_unseen(self, days: int) -> int:
        """For searches that show only the newest ads: unseen for `days` means gone."""
        return self._expire_seen_before(utcnow() - timedelta(days=days))

    def _expire_seen_before(self, cutoff: datetime, kraje: Collection[str] | None = None) -> int:
        self.commit()
        stale = [
            JobSource.source_id == self.source_id,
            JobSource.last_seen_at < cutoff,
            JobSource.status == "active",
        ]
        if kraje is not None:
            stale.append(_in_regions(kraje))
        self.session.execute(update(JobSource).where(*stale).values(status="expired"))
        active_source = exists().where(JobSource.job_id == Job.id, JobSource.status == "active")
        result = self.session.execute(
            update(Job)
            .where(Job.status == "active", ~active_source)
            .values(status="expired", updated_at=utcnow())
            .execution_options(synchronize_session=False)
        )
        assert isinstance(result, CursorResult)
        expired = result.rowcount or 0
        self.stats.expired += expired
        self._move_primary()
        self.session.commit()
        return expired

    def _move_primary(self) -> None:
        """A job whose primary source expired but another source still offers it."""
        other = aliased(JobSource)
        orphaned = select(JobSource.job_id).where(
            JobSource.is_primary,
            JobSource.status == "expired",
            exists().where(and_(other.job_id == JobSource.job_id, other.status == "active")),
        )
        job_ids = list(self.session.scalars(orphaned))
        if not job_ids:
            return
        heirs = (
            select(func.min(JobSource.id))
            .where(JobSource.job_id.in_(job_ids), JobSource.status == "active")
            .group_by(JobSource.job_id)
        )
        self.session.execute(
            update(JobSource)
            .where(JobSource.job_id.in_(job_ids))
            .values(is_primary=JobSource.id.in_(heirs))
            .execution_options(synchronize_session=False)
        )
        for external_id, is_primary in self.session.execute(
            select(JobSource.external_id, JobSource.is_primary).where(
                JobSource.job_id.in_(job_ids), JobSource.source_id == self.source_id
            )
        ):
            if external_id in self._known:
                self._known[external_id].is_primary = is_primary

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
            self._precision[row.id] = loc.precision
        return self._locations[loc.key]

    def mostly_unseen(self) -> bool:
        """Fewer than half of the jobs the source offered before this pass were seen in it.

        Then the dump or the pages are rather broken than so many jobs gone at once, so the
        caller expires nothing. A source with fewer than 20 jobs may lose them all.
        """
        self.commit()
        last_seen = self.session.scalars(
            select(JobSource.last_seen_at).where(
                JobSource.source_id == self.source_id,
                JobSource.first_seen_at < self.started_at,
                JobSource.status == "active",
            )
        ).all()
        unseen = sum(when < self.started_at for when in last_seen)
        return len(last_seen) >= 20 and unseen * 2 > len(last_seen)


def _in_regions(kraje: Collection[str]) -> ColumnElement[bool]:
    """Sources whose job is in one of `kraje` or has no region (a pass covering only those)."""
    elsewhere = (
        select(Job.id)
        .join(Location, Location.id == Job.location_id)
        .where(Location.kraj.is_not(None), Location.kraj.not_in(list(kraje)))
    )
    return JobSource.job_id.not_in(elsewhere)
