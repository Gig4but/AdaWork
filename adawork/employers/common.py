"""What the parts of the employer work share: `Context` (the database, clock, settings and the
state row), and the rules they all apply."""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from adawork.ai.manager import ModelManager
from adawork.config import EmployerSettings
from adawork.db.models import Job, SourceState

STATE_ID = "employers"  # SourceState: daily counts, list version, Google quota
DUE = timedelta(days=30)  # checks, reviews and mentions are fetched again after this
PER_DAY = {"atmoskop": 150, "google": 30, "mentions": 20}  # background only


@dataclass
class EmployerProgress:
    running: bool = False
    phase: str = ""  # link | resolve | check | reviews | mentions | summaries | done | failed
    done: int = 0
    total: int = 0
    message: str = ""


@dataclass
class Context:
    """Made once by the service (service.py) and given to each of its parts."""

    sessions: sessionmaker[Session]
    settings: Callable[[], EmployerSettings]
    client_factory: Callable[[], httpx.Client]
    manager: ModelManager | None  # the AI choice and the summaries; None: rules only
    profile_jobs: Callable[[Session], set[int]]
    clock: Callable[[], datetime]
    stopping: Callable[[], bool]  # the app closes: long loops end early
    progress: EmployerProgress = field(default_factory=EmployerProgress)
    lock: threading.Lock = field(default_factory=threading.Lock)  # one writer of employers
    state_lock: threading.RLock = field(default_factory=threading.RLock)  # the state row

    def state(self) -> dict:
        with self.sessions() as s:
            row = s.get(SourceState, STATE_ID)
            return dict(row.state or {}) if row else {}

    def save_state(self, changes: dict) -> None:
        with self.state_lock, self.sessions() as s:
            row = s.get(SourceState, STATE_ID) or SourceState(source_id=STATE_ID)
            row.state = {**(row.state or {}), **changes}
            s.add(row)
            s.commit()

    def spend(self, kind: str) -> bool:
        """One more of today's background `kind` (Atmoskop pages, Google, web searches)."""
        with self.state_lock:
            state = self.state()
            today = f"{self.clock():%Y-%m-%d}"
            used = state.get("used", {}) if state.get("day") == today else {}
            if used.get(kind, 0) >= PER_DAY[kind]:
                return False
            used[kind] = used.get(kind, 0) + 1
            self.save_state({"day": today, "used": used})
        return True

    def profile_employers(self, s: Session | None = None) -> set[int]:
        """Employers of active jobs matching the user's profile."""
        if s is None:
            with self.sessions() as s2:
                return self.profile_employers(s2)
        wanted = self.profile_jobs(s)
        if not wanted:
            return set()
        rows = s.execute(
            select(Job.id, Job.employer_id).where(
                Job.status == "active", Job.employer_id.is_not(None)
            )
        )
        return {eid for job_id, eid in rows if job_id in wanted and eid is not None}
