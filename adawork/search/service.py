"""Job search: full text (FTS5), filters on normalized fields, facet counts, sorting."""

import itertools
import re
import time
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Literal, cast

from sqlalchemy import (
    ColumnElement,
    Select,
    and_,
    case,
    event,
    exists,
    func,
    literal,
    literal_column,
    or_,
    select,
    table,
    text,
)
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session, selectinload

from adawork.core import utcnow
from adawork.db.models import Job, JobSource, Location, SourceState
from adawork.normalize.text import accented_words, fold
from adawork.normalize.vocab import EDUCATION_LEVEL
from adawork.search import kinds

# Column weights for bm25: title, employer_name, place_text, description.
_BM25 = "bm25(jobs_fts, 10.0, 3.0, 2.0, 1.0)"
_TOKEN = re.compile(r"\w+", re.UNICODE)

Sort = Literal["relevance", "date", "salary"]
LIST_FIELDS = ("categories", "contract_types", "shifts", "suitable_for", "benefits")
FACETS = (*LIST_FIELDS, "kraj", "education", "sources")
BATCHES = "batches"  # SourceState row: the last job id stored before each search
MAX_BATCHES = 50


def fts_query(user_text: str) -> str | None:
    """Turn free text into a safe FTS5 query: every word of a phrase must match, as a
    prefix; phrases separated by commas are alternatives.

    "skladník Brno" -> '"skladník"* AND "brno"*', "skladník, řidič" -> '("skladník"*) OR
    ("řidič"*)'. Quotes neutralize FTS5 operators.
    """
    queries = [q for q in map(_phrase_query, user_text.split(",")) if q]
    if len(queries) <= 1:
        return queries[0] if queries else None
    return " OR ".join(f"({q})" for q in queries)


def _phrase_query(phrase: str) -> str | None:
    tokens = [t.lower() for t in _TOKEN.findall(phrase)]
    return " AND ".join(f'"{t}"*' for t in tokens) if tokens else None


@dataclass(frozen=True)
class TextMatch:
    """What the search text matches: `fts` in the full-text index (also the rank), each
    phrase's own query with its words typed with diacritics, and the occupations the
    profile's kinds of work were mapped to (their ads match without sharing a word)."""

    fts: str | None = None
    phrases: tuple[tuple[str, str], ...] = ()
    codes: tuple[str, ...] = ()

    @property
    def params(self) -> dict[str, str]:
        return {"match": self.fts} if self.fts else {}


def text_match(session: Session, q: str) -> TextMatch:
    fts = fts_query(q)
    if fts is None:
        return TextMatch()
    parts = [(p, query) for p in q.split(",") if (query := _phrase_query(p))]
    mapped = {fold(k): v.get("codes", ()) for k, v in kinds.load(session).items()}
    codes = (c for p, _ in parts for c in mapped.get(fold(p.strip()), ()))
    phrases = tuple((query, " ".join(accented_words(p))) for p, query in parts)
    return TextMatch(fts, phrases, tuple(dict.fromkeys(codes)))


@dataclass
class Filters:
    q: str = ""
    categories: list[str] = field(default_factory=list)
    contract_types: list[str] = field(default_factory=list)
    shifts: list[str] = field(default_factory=list)
    suitable_for: list[str] = field(default_factory=list)
    benefits: list[str] = field(default_factory=list)
    kraj: list[str] = field(default_factory=list)
    sources_off: list[str] = field(default_factory=list)  # sources the user switched off
    education: str | None = None  # the user's education: show jobs that need at most this
    salary_min: int | None = None  # monthly CZK; jobs whose pay can reach this amount
    with_salary: bool = False
    posted_days: int | None = None
    flagged: bool = False  # also show offers with red flags (hidden by default)
    sort: Sort = "relevance"

    def is_filtered(self) -> bool:
        return replace(self, sort="relevance") != Filters()

    def new_first(self) -> bool:
        """The last search's finds are listed above the jobs found before: in the order
        found, or by rank when there are words to rank by."""
        return self.sort == "relevance"


