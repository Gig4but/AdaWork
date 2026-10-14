"""Map and timetable data for MOTIS, downloaded and imported in the background (PLAN §4.6).

- OpenStreetMap Czech extract (Geofabrik, ~1 GB): refreshed monthly.
- Spojenka GTFS (nationwide timetables, rebuilt daily, valid 14 days): refreshed weekly.
  Licensed for non-commercial use only; the app stays personal and free (PLAN §12).

The area depends on the PC (PLAN §3.2.1): with enough RAM the whole of Czechia is imported
(geocoding on, the detailed map). Otherwise ~150 km around home: osmium clips and renumbers the
map (renumbering is what lowers the import's RAM), the timetables are clipped too, geocoding is
off and the map is lighter. A home that moves out of the area starts a new import.

Each step runs only when its input changed. A new import is made in a copy of the data in use
and swapped in when done, so routing keeps working meanwhile and after a failure; only the first
import (or one without disk room for a copy) runs in place. Until the first import is done,
travel times are estimates (commute.py).
"""

import json
import logging
import math
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import yaml

from adawork.geo.places import distance_km
from adawork.net.http import retry_dropped
from adawork.routing import gtfs
from adawork.routing.motis import MotisServer
from adawork.system import winjob
from adawork.worker import Worker

log = logging.getLogger(__name__)

OSM_URL = "https://download.geofabrik.de/europe/czech-republic-latest.osm.pbf"
GTFS_URL = "https://www.spojenka.cz/jrdata/jizdnirady-gtfs.zip"
OSM_EVERY = timedelta(days=30)
GTFS_EVERY = timedelta(days=7)
FETCH_RETRY_WAITS = (10.0, 60.0, 300.0)  # a dropped connection: soon again
LOOK_EVERY = 600  # seconds between looks for due updates
# A first map for the home area waits for the home's point, which a new install has only
# once its first search fetched the place names: looked for again soon.
WAIT_FOR_HOME = 60
RETRY_AFTER = timedelta(hours=1)
HOME_KM = 150  # the area imported around home on smaller PCs
MOVED_KM = 25  # a home this far from the area's centre gets a new area
TIMETABLE_DAYS = 16
IMPORT_TIMEOUT = 3 * 3600
OSMIUM_TIMEOUT = 3600
CHUNK = 1 << 20
# Free RAM the import wants before it starts (the 8 GB profile peaks at ~1.7 GB, the osmium
# clip at ~3.7 GB, M0 spike).
IMPORT_RAM = 2 * 1024**3
COPY_ROOM = 2.0  # the import into a copy: the copy, and as much again for what it rewrites
DISK_RESERVE = 2 * 1024**3
_PROGRESS = re.compile(rb"([a-z_]+): \[[^\]]*\]\s*(\d+)%")


@dataclass(frozen=True)
class Area:
    """What is imported: everything (`km` None) or a circle around home."""

    km: float | None = None
    lat: float = 0.0
    lon: float = 0.0

    @property
    def whole(self) -> bool:
        return self.km is None

    def same_as(self, other: "Area | None") -> bool:
        if other is None or self.whole != other.whole:
            return False
        if self.whole:
            return True
        return distance_km(self.lat, self.lon, other.lat, other.lon) <= MOVED_KM

    def contains(self, lat: float, lon: float) -> bool:
        return self.whole or distance_km(self.lat, self.lon, lat, lon) <= (self.km or 0)


def circle(area: Area, points: int = 64) -> dict:
    """The area as a GeoJSON polygon, for osmium extract."""
    assert area.km is not None  # only the area around home is a circle
    cos = math.cos(math.radians(area.lat))
    ring = [
        [
            round(area.lon + area.km * math.cos(a) / (111.32 * cos), 5),
            round(area.lat + area.km * math.sin(a) / 111.32, 5),
        ]
        for a in (2 * math.pi * i / points for i in range(points))
    ]
    ring.append(ring[0])
    geometry = {"type": "Polygon", "coordinates": [ring]}
    return {"type": "Feature", "properties": {}, "geometry": geometry}


def fetch(
    client: httpx.Client,
    url: str,
    path: Path,
    progress: Callable[[int, int], None] = lambda done, total: None,
) -> bool:
    """`_fetch_once`, continued after a pause when the connection drops (it resumes)."""
    return retry_dropped(lambda: _fetch_once(client, url, path, progress), url, FETCH_RETRY_WAITS)


