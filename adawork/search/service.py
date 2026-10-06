"""Full-text job search over the FTS5 index."""

import re
import time
from dataclasses import dataclass

from sqlalchemy import func, literal_column, select, table, text
from sqlalchemy.orm import Session, selectinload

from adawork.db.models import Job

# Column weights for bm25: title, employer_name, place_text, description.
_BM25 = "bm25(jobs_fts, 10.0, 3.0, 2.0, 1.0)"
_TOKEN = re.compile(r"\w+", re.UNICODE)


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


@dataclass
class SearchResult:
    total: int
    jobs: list[Job]
    took_ms: float


def search_jobs(
    session: Session, q: str = "", limit: int = 30, offset: int = 0, up_to_id: int | None = None
) -> SearchResult:
    """`up_to_id`: only jobs stored up to this one (the rest is shown by `jobs_found_since`)."""
    started = time.perf_counter()
    match = fts_query(q)
    base = select(Job).where(Job.status == "active")
    if up_to_id is not None:
        base = base.where(Job.id <= up_to_id)

    if match is None:  # without words to rank by, the jobs found last come first
        total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
        stmt = base.order_by(Job.id.desc())
    else:
        hits = (
            select(literal_column("rowid").label("job_id"), literal_column(_BM25).label("rank"))
            .select_from(table("jobs_fts"))
            .where(text("jobs_fts MATCH :match"))
            .subquery()
        )
        base = base.join(hits, hits.c.job_id == Job.id)
        total = (
            session.scalar(select(func.count()).select_from(base.subquery()), {"match": match}) or 0
        )
        stmt = base.order_by(hits.c.rank)

    stmt = stmt.options(selectinload(Job.sources)).limit(limit).offset(offset)
    jobs = list(session.scalars(stmt, {"match": match} if match else {}))
    return SearchResult(total, jobs, (time.perf_counter() - started) * 1000)


def jobs_found_since(
    session: Session, since: int, up_to_id: int, limit: int
) -> tuple[list[Job], int]:
    """(Active jobs stored after `since` up to `up_to_id`, the last found first; how many
    active jobs there are in all.) Lets the list put a running search's finds on top of it."""
    active = select(Job).where(Job.status == "active", Job.id <= up_to_id)
    total = session.scalar(select(func.count()).select_from(active.subquery())) or 0
    stmt = (
        active.where(Job.id > since)
        .order_by(Job.id.desc())
        .options(selectinload(Job.sources))
        .limit(limit)
    )
    return list(session.scalars(stmt)), total
