"""The Edge window the social reader works in (Playwright with the installed Microsoft Edge).

Each platform has its own browser profile in the app's data folder, separate from the user's
everyday Edge profile. The user logs in there once in a visible window; only the session
cookies stay in that profile, the password is never seen or stored by the app.

The reading window is a real, minimized Edge window, not a headless browser. Nothing hides
that the browser is automated: Meta can notice it, which is part of the risk the user accepts
(PLAN §4.3.1). Safety comes from reading little, slowly and only in daytime.
"""

import logging
import random
import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

LOGIN_URLS = {
    "facebook": "https://www.facebook.com/login/",
    "instagram": "https://www.instagram.com/accounts/login/",
}
SESSION_COOKIES = {"facebook": "c_user", "instagram": "sessionid"}
LOGGED_IN = "adawork-logged-in"
_SEE_MORE = re.compile(r"^(?:Zobrazit víc|Zobrazit více|See more)$")


class Browser(Protocol):
    def goto(self, url: str) -> None: ...
    def scroll(self) -> None: ...
    def expand(self) -> None: ...
    def html(self) -> str: ...
    def url(self) -> str: ...
    def close(self) -> None: ...


def profile_dir(data_dir: Path, platform: str) -> Path:
    return data_dir / "social" / platform


class ProfileBusy(RuntimeError):
    """Another window of the app (a login or a reading session) uses the profile."""


class BrowserFailed(RuntimeError):
    """A page did not load in time, or the user closed the reading window."""


_profiles: dict[Path, threading.Lock] = {}
_profiles_lock = threading.Lock()


def hold_profile(profile: Path) -> threading.Lock:
    """The profile's lock, acquired; raises ProfileBusy when it is held already."""
    with _profiles_lock:
        lock = _profiles.setdefault(profile.resolve(), threading.Lock())
    if not lock.acquire(blocking=False):
        raise ProfileBusy(f"the {profile.name} window is open already")
    return lock


class EdgeBrowser:
    def __init__(self, profile: Path, visible: bool = False) -> None:
        from playwright.sync_api import Error, sync_playwright

        self._error = Error
        profile.mkdir(parents=True, exist_ok=True)
        self._lock = hold_profile(profile)
        playwright = context = None
        try:
            playwright = sync_playwright().start()
            context = playwright.chromium.launch_persistent_context(
                str(profile),
                channel="msedge",
                headless=False,
                no_viewport=True,
                # Minimized windows would otherwise pause the page's scripts and loading.
                args=[] if visible else [
                    "--start-minimized",
                    "--disable-background-timer-throttling",
                    "--disable-renderer-backgrounding",
                ],
            )  # fmt: skip
            self._page = context.pages[0] if context.pages else context.new_page()
        except BaseException:
            try:
                if context is not None:
                    context.close()
                if playwright is not None:
                    playwright.stop()
            finally:
                self._lock.release()
            raise
        self._playwright, self._context = playwright, context

    @contextmanager
    def _failing(self) -> Iterator[None]:
        try:
            yield
        except self._error as exc:
            raise BrowserFailed(str(exc).splitlines()[0]) from exc

    def goto(self, url: str) -> None:
        with self._failing():
            self._page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            self._page.wait_for_timeout(random.uniform(2000, 4000))

    def scroll(self) -> None:
        with self._failing():
            self._page.mouse.wheel(0, random.randint(600, 1400))
            self._page.wait_for_timeout(random.uniform(1200, 2500))

    def expand(self) -> None:
        """Opens the "See more" of long posts on the screen (reading only, nothing is sent)."""
        with self._failing():
            buttons = self._page.get_by_role("button", name=_SEE_MORE).all()[:10]
        for button in buttons:
            try:
                button.click(timeout=2000)
                self._page.wait_for_timeout(random.uniform(300, 900))
            except Exception:  # scrolled away or already open
                continue

    def html(self) -> str:
        with self._failing():
            return self._page.content()

    def url(self) -> str:
        return self._page.url

    def logged_in(self, platform: str) -> bool:
        names = {c.get("name") for c in self._context.cookies()}
        return SESSION_COOKIES[platform] in names

    def wait_closed_or(self, done, timeout: float) -> bool:
        """Waits until `done()` or the user closes the window; True when done."""
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline and not self._page.is_closed():
                if done():
                    return True
                self._page.wait_for_timeout(500)
        except self._error:  # the window was closed while it was checked
            pass
        return False

    def close(self) -> None:
        try:
            self._context.close()
        except self._error:  # the user closed the window already
            log.debug("Edge window already closed")
        finally:
            try:
                self._playwright.stop()
            finally:
                self._lock.release()


def login(data_dir: Path, platform: str, timeout: float = 600) -> bool:
    """Opens a visible Edge window on the platform's login page and waits until the user has
    logged in (or closed the window). True when a session is saved."""
    browser = EdgeBrowser(profile_dir(data_dir, platform), visible=True)
    try:
        ok = browser.logged_in(platform)
        if not ok:
            browser.goto(LOGIN_URLS[platform])
            ok = browser.wait_closed_or(lambda: browser.logged_in(platform), timeout)
    finally:
        browser.close()
    marker = profile_dir(data_dir, platform) / LOGGED_IN
    if ok:
        marker.touch()
    else:
        marker.unlink(missing_ok=True)
    return ok


def has_session(data_dir: Path, platform: str) -> bool:
    """A saved session exists (without opening a browser): the last login window saw the
    platform's session cookie."""
    return (profile_dir(data_dir, platform) / LOGGED_IN).exists()