def _fetch_once(
    client: httpx.Client,
    url: str,
    path: Path,
    progress: Callable[[int, int], None] = lambda done, total: None,
) -> bool:
    """Downloads `url` to `path` unless unchanged (ETag / Last-Modified); an interrupted
    download continues where it stopped (from the start when it no longer fits). True if a
    new file was written."""
    meta_path = path.with_name(path.name + ".meta.json")
    part = path.with_name(path.name + ".part")
    part_meta = path.with_name(path.name + ".part.json")
    meta = _read_json(meta_path)
    headers = {}
    if path.exists():
        if meta.get("etag"):
            headers["If-None-Match"] = meta["etag"]
        if meta.get("last_modified"):
            headers["If-Modified-Since"] = meta["last_modified"]
    started = _read_json(part_meta) if part.exists() else {}
    started_as = started.get("etag") or started.get("last_modified")
    have = part.stat().st_size if started_as else 0
    if have:
        headers["Range"] = f"bytes={have}-"
        headers["If-Range"] = started_as
    path.parent.mkdir(parents=True, exist_ok=True)
    with client.stream("GET", url, headers=headers, follow_redirects=True) as r:
        if r.status_code == 304:
            return False
        if r.status_code == 416 and have:  # the part is whole or longer: start again
            r.close()
            part.unlink()
            part_meta.unlink(missing_ok=True)
            return _fetch_once(client, url, path, progress)
        r.raise_for_status()
        validators = {
            "etag": r.headers.get("ETag"),
            "last_modified": r.headers.get("Last-Modified"),
        }
        if r.status_code == 206 and validators["etag"] not in (None, started.get("etag")):
            part.unlink()
            raise RuntimeError(f"{url} changed during the download")
        if r.status_code != 206:
            have = 0  # the server sends it all again
            part_meta.write_text(json.dumps(validators), encoding="utf-8")
        total = have + int(r.headers.get("Content-Length") or 0)
        with part.open("ab" if have else "wb") as file:
            done = have
            for chunk in r.iter_bytes(CHUNK):
                file.write(chunk)
                done += len(chunk)
                progress(done, total)
    part.replace(path)
    meta_path.write_text(json.dumps(validators), encoding="utf-8")
    part_meta.unlink(missing_ok=True)
    return True


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class RoutingData(Worker):
    name = "routing-data"
    stop_timeout = 0  # an import takes minutes: it ends with the app

    def __init__(
        self,
        root: Path,
        vendor: Path,
        server: MotisServer,
        wanted_area: Callable[[], Area | None],
        client_factory: Callable[[], httpx.Client],
        allowed: Callable[[], bool] = lambda: True,
        heavy: Callable[[], AbstractContextManager[object]] = nullcontext,  # a heavy-job slot
        busy: Callable[[int], bool] = lambda need: False,
        before_import: Callable[[], None] = lambda: None,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        super().__init__()
        self.root = root
        self.vendor = vendor
        self.server = server
        self.wanted_area = wanted_area  # None: not known yet (no home for a home area)
        self.client_factory = client_factory
        self.allowed = allowed  # e.g. only after the wizard
        self.heavy = heavy
        self.busy = busy
        self.before_import = before_import  # e.g. stop an idle AI model on small PCs
        self.clock = clock
        self.on_ready: Callable[[], None] = lambda: None  # a new import is in use
        self.state: dict = _read_json(self.root / "state.json")
        old = self.root / "motis.old"
        if old.is_dir() and not self.data.exists():  # the app ended between the two renames
            old.replace(self.data)
        self.step = ""  # what runs now: download_osm | download_gtfs | clip | timetable | import
        self.percent = 0
        self._lock = threading.Lock()  # one update at a time

    # --- Paths and state ----------------------------------------------------------------------

    @property
    def data(self) -> Path:
        """The imported data MOTIS serves."""
        return self.root / "motis"

    @property
    def osm_download(self) -> Path:
        return self.root / "downloads" / "czech-republic-latest.osm.pbf"

    @property
    def gtfs_download(self) -> Path:
        return self.root / "downloads" / "spojenka-gtfs.zip"

    def _save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / "state.json.tmp"
        tmp.write_text(json.dumps(self.state, indent=1), encoding="utf-8")
        tmp.replace(self.root / "state.json")

    @property
    def version(self) -> int:
        """Grows with every successful import; 0 = nothing imported yet."""
        return int(self.state.get("version", 0))

    @property
    def area(self) -> Area | None:
        """The area of the data in use."""
        area = self.state.get("area")
        return Area(**area) if area and self.version else None

    @property
    def ready(self) -> bool:
        """Data to route with (also while a new import is made beside it)."""
        return self.version > 0 and self.usable

    @property
    def usable(self) -> bool:
        return self.state.get("usable", True)

    @property
    def timetable_until(self) -> date | None:
        """The last day the imported timetables cover (`config`: from the day before)."""
        imported = self._when("imported_at")
        return imported.date() + timedelta(days=TIMETABLE_DAYS - 2) if imported else None

    @property
    def geocoding(self) -> bool:
        return self.ready and bool(self.area and self.area.whole)

    def _when(self, key: str) -> datetime | None:
        value = self.state.get(key)
        return datetime.fromisoformat(value) if value else None

    # --- Background thread --------------------------------------------------------------------

    def work(self) -> float:
        return self.look()

    def look(self) -> float:
        """Updates what is due; returns the seconds until the next look."""
        try:
            if self.allowed():
                if self.due():
                    self.update()
                elif not self.version and self.wanted_area() is None:
                    return WAIT_FOR_HOME
        except Exception:
            log.exception("Routing data update failed")
        return LOOK_EVERY

    # --- What to do ---------------------------------------------------------------------------

    def due(self, force: bool = False) -> bool:
        now = self.clock()
        failed = self._when("failed_at")
        if failed and now - failed < RETRY_AFTER and not force:
            return False
        area = self.wanted_area()
        if area is None:
            return False
        if not self.version or not self.usable or not area.same_as(self.area):
            return True
        gtfs_at, osm_at = self._when("gtfs_at"), self._when("osm_at")
        return (
            force
            or self.state.get("osm_pending", False)  # downloaded, not imported yet
            or self.state.get("gtfs_pending", False)
            or gtfs_at is None
            or now - gtfs_at >= GTFS_EVERY
            or osm_at is None
            or now - osm_at >= OSM_EVERY
        )

    def status(self) -> dict:
        area = self.area
        return {
            "ready": self.ready,
            "step": self.step,
            "percent": self.percent,
            "whole": area.whole if area else None,
            "km": area.km if area else None,
            "osm_at": self._when("osm_at"),
            "gtfs_at": self._when("gtfs_at"),
            "imported_at": self._when("imported_at"),
            "error": self.state.get("error") if self.state.get("failed_at") else None,
        }

    def update(self, force: bool = False) -> bool:
        """Downloads what is due and imports it. False if another update runs."""
        if not self._lock.acquire(blocking=False):
            return False
        try:
            self._update(force)
            self.state.pop("failed_at", None)
            self.state.pop("error", None)
            self._save()
            return True
        except Exception as exc:
            self.state["failed_at"] = self.clock().isoformat()
            self.state["error"] = str(exc)[:300]
            self._save()
            raise
        finally:
            self.step, self.percent = "", 0
            self._lock.release()

    def update_in_background(self) -> None:
        """Downloads and imports the newest map and timetables now (Settings → Mapy)."""

        def run() -> None:
            try:
                self.update(force=True)
            except Exception:
                log.exception("Routing data update failed")

        threading.Thread(target=run, name="routing-update", daemon=True).start()

    def _update(self, force: bool) -> None:
        area = self.wanted_area()
        if area is None:
            return
        now = self.clock()
        osm_at, gtfs_at = self._when("osm_at"), self._when("gtfs_at")
        new_osm = new_gtfs = False
        with self.client_factory() as client:
            if (
                force
                or osm_at is None
                or now - osm_at >= OSM_EVERY
                or not self.osm_download.exists()
            ):
                self.step = "download_osm"
                new_osm = fetch(client, OSM_URL, self.osm_download, self._progress)
                self.state["osm_at"] = now.isoformat()
            if (
                force
                or gtfs_at is None
                or now - gtfs_at >= GTFS_EVERY
                or not self.gtfs_download.exists()
            ):
                self.step = "download_gtfs"
                new_gtfs = fetch(client, GTFS_URL, self.gtfs_download, self._progress)
                self.state["gtfs_at"] = now.isoformat()
            self._save()
        moved = not area.same_as(Area(**self.state["area"]) if self.state.get("area") else None)
        # New downloads stay pending until imported, so a failed import is tried again.
        osm_new = self.state["osm_pending"] = self.state.get("osm_pending", False) or new_osm
        gtfs_new = self.state["gtfs_pending"] = self.state.get("gtfs_pending", False) or new_gtfs
        self._save()
        self.step, self.percent = "", 0
        with self.heavy():
            self._wait_until_free()
            if self.stopping:
                raise RuntimeError("The app is closing")
            osm = self._prepare_osm(area, osm_new or moved)
            timetable = self.root / "input" / "timetable.zip"
            previous = self.root / "input" / "timetable.previous.zip"
            replaced = False
            if gtfs_new or moved or not timetable.exists():
                self.step, self.percent = "timetable", 0
                if timetable.exists():
                    timetable.replace(previous)
                    replaced = True
                # The same circle the routing uses (Area.contains), so edges agree.
                gtfs.prepare(self.gtfs_download, timetable, None if area.whole else area.contains)
            if self.version and self.usable and not (osm_new or gtfs_new or moved):
                return  # nothing new to import
            self.before_import()
            try:
                self._import(area, osm, timetable)
            except Exception:
                rejected = replaced and not (moved or osm_new)  # only the timetables were new
                if replaced:  # the inputs the data in use was made from
                    previous.replace(timetable)
                if rejected:  # that feed is not tried again: the next one downloaded is
                    self.state["gtfs_pending"] = False
                if rejected and self.version and not self.usable:  # it was imported in place
                    log.warning("Import with new timetables failed; importing the previous ones")
                    self._import(area, osm, timetable)
                raise
        self.state["osm_pending"] = self.state["gtfs_pending"] = False
        self.state["area"] = asdict(area)
        self.state["version"] = self.version + 1
        self.state["imported_at"] = self.clock().isoformat()
        self._save()
        self.step, self.percent = "", 0  # ready from now on
        self.on_ready()

    def _wait_until_free(self) -> None:
        """Waits while the user is at the PC and free RAM is low (PLAN §3.2.1)."""
        while self.busy(IMPORT_RAM) and not self.stopping:
            self.sleep(60)

    def _progress(self, done: int, total: int) -> None:
        self.percent = int(done * 100 / total) if total else 0

    # --- Steps --------------------------------------------------------------------------------

    def _prepare_osm(self, area: Area, changed: bool) -> Path:
        """The map for the import: the download itself, or the home area clipped from it and
        renumbered."""
        if area.whole:
            return self.osm_download
        target = self.root / "input" / "area.osm.pbf"
        if target.exists() and not changed:
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        shape = self.root / "input" / "area.geojson"
        shape.write_text(json.dumps(circle(area)), encoding="utf-8")
        clipped = self.root / "input" / "area_clipped.osm.pbf"
        self.step, self.percent = "clip", 0
        self._osmium("extract", "-p", str(shape), "-s", "complete_ways", "-o", str(clipped),
                     "--overwrite", str(self.osm_download))  # fmt: skip
        self.percent = 50
        part = target.with_name("area.part.osm.pbf")  # never a half-written map in use
        self._osmium("renumber", "-o", str(part), "--overwrite", str(clipped))
        part.replace(target)
        clipped.unlink()
        return target

    def _osmium(self, *args: str) -> None:
        binary = self.vendor / "osmium" / "osmium.exe"  # ~3.7 GB of RAM at its peak
        log_path = self.root / "osmium.log"
        self._run_program(f"osmium {args[0]}", [str(binary), *args], log_path, OSMIUM_TIMEOUT)

    def _run_program(
        self,
        what: str,
        args: list[str],
        log_path: Path,
        timeout: float,
        cwd: Path | None = None,
        on_tick: Callable[[], None] = lambda: None,
    ) -> None:
        """Runs a program in the background, its output in `log_path`; it ends with the app,
        and is stopped after `timeout` seconds or when the app closes."""
        started = time.monotonic()
        with log_path.open("wb") as out:
            proc = subprocess.Popen(
                args,
                cwd=cwd,
                stdout=out,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=_BACKGROUND,
            )
            winjob.bind_to_app(proc.pid)
            while proc.poll() is None:
                if time.monotonic() - started > timeout or self.stopping:
                    proc.kill()
                    proc.wait()
                    raise RuntimeError(f"{what} stopped (time limit or app closing)")
                on_tick()
                time.sleep(1)
        if proc.returncode:
            tail = log_path.read_bytes()[-400:].decode("utf-8", "replace").strip()
            raise RuntimeError(f"{what} failed ({proc.returncode}): {tail}")

    def _profile(self, area: Area) -> tuple[Path, Path]:
        """The map tiles profile, and its copy beside the data (see `config`)."""
        name = "full.lua" if area.whole else "background.lua"
        return self.vendor / "motis" / "tiles-profiles" / name, self.root / f"tiles-{name}"

    def config(self, area: Area, osm: Path, timetable: Path) -> dict:
        """Paths relative to the routing folder, MOTIS's working folder: motis.exe takes
        only paths of the system code page, and a user name with "ř" is not in all of them
        (Windows passes the working folder itself in Unicode)."""
        first = self.clock().date() - timedelta(days=1)
        return {
            "server": {"host": "127.0.0.1", "port": 0},
            "osm": self._relative(osm),
            "tiles": {
                "profile": self._relative(self._profile(area)[1]),
                "db_size": 256 * 1024**3,  # address space reserved, not disk or RAM
                "flush_threshold": 100000 if area.whole else 10000,
            },
            "timetable": {
                "first_day": first.isoformat(),
                "num_days": TIMETABLE_DAYS,
                "railviz": False,
                "with_shapes": True,
                "datasets": {"spoj": {"path": self._relative(timetable)}},
            },
            "street_routing": True,
            "osr_footpath": True,
            # Geocoding cannot be built from a renumbered map (M0 spike: the import hangs).
            "geocoding": area.whole,
            "reverse_geocoding": area.whole,
            "limits": {"onetomany_max_many": 10000, "onetoall_max_travel_minutes": 120},
        }

    def _import(self, area: Area, osm: Path, timetable: Path) -> None:
        """Into a copy of the data in use, swapped in when done: routes and the map keep
        working meanwhile and after a failure, and MOTIS still skips the steps whose inputs
        did not change (its records are copied too). In place for the first import, or when
        the disk has no room for a copy: unusable until it succeeds."""
        self.step, self.percent = "import", 0
        staging = self.root / "motis.new"
        shutil.rmtree(staging, ignore_errors=True)
        if self.version and self.usable and self.data.is_dir() and self._room_for_copy():
            shutil.copytree(self.data, staging)
            try:
                self._run_import(area, osm, timetable, staging)
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                raise
            with self.server.paused():  # a moment: two folders renamed
                self._swap(staging)
            return
        self.state["usable"] = False
        self._save()
        with self.server.paused():  # its files are rewritten
            self._run_import(area, osm, timetable, self.data)
        self.state["usable"] = True
        self._save()

    def _room_for_copy(self) -> bool:
        """Free disk for a copy of the data, room to grow, and some left over."""
        size = sum(f.stat().st_size for f in self.data.rglob("*") if f.is_file())
        return shutil.disk_usage(self.root).free > size * COPY_ROOM + DISK_RESERVE

    def _swap(self, staging: Path) -> None:
        old = self.root / "motis.old"
        shutil.rmtree(old, ignore_errors=True)
        self.data.replace(old)
        try:
            staging.replace(self.data)
        except OSError:
            old.replace(self.data)  # the data in use stays
            raise
        shutil.rmtree(old, ignore_errors=True)

    def _relative(self, path: Path) -> str:
        return str(path.relative_to(self.root)) if path.is_relative_to(self.root) else str(path)

    def _run_import(self, area: Area, osm: Path, timetable: Path, target: Path) -> None:
        shutil.copyfile(*self._profile(area))
        config_path = self.root / "import.yml"
        config_path.write_text(yaml.safe_dump(self.config(area, osm, timetable)), encoding="utf-8")
        log_path = self.root / "import.log"
        binary = self.vendor / "motis" / "motis.exe"
        log.info("MOTIS import (%s)", "whole country" if area.whole else f"{area.km:.0f} km")
        started = time.monotonic()
        self._run_program(
            "MOTIS import",
            [str(binary), "import", "-c", config_path.name, "-d", self._relative(target)],
            log_path,
            IMPORT_TIMEOUT,
            cwd=self.root,  # paths relative to it: see `config`
            on_tick=lambda: setattr(self, "percent", import_percent(log_path)),
        )
        log.info("MOTIS import done in %.0f s", time.monotonic() - started)


_BACKGROUND = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
    subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0
)


def import_percent(log_path: Path) -> int:
    """Average progress of the import's tasks, read from the progress bars it prints."""
    try:
        with log_path.open("rb") as f:
            f.seek(max(0, log_path.stat().st_size - 20000))
            tail = f.read()
    except OSError:
        return 0
    tasks = {name: int(pct) for name, pct in _PROGRESS.findall(tail)}
    return int(sum(tasks.values()) / len(tasks)) if tasks else 0
