"""A refresh checks that the stored ads are still offered, each on its own page:
- first the ads the user's list shows (`focus`: the filters it was last shown with), in list
  order, before the sources are searched for new ones (runner.py);
- after the search, every other ad not seen since it started.

An ad seen since `since` (in a listing or on its page) counts as checked, so a filter changed
meanwhile only adds what is not checked yet. Only portals' ads have pages to check: the Úřad
práce file and the APIs list all their ads at once, and social posts expire on their own.
"""

import logging
from collections.abc import Callable, Collection, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session, sessionmaker

from adawork.core import utcnow
from adawork.db.models import JobSource
from adawork.net.polite import PoliteFetcher, SourceBlocked, Stopped
from adawork.scraper.crawl import MAX_UNREACHABLE, CrawlSource, page_gone
from adawork.scraper.store import expire_ads, mark_seen
from adawork.search.service import Filters, in_list_order

log = logging.getLogger(__name__)

RECENT = timedelta(minutes=30)  # an ad seen this recently is not checked again
BATCH = 10  # ads picked at a time: the next pick follows the filters as they are then
MAX_THREADS = 8  # sources checked side by side (each host keeps its own polite pace)


@dataclass
class AdCheck:
    sessions: sessionmaker[Session]
    fetcher: PoliteFetcher
    writes: AbstractContextManager  # one database writer at a time (runner.py)
    since: datetime
    focus: Callable[[], Filters | None]
    progress: Callable[[str, int], object] = lambda source_id, checked: None

    def run(self, sources: Sequence[CrawlSource], everything: bool) -> int:
        """Checks the ads of `sources` side by side: those the list shows, and with
        `everything` also the rest. Returns how many jobs expired."""
        if not sources:
            return 0
        threads = min(len(sources), MAX_THREADS)
        with ThreadPoolExecutor(threads, thread_name_prefix="check") as pool:
            return sum(pool.map(lambda s: self.check_source(s, everything), sources))

    def check_source(self, source: CrawlSource, everything: bool) -> int:
        tried: set[str] = set()  # also pages that could not be read: not again in this run
        expired = checked = failures = 0
        try:
            while batch := self.next_ads(source.id, everything, tried):
                for external_id, url in batch:
                    tried.add(external_id)
                    try:
                        gone = page_gone(source, self.fetcher, url)
                        failures = 0
                    except httpx.HTTPError as exc:
                        log.warning("%s: cannot check %s: %s", source.id, url, exc)
                        failures += 1
                        if failures >= MAX_UNREACHABLE:
                            return expired  # the site is down: its ads are not known gone
                        continue
                    if gone is None:
                        continue
                    with self.writes, self.sessions() as session:
                        if gone:
                            expired += expire_ads(
                                session, source.id, utcnow(), external_ids=[external_id]
                            )[0]
                        else:
                            mark_seen(session, source.id, [external_id])
                        session.commit()
                    checked += 1
                    self.progress(source.id, checked)
        except SourceBlocked as exc:
            log.warning("%s blocked while checking ads: %s", source.id, exc)
        except Stopped:
            pass
        return expired

    def next_ads(self, source_id: str, everything: bool, tried: set[str]) -> list[tuple[str, str]]:
        """(external_id, url) of the next ads to check: those the list shows first."""
        due = [*due_for_check(self.since), JobSource.source_id == source_id]
        due.append(JobSource.external_id.not_in(tried))
        columns = [JobSource.external_id, JobSource.url]
        focus = self.focus()
        with self.sessions() as session:
            rows = in_list_order(session, focus, columns, due, BATCH) if focus else []
            if not rows and everything:
                stmt = select(*columns).where(*due).order_by(JobSource.last_seen_at)
                rows = session.execute(stmt.limit(BATCH)).all()
        return [(row.external_id, row.url) for row in rows if row.url]


def due_for_check(since: datetime) -> list[ColumnElement[bool]]:
    """A source's ad not seen since `since`, with a page to check."""
    return [
        JobSource.status == "active",
        JobSource.url.is_not(None),
        JobSource.last_seen_at < since,
    ]


def unchecked(
    session: Session, source_ids: Collection[str], since: datetime, job_ids: Collection[int]
) -> set[int]:
    """Those of the jobs with an ad of `source_ids` not checked yet."""
    stmt = select(JobSource.job_id).where(
        *due_for_check(since),
        JobSource.source_id.in_(list(source_ids)),
        JobSource.job_id.in_(list(job_ids)),
    )
    return set(session.scalars(stmt))
