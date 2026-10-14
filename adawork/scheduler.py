"""Automatic checks for new jobs and background data refreshes (PLAN §7.1 step 6, M7).

One background thread looks at the clock every minute:
- automatic checks (off until the user switches them on) start a search when the interval
  has passed since the last one began, manual searches included; a pause holds them back;
  a check that failed everywhere (e.g. no internet) is tried again after an hour;
- after an automatic check, the user is told about new matches of the saved searches (or
  of all jobs when none are saved), and about a social network that stopped reading;
- small official lists are downloaded again when they are due, whether automatic checks
  are on or not (`Refresh`); a failed download is retried after an hour.

Settings changed in the UI or the tray take effect at once (`wake`), without a restart.
"""

import logging
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from adawork.config import Settings
from adawork.core import utcnow
from adawork.db.models import SavedSearch, SourceState
from adawork.i18n import translate
from adawork.net.http import Refresh, make_client
from adawork.scraper.runner import Progress, ScrapeRunner
from adawork.scraper.sources import LABELS
from adawork.search.saved import filters_of, matches_after
from adawork.search.service import Filters
from adawork.worker import Worker

log = logging.getLogger(__name__)

LOOK_EVERY = 60.0  # seconds between looks at the clock
RETRY_AFTER = timedelta(hours=1)
PAUSE = timedelta(hours=24)  # "Pozastavit" in the tray and Settings
MAX_LINES = 4  # saved searches named in one notification


class Scheduler(Worker):
    name = "scheduler"
    stop_timeout = 5

    def __init__(
        self,
        settings: Settings,
        runner: ScrapeRunner,
        sessions: sessionmaker[Session],
        refreshes: Sequence[Refresh] = (),
        notify: Callable[[str, str], None] = lambda title, text: None,
        prepare: Callable[[Filters], None] = lambda f: None,
        client_factory: Callable[[], httpx.Client] = make_client,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.runner = runner
        self.sessions = sessions
        self.refreshes = refreshes
        self.notify = notify  # (title, text): a Windows notification from the tray icon
        self.client_factory = client_factory
        self.clock = clock
        # The tray icon, made after the services, sets `notify` and this.
        self.on_change: Callable[[], None] = lambda: None  # e.g. redraw the tray menu
        self.prepare = prepare  # what saved searches need before counting: travel times
        self._watching: Progress | None = None  # the running automatic check
        self._failed_at: datetime | None = None  # when the last automatic check failed
        self._was_paused = False

    # --- control ----------------------------------------------------------------------------

    def wake(self) -> None:
        """The settings changed: look again now."""
        super().wake()
        self.on_change()

    def set_enabled(self, on: bool) -> None:
        self.settings.automation.enabled = on
        self.settings.automation.paused_until = None
        self.settings.save()
        self.wake()

    def pause(self) -> None:
        self.settings.automation.paused_until = self.clock() + PAUSE
        self.settings.save()
        self.wake()

    def resume(self) -> None:
        self.settings.automation.paused_until = None
        self.settings.save()
        self.wake()

    def paused(self) -> bool:
        return self.settings.automation.paused(self.clock())

    def next_check(self) -> datetime | None:
        """When the next automatic check starts (UTC); None while they are off."""
        auto = self.settings.automation
        if not auto.enabled or not self.settings.wizard_completed:
            return None
        last = self.runner.last_start()
        due = last + timedelta(hours=auto.interval_hours) if last else self.clock()
        if self._failed_at is not None and (last is None or last <= self._failed_at):
            due = min(due, self._failed_at + RETRY_AFTER)  # no search since the failed check
        if auto.paused_until is not None and auto.paused(self.clock()):
            due = max(due, auto.paused_until)
        return due

    # --- the loop ---------------------------------------------------------------------------

    def work(self) -> float:
        self.tick()
        return LOOK_EVERY

    def tick(self) -> None:
        """One look at the clock: what is due runs now."""
        if not self.settings.wizard_completed:
            return
        watched = self._watching
        if watched is not None and not watched.running:
            self._watching = None
            self._finished(watched)
        if self.paused() != self._was_paused:  # also when a pause ran out: the tray menu changes
            self._was_paused = not self._was_paused
            self.on_change()
        self._refresh_data()
        due = self.next_check()
        if due is not None and due <= self.clock() and not self.runner.progress.running:
            log.info("automatic check starts")
            if self.runner.start():
                self._watching, self._failed_at = self.runner.progress, None
                self.on_change()

    def _finished(self, p: Progress) -> None:
        if p.phase == "failed":
            self._failed_at = self.clock()
            log.warning("automatic check failed, tried again in an hour: %s", p.message)
        self.on_change()
        if self.settings.automation.notifications:
            message = self.news(p)
            if message:
                self.notify("AdaWork", message)

    def news(self, p: Progress) -> str:
        """What the check found for the user, as notification lines ("" = nothing)."""
        lang = self.settings.language
        lines = []
        if not p.cleared:  # after a clear every job is "new": not news
            with self.sessions() as session:
                saved = list(session.scalars(select(SavedSearch).where(SavedSearch.notify)))
                if saved:
                    for s in saved:
                        f = filters_of(s)
                        self.prepare(f)
                        n = matches_after(session, f, p.after_id)
                        if n:
                            lines.append(translate(lang, "notify.saved", name=s.name, n=n))
                    lines = lines[:MAX_LINES]
                elif n := matches_after(session, Filters(), p.after_id):
                    lines.append(translate(lang, "notify.new", n=n))
        for source_id, error in p.errors.items():
            if error.startswith("social.stopped."):
                reason = translate(lang, error)
                label = LABELS.get(source_id, source_id)
                lines.append(translate(lang, "notify.social", source=label, reason=reason))
        return "\n".join(lines)

    def _refresh_data(self) -> None:
        due = [r for r in self.refreshes if self._refresh_due(r)]
        if not due:
            return
        with self.client_factory() as client:
            for refresh in due:
                self._run_refresh(refresh, client)

    def _refresh_due(self, refresh: Refresh) -> bool:
        with self.sessions() as session:
            row = session.get(SourceState, refresh.state_id)
        state = (row.state if row else None) or {}
        now = self.clock()
        failed = state.get("failed_at")
        if failed and datetime.fromisoformat(failed) + RETRY_AFTER > now:
            return False
        done = state.get("done_at")
        return not done or datetime.fromisoformat(done) + refresh.every <= now

    def _run_refresh(self, refresh: Refresh, client: httpx.Client) -> None:
        state: dict[str, str] = {}
        try:
            refresh.run(client)
            state["done_at"] = self.clock().isoformat()
            log.info("%s refreshed", refresh.name)
        except Exception as exc:
            log.warning("%s not refreshed: %s", refresh.name, exc)
            state["failed_at"] = self.clock().isoformat()
            state["error"] = str(exc)[:300]
        with self.sessions() as session:
            row = session.get(SourceState, refresh.state_id)
            if row is None:
                row = SourceState(source_id=refresh.state_id)
                session.add(row)
            row.state = {k: v for k, v in (row.state or {}).items() if k == "done_at"} | state
            row.last_run_at = self.clock()
            session.commit()
