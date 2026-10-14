"""Travel times from a start point to every job location, routes, and the reachable area
(PLAN §4.6).

Times are kept in `travel_times` for all four modes up to two hours, plus "any" (the fastest
of them), so the travel-time filter and its slider only look rows up by key. A new start point
gets straight-line estimates at once (labelled as such); MOTIS then replaces them in the
background: one-to-many requests per street mode and intermodal ones for public transport.
Only locations that can be reached in two hours at all (by straight line) are asked; the rest
are "not within two hours".

Jobs without a place, or only with a region, cannot be judged and always pass the filter.
"""

import logging
import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable
from contextlib import nullcontext
from datetime import datetime, timedelta

from sqlalchemy import delete, exists, func, insert, or_, select
from sqlalchemy.orm import Session, sessionmaker

from adawork.db.models import Location, TravelSet, TravelTime
from adawork.geo.home import Origin
from adawork.geo.places import REACH_KM_PER_MINUTE, distance_km
from adawork.routing.data import RoutingData
from adawork.routing.motis import MotisServer

log = logging.getLogger(__name__)

MODES = ("car", "transit", "walk", "bike")
MOTIS_MODES = {"car": "CAR", "walk": "WALK", "bike": "BIKE"}
MAX_MINUTES = 120
# Estimates: a door-to-door average speed along the straight line (km/h) plus minutes to get
# going (parking, waiting for the first connection). Rough on purpose; labelled "odhad".
ESTIMATE = {"car": (45, 5), "transit": (22, 10), "walk": (4.5, 0), "bike": (14, 2)}
UNJUDGED = ("region",)  # place precisions the filter cannot judge
AREA_CELLS = 40  # grid cells across the reachable area's radius
AREA_BUCKETS = (30, 60, 90, 120)  # the slider's ranges with their own grid
AREA_CACHE = 24
# MOTIS counts the wait from the asked time, while a commuter takes the connection that fits:
# the best of several departures within the hour is kept (the wait left is at most 15 min).
DEPARTURES = (0, 15, 30, 45)  # minutes after the departure time
KEEP_STARTS = 10
IN_USE = 60  # seconds: a start point asked for this lately is not dropped (being listed)
RETRY_SECONDS = 300


# MOTIS drives faster than other routers: on 10 trips from Kolín (M8), OSRM's car times were
# 1.10-1.25 times MOTIS's (median 1.14) and closer to what commuters see. MOTIS's are scaled.
CAR_FACTOR = 1.14


def street_seconds(motis, origin, points, mode: str, max_seconds: float) -> list[float | None]:
    """Seconds by car, on foot or by bike to each point (car calibrated, `CAR_FACTOR`)."""
    factor = CAR_FACTOR if mode == "car" else 1.0
    found = motis.one_to_many(origin, points, MOTIS_MODES[mode], int(max_seconds / factor))
    return [sec * factor if sec is not None else None for sec in found]


def transit_seconds(motis, origin, points, when: datetime, max_minutes: int) -> list[float | None]:
    """Seconds by public transport to each point, of the best connection leaving within the
    hour after `when` (counted from its own departure, within `DEPARTURES`' step)."""
    best: list[float | None] = [None] * len(points)
    for offset in DEPARTURES:
        found = motis.transit_to_many(origin, points, when + timedelta(minutes=offset), max_minutes)
        for i, sec in enumerate(found):
            if sec is not None and (best[i] is None or sec < best[i]):
                best[i] = sec
    return best


def estimate(km: float, mode: str) -> int | None:
    speed, start = ESTIMATE[mode]
    minutes = start + km / speed * 60
    return round(minutes) if minutes <= MAX_MINUTES else None


def reach_km(mode: str, minutes: int = MAX_MINUTES) -> float:
    return REACH_KM_PER_MINUTE[mode] * minutes


def departure(depart: str, today: datetime | None = None) -> datetime:
    """The next working day (from tomorrow) at `depart` ("07:00"), local time, aware."""
    day = (today or datetime.now()).date() + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    hour, minute = map(int, depart.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute).astimezone()


def set_id(origin: str, depart: str, mode: str):
    """SQL: the number of the times from `origin` at `depart` by `mode`."""
    return (
        select(TravelSet.id)
        .where(TravelSet.origin == origin, TravelSet.depart == depart, TravelSet.mode == mode)
        .scalar_subquery()
    )


