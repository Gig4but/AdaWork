"""Runs scraping in a background thread and reports progress to the UI."""

import logging
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from adawork.config import Profile
from adawork.core import Source, utcnow
from adawork.db.models import Job, Municipality, Run, SourceState
from adawork.geo import ruian
from adawork.geo.geocode import geocode_locations, rebuild_municipalities
from adawork.geo.places import PlaceResolver
from adawork.geo.scope import crawl_kraje
from adawork.net.http import make_client
from adawork.net.polite import PoliteFetcher, Stopped
from adawork.scraper.crawl import MAX_DETAILS, CrawlSource, crawl
from adawork.scraper.dedup import DedupIndex
from adawork.scraper.sources import LABELS, SOURCES, mpsv
from adawork.scraper.store import JobStore

log = logging.getLogger(__name__)

BLOCKED_PAUSE = timedelta(hours=12)  # a source that refused us is left alone this long
SCOPE_ID = "search"  # SourceState row: what the stored jobs were searched for


@dataclass
class Progress:
    running: bool = False
    source_id: str | None = None
    phase: str = ""  # clear | download | import | listing | details | geocode | done | failed
    cleared: bool = False  # this run deleted the jobs found before
    processed: int = 0
    message: str = ""
    results: dict[str, dict[str, int]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)  # source id -> reason

    @property
    def source_label(self) -> str:
        return LABELS.get(self.source_id or "", self.source_id or "")

    def totals(self) -> dict[str, int]:
        keys = ("new", "merged", "updated", "removed")
        return {k: sum(r.get(k, 0) for r in self.results.values()) for k in keys}

    def error_labels(self) -> str:
        return ", ".join(LABELS.get(s, s) for s in self.errors)


