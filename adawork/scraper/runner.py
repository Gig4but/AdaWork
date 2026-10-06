"""Runs scraping in a background thread and reports progress to the UI."""

import logging
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from adawork.core import Source, utcnow
from adawork.db.models import Job, Run, SourceState
from adawork.net.http import make_client
from adawork.scraper.sources import SOURCES, mpsv
from adawork.scraper.store import JobStore

log = logging.getLogger(__name__)


@dataclass
class Progress:
    running: bool = False
    source_id: str | None = None
    phase: str = ""  # clear | download | import | done | failed
    cleared: bool = False  # this run deleted the jobs found before
    processed: int = 0
    message: str = ""
    results: dict[str, dict[str, int]] = field(default_factory=dict)


class ScrapeRunner:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        cache_dir: Path,
        client_factory=make_client,
        sources: Sequence[Source] = SOURCES,
    ) -> None:
        self.sessions = sessions
        self.cache_dir = cache_dir
        self.client_factory = client_factory
        self.sources = sources
        self.progress = Progress()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self, fresh: bool = False) -> bool:
        """Start a run in the background. Returns False if one is already running.

        `fresh`: the run first deletes the stored jobs and finds everything again.
        """
        with self._lock:
            if self.progress.running:
                return False
            self.progress = Progress(running=True, phase="clear" if fresh else "start")
            self._thread = threading.Thread(
                target=self._run_all, args=(fresh,), name="scrape", daemon=True
            )
            self._thread.start()
            return True

    def wait(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _run_all(self, clear: bool = False) -> None:
        try:
            if clear:
                self.clear_jobs()
                self.progress.cleared = True
            with self.client_factory() as client:
                for dump in self._enabled_of(mpsv.MpsvSource):
                    self.run_mpsv(dump, client)
            self.progress.phase = "done"
        except Exception as exc:  # reported in the UI and in the runs table
            log.exception("scrape failed")
            self.progress.phase = "failed"
            self.progress.message = str(exc)
        finally:
            self.progress.running = False

    @contextmanager
    def _record(
        self, session: Session, source_id: str, make_store: Callable[[], JobStore]
    ) -> Iterator[tuple[Run, JobStore]]:
        """The source's row in the runs table and its store: the row is finished on the way
        out, with the store's counts, or failed with the error the step raised."""
        run = Run(source_id=source_id, started_at=utcnow())
        session.add(run)
        session.commit()
        store = make_store()
        session.commit()  # ends the read transaction of loading the known jobs
        try:
            yield run, store
        except Exception as exc:
            session.rollback()
            run.status, run.error = "failed", str(exc)[:2000]
            raise
        finally:
            run.finished_at = utcnow()
            run.new_count = store.stats.new
            run.updated_count = store.stats.updated
            run.removed_count = store.stats.expired
            session.add(run)
            session.commit()
            self.progress.results[source_id] = store.stats.counts()

    def run_mpsv(self, source: mpsv.MpsvSource, client: httpx.Client) -> Run:
        p = self.progress
        p.source_id = source.id

        def progress(phase: str, n: int) -> None:
            p.phase, p.processed = phase, n

        with (
            self.sessions() as session,
            self._record(session, source.id, lambda: JobStore(session, source.id)) as (run, store),
        ):
            state = self._state(session, source.id)
            done = source.read(client, self.cache_dir / "mpsv", store, state.state or {}, progress)
            if done.skipped:
                run.status = "skipped"  # same file as last successful import
                return run
            if done.notes:
                log.warning("mpsv: %s", "; ".join(done.notes))
                run.error = "; ".join(done.notes)
            run.status = "ok"
            state.last_run_at = utcnow()
            state.state = (state.state or {}) | done.state
            return run

    def clear_jobs(self) -> None:
        """Deletes the found jobs, so they are all found again. Cached downloads stay."""
        with self.sessions() as session:
            session.execute(delete(Job))
            state = session.get(SourceState, mpsv.SOURCE_ID)
            if state is not None:  # else the unchanged dump would be skipped
                state.state = {k: v for k, v in (state.state or {}).items() if k != "dump"}
            session.commit()

    def _enabled_of[T: Source](self, kind: type[T]) -> list[T]:
        return [s for s in self.sources if isinstance(s, kind) and s.default_enabled]

    @staticmethod
    def _state(session: Session, source_id: str) -> SourceState:
        state = session.scalar(select(SourceState).where(SourceState.source_id == source_id))
        if state is None:
            state = SourceState(source_id=source_id)
            session.add(state)
        return state