def travel_table(origin: str, depart: str, mode: str):
    """SQL: location_id -> minutes by `mode` (or "any"), for joining to jobs."""
    return (
        select(TravelTime.location_id, TravelTime.minutes)
        .where(TravelTime.set_id == set_id(origin, depart, mode))
        .subquery("travel")
    )


def _rows(sets: dict[str, int], times: dict[int, dict[str, tuple[int | None, int]]]):
    """Table rows of location -> mode -> (minutes, version), with "any" added."""
    rows = []
    for loc_id, by_mode in times.items():
        reachable = [m for m, _ in by_mode.values() if m is not None]
        best = (min(reachable, default=None), by_mode["car"][1])
        for mode, (minutes, version) in [*by_mode.items(), ("any", best)]:
            rows.append(
                {"set_id": sets[mode], "location_id": loc_id, "minutes": minutes,
                 "version": version}
            )  # fmt: skip
    return rows


def within(travel, location_id, max_minutes: int):
    """SQL: reachable within `max_minutes`, or the place cannot be judged (none, or only a
    region). The query must join Location and `travel`."""
    return or_(
        location_id.is_(None),
        Location.lat.is_(None),
        Location.precision.in_(UNJUDGED),
        travel.c.minutes <= max_minutes,
    )


class CommuteService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        data: RoutingData,
        server: MotisServer,
        clock: Callable[[], datetime] = datetime.now,
        keep: Callable[[Session], Iterable[tuple[str, str]]] = lambda s: (),
    ) -> None:
        self.sessions = sessions
        self.data = data
        self.server = server
        self.clock = clock
        self.keep = keep
        self._lock = threading.Lock()
        self._writing = threading.Lock()
        self._retry_at = 0.0
        self._complete: dict[tuple, bool] = {}  # (origin, depart, version, places, located)
        self._running: tuple[str, str] | None = None  # being computed in the background
        # Asked for while another one ran, the last asked first: (key, depart) -> origin
        self._wanted: OrderedDict[tuple[str, str], Origin] = OrderedDict()
        self._area_lock = threading.Lock()  # one reachable area at a time (MOTIS is busy)
        self._used: dict[tuple[str, str], float] = {}  # start -> last asked (monotonic)
        self.progress = 0  # percent of the running computation
        self._areas: OrderedDict[tuple, dict] = OrderedDict()

    # --- The table ----------------------------------------------------------------------------

    def ensure(self, origin: Origin, depart: str) -> None:
        """Every location has times from `origin`: estimates now, exact ones in the
        background when the routing data is ready."""
        version = self.data.version if self.data.ready else 0
        self._used[(origin.key, depart)] = time.monotonic()
        with self.sessions() as s:
            # Places are added, and get their point later (geocoding): both make work.
            newest, located = s.execute(
                select(func.max(Location.id), func.count(Location.lat))
            ).one()
            key = (origin.key, depart, version, newest, located)
            if self._complete.get(key):
                return
            with self._writing:
                sets = self._sets(s, origin, depart)
                self._estimate(s, origin, sets)
                pending = self._pending(s, sets, version) if version else 0
        if not pending:
            self._complete[key] = True
            return
        self.compute_later(origin, depart)

    def computing(self, origin: Origin, depart: str) -> bool:
        """Being computed, or waiting for its turn."""
        start = (origin.key, depart)
        return self._running == start or start in self._wanted

    def _locations(self, s: Session, where=()) -> list[tuple[int, float, float]]:
        stmt = select(Location.id, Location.lat, Location.lon).where(
            Location.lat.is_not(None), Location.precision.not_in(UNJUDGED), *where
        )
        return [
            (i, lat, lon) for i, lat, lon in s.execute(stmt) if lat is not None and lon is not None
        ]

    def _sets(self, s: Session, origin: Origin, depart: str) -> dict[str, int]:
        """Mode -> number of the times from `origin` at `depart` (made when missing; then
        the start points beyond the newest `KEEP_STARTS` are dropped)."""
        where = (TravelSet.origin == origin.key, TravelSet.depart == depart)
        sets = {m: i for m, i in s.execute(select(TravelSet.mode, TravelSet.id).where(*where))}
        if len(sets) > len(MODES):
            return sets
        for mode in (*MODES, "any"):
            if mode not in sets:
                row = TravelSet(origin=origin.key, depart=depart, mode=mode)
                s.add(row)
                s.flush()
                sets[mode] = row.id
        self._prune(s, (origin.key, depart))
        return sets

    def _prune(self, s: Session, new: tuple[str, str]) -> None:
        starts = s.execute(
            select(TravelSet.origin, TravelSet.depart)
            .group_by(TravelSet.origin, TravelSet.depart)
            .order_by(func.max(TravelSet.id).desc())
        ).all()
        now = time.monotonic()
        # A list first: a request may add a start meanwhile (ensure() writes without a lock).
        for start, used in list(self._used.items()):
            if now - used >= IN_USE:
                self._used.pop(start, None)
        keep = {new, self._running, *self.keep(s), *self._used}
        old = [tuple(start) for start in starts[KEEP_STARTS:] if tuple(start) not in keep]
        for origin, depart in old:
            start = (TravelSet.origin == origin, TravelSet.depart == depart)
            ids = select(TravelSet.id).where(*start)
            s.execute(delete(TravelTime).where(TravelTime.set_id.in_(ids)))
            s.execute(delete(TravelSet).where(*start))
        if old:
            self._complete.clear()

    @staticmethod
    def _of_car(sets: dict[str, int], *where):
        """SQL: the location has a time by car (all modes are stored together) that is..."""
        return exists(
            select(TravelTime.location_id).where(
                TravelTime.set_id == sets["car"], TravelTime.location_id == Location.id, *where
            )
        )

    def _estimate(self, s: Session, origin: Origin, sets: dict[str, int]) -> None:
        """Straight-line estimates for locations with no time yet."""
        times = {}
        for loc_id, lat, lon in self._locations(s, [~self._of_car(sets)]):
            km = distance_km(origin.lat, origin.lon, lat, lon)
            times[loc_id] = {mode: (estimate(km, mode), 0) for mode in MODES}
        if times:
            s.execute(insert(TravelTime).prefix_with("OR REPLACE"), _rows(sets, times))
        s.commit()

    def _pending(self, s: Session, sets: dict[str, int], version: int) -> int:
        """Locations whose times are not from the current routing data."""
        stale = self._of_car(sets, TravelTime.version.not_in((version, -version)))
        stmt = select(func.count(Location.id)).where(
            Location.lat.is_not(None), Location.precision.not_in(UNJUDGED), stale
        )
        return s.scalar(stmt) or 0

    def compute_later(self, origin: Origin, depart: str) -> None:
        with self._lock:
            if time.monotonic() < self._retry_at:
                return
            if self._running is not None:
                start = (origin.key, depart)
                if self._running != start:
                    self._wanted[start] = origin
                    self._wanted.move_to_end(start, last=False)
                return
            self._running = (origin.key, depart)
        threading.Thread(
            target=self._compute_loop, args=(origin, depart), name="commute", daemon=True
        ).start()

    def _compute_loop(self, origin: Origin, depart: str) -> None:
        while True:
            try:
                self.compute(origin, depart)
            except Exception:
                log.exception("Travel times from %s failed", origin.label)
                self._retry_at = time.monotonic() + RETRY_SECONDS
            with self._lock:
                if not self._wanted:
                    self._running = None
                    return
                (_, depart), origin = self._wanted.popitem(last=False)
                self._running = (origin.key, depart)

    def compute(self, origin: Origin, depart: str) -> int:
        """Exact times from MOTIS for every location not computed with the current data.
        Returns how many locations were computed."""
        if not self.data.ready:
            return 0
        version, area = self.data.version, self.data.area
        started = time.monotonic()
        self.progress = 0
        with self.sessions() as s:
            with self._writing:
                sets = self._sets(s, origin, depart)
                s.commit()
            current = self._of_car(sets, TravelTime.version.in_((version, -version)))
            todo = self._locations(s, [~current])
        if not todo:
            return 0
        distances = {i: distance_km(origin.lat, origin.lon, lat, lon) for i, lat, lon in todo}
        when = departure(depart, self.clock())
        timetable = self._timetable_covers(when)
        result: dict[int, dict[str, tuple[int | None, int]]] = {}
        start_inside = area is None or area.contains(*origin.point)
        for loc_id, lat, lon in todo:  # outside the data: estimates are all there is
            if not (start_inside and (area is None or area.contains(lat, lon))):
                result[loc_id] = {m: (estimate(distances[loc_id], m), -version) for m in MODES}
        inside = [t for t in todo if t[0] not in result]
        with self.server.use(self.data.data) if inside else nullcontext() as motis:
            for i, mode in enumerate(MODES):
                near = [t for t in inside if distances[t[0]] <= reach_km(mode)]
                points = [(lat, lon) for _, lat, lon in near]
                if mode == "transit" and not timetable:  # estimates until new timetables
                    for loc_id, _, _ in inside:
                        result.setdefault(loc_id, {})[mode] = (
                            estimate(distances[loc_id], mode),
                            -version,
                        )
                    continue
                if not points:
                    seconds = []
                elif mode == "transit":
                    seconds = transit_seconds(motis, origin.point, points, when, MAX_MINUTES)
                else:
                    seconds = street_seconds(motis, origin.point, points, mode, MAX_MINUTES * 60)
                found = dict(zip((t[0] for t in near), seconds, strict=True))
                for loc_id, _, _ in inside:
                    sec = found.get(loc_id)
                    minutes = math.ceil(sec / 60) if sec is not None else None
                    if minutes is not None and minutes > MAX_MINUTES:
                        minutes = None
                    result.setdefault(loc_id, {})[mode] = (minutes, version)
                self.progress = int((i + 1) * 100 / len(MODES))
        with self._writing, self.sessions() as s:
            s.execute(insert(TravelTime).prefix_with("OR REPLACE"), _rows(sets, result))
            s.commit()
        took = time.monotonic() - started
        log.info("Travel times from %s: %d locations in %.1f s", origin.label, len(todo), took)
        return len(todo)

    def _timetable_covers(self, when: datetime) -> bool:
        until = self.data.timetable_until
        return until is None or when.date() <= until

    def times(
        self, s: Session, origin: Origin, depart: str, location_ids
    ) -> dict[int, dict[str, tuple[int | None, bool]]]:
        """Location -> mode -> (minutes, exact) for the listed locations."""
        found: dict[int, dict[str, tuple[int | None, bool]]] = {}
        ids = [i for i in set(location_ids) if i is not None]
        if not ids:
            return found
        stmt = (
            select(TravelTime.location_id, TravelSet.mode, TravelTime.minutes, TravelTime.version)
            .join(TravelSet)
            .where(
                TravelSet.origin == origin.key,
                TravelSet.depart == depart,
                TravelTime.location_id.in_(ids),
            )
        )
        for loc_id, mode, minutes, version in s.execute(stmt):
            found.setdefault(loc_id, {})[mode] = (minutes, version > 0)
        return found

    def forget(self) -> None:
        """New routing data: every start point is computed again when next used."""
        self._complete.clear()
        with self._lock:
            self._areas.clear()

    # --- Reachable area -----------------------------------------------------------------------

    def area(self, origin: Origin, modes: tuple[str, ...], depart: str, minutes: int) -> dict:
        """GeoJSON grid cells around `origin`, each with the minutes to its centre by the
        fastest of `modes` (property "m"), for slider values up to `minutes`' bucket. The map
        shows the cells with m <= the slider, so moving it needs no new request."""
        bucket = next((b for b in AREA_BUCKETS if minutes <= b), MAX_MINUTES)
        version = self.data.version if self.data.ready else 0
        key = (origin.key, modes, depart, bucket, version)
        with self._area_lock:  # the same area asked twice (a slider moved) is made once
            with self._lock:
                if key in self._areas:
                    self._areas.move_to_end(key)
                    return self._areas[key]
            result = self._area(origin, modes, depart, bucket, version)
            with self._lock:
                self._areas[key] = result
                while len(self._areas) > AREA_CACHE:
                    self._areas.popitem(last=False)
            return result

    def _area(self, origin: Origin, modes: tuple[str, ...], depart: str, bucket: int, version):
        radius = max(reach_km(m, bucket) for m in modes)
        step = radius / AREA_CELLS
        dlat = step / 111.32
        dlon = step / (111.32 * math.cos(math.radians(origin.lat)))
        centres = []
        n = AREA_CELLS
        for i in range(-n, n + 1):
            for j in range(-n, n + 1):
                if i * i + j * j <= n * n:
                    centres.append((origin.lat + i * dlat, origin.lon + j * dlon))
        best: list[float | None] = [None] * len(centres)

        def take(index: int, minutes_: float | None) -> None:
            if minutes_ is None or minutes_ > bucket:
                return
            current = best[index]
            if current is None or minutes_ < current:
                best[index] = minutes_

        exact = version and (self.data.area is None or self.data.area.contains(*origin.point))
        estimated = not exact
        when = departure(depart, self.clock())
        if exact:
            with self.server.use(self.data.data) as motis:
                for mode in modes:
                    if mode == "transit" and not self._timetable_covers(when):
                        estimated = True
                        for k, c in enumerate(centres):
                            take(k, estimate(distance_km(origin.lat, origin.lon, *c), mode))
                        continue
                    near = [
                        k for k, c in enumerate(centres)
                        if distance_km(origin.lat, origin.lon, *c) <= reach_km(mode, bucket)
                    ]  # fmt: skip
                    points = [centres[k] for k in near]
                    if mode == "transit":
                        seconds = transit_seconds(motis, origin.point, points, when, bucket)
                    else:
                        seconds = street_seconds(motis, origin.point, points, mode, bucket * 60)
                    for k, sec in zip(near, seconds, strict=True):
                        take(k, sec / 60 if sec is not None else None)
        else:
            for k, c in enumerate(centres):
                km = distance_km(origin.lat, origin.lon, *c)
                for mode in modes:
                    take(k, estimate(km, mode))
        features = []
        for (lat, lon), m in zip(centres, best, strict=True):
            if m is None:
                continue
            ring = [
                [round(lon - dlon / 2, 5), round(lat - dlat / 2, 5)],
                [round(lon + dlon / 2, 5), round(lat - dlat / 2, 5)],
                [round(lon + dlon / 2, 5), round(lat + dlat / 2, 5)],
                [round(lon - dlon / 2, 5), round(lat + dlat / 2, 5)],
                [round(lon - dlon / 2, 5), round(lat - dlat / 2, 5)],
            ]
            features.append(
                {"type": "Feature", "properties": {"m": math.ceil(m)},
                 "geometry": {"type": "Polygon", "coordinates": [ring]}}
            )  # fmt: skip
        return {
            "type": "FeatureCollection",
            "features": features,
            "bucket": bucket,
            "estimate": estimated,
        }


