"""Saved searches (PLAN §7.2): named filters whose new matches are counted on the search page
and announced after an automatic check."""

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from starlette.datastructures import QueryParams

from adawork.db.models import Job, SavedSearch
from adawork.search.service import Filters, count_between, filters_from

MAX_SAVED = 50


def filters_of(saved: SavedSearch) -> Filters:
    return filters_from(QueryParams(saved.query))


def newest_job_id(session: Session) -> int:
    return session.scalar(select(func.max(Job.id))) or 0


def mark_all_seen(session: Session) -> None:
    """After a run that cleared the list: what it found again is not new. Ids of deleted jobs
    are given out again, so the old `seen_up_to` could be above every new job."""
    session.execute(update(SavedSearch).values(seen_up_to=newest_job_id(session)))
    session.commit()


def matches_after(session: Session, f: Filters, after: int) -> int:
    """Jobs matching the filters that were stored after the job `after`."""
    newest = newest_job_id(session)
    return count_between(session, f, after, newest) if newest > after else 0


def with_new_counts(session: Session) -> list[tuple[SavedSearch, int]]:
    """Every saved search with its matches the user has not opened it for yet."""
    saved = session.scalars(select(SavedSearch).order_by(SavedSearch.name, SavedSearch.id))
    return [(s, matches_after(session, filters_of(s), s.seen_up_to)) for s in saved]
