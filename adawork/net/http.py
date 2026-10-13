"""Shared HTTP client settings and conditional (ETag) downloads."""

import gzip
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import BinaryIO, cast

import httpx

from adawork import __version__

USER_AGENT = f"AdaWork/{__version__} (local desktop job search; personal use)"

log = logging.getLogger(__name__)


def make_client(timeout: float = 60.0) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "cs,en;q=0.8"},
        timeout=timeout,
        follow_redirects=False,  # PoliteFetcher checks robots.txt and waits for each hop
    )


@dataclass
class Download:
    path: Path
    changed: bool


# A connection that breaks mid-way (the server or the network drops it) is tried again after
# these pauses; an offline computer (a refused connection) fails at once.
DROPPED = (httpx.RemoteProtocolError, httpx.ReadError, httpx.ReadTimeout)
RETRY_WAITS = (5.0, 30.0)


def retry_dropped[T](
    call: Callable[[], T],
    what: str,
    waits: tuple[float, ...] | None = None,
    cancel: threading.Event | None = None,
) -> T:
    """`call()`, again after each of `waits` seconds (default `RETRY_WAITS`) while its
    connection drops. A set `cancel` ends a wait early: the last error is raised then."""
    for wait in RETRY_WAITS if waits is None else waits:
        try:
            return call()
        except DROPPED as exc:
            log.warning("%s: connection dropped (%s), trying again in %.0f s", what, exc, wait)
            if cancel is None:
                time.sleep(wait)
            elif cancel.wait(wait):
                raise
    return call()


def download_if_changed(
    client: httpx.Client, url: str, path: Path, *, keep_gzip: bool = False
) -> Download:
    """`_download_once`, again when the connection drops mid-way."""
    return retry_dropped(lambda: _download_once(client, url, path, keep_gzip=keep_gzip), url)


def _download_once(
    client: httpx.Client, url: str, path: Path, *, keep_gzip: bool = False
) -> Download:
    """GET `url` into `path`, sending the previous ETag/Last-Modified; 304 keeps the old file.

    Validators live in `<path>.meta.json` so they always match the file on disk.
    `keep_gzip`: for `.gz` files served with `Content-Encoding: gzip` (MPSV does this), store
    the bytes as sent instead of letting httpx decompress them. Read them with `open_maybe_gzip`.
    """
    meta_path = path.with_name(path.name + ".meta.json")
    headers = {}
    if path.exists() and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):  # cut short by a crash: the file is fetched whole
            meta = {}
        if meta.get("etag"):
            headers["If-None-Match"] = meta["etag"]
        if meta.get("last_modified"):
            headers["If-Modified-Since"] = meta["last_modified"]

    path.parent.mkdir(parents=True, exist_ok=True)
    with client.stream("GET", url, headers=headers, follow_redirects=True) as resp:
        if resp.status_code == 304:
            return Download(path, changed=False)
        resp.raise_for_status()
        tmp = path.with_name(path.name + ".part")
        with tmp.open("wb") as f:
            chunks = resp.iter_raw(1 << 20) if keep_gzip else resp.iter_bytes(1 << 20)
            for chunk in chunks:
                f.write(chunk)
        tmp.replace(path)
        meta = {
            "etag": resp.headers.get("ETag"),
            "last_modified": resp.headers.get("Last-Modified"),
        }
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return Download(path, changed=True)


def open_maybe_gzip(path: Path) -> BinaryIO:
    """Open a file that may or may not be gzip-compressed, judged by its magic bytes."""
    with path.open("rb") as f:
        compressed = f.read(2) == b"\x1f\x8b"
    return cast(BinaryIO, gzip.open(path, "rb")) if compressed else path.open("rb")


@dataclass(frozen=True)
class Refresh:
    """A file downloaded again every `every`; `run` raises when the download failed."""

    name: str
    every: timedelta
    run: Callable[[httpx.Client], object]

    @property
    def state_id(self) -> str:
        return f"data:{self.name}"