def decode_polyline(encoded: str, precision: int = 6) -> list[list[float]]:
    """Google's encoded polyline (MOTIS uses precision 6) -> [[lon, lat], ...]."""
    coords, index, lat, lon = [], 0, 0, 0
    factor = 10**precision
    while index < len(encoded):
        for which in (0, 1):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if which == 0:
                lat += delta
            else:
                lon += delta
        coords.append([round(lon / factor, 6), round(lat / factor, 6)])
    return coords


def _local(iso: str | None) -> datetime | None:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone() if iso else None


def _legs(itinerary: dict) -> list[dict]:
    legs = []
    for leg in itinerary.get("legs", []):
        geometry = leg.get("legGeometry") or {}
        legs.append(
            {
                "mode": leg.get("mode", ""),
                "line": leg.get("routeShortName") or leg.get("displayName") or "",
                "headsign": leg.get("headsign") or "",
                "from": (leg.get("from") or {}).get("name", ""),
                "to": (leg.get("to") or {}).get("name", ""),
                "start": _local(leg.get("startTime")),
                "end": _local(leg.get("endTime")),
                "minutes": round(leg.get("duration", 0) / 60),
                "distance": leg.get("distance"),
                "coords": decode_polyline(geometry["points"], geometry.get("precision", 6))
                if geometry.get("points")
                else [],
            }
        )
    return legs


def routes(plan: dict, mode: str) -> list[dict]:
    """The routes of a MOTIS plan answer for the commute window: start, end, minutes,
    transfers, km and legs (with line numbers, stops, times and the drawn line)."""
    # For public transport, walking all the way comes as a "direct" route: listed after.
    direct = plan.get("direct", [])
    found = [*plan.get("itineraries", []), *direct] if mode == "transit" else direct
    factor = CAR_FACTOR if mode == "car" else 1.0
    out = []
    for itinerary in found:
        legs = _legs(itinerary)
        meters = sum(leg["distance"] or 0 for leg in legs)
        start = _local(itinerary.get("startTime"))
        seconds = itinerary.get("duration", 0) * factor
        out.append(
            {
                "start": start,
                "end": start + timedelta(seconds=seconds) if start else None,
                "minutes": round(seconds / 60),
                "transfers": itinerary.get("transfers", 0),
                "km": round(meters / 1000, 1) if meters else None,
                "legs": legs,
            }
        )
    return out
