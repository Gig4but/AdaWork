"""The bundled MOTIS routing server (PLAN §2.5, §4.6) and a client for the parts of its API the
app uses.

The server runs on demand for travel times, routes and map tiles, and stops after a few idle
minutes (0.2–1.6 GB of RAM while it runs). It listens on 127.0.0.1 only: "localhost" costs
~2 s per request on Windows.
"""

import logging
import math
import os
import subprocess
import threading
import time
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml

from adawork.system import winjob
from adawork.system.ports import HOST, free_port

log = logging.getLogger(__name__)

IDLE_SECONDS = 300
START_TIMEOUT = 120
STOP_WAIT = 900  # an import waits this long for running requests
MAX_MANY = 5000  # destinations per one-to-many request (the import allows 10 000)
MIN_CHUNK = 500
WORKERS = min(8, os.cpu_count() or 2)
# How far a point may be from a road or path and still be routed (m).
MATCHING_DISTANCE = 250

Point = tuple[float, float]  # (lat, lon)


class MotisUnavailable(Exception):
    pass


class MotisClient:
    def __init__(self, base_url: str, http: httpx.Client) -> None:
        self.base_url = base_url
        self.http = http

    def _post(self, path: str, body: dict):
        r = self.http.post(self.base_url + path, json=body)
        r.raise_for_status()
        return r.json()

    def _get(self, path: str, params: dict):
        r = self.http.get(self.base_url + path, params=params)
        r.raise_for_status()
        return r.json()

    def _many(self, points: Sequence[Point], ask) -> list[float | None]:
        """`ask(chunk)` for the points in parallel chunks, one per worker: MOTIS answers each
        request on one thread (M8: public transport to 13 800 places 24 s -> 4 s)."""
        if not points:
            return []
        size = min(MAX_MANY, max(MIN_CHUNK, math.ceil(len(points) / WORKERS)))
        chunks = [points[i : i + size] for i in range(0, len(points), size)]
        with ThreadPoolExecutor(min(WORKERS, len(chunks)), thread_name_prefix="motis") as pool:
            return [seconds for found in pool.map(ask, chunks) for seconds in found]

    def one_to_many(
        self, origin: Point, points: Sequence[Point], mode: str, max_seconds: int
    ) -> list[float | None]:
        """Seconds from `origin` to each point by CAR, WALK or BIKE; None = not within
        `max_seconds` (or not on the map)."""

        def ask(chunk: Sequence[Point]) -> list[float | None]:
            answer = self._post(
                "/api/v1/one-to-many",
                {
                    "one": f"{origin[0]};{origin[1]}",
                    "many": [f"{lat};{lon}" for lat, lon in chunk],
                    "mode": mode,
                    "max": max_seconds,
                    "maxMatchingDistance": MATCHING_DISTANCE,
                    "arriveBy": False,
                },
            )
            return [item.get("duration") for item in answer]

        return self._many(points, ask)

    def transit_to_many(
        self, origin: Point, points: Sequence[Point], when: datetime, max_minutes: int
    ) -> list[float | None]:
        """Seconds by public transport (walking included, or walking only when faster),
        leaving at `when`; None = not within `max_minutes`."""

        def ask(chunk: Sequence[Point]) -> list[float | None]:
            answer = self._post(
                "/api/experimental/one-to-many-intermodal",
                {  # here "lat,lon", unlike one-to-many
                    "one": f"{origin[0]},{origin[1]}",
                    "many": [f"{lat},{lon}" for lat, lon in chunk],
                    "time": iso_utc(when),
                    "maxTravelTime": max_minutes,
                    "maxMatchingDistance": MATCHING_DISTANCE,
                    "arriveBy": False,
                },
            )
            found = []
            for street, transit in zip(
                answer["street_durations"], answer["transit_durations"], strict=True
            ):
                options = [street["duration"]] if "duration" in street else []
                options += [t["duration"] for t in transit]
                found.append(min(options) if options else None)
            return found

        return self._many(points, ask)

    def plan(
        self,
        origin: Point,
        destination: Point,
        when: datetime,
        mode: str,
        arrive_by: bool = False,
        count: int = 3,
    ) -> dict:
        """Routes for the commute window: `mode` "transit" (with walking), or a street mode
        (CAR, WALK, BIKE) as one direct route."""
        params: dict[str, str | int] = {
            "fromPlace": f"{origin[0]},{origin[1]}",
            "toPlace": f"{destination[0]},{destination[1]}",
            "time": iso_utc(when),
            "arriveBy": str(arrive_by).lower(),
        }
        if mode == "transit":
            params["numItineraries"] = count
        else:
            # MOTIS limits direct street routes to 30 min unless told otherwise.
            params |= {"directModes": mode, "transitModes": "", "maxDirectTime": 2 * 3600}
        return self._get("/api/v6/plan", params)

    def geocode(self, text: str) -> list[dict]:
        return self._get("/api/v1/geocode", {"text": text, "language": "cs"})

    def tile(self, path: str) -> httpx.Response:
        """A vector tile (the status is not checked)."""
        return self.http.get(self.base_url + path)


