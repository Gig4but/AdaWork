"""Writes parsed jobs into the database: insert new, merge duplicates, update changed, expire.

One job can be offered by several sources (see scraper/dedup.py). The source that created
the job is its primary source and owns its fields; other sources only fill fields the job
lacks. A job expires when none of its sources offers it any more.
"""

import threading
from collections.abc import Collection, Iterable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta

from sqlalchemy import (
    ColumnElement,
    CursorResult,
    and_,
    case,
    exists,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.orm import Session, aliased, object_session

from adawork.core import ParsedJob, ParsedLocation, utcnow
from adawork.db.models import Job, JobImage, JobSource, Location
from adawork.normalize.offers import red_flags
from adawork.normalize.vocab import derived_values
from adawork.scraper.dedup import DedupIndex, Entry

# Copied to the job's columns as they are; the rest goes to the source's row, the location
# and the pictures.
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
    return values | derived_values(values) | offer_values(parsed)


def offer_values(parsed: ParsedJob) -> dict:
    """A source marks a post that is not a job offer with extra["not_offer"]."""
    return {
        "is_offer": not parsed.extra.get("not_offer"),
        "red_flags": red_flags(parsed.title, parsed.description),
    }


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
        write_lock: AbstractContextManager | None = None,
    ) -> None:
        # Sources read in parallel write one at a time, in short transactions: SQLite allows
        # one writer, and a transaction left open while a page downloads would block the rest.
        self.lock = write_lock or threading.RLock()
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

    @contextmanager
    def writing(self) -> Iterator[None]:
        """Holds the write lock and commits at the end."""
        with self.lock:
            yield
            self.commit()

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
            with self.dedup.lock:
                match = self.dedup.find(parsed, self.source_id)
                ref = match.ref if match is not None else None
                if isinstance(ref, Job) and object_session(ref) is not self.session:
                    match = None  # another source's job, not saved yet: no id to join
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
        self._add_images(job, parsed.images, set(), now)
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
        if parsed.images:
            self._add_images(job, parsed.images, {i.url for i in job.images}, now)
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
        if parsed.images:
            job = self.session.get(Job, known.job_id)
            if job is not None:
                self._add_images(job, parsed.images, {i.url for i in job.images}, now)

    @staticmethod
    def _add_images(job: Job, urls: list[str], have: set[str], now: datetime) -> None:
        """New pictures of an ad; ones already stored are kept with their text."""
        position = len(have)
        for url in dict.fromkeys(urls):
            if url not in have:
                job.images.append(JobImage(url=url, position=position, created_at=now))
                position += 1

    def commit(self) -> None:
        if self._seen_external_ids:
            mark_seen(self.session, self.source_id, self._seen_external_ids)
            self._seen_external_ids.clear()
        self.session.flush()
        with self.dedup.lock:
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

    def unseen_for(self, days: int, limit: int) -> list[tuple[str, str]]:
        """(external_id, url) of active ads unseen for `days`, the longest unseen first."""
        rows = self.session.execute(
            select(JobSource.external_id, JobSource.url)
            .where(
                JobSource.source_id == self.source_id,
                JobSource.status == "active",
                JobSource.last_seen_at < utcnow() - timedelta(days=days),
                JobSource.url.is_not(None),
            )
            .order_by(JobSource.last_seen_at)
            .limit(limit)
        )
        return [(external_id, url) for external_id, url in rows if url]

    def expire_gone(self, external_ids: Collection[str]) -> int:
        """Ads whose own page says they are gone (the listings were not read to the end)."""
        if not external_ids:
            return 0
        return self._expire_seen_before(utcnow(), external_ids=external_ids)

    def _expire_seen_before(
        self,
        cutoff: datetime,
        kraje: Collection[str] | None = None,
        external_ids: Collection[str] | None = None,
    ) -> int:
        self.commit()
        expired, moved = expire_ads(self.session, self.source_id, cutoff, kraje, external_ids)
        self.stats.expired += expired
        for external_id, is_primary in self.session.execute(
            select(JobSource.external_id, JobSource.is_primary).where(
                JobSource.job_id.in_(moved), JobSource.source_id == self.source_id
            )
        ):
            if external_id in self._known:
                self._known[external_id].is_primary = is_primary
        self.session.commit()
        return expired

    def _location_id(self, loc: ParsedLocation | None) -> int | None:
        if loc is None:
            return None
        if loc.key not in self._locations:
            # Another source read in parallel may have added this place since we loaded ours.
            found = (
                self.session.connection()
                .execute(select(Location.id, Location.precision).where(Location.key == loc.key))
                .first()
            )
            if found is not None:
                self._locations[loc.key], self._precision[found.id] = found.id, found.precision
                return found.id
            insert_row = insert(Location).values(asdict(loc))
            loc_id = self.session.connection().execute(insert_row).lastrowid
            self._locations[loc.key], self._precision[loc_id] = loc_id, loc.precision
        return self._locations[loc.key]

    def mostly_unseen(self, kraje: Collection[str] | None = None) -> bool:
        """Fewer than half of the jobs the source offered before this pass were seen in it.

        Then the dump or the pages are rather broken than so many jobs gone at once, so the
        caller expires nothing. A source with fewer than 20 jobs may lose them all.
        `kraje`: the pass covered only these regions, as for `expire_missing`.
        """
        self.commit()
        offered = [
            JobSource.source_id == self.source_id,
            JobSource.first_seen_at < self.started_at,
            JobSource.status == "active",
        ]
        if kraje is not None:
            offered.append(_in_regions(kraje))
        last_seen = self.session.scalars(select(JobSource.last_seen_at).where(*offered)).all()
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


