"""Updates from the GitHub releases (Settings → Aktualizace).

The installer of a newer release is downloaded into `<data>/updates` and accepted only if it
matches the size and SHA-256 GitHub reports for it. It runs over the installed app (same
folder; the installer removes the old program files) and is deleted once the new version runs.
"""

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import httpx
from packaging.version import InvalidVersion, Version

from adawork import __version__
from adawork.ai import download
from adawork.ai.download import RemoteFile
from adawork.worker import Worker

log = logging.getLogger(__name__)

REPO = "Gig4but/AdaWork"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET = "AdaWork-Setup.exe"
ASSET_URL = f"https://github.com/{REPO}/releases/download/"
MAX_SIZE = 1 << 30
CHECK_EVERY = 24 * 3600.0
FOLDER = "updates"  # in the data folder
PENDING = "pending.json"
TRIED = "tried.txt"  # the version whose installer ran at a start
NAME = re.compile(r"AdaWork-Setup-[0-9A-Za-z.+!-]+\.exe")


class UpdateError(Exception):
    pass


@dataclass(frozen=True)
class Release:
    version: str
    file: RemoteFile


@dataclass
class UpdateStatus:
    # idle | checking | current | available | downloading | ready | installing | failed
    state: str = "idle"
    version: str = ""
    done: int = 0
    total: int = 0
    error: str = ""

    @property
    def busy(self) -> bool:
        return self.state in ("checking", "downloading")


def installed() -> bool:
    """Only the installed app can update itself (not a developer's `uv run`)."""
    return bool(getattr(sys, "frozen", False))


def newer_release(data: dict, current: str = __version__) -> Release | None:
    """The release in a GitHub API answer if it is newer than `current`, else None."""
    tag = str(data.get("tag_name") or "")
    try:
        version = Version(tag.removeprefix("v"))
    except InvalidVersion as exc:
        raise UpdateError(f"unknown version {tag!r}") from exc
    if data.get("draft") or data.get("prerelease") or version <= Version(current):
        return None
    asset = next((a for a in data.get("assets") or () if a.get("name") == ASSET), None)
    if asset is None:
        raise UpdateError("the release has no installer")
    url, size, digest = asset.get("browser_download_url"), asset.get("size"), asset.get("digest")
    if not isinstance(url, str) or not url.startswith(ASSET_URL):
        raise UpdateError(f"unexpected installer address {url!r}")
    if not isinstance(size, int) or not 0 < size <= MAX_SIZE:
        raise UpdateError(f"unexpected installer size {size!r}")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise UpdateError("the installer has no checksum")
    name = f"AdaWork-Setup-{version}.exe"
    return Release(str(version), RemoteFile(url, name, size, digest.removeprefix("sha256:")))


def clean(folder: Path, keep: tuple[str, ...] = ()) -> None:
    """Removes what earlier updates left, except `keep` (and its `.part`)."""
    if not folder.exists():
        return
    names = {TRIED, *keep, *(k + ".part" for k in keep)}
    for path in folder.iterdir():
        if path.name not in names:
            with suppress(OSError):  # still open (a download ending): removed next time
                path.unlink(missing_ok=True)


def matches(path: Path, file: RemoteFile) -> bool:
    """`path` is exactly `file` (size and SHA-256)."""
    if not path.is_file() or path.stat().st_size != file.size:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(8 << 20):
            digest.update(block)
    return digest.hexdigest() == file.sha256


def tried(folder: Path) -> str:
    try:
        return (folder / TRIED).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def take_pending(folder: Path, current: str = __version__) -> tuple[Path, RemoteFile] | None:
    """The verified installer downloaded for a newer version, once per version (a failing
    installer is not run at every start); None otherwise, then the folder is emptied."""
    try:
        info = json.loads((folder / PENDING).read_text(encoding="utf-8"))
        file = RemoteFile("", str(info["name"]), int(info["size"]), str(info["sha256"]))
        version = str(info["version"])
        newer = Version(version) > Version(current)
    except (OSError, ValueError, KeyError, TypeError, InvalidVersion):
        clean(folder)
        return None
    path = folder / file.name
    if not NAME.fullmatch(file.name) or not newer or tried(folder) == version:
        clean(folder)
        return None
    if not matches(path, file):
        clean(folder)
        return None
    (folder / TRIED).write_text(version, encoding="utf-8")
    return path, file


