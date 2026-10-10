"""Resumable, verified downloads of model files (PLAN §6.6).

A file is written to `<name>.part`; an interrupted download continues from where it stopped
(HTTP Range). The SHA-256 from the catalog is checked before the file gets its real name, so a
half-written or corrupted model is never loaded.

Free disk space is checked before a download starts or resumes. A download that fills the disk
keeps its `.part` file and continues only once there is space again.
"""

import errno
import hashlib
import logging
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from adawork.net.http import DROPPED, retry_dropped

log = logging.getLogger(__name__)

MODEL_RETRY_WAITS = (10.0, 60.0, 300.0)  # gigabytes: worth waiting for
CHUNK = 1 << 20
HASH_READ = 8 << 20
SPACE_MARGIN = 1 << 30  # left free for Windows (updates, page file)


@dataclass(frozen=True)
class RemoteFile:
    url: str
    name: str
    size: int
    sha256: str


class DownloadError(Exception):
    pass


class Cancelled(Exception):
    pass


class NoSpace(DownloadError):
    def __init__(self, need: int, free: int) -> None:
        gb = 1 << 30
        super().__init__(
            f"not enough free disk space: {need / gb:.1f} GB needed, {free / gb:.1f} GB free"
        )
        self.need, self.free = need, free


Progress = Callable[[int, int], None]  # (bytes done, bytes total)


def _hash_existing(path: Path, digest: "hashlib._Hash") -> int:
    done = 0
    with path.open("rb") as f:
        while block := f.read(HASH_READ):
            digest.update(block)
            done += len(block)
    return done


def remaining(file: RemoteFile, dest_dir: Path) -> int:
    """Bytes still to download for `file` (a `.part` file counts as done)."""
    target = dest_dir / file.name
    if target.exists():
        return 0
    part = target.with_name(target.name + ".part")
    done = part.stat().st_size if part.exists() else 0
    return file.size - done if done <= file.size else file.size


def ensure_space(path: Path, need: int) -> None:
    """Raises NoSpace unless `need` bytes and a margin fit on the disk of `path`."""
    if need <= 0:
        return
    existing = next(p for p in (path, *path.parents) if p.exists())
    free = shutil.disk_usage(existing).free
    if need + SPACE_MARGIN > free:
        raise NoSpace(need + SPACE_MARGIN, free)


def download(
    file: RemoteFile,
    dest_dir: Path,
    client: httpx.Client,
    progress: Progress | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """`_download_once`, continued after a pause when the connection drops (it resumes)."""
    try:
        return retry_dropped(
            lambda: _download_once(file, dest_dir, client, progress, cancel),
            file.name,
            MODEL_RETRY_WAITS,
            cancel,
        )
    except DROPPED:
        if cancel is not None and cancel.is_set():
            raise Cancelled(file.name) from None
        raise


def _download_once(
    file: RemoteFile,
    dest_dir: Path,
    client: httpx.Client,
    progress: Progress | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Download `file` into `dest_dir`; returns the final path. Safe to call again after a
    crash or cancel: a finished file is only re-checked by size, a partial one is resumed."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / file.name
    if target.exists() and target.stat().st_size == file.size:
        return target
    part = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    done = _hash_existing(part, digest) if part.exists() else 0
    if done > file.size:
        part.unlink()
        digest, done = hashlib.sha256(), 0

    headers = {"Range": f"bytes={done}-"} if done else {}
    if done < file.size:
        ensure_space(dest_dir, file.size - done)
        with client.stream("GET", file.url, headers=headers, follow_redirects=True) as resp:
            if resp.status_code == 200 and done:  # server ignored Range: start over
                digest, done = hashlib.sha256(), 0
            elif resp.status_code not in (200, 206):
                raise DownloadError(f"{file.name}: HTTP {resp.status_code}")
            try:
                with part.open("ab" if done else "wb") as out:
                    for chunk in resp.iter_bytes(CHUNK):
                        if cancel is not None and cancel.is_set():
                            raise Cancelled(file.name)
                        out.write(chunk)
                        digest.update(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, file.size)
            except OSError as exc:  # the .part file is kept and resumed when there is space
                if exc.errno != errno.ENOSPC:
                    raise
                raise NoSpace(file.size - done, shutil.disk_usage(dest_dir).free) from exc

    if done != file.size or digest.hexdigest() != file.sha256:
        part.unlink(missing_ok=True)
        raise DownloadError(f"{file.name}: size or checksum mismatch, the file was discarded")
    part.replace(target)
    return target


def is_metered() -> bool:
    """Windows "metered connection" flag (mobile hotspot, data cap). Unknown -> False."""
    if sys.platform != "win32":
        return False
    script = (
        "[void][Windows.Networking.Connectivity.NetworkInformation,"
        "Windows.Networking.Connectivity,ContentType=WindowsRuntime];"
        "$p=[Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile();"
        "if ($p) { $p.GetConnectionCost().NetworkCostType } else { 'Unknown' }"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        log.info("Metered check failed: %s", exc)
        return False
    return out in ("Fixed", "Variable")  # "Unrestricted" or "Unknown" otherwise