def mark_seen(session: Session, source_id: str, external_ids: Collection[str]) -> None:
    """The source still offers these ads (also an ad that had expired): seen now."""
    now = utcnow()
    ids = list(external_ids)
    seen = select(JobSource.job_id).where(
        JobSource.source_id == source_id, JobSource.external_id.in_(ids)
    )
    session.execute(
        update(Job)
        .where(Job.id.in_(seen))
        .values(
            last_seen_at=now,
            status="active",
            updated_at=case((Job.status == "active", Job.updated_at), else_=now),
        )
        .execution_options(synchronize_session=False)
    )
    session.execute(
        update(JobSource)
        .where(JobSource.source_id == source_id, JobSource.external_id.in_(ids))
        .values(last_seen_at=now, status="active")
        .execution_options(synchronize_session=False)
    )


def expire_ads(
    session: Session,
    source_id: str,
    cutoff: datetime,
    kraje: Collection[str] | None = None,
    external_ids: Collection[str] | None = None,
) -> tuple[int, list[int]]:
    """The source's ads last seen before `cutoff` (only in `kraje`, or only `external_ids`,
    when given) are no longer offered by it; a job no source offers any more expires.
    (Jobs expired, ids of jobs whose primary source moved to another.) Not committed."""
    stale = [
        JobSource.source_id == source_id,
        JobSource.last_seen_at < cutoff,
        JobSource.status == "active",
    ]
    if external_ids is not None:
        stale.append(JobSource.external_id.in_(list(external_ids)))
    if kraje is not None:
        stale.append(_in_regions(kraje))
    session.execute(update(JobSource).where(*stale).values(status="expired"))
    active_source = exists().where(JobSource.job_id == Job.id, JobSource.status == "active")
    result = session.execute(
        update(Job)
        .where(Job.status == "active", ~active_source)
        .values(status="expired", updated_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    assert isinstance(result, CursorResult)
    return result.rowcount or 0, _move_primary(session)


def _move_primary(session: Session) -> list[int]:
    """Jobs whose primary source expired but another source still offers them: the other
    one becomes primary. Returns their ids."""
    other = aliased(JobSource)
    orphaned = select(JobSource.job_id).where(
        JobSource.is_primary,
        JobSource.status == "expired",
        exists().where(and_(other.job_id == JobSource.job_id, other.status == "active")),
    )
    job_ids = list(session.scalars(orphaned))
    if not job_ids:
        return []
    heirs = (
        select(func.min(JobSource.id))
        .where(JobSource.job_id.in_(job_ids), JobSource.status == "active")
        .group_by(JobSource.job_id)
    )
    session.execute(
        update(JobSource)
        .where(JobSource.job_id.in_(job_ids))
        .values(is_primary=JobSource.id.in_(heirs))
        .execution_options(synchronize_session=False)
    )
    return job_ids
