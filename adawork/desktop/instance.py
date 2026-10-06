"""One running app per user. A second launch brings up the running one and exits, so there
are never two scrapers or two writers of the same database."""

import json
import logging
import secrets
import sys
import time
import urllib.request
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException

from adawork import paths

if sys.platform == "win32":
    import msvcrt

    def _lock(file) -> None:
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(file) -> None:
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(file) -> None:
        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(file) -> None:
        fcntl.flock(file, fcntl.LOCK_UN)


SHOW_PATH = "instance/show"
TOKEN_HEADER = "X-AdaWork-Token"

log = logging.getLogger(__name__)


class SingleInstance:
    """An OS file lock in the data folder; it goes away with the process, even after a crash.
    The running instance publishes its address in instance.json for later launches."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or paths.data_dir()
        self.token = secrets.token_urlsafe(16)  # only launches that can read our files may wake us
        self.on_show: Callable[[], object] = lambda: None
        self._file = None

    @property
    def _info_file(self) -> Path:
        return self.directory / "instance.json"

    def acquire(self) -> bool:
        """True if no other instance runs; the lock is held until release() or exit."""
        # Kept open while the app runs: closing it would drop the lock.
        file = open(self.directory / "instance.lock", "a+b")  # noqa: SIM115
        try:
            _lock(file)
        except OSError:
            file.close()
            return False
        self._file = file
        self._info_file.unlink(missing_ok=True)  # left by a crashed instance
        return True

    def release(self) -> None:
        if self._file:
            _unlock(self._file)
            self._file.close()
            self._file = None

    def add_show_route(self, app: FastAPI) -> None:
        """POST /instance/show: a later launch asks this instance to show its window."""

        @app.post("/" + SHOW_PATH, include_in_schema=False)
        def show(token: Annotated[str, Header(alias=TOKEN_HEADER)] = "") -> dict[str, bool]:
            if not secrets.compare_digest(token, self.token):
                raise HTTPException(403)
            self.on_show()
            return {"ok": True}

    def publish(self, url: str) -> None:
        self._info_file.write_text(json.dumps({"url": url, "token": self.token}), "utf-8")

    def wake_running(self, open_browser: bool, wait: float = 10.0) -> bool:
        """Shows the running instance's window, or opens the browser at its address.
        False if it does not answer."""
        info = self._running_info(wait)
        if info is None:
            return False
        if open_browser:
            webbrowser.open(info["url"])
            return True
        request = urllib.request.Request(
            info["url"] + SHOW_PATH, method="POST", headers={TOKEN_HEADER: info["token"]}
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # no proxy
        if sys.platform == "win32":  # let the running app take the foreground from us
            import ctypes

            ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
        try:
            with opener.open(request, timeout=5):
                return True
        except OSError as exc:
            log.warning("The running AdaWork did not answer at %s: %s", info["url"], exc)
            return False

    def _running_info(self, wait: float) -> dict | None:
        # The running instance may still be starting: instance.json appears once it serves.
        deadline = time.monotonic() + wait
        while True:
            try:
                return json.loads(self._info_file.read_text("utf-8"))
            except (OSError, ValueError):
                if time.monotonic() >= deadline:
                    return None
                time.sleep(0.1)