@dataclass
class SearchResult:
    total: int
    jobs: list[Job]
    took_ms: float
    facets: dict[str, list[tuple[str, int]]] = field(default_factory=dict)
    flagged: int = 0  # matching offers hidden for their red flags (with facets=True)


def _any_of(column, values: list[str]) -> ColumnElement[bool]:
    """A JSON list column contains at least one of the values."""
    items = func.json_each(column).table_valued("value")
    return exists(select(literal(1)).select_from(items).where(items.c.value.in_(values)))


def _from_sources_on(off: list[str]) -> ColumnElement[bool]:
    """The job is offered by at least one source that is not switched off."""
    return exists(
        select(literal(1)).where(
            JobSource.job_id == Job.id,
            JobSource.status == "active",
            JobSource.source_id.not_in(off),
        )
    )


def _conditions(f: Filters, skip: frozenset[str] = frozenset()) -> list[ColumnElement[bool]]:
    """WHERE conditions for the filters; `skip` leaves facet dimensions out."""
    conds: list[ColumnElement[bool]] = [
        Job.status == "active",
        Job.is_offer.is_(True),
        Job.hidden_at.is_(None),
    ]
    if not f.flagged:
        conds.append(func.json_array_length(Job.red_flags) == 0)
    for name in LIST_FIELDS:
        values = getattr(f, name)
        if values and name not in skip:
            conds.append(_any_of(getattr(Job, name), values))
    if f.kraj and "kraj" not in skip:
        conds.append(Location.kraj.in_(f.kraj))
    if f.sources_off and "sources" not in skip:
        conds.append(_from_sources_on(f.sources_off))
    if f.education and "education" not in skip and f.education in EDUCATION_LEVEL:
        level = EDUCATION_LEVEL[f.education]
        conds.append(or_(Job.education_level.is_(None), Job.education_level <= level))
    if f.salary_min:
        conds.append(func.coalesce(Job.salary_month_max, Job.salary_month_min) >= f.salary_min)
    elif f.with_salary:
        conds.append(Job.salary_month_min.is_not(None))
    if f.posted_days:
        conds.append(Job.posted_at >= utcnow() - timedelta(days=f.posted_days))
    return conds


def _base(f: Filters, match: TextMatch, columns, skip: frozenset[str] = frozenset()):
    stmt: Select = select(*columns).select_from(Job).outerjoin(Location)
    hits = None
    if match.fts:
        hits = (
            select(literal_column("rowid").label("job_id"), literal_column(_BM25).label("rank"))
            .select_from(table("jobs_fts"))
            .where(text("jobs_fts MATCH :match"))
            .subquery()
        )
        if match.codes:
            stmt = stmt.outerjoin(hits, hits.c.job_id == Job.id)
            occupation = or_(*(Job.isco_code.startswith(c, autoescape=True) for c in match.codes))
            stmt = stmt.where(or_(and_(hits.c.job_id.is_not(None), *_accents(match)), occupation))
        else:
            stmt = stmt.join(hits, hits.c.job_id == Job.id).where(*_accents(match))
    return stmt.where(and_(*_conditions(f, skip))), hits


def _accents(match: TextMatch) -> list[ColumnElement[bool]]:
    """The index ignores diacritics, so "řidič"* also finds "řídící"; a word typed with
    diacritics must also match with them, in the phrase that found the job."""
    if not any(accented for _, accented in match.phrases):
        return []
    haystack = Job.title + " " + func.coalesce(Job.description, "")

    def check(accented: str) -> ColumnElement[bool]:
        return func.has_word_starts(haystack, accented) == 1

    if len(match.phrases) == 1:
        return [check(match.phrases[0][1])]
    either = []
    for n, (query, accented) in enumerate(match.phrases):
        ids = (
            select(literal_column("rowid"))
            .select_from(table("jobs_fts"))
            .where(text(f"jobs_fts MATCH :phrase{n}").bindparams(**{f"phrase{n}": query}))
        )
        either.append(and_(Job.id.in_(ids), check(accented)) if accented else Job.id.in_(ids))
    return [or_(*either)]


