"""Login windows the user opens from the Sources page, one per platform at a time."""

import logging
import threading
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from adawork.db.models import SourceState
from adawork.social import browser
from adawork.social.reader import SocialState

log = logging.getLogger(__name__)


class SocialLogins:
    def __init__(self, sessions: sessionmaker[Session], data_dir: Path) -> None:
        self.sessions = sessions
        self.data_dir = data_dir
        self._windows: dict[str, threading.Thread] = {}
        self._failed: dict[str, str] = {}  # platform -> message key

    def start(self, platform: str) -> None:
        if not self.running(platform):
            self._windows[platform] = thread = threading.Thread(
                target=self._login, args=(platform,), name=f"login-{platform}", daemon=True
            )
            thread.start()

    def running(self, platform: str) -> bool:
        return platform in self._windows and self._windows[platform].is_alive()

    def failed(self, platform: str) -> str | None:
        """Why the last login did not work (a message key), if it did not."""
        return self._failed.get(platform)

    def _login(self, platform: str) -> None:
        """The login window; a successful login lifts a pause caused by an expired session."""
        failed = "social.login_failed"
        try:
            ok = browser.login(self.data_dir, platform)
        except browser.ProfileBusy:
            ok, failed = False, "social.login_busy"
        except Exception:
            log.exception("%s login window failed", platform)
            ok = False
        if not ok:
            self._failed[platform] = failed
            return
        self._failed.pop(platform, None)
        with self.sessions() as s:
            row = s.get(SourceState, platform)
            if row is not None:
                state = SocialState.load(row.state)
                if state.pause_reason == "login":
                    state.paused_until, state.pause_reason = None, ""
                    row.state = state.dump()
                    s.commit()