def run_installer(path: Path, file: RemoteFile, minimized: bool = False) -> bool:
    """Runs the installer without questions (only a progress bar, none when `minimized`); it
    ends this app if still running, replaces the program files and starts the new version."""
    if not matches(path, file):  # checked again just before it runs
        log.warning("Installer %s changed since it was checked, not run", path)
        return False
    args = [str(path), "/SUPPRESSMSGBOXES", "/NORESTART", "/NOCANCEL", "/RELAUNCH=1"]
    args += ["/VERYSILENT", "/MINIMIZED=1"] if minimized else ["/SILENT"]
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # outlives us
    env = os.environ | {"PYINSTALLER_RESET_ENVIRONMENT": "1"}  # the new app is not our child
    try:
        subprocess.Popen(args, creationflags=flags, close_fds=True, env=env)
    except OSError as exc:  # e.g. blocked by an antivirus: the app keeps its version
        log.warning("Could not start the installer: %s", exc)
        return False
    return True


class Updater(Worker):
    """Checks once a day when automatic updates are on, or when asked; downloads a newer
    installer (auto: installed at the next start; "install now": the app quits for it)."""

    name = "updates"

    def __init__(
        self,
        folder: Path,
        auto: Callable[[], bool],
        current: str = __version__,
        http_factory: Callable[[], httpx.Client] = lambda: httpx.Client(timeout=60),
        can_install: Callable[[], bool] = installed,
    ) -> None:
        super().__init__()
        self.folder = folder
        self.auto = auto
        self.current = current
        self.http_factory = http_factory
        self.can_install = can_install
        self.status = UpdateStatus()
        self.quit: Callable[[], None] | None = None  # set by the app's window or server
        self.installer: tuple[Path, RemoteFile] | None = None  # run when the app has quit
        self._asked: str | None = None  # "check" | "install"
        self._requested = False  # this round downloads for the "Nainstalovat" button
        self._cancel = threading.Event()

    def check_now(self) -> None:
        self._ask("check")

    def install_now(self) -> None:
        self._ask("install")

    def _ask(self, what: str) -> None:
        if self.status.busy or self.status.state == "installing":
            return
        self._asked = what
        self.status = UpdateStatus("checking")  # shown until the worker reports
        self.wake()

    def discard(self) -> None:
        """Automatic updates switched off: nothing downloaded is run at the next start."""
        if self._requested or self.status.state == "installing":  # the button, not automatic
            return
        if self.status.state == "downloading":
            self._cancel.set()
        clean(self.folder)
        if self.status.state == "ready":
            self.status = UpdateStatus("available", self.status.version)

    def stop(self) -> None:
        self._cancel.set()
        super().stop()

    def work(self) -> float:
        asked, self._asked = self._asked, None
        self._requested = asked == "install"
        if asked is None and not self.auto():
            return CHECK_EVERY
        try:
            self.status = UpdateStatus("checking")
            release = self._latest()
            if release is None:
                self.status = UpdateStatus("current")
                clean(self.folder)
                return CHECK_EVERY
            self.status = UpdateStatus("available", release.version)
            if not self.can_install() or (asked != "install" and not self.auto()):
                return CHECK_EVERY
            if asked != "install" and tried(self.folder) == release.version:
                return CHECK_EVERY  # its installer failed once: only on request
            path = self._download(release)
        except download.Cancelled:
            self.status = UpdateStatus()
            return CHECK_EVERY
        except Exception as exc:  # shown; the next round tries again
            log.warning("Update failed: %s", exc)
            self.status = UpdateStatus("failed", error=str(exc)[:300] or type(exc).__name__)
            return CHECK_EVERY
        if asked != "install" and not self.auto():  # switched off while downloading
            clean(self.folder)
            self.status = UpdateStatus("available", release.version)
            return CHECK_EVERY
        self.status = UpdateStatus("ready", release.version)
        if asked == "install" and self.quit is not None and matches(path, release.file):
            self.installer = (path, release.file)
            self.status = UpdateStatus("installing", release.version)
            threading.Thread(target=self.quit, name="update-quit", daemon=True).start()
        return CHECK_EVERY

    def _latest(self) -> Release | None:
        headers = {"Accept": "application/vnd.github+json"}
        with self.http_factory() as client:
            resp = client.get(LATEST, headers=headers, follow_redirects=True)
        if resp.status_code == 404:  # no release yet
            return None
        resp.raise_for_status()
        return newer_release(resp.json(), self.current)

    def _download(self, release: Release) -> Path:
        file = release.file
        clean(self.folder, keep=(file.name, PENDING))
        status = self.status = UpdateStatus("downloading", release.version, total=file.size)
        self._cancel.clear()
        if self.stopping:
            raise download.Cancelled(file.name)

        def progress(done: int, _total: int) -> None:
            status.done = done

        with self.http_factory() as client:
            path = download.download(file, self.folder, client, progress, self._cancel)
        info = {"version": release.version, "name": file.name, "size": file.size}
        info["sha256"] = file.sha256
        (self.folder / PENDING).write_text(json.dumps(info), encoding="utf-8")
        return path