_FACET_COLUMNS = (*LIST_FIELDS, "kraj", "education", "sources")
# Per-database cache of every active job's facet values, already decoded from JSON.
# Rebuilt only when the jobs table changes (after an import), so a facet request needs
# just the matching ids from SQL plus one Python pass (~10x faster than decoding each time).
_facet_cache: dict[tuple[int, str], tuple[tuple, dict[int, tuple]]] = {}
# Commits of this process that changed jobs. updated_at alone misses a change made within
# one tick of the clock (~16 ms on Windows); it still tells changes made by another process.
_job_commits = itertools.count(1)
_last_job_commit = 0


@event.listens_for(Session, "after_flush")
def _jobs_flushed(session: Session, _context) -> None:
    changed = (*session.new, *session.dirty, *session.deleted)
    if any(isinstance(o, (Job, JobSource)) for o in changed):
        session.info["jobs_changed"] = True


@event.listens_for(Session, "do_orm_execute")
def _jobs_written(state) -> None:
    if (state.is_update or state.is_delete or state.is_insert) and state.bind_mapper in (
        Job.__mapper__,
        JobSource.__mapper__,
    ):
        state.session.info["jobs_changed"] = True


@event.listens_for(Session, "after_commit")
def _jobs_committed(session: Session) -> None:
    global _last_job_commit
    if session.info.pop("jobs_changed", False):
        _last_job_commit = next(_job_commits)


@event.listens_for(Session, "after_rollback")
def _jobs_rolled_back(session: Session) -> None:
    session.info.pop("jobs_changed", None)


@event.listens_for(Session, "after_begin")
def _transaction_began(session: Session, _transaction, _connection) -> None:
    # What this transaction reads includes every job commit counted so far (maybe more).
    session.info["job_commits_seen"] = _last_job_commit


def _facet_index(session: Session) -> dict[int, tuple]:
    totals = session.execute(
        select(func.count(Job.id), func.total(Job.id), func.max(Job.updated_at)).where(
            Job.status == "active"
        )
    ).one()
    version = (session.info.get("job_commits_seen"), *totals)
    bind = session.get_bind()
    key = (id(bind), str(bind.engine.url))
    cached = _facet_cache.get(key)
    if cached and cached[0] == version:
        return cached[1]
    level_code = {lvl: code for code, lvl in EDUCATION_LEVEL.items()}
    columns = [getattr(Job, n) for n in LIST_FIELDS] + [Location.kraj, Job.education_level]
    rows = session.execute(
        select(Job.id, *columns).outerjoin(Location).where(Job.status == "active")
    )
    sources: dict[int, set[str]] = {}
    for job_id, source_id in session.execute(
        select(JobSource.job_id, JobSource.source_id)
        .join(Job)
        .where(Job.status == "active", JobSource.status == "active")
    ):
        sources.setdefault(job_id, set()).add(source_id)
    index = {
        row[0]: (
            *(frozenset(cast(list[str], v) or ()) for v in row[1 : 1 + len(LIST_FIELDS)]),
            row[-2],
            level_code.get(row[-1]),
            frozenset(sources.get(row[0], ())),
        )
        for row in rows
    }
    _facet_cache[key] = (version, index)
    return index


