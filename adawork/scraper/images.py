"""Pictures attached to ads (PLAN §6.8): download, shrink, hash, merge reposts, clean up.

Runs after each scrape. Pictures are fetched politely (robots.txt, pause per host) and kept as
JPEGs of at most 1280 px in the image cache; that is enough for the vision model to read a
poster and for the result card to show it.

A difference hash (dHash, 64 bits) finds the same picture posted again: the same poster in
two groups or boards is one offer, so the newer job joins the older one as another source.
The words must agree too (scraper/dedup.same_post), since an employer's logo alone appears
on all of its different ads.
"""

import hashlib
import io
import logging
import threading
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import httpx
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from adawork.core import utcnow
from adawork.db.models import Job, JobImage
from adawork.net.polite import MAX_DELAY, PoliteFetcher, SourceBlocked
from adawork.scraper.dedup import same_post
from adawork.scraper.reposts import merge

log = logging.getLogger(__name__)

MAX_SIDE = 1280
MAX_BYTES = 10_000_000
MAX_PIXELS = 50_000_000
# Pictures per host and scrape: a limit per host, so one big board does not starve the
# others, and at most about PER_HOST_SECONDS of its polite pace (30 s pauses: 10 pictures).
PER_HOST = 150
PER_HOST_SECONDS = 300
PARALLEL_HOSTS = 4  # picture hosts fetched at the same time
NEAR = 4  # differing bits of two hashes that still count as the same picture
KEEP_DAYS = 30  # pictures of jobs gone this long are deleted


def dhash(img: Image.Image) -> str:
    """64-bit difference hash as 16 hex digits: robust to resizing and recompression."""
    small = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = small.tobytes()  # one byte per pixel in mode "L"
    bits = 0
    for row in range(8):
        for col in range(8):
            left, right = pixels[row * 9 + col], pixels[row * 9 + col + 1]
            bits = (bits << 1) | (left > right)
    return f"{bits:016x}"