def iso_utc(when: datetime) -> str:
    """`when` (aware) as UTC in the format MOTIS wants."""
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class MotisServer:
    def __init__(self, vendor: Path, log_path: Path, idle_seconds: float = IDLE_SECONDS) -> None:
        self.vendor = vendor
        self.log_path = log_path
        self.idle_seconds = idle_seconds
        self._proc: subprocess.Popen | None = None
        self._data: Path | None = None
        self._port = 0
        self._last_used = 0.0
        self._users = 0
        self._paused = 0
        self._lock = threading.RLock()  # the state above
        self._released = threading.Condition(self._lock)
        self._starting = threading.Lock()  # one start at a time, waited for outside `_lock`
        self._closed = threading.Event()
        self._http = httpx.Client(timeout=120)
        self._watcher: threading.Thread | None = None

    @property
    def binary(self) -> Path:
        return self.vendor / "motis" / "motis.exe"

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @contextmanager
    def use(self, data: Path) -> Iterator[MotisClient]:
        """A client of a server for the imported `data`; not stopped while in use. A start
        (up to START_TIMEOUT) holds back only the others who want the server: not a pause,
        and not the idle check."""
        with self._lock:
            if self._paused or self._closed.is_set():
                raise MotisUnavailable("the routing data is being imported")
            self._users += 1  # at once: a pause waits for this request too
            if self._watcher is None:
                self._watcher = threading.Thread(
                    target=self._stop_when_idle, name="motis-idle", daemon=True
                )
                self._watcher.start()
        try:
            with self._starting:
                if not (self.running and self._data == data):
                    self.stop()
                    self._start(data)
            self._last_used = time.monotonic()
            yield MotisClient(f"http://{HOST}:{self._port}", self._http)
        finally:
            with self._released:
                self._users -= 1
                self._last_used = time.monotonic()
                self._released.notify_all()

    @contextmanager
    def paused(self) -> Iterator[None]:
        """Stopped (after running requests finish) and not started again until the end."""
        with self._lock:
            self._paused += 1
        try:
            self.stop(wait_for_users=True)
            yield
        finally:
            with self._lock:
                self._paused -= 1

    def stop(self, wait_for_users: bool = False) -> None:
        """`wait_for_users`: first let running requests finish (before an import); a whole
        computation of travel times can take minutes."""
        with self._released:
            if wait_for_users and not self._released.wait_for(
                lambda: self._users == 0, timeout=STOP_WAIT
            ):
                log.warning("MOTIS stopped while still in use (%d)", self._users)
            proc, self._proc, self._data = self._proc, None, None
            if proc is None or proc.poll() is not None:
                return
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            log.info("MOTIS stopped")

    def _start(self, data: Path) -> None:
        if not self.binary.exists():
            raise MotisUnavailable(f"{self.binary} is missing")
        self._port = free_port()
        config_path = data / "config.yml"
        try:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            config["server"] = {**config.get("server", {}), "host": HOST, "port": self._port}
            config["server"].pop("web_folder", None)
            config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            log.info("Starting MOTIS on port %d for %s", self._port, data)
            with self.log_path.open("ab") as log_file:
                self._proc = subprocess.Popen(
                    # Relative: motis.exe takes only paths of the system code page (data.py).
                    [str(self.binary), "server", "-d", data.name],
                    cwd=data.parent,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
        except (OSError, yaml.YAMLError, AttributeError, TypeError) as exc:  # no or bad data
            raise MotisUnavailable(f"MOTIS could not start: {exc}") from exc
        winjob.bind_to_app(self._proc.pid)
        self._data = data
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                self._proc = None
                raise MotisUnavailable(f"MOTIS exited, see {self.log_path}")
            try:
                if self._http.get(f"http://{HOST}:{self._port}/api/v1/health").is_success:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        else:
            self.stop()
            raise MotisUnavailable("MOTIS did not become ready")

    def close(self) -> None:
        """The app ends: the server stops, and is not started again."""
        self._closed.set()
        self.stop()
        self._http.close()

    def _stop_when_idle(self) -> None:
        while not self._closed.wait(min(10.0, self.idle_seconds / 4)):
            with self._lock:
                idle = time.monotonic() - self._last_used > self.idle_seconds
                if self.running and self._users == 0 and idle:
                    log.info("MOTIS idle for %.0f s", self.idle_seconds)
                    self.stop()