def _facet_counts(session: Session, f: Filters, match: TextMatch) -> dict:
    """Counts per facet value. Each facet ignores its own selection, so choosing one
    category still shows how many jobs the other categories have.
    """
    index = _facet_index(session)
    stmt, _ = _base(f, match, [Job.id], skip=frozenset(FACETS))
    ids = session.scalars(stmt, match.params)

    level = EDUCATION_LEVEL.get(f.education or "")
    selected = {n: frozenset(getattr(f, n)) for n in (*LIST_FIELDS, "kraj")}
    checks = []  # (position, test) for each facet with an active selection
    for pos, name in enumerate(FACETS):
        if name == "education":
            if level is not None:
                checks.append((pos, lambda v: v is None or EDUCATION_LEVEL[v] <= level))
        elif name == "sources":
            if f.sources_off:
                checks.append((pos, lambda v, off=frozenset(f.sources_off): not v <= off))
        elif selected[name]:
            wanted = selected[name]
            if name in LIST_FIELDS:
                checks.append((pos, lambda v, w=wanted: not w.isdisjoint(v)))
            else:
                checks.append((pos, lambda v, w=wanted: v in w))

    counts: list[dict[str, int]] = [{} for _ in FACETS]
    for job_id in ids:
        values = index.get(job_id)
        if values is None:
            continue
        failing = -1
        for pos, test in checks:
            if not test(values[pos]):
                if failing >= 0:
                    break  # fails two selections: counts nowhere
                failing = pos
        else:
            positions = range(len(FACETS)) if failing < 0 else (failing,)
            for pos in positions:
                bucket = counts[pos]
                value = values[pos]
                for item in value if isinstance(value, frozenset) else (value,):
                    if item is not None:
                        bucket[item] = bucket.get(item, 0) + 1
    return {
        name: sorted(c.items(), key=lambda kv: -kv[1])
        for name, c in zip(FACETS, counts, strict=True)
    }


def facet_counts(session: Session, f: Filters) -> tuple[dict, int]:
    """Counts per facet value, and how many matching offers are hidden for their red flags."""
    match = text_match(session, f.q)
    flagged = 0
    if not f.flagged:
        stmt, _ = _base(replace(f, flagged=True), match, [func.count()])
        stmt = stmt.where(func.json_array_length(Job.red_flags) > 0)
        flagged = session.scalar(stmt, match.params) or 0
    return _facet_counts(session, f, match), flagged


def batch_starts(session: Session) -> list[int]:
    row = session.get(SourceState, BATCHES)
    return list((row.state or {}).get("starts", [])) if row else []


def start_batch(session: Session, last_id: int | None = None) -> None:
    """Jobs stored from now on are listed above the ones before, in the order found.
    `last_id`: the last job id kept (after a clear, ids continue from it)."""
    cleared = last_id is not None
    if last_id is None:
        last_id = session.scalar(select(func.max(Job.id))) or 0
    row = session.get(SourceState, BATCHES) or SourceState(source_id=BATCHES)
    starts = [b for b in batch_starts(session) if b < last_id] + [last_id]
    row.state = {"starts": starts[-MAX_BATCHES:], "cleared": cleared}
    session.add(row)
    session.commit()


def new_after(session: Session) -> int | None:
    """Jobs stored after this id are new: the last search found them. None when that search
    found everything (the first one, or one that started over)."""
    row = session.get(SourceState, BATCHES)
    state = (row.state or {}) if row else {}
    starts = state.get("starts") or [0]
    return None if state.get("cleared") or not starts[-1] else starts[-1]


def _batch_order(session: Session) -> tuple:
    """The last search's finds first, then those of the search before, …"""
    starts = batch_starts(session)
    batch = case(*((Job.id > b, i) for i, b in reversed(list(enumerate(starts, 1)))), else_=0)
    return (batch.desc(),) if starts else ()


def search_jobs(
    session: Session,
    q: str | Filters = "",
    limit: int = 30,
    offset: int = 0,
    facets: bool = False,
    up_to_id: int | None = None,
) -> SearchResult:
    """`up_to_id`: only jobs stored up to this one (the rest is shown by `jobs_found_since`)."""
    started = time.perf_counter()
    f = q if isinstance(q, Filters) else Filters(q=q)
    match = text_match(session, f.q)
    params = match.params
    known = [Job.id <= up_to_id] if up_to_id is not None else []

    count_stmt, _ = _base(f, match, [func.count()])
    total = session.scalar(count_stmt.where(*known), params) or 0

    stmt = _sorted(session, f, match).where(*known)
    stmt = stmt.options(
        selectinload(Job.sources), selectinload(Job.location), selectinload(Job.images)
    )
    jobs = list(session.scalars(stmt.limit(limit).offset(offset), params))
    result = SearchResult(total, jobs, 0.0)
    if facets:
        result.facets, result.flagged = facet_counts(session, f)
    result.took_ms = (time.perf_counter() - started) * 1000
    return result