def distance(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


def shrink(data: bytes, max_side: int = MAX_SIDE) -> tuple[bytes, int, int, str]:
    """JPEG bytes, width, height and dHash of a downloaded picture. Raises ValueError for a
    picture of more than MAX_PIXELS (a few kB can unpack into gigabytes)."""
    try:
        img = Image.open(io.BytesIO(data))
    except Image.DecompressionBombError as exc:
        raise ValueError(str(exc)) from exc
    with img:
        if img.width * img.height > MAX_PIXELS:
            raise ValueError(f"picture of {img.width}x{img.height} pixels")
        img.load()
        rgb = img.convert("RGB")
    rgb.thumbnail((max_side, max_side))
    out = io.BytesIO()
    rgb.save(out, format="JPEG", quality=85)
    return out.getvalue(), rgb.width, rgb.height, dhash(rgb)


class TooLarge(ValueError):
    """A picture download over MAX_BYTES."""


class _CappedClient:
    """The fetcher's client, reading at most `max_bytes` of a body."""

    def __init__(self, client: httpx.Client, max_bytes: int) -> None:
        self.client, self.max_bytes = client, max_bytes

    def __getattr__(self, name: str):
        return getattr(self.client, name)

    def get(self, url, **kwargs) -> httpx.Response:
        with self.client.stream("GET", url, **kwargs) as resp:
            if int(resp.headers.get("Content-Length") or 0) > self.max_bytes:
                raise TooLarge(f"{resp.headers['Content-Length']} bytes")
            body = bytearray()
            for chunk in resp.iter_bytes():
                body += chunk
                if len(body) > self.max_bytes:
                    raise TooLarge(f"over {self.max_bytes} bytes")
        skip = ("content-encoding", "content-length", "transfer-encoding")
        headers = [(k, v) for k, v in resp.headers.multi_items() if k.lower() not in skip]
        return httpx.Response(
            resp.status_code, headers=headers, content=bytes(body), request=resp.request
        )


class ImageStore:
    def __init__(self, sessions: sessionmaker[Session], directory: Path) -> None:
        self.sessions = sessions
        self.directory = directory

    def keep(self, image: JobImage, data: bytes) -> None:
        """Stores a picture the user dropped in (manual import) like a downloaded one.
        Raises ValueError when the data is not a picture."""
        try:
            jpeg, image.width, image.height, image.phash = shrink(data)
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise ValueError("not a picture") from exc
        self.directory.mkdir(parents=True, exist_ok=True)
        name = hashlib.sha1(jpeg).hexdigest() + ".jpg"
        (self.directory / name).write_bytes(jpeg)
        image.file, image.status = name, "stored"

    def path(self, image: JobImage) -> Path | None:
        return self.directory / image.file if image.file else None

    def download(
        self,
        fetcher: PoliteFetcher,
        per_host: int = PER_HOST,
        progress: Callable[[int], None] = lambda n: None,
    ) -> int:
        """Fetch new pictures of listed offers, newest jobs first. Each host's pictures are
        fetched one after another at its polite pace, different hosts at the same time."""
        self.directory.mkdir(parents=True, exist_ok=True)
        fetcher = fetcher.with_client(_CappedClient(fetcher.client, MAX_BYTES))
        with self.sessions() as session:
            pending = session.execute(
                select(JobImage.id, JobImage.url)
                .join(Job, Job.id == JobImage.job_id)
                .where(JobImage.status == "new", Job.status == "active", Job.is_offer)
                .order_by(Job.first_seen_at.desc(), JobImage.position)
            ).all()
        by_host: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for image_id, url in pending:
            by_host[httpx.URL(url).host].append((image_id, url))
        writes = threading.Lock()
        stored = 0

        def save(image_id: int, values: dict[str, object]) -> None:
            nonlocal stored
            with writes, self.sessions() as session:
                image = session.get(JobImage, image_id)
                if image is not None:
                    for key, value in values.items():
                        setattr(image, key, value)
                    session.commit()
                if values["status"] == "stored":
                    stored += 1
                progress(stored)

        def fetch_host(host: str, images: list[tuple[int, str]]) -> None:
            try:
                crawl_delay = min(fetcher.robots(images[0][1]).crawl_delay or 0, MAX_DELAY)
                pace = max(fetcher.delay, crawl_delay)
            except Exception:  # refused or unreachable: the first download says so
                pace = fetcher.delay
            fit = int(PER_HOST_SECONDS / pace) if pace > 0 else per_host
            for image_id, url in images[: max(1, min(per_host, fit))]:
                try:
                    values = self._fetch(fetcher, url)
                except SourceBlocked as exc:
                    log.warning("pictures of %s refused (%s): left for a later run", host, exc)
                    return
                except Exception:
                    log.exception("picture %s failed", url)
                    values: dict[str, object] = {"status": "failed"}
                save(image_id, values)

        with ThreadPoolExecutor(PARALLEL_HOSTS, thread_name_prefix="images") as pool:
            for future in [pool.submit(fetch_host, h, i) for h, i in by_host.items()]:
                future.result()
        return stored

    def _fetch(self, fetcher: PoliteFetcher, url: str) -> dict[str, object]:
        """The new column values of a picture: stored with its file, or failed."""
        try:
            resp = fetcher.get(url)
            if resp is None:
                return {"status": "failed"}
            jpeg, width, height, phash = shrink(resp.content)
            name = hashlib.sha1(url.encode()).hexdigest() + ".jpg"
            (self.directory / name).write_bytes(jpeg)
        except (httpx.HTTPError, UnidentifiedImageError, OSError, ValueError) as exc:
            log.info("picture %s not stored: %s", url, exc)
            return {"status": "failed"}
        return {"status": "stored", "file": name, "width": width, "height": height, "phash": phash}

    def merge_reposts(self) -> int:
        """Joins jobs that show the same picture and say the same: the newer job's sources
        move to the older job, and the newer job is deleted. Returns the number merged."""
        with self.sessions() as session:
            rows = session.execute(
                select(JobImage.job_id, JobImage.phash, Job.first_seen_at)
                .join(Job, Job.id == JobImage.job_id)
                .where(JobImage.phash.is_not(None), Job.status == "active", Job.is_offer)
            ).all()
            # Hashes within NEAR bits share at least one of 5 bands exactly (pigeonhole).
            bands: dict[tuple[int, str], set[int]] = defaultdict(set)
            hashes: dict[int, set[str]] = defaultdict(set)
            first_seen = {}
            for job_id, phash, seen in rows:
                if phash is None:
                    continue
                hashes[job_id].add(phash)
                first_seen[job_id] = seen
                for band in range(5):
                    bands[band, phash[band * 3 : band * 3 + 3]].add(job_id)
            pairs: set[tuple[int, int]] = set()
            for job_ids in bands.values():
                if 1 < len(job_ids) < 50:  # a picture on 50+ jobs is a logo, not a repost
                    ordered = sorted(job_ids, key=lambda j: (first_seen[j], j))
                    pairs.update((a, b) for i, a in enumerate(ordered) for b in ordered[i + 1 :])
            merged: dict[int, int] = {}  # newer job -> older job
            for older, newer in sorted(pairs):
                older = merged.get(older, older)
                if newer in merged or older == newer:
                    continue
                near = any(distance(a, b) <= NEAR for a in hashes[older] for b in hashes[newer])
                if not near:
                    continue
                a, b = session.get(Job, older), session.get(Job, newer)
                if (
                    a is None
                    or b is None
                    or not same_post(a.title, a.description, b.title, b.description)
                ):
                    continue
                merge(session, a, b)
                merged[newer] = older
            session.commit()
            return len(merged)

    def clean_up(self, days: int = KEEP_DAYS) -> int:
        """Deletes the pictures (rows and files) of jobs gone for `days`."""
        cutoff = utcnow() - timedelta(days=days)
        with self.sessions() as session:
            old = list(
                session.scalars(
                    select(JobImage)
                    .join(Job, Job.id == JobImage.job_id)
                    .where(Job.status != "active", Job.updated_at < cutoff)
                )
            )
            for image in old:
                if (path := self.path(image)) is not None:
                    path.unlink(missing_ok=True)
                session.delete(image)
            session.commit()
            # Files whose job was deleted (merged reposts, social posts past 14 days).
            kept = set(session.scalars(select(JobImage.file).where(JobImage.file.is_not(None))))
        if self.directory.exists():
            for path in self.directory.glob("*.jpg"):
                if path.name not in kept:
                    path.unlink(missing_ok=True)
        return len(old)