class ScrapeRunner:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        cache_dir: Path,
        client_factory=make_client,
        geocode: bool = True,
        profile: Callable[[], Profile | None] | None = None,
        sources: Sequence[Source] = SOURCES,
        fetcher_factory=PoliteFetcher,
        max_details: int = MAX_DETAILS,
    ) -> None:
        self.sessions = sessions
        self.cache_dir = cache_dir
        self.client_factory = client_factory
        self.geocode = geocode
        self.profile = profile or (lambda: None)
        self.sources = sources
        self.fetcher_factory = fetcher_factory
        self.max_details = max_details
        self.progress = Progress()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()  # the app closes: every site stops at its next page

    def start(self, fresh: bool = False) -> bool:
        """Start a run in the background. Returns False if one is already running.

        `fresh`, or a search scope other than the stored jobs were found for: the run first
        deletes them and finds everything again.
        """
        with self._lock:
            if self.progress.running:
                return False
            scope = self.scope()
            with self.sessions() as session:
                state = self._state(session, SCOPE_ID)
                before = (state.state or {}).get("scope")
                state.state = {"scope": scope}
                session.commit()
            clear = fresh or (before is not None and before != scope)
            self.progress = Progress(running=True, phase="clear" if clear else "start")
            self._stopping.clear()
            self._thread = threading.Thread(
                target=self._run_all, args=(clear,), name="scrape", daemon=True
            )
            self._thread.start()
            return True

    def scope(self) -> dict:
        """What decides which jobs a run finds."""
        profile = self.profile()
        return {"profile": profile.model_dump() if profile else None}

    def wait(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def stop(self, timeout: float = 10) -> bool:
        """Ends a running search at its sites' next requests; what it found so far stays.
        False when it still runs after `timeout` seconds (a download that cannot stop)."""
        self._stopping.set()
        self.wait(timeout)
        return not (self._thread and self._thread.is_alive())

    def _run_all(self, clear: bool = False) -> None:
        """Every source on its own: one failing source does not stop the others."""
        p = self.progress
        ran: list[str] = []
        try:
            if clear:
                self.clear_jobs()
                p.cleared = True
            with self.client_factory() as client:
                try:
                    self._read_sources(client, ran)
                finally:  # what was imported gets its places even when a step failed
                    if self.geocode:
                        self.run_geocode(client)
            failed_all = bool(ran) and all(s in p.errors for s in ran)
            p.phase = "failed" if failed_all else "done"
            if failed_all:
                p.message = "; ".join(p.errors[s] for s in ran)
        except Exception as exc:  # reported in the UI
            log.exception("scrape failed")
            p.phase = "failed"
            p.message = str(exc)
        finally:
            p.running = False

    def _read_sources(self, client: httpx.Client, ran: list[str]) -> None:
        """Imports every source switched on; `ran` collects the ids of those tried."""
        for dump in self._enabled_of(mpsv.MpsvSource):  # the whole country at once, first
            ran.append(dump.id)
            self._guard(dump.id, partial(self.run_mpsv, dump, client))
        sources = self._enabled_of(CrawlSource)
        if sources:
            if self.geocode:
                # What is imported so far (Úřad práce) gets its places now, not after the
                # crawl (an hour on a first run): the place filters and the map need them.
                self._guard("ruian", lambda: self.run_geocode(client))
            fetcher = self.fetcher_factory(client)
            fetcher.stop = self._stopping
            with self.sessions() as session:
                places = PlaceResolver(session)
                dedup = DedupIndex.load(session, self.sessions)
            kraje = crawl_kraje(places, self.profile())
            for source in sources:
                ran.append(source.id)
                self._guard(
                    source.id, partial(self.run_crawl, source, fetcher, places, dedup, kraje)
                )

    def _guard(self, source_id: str, step: Callable[[], object]) -> None:
        try:
            step()
        except Stopped:
            log.info("%s stopped: the app closes", source_id)
        except Exception as exc:  # also recorded in the runs table
            log.exception("%s failed", source_id)
            self.progress.errors[source_id] = str(exc)[:300]

    def _enabled_of[T: Source](self, kind: type[T]) -> list[T]:
        return [s for s in self.sources if isinstance(s, kind) and self._enabled(s)]

    def _enabled(self, source: Source) -> bool:
        """The user's choice (else the source's default), unless the site refused us lately."""
        with self.sessions() as session:
            state = session.scalar(select(SourceState).where(SourceState.source_id == source.id))
        if state is None:
            return source.default_enabled
        until = (state.state or {}).get("blocked_until")
        if until and datetime.fromisoformat(until) > utcnow():
            log.info("%s paused until %s", source.id, until)
            return False
        return state.enabled

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

    def run_crawl(
        self,
        source: CrawlSource,
        fetcher: PoliteFetcher,
        places: PlaceResolver,
        dedup: DedupIndex,
        kraje: list[str],
    ) -> Run:
        p = self.progress
        p.source_id, p.phase, p.processed = source.id, "listing", 0

        def progress(phase: str, n: int) -> None:
            p.phase, p.processed = phase, n

        def make_store() -> JobStore:
            return JobStore(session, source.id, dedup=dedup)

        with (
            self.sessions() as session,
            self._record(session, source.id, make_store) as (run, store),
        ):
            result = crawl(source, fetcher, store, places, kraje, self.max_details, progress)
            state = self._state(session, source.id)
            state.last_run_at = utcnow()
            run.status = "ok" if result.complete else "partial"
            if result.blocked:
                run.status, run.error = "blocked", result.blocked
                until = (utcnow() + BLOCKED_PAUSE).isoformat()
                state.state = (state.state or {}) | {"blocked_until": until}
                p.errors[source.id] = result.blocked
            return run

    def run_geocode(self, client: httpx.Client) -> None:
        """Coordinates for new locations. Failure here never loses the imported jobs."""
        p = self.progress
        p.source_id, p.phase, p.processed = None, "geocode", 0
        try:
            directory = self.cache_dir / "ruian"
            file = directory / ruian.FILE_NAME
            before = file.stat().st_mtime_ns if file.exists() else None
            path = ruian.download(client, directory)
            changed = before != path.stat().st_mtime_ns
            addresses = ruian.RuianAddresses(path)
            try:
                with self.sessions() as session:
                    empty = not session.scalar(select(func.count(Municipality.code)))
                    if changed or empty:
                        codebooks = mpsv.Codebooks.fetch(
                            client, self.cache_dir / "mpsv" / "codebooks"
                        )
                        rebuild_municipalities(
                            session,
                            addresses,
                            codebooks.okres_map(),
                            progress=lambda n: setattr(p, "processed", n),
                        )
                    geocode_locations(session, addresses)
            finally:
                addresses.close()
        except Exception as exc:
            log.exception("geocoding failed")
            p.message = f"geocoding: {exc}"

    def clear_jobs(self) -> None:
        """Deletes the found jobs, so they are all found again. Cached downloads stay."""
        with self.sessions() as session:
            session.execute(delete(Job))
            state = session.get(SourceState, mpsv.SOURCE_ID)
            if state is not None:  # else the unchanged dump would be skipped
                state.state = {k: v for k, v in (state.state or {}).items() if k != "dump"}
            session.commit()

    @staticmethod
    def _state(session: Session, source_id: str) -> SourceState:
        state = session.scalar(select(SourceState).where(SourceState.source_id == source_id))
        if state is None:
            state = SourceState(source_id=source_id)
            session.add(state)
        return state