def _sorted(session: Session, f: Filters, match: TextMatch) -> Select:
    """The matching jobs in the order the list shows them."""
    newest = (Job.posted_at.desc().nulls_last(), Job.id.desc())
    stmt, hits = _base(f, match, [Job])
    if f.sort == "salary":
        salary = func.coalesce(Job.salary_month_max, Job.salary_month_min)
        return stmt.order_by(salary.desc().nulls_last(), *newest)
    if f.sort == "relevance" and hits is not None:
        return stmt.order_by(*_batch_order(session), hits.c.rank.nulls_last(), Job.id.desc())
    if f.sort == "relevance":
        return stmt.order_by(*_batch_order(session), Job.id)
    return stmt.order_by(*newest)


def in_list_order(
    session: Session, f: Filters, columns, where: list[ColumnElement[bool]], limit: int
) -> list[Row]:
    """`columns` of the matching jobs' sources that meet `where`, in list order."""
    match = text_match(session, f.q)
    stmt = (
        _sorted(session, f, match)
        .with_only_columns(*columns)
        .join(JobSource, JobSource.job_id == Job.id)
        .where(*where)
        .limit(limit)
    )
    return list(session.execute(stmt, match.params))


def jobs_found_since(
    session: Session, f: Filters, since: int, up_to_id: int, limit: int, skip=()
) -> tuple[list[Job], int]:
    """(Matching jobs stored after `since` up to `up_to_id` in the order found, without the
    `skip` ids; how many jobs match in all.) Lets the list add a running search's finds."""
    match = text_match(session, f.q)
    params = match.params
    count_stmt, _ = _base(f, match, [func.count()])
    total = session.scalar(count_stmt.where(Job.id <= up_to_id), params) or 0
    stmt, _ = _base(f, match, [Job])
    stmt = (
        stmt.where(Job.id > since, Job.id <= up_to_id, Job.id.not_in(skip))
        .order_by(Job.id)
        .options(selectinload(Job.sources), selectinload(Job.location), selectinload(Job.images))
        .limit(limit)
    )
    return list(session.scalars(stmt, params)), total


def still_matching(session: Session, f: Filters, ids: list[int]) -> set[int]:
    """Those of the jobs that still match the filters (the others expired, were merged or
    hidden)."""
    match = text_match(session, f.q)
    stmt, _ = _base(f, match, [Job.id])
    return set(session.scalars(stmt.where(Job.id.in_(ids)), match.params))


def count_between(session: Session, f: Filters, after: int, up_to_id: int) -> int:
    match = text_match(session, f.q)
    stmt, _ = _base(f, match, [func.count()])
    stmt = stmt.where(Job.id > after, Job.id <= up_to_id)
    return session.scalar(stmt, match.params) or 0


def job_points(session: Session, f: Filters) -> list[tuple]:
    """(location id, lat, lon, precision, job count) for every location with matching jobs."""
    match = text_match(session, f.q)
    params = match.params
    columns = [
        Location.id,
        Location.lat,
        Location.lon,
        Location.precision,
        func.count(Job.id),
    ]
    stmt, _ = _base(f, match, columns)
    stmt = stmt.where(Location.lat.is_not(None)).group_by(Location.id)
    return [tuple(row) for row in session.execute(stmt, params)]


def jobs_at_location(
    session: Session, f: Filters, location_id: int, limit: int = 50
) -> tuple[list[Job], int]:
    match = text_match(session, f.q)
    params = match.params
    count_stmt, _ = _base(f, match, [func.count()])
    total = session.scalar(count_stmt.where(Job.location_id == location_id), params) or 0
    stmt, _ = _base(f, match, [Job])
    stmt = (
        stmt.where(Job.location_id == location_id)
        .order_by(Job.posted_at.desc().nulls_last(), Job.id.desc())
        .options(selectinload(Job.sources), selectinload(Job.location))
        .limit(limit)
    )
    return list(session.scalars(stmt, params)), total
