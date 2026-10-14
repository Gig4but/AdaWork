import csv
import io
import json
import threading
import time
import zipfile
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select
from starlette.datastructures import QueryParams

from adawork.core import ParsedJob, ParsedLocation
from adawork.db.models import Job, TravelSet, TravelTime
from adawork.db.session import make_engine, make_sessionmaker, migrate
from adawork.geo.home import Origin, locate, parse_point
from adawork.geo.places import PlaceResolver, distance_km
from adawork.routing import deeplinks, gtfs
from adawork.routing.commute import (
    CAR_FACTOR,
    DEPARTURES,
    KEEP_STARTS,
    CommuteService,
    decode_polyline,
    departure,
    routes,
)
from adawork.routing.data import GTFS_EVERY, LOOK_EVERY, WAIT_FOR_HOME, Area, RoutingData, fetch
from adawork.scraper.store import JobStore
from adawork.search.service import Filters, filters_from, search_jobs

KOLIN = Origin(50.03, 15.20, "Kolín")
# name, precision, lat, lon (Pardubice ~41 km and Praha ~55 km from Kolín)
PLACES = [
    ("Kolín", "address", 50.031, 15.201),
    ("Pardubice", "municipality", 50.04, 15.78),
    ("Praha", "municipality", 50.08, 14.43),
    ("Středočeský kraj", "region", 49.95, 14.60),
]


@pytest.fixture
def sessions(tmp_path, municipalities):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    sessions = make_sessionmaker(engine)
    with sessions() as s:
        municipalities(s)
        store = JobStore(s, "x")
        for i, (name, precision, lat, lon) in enumerate(PLACES):
            location = ParsedLocation(
                key=f"test:{i}", precision=precision, municipality=name, lat=lat, lon=lon
            )
            store.add(ParsedJob(external_id=name, title=f"Skladník {name}", location=location))
        store.add(ParsedJob(external_id="nowhere", title="Skladník kdekoli"))
        store.commit()
    yield sessions
    engine.dispose()


class FakeMotis:
    """Seconds = km / speed; transit gets slower the later it is asked (the best departure
    must win)."""

    SPEED = {"CAR": 60, "BIKE": 15, "WALK": 5}

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def one_to_many(self, origin, points, mode, max_seconds):
        self.calls.append((mode, len(points), max_seconds))
        out = []
        for p in points:
            sec = distance_km(*origin, *p) / self.SPEED[mode] * 3600
            out.append(sec if sec <= max_seconds else None)
        return out

    def transit_to_many(self, origin, points, when, max_minutes):
        self.calls.append(("TRANSIT", len(points), when.strftime("%H:%M")))
        wait = (when.minute % 30) * 60
        out = []
        for p in points:
            sec = distance_km(*origin, *p) / 40 * 3600 + 600 + wait
            out.append(sec if sec <= max_minutes * 60 else None)
        return out


class FakeServer:
    def __init__(self) -> None:
        self.motis = FakeMotis()

    @contextmanager
    def use(self, data):
        yield self.motis


class FakeData:
    def __init__(self, ready=False, version=0, area=None) -> None:
        self.ready, self.version, self.area, self.data = ready, version, area, Path("motis")
        self.timetable_until = None


def rows(sessions, mode):
    with sessions() as s:
        stmt = select(Job.title, TravelTime.minutes, TravelTime.version).join(
            TravelTime, TravelTime.location_id == Job.location_id
        )
        from adawork.db.models import TravelSet

        stmt = stmt.join(TravelSet).where(TravelSet.mode == mode)
        return {title: (minutes, version) for title, minutes, version in s.execute(stmt)}


def wait_idle(commute):
    deadline = time.monotonic() + 10
    while commute._running is not None and time.monotonic() < deadline:
        time.sleep(0.02)


def test_estimates_at_once_then_exact_times_in_the_background(sessions):
    data = FakeData()
    commute = CommuteService(sessions, data, FakeServer())
    commute.ensure(KOLIN, "07:00")
    est = rows(sessions, "car")
    assert est["Skladník Kolín"] == (5, 0)  # 0 km: the minutes to get going
    assert est["Skladník Praha"][1] == 0 and "Skladník Středočeský kraj" not in est
    assert rows(sessions, "any")["Skladník Pardubice"][0] == min(
        m for m, _ in (rows(sessions, mode)["Skladník Pardubice"] for mode in ("car", "transit"))
        if m is not None
    )  # fmt: skip

    data.ready, data.version = True, 1
    commute.ensure(KOLIN, "07:00")  # stale estimates: computed in the background
    wait_idle(commute)
    car = rows(sessions, "car")
    km = distance_km(KOLIN.lat, KOLIN.lon, 50.04, 15.78)
    assert car["Skladník Pardubice"] == (round(km / 60 * 60 * CAR_FACTOR + 0.5), 1)
    assert rows(sessions, "walk")["Skladník Pardubice"] == (None, 1)  # beyond reach: not asked
    calls = commute.server.motis.calls
    assert [c[2] for c in calls if c[0] == "TRANSIT"] == [
        f"07:{m:02d}" if m else "07:00" for m in DEPARTURES
    ]
    # The best departure: no waiting at 07:00 or 07:30 in the fake.
    transit = rows(sessions, "transit")["Skladník Pardubice"][0]
    assert transit == pytest.approx(km / 40 * 60 + 10, abs=1)

    calls.clear()
    commute.ensure(KOLIN, "07:00")  # nothing new: no requests
    wait_idle(commute)
    assert calls == []


def test_places_geocoded_later_get_times(sessions):
    with sessions() as s:
        store = JobStore(s, "x")
        late = ParsedLocation(key="test:late", precision="address", municipality="Kolín")
        store.add(ParsedJob(external_id="late", title="Skladník později", location=late))
        store.commit()
    commute = CommuteService(sessions, FakeData(), FakeServer())
    commute.ensure(KOLIN, "07:00")
    assert "Skladník později" not in rows(sessions, "car")
    with sessions() as s:  # geocoding fills the point of the place stored before
        s.scalar(select(Job).where(Job.title == "Skladník později")).location.lat = 50.04
        s.scalar(select(Job).where(Job.title == "Skladník později")).location.lon = 15.21
        s.commit()
    commute.ensure(KOLIN, "07:00")
    assert rows(sessions, "car")["Skladník později"][1] == 0


def test_a_start_point_waiting_for_its_turn_counts_as_computing(sessions):
    commute = CommuteService(sessions, FakeData(ready=True, version=1), FakeServer())
    other = Origin(50.1, 15.1, "Jinde")
    commute._running = (other.key, "07:00")  # another one is being computed
    commute.compute_later(KOLIN, "07:00")
    commute.compute_later(Origin(50.2, 15.0, "Ještě jinde"), "07:00")
    assert commute.computing(KOLIN, "07:00")  # queued, not dropped: the bar keeps asking
    assert list(commute._wanted)[0][0] == "50.2000,15.0000"  # the last asked goes first


def test_times_beyond_the_timetables_are_estimates(sessions):
    data = FakeData(ready=True, version=1)
    data.timetable_until = datetime(2026, 1, 1).date()  # the PC was off for weeks
    commute = CommuteService(sessions, data, FakeServer())
    commute.ensure(KOLIN, "07:00")
    wait_idle(commute)
    assert rows(sessions, "transit")["Skladník Pardubice"][1] == -1  # final for this data
    assert rows(sessions, "car")["Skladník Pardubice"][1] == 1
    assert not any(c[0] == "TRANSIT" for c in commute.server.motis.calls)


def test_travel_filter_and_sort(sessions):
    commute = CommuteService(sessions, FakeData(ready=True, version=1), FakeServer())
    commute.ensure(KOLIN, "07:00")
    wait_idle(commute)
    with sessions() as s:

        def titles(**kw):
            f = Filters(origin=KOLIN.key, **kw)
            return [j.title for j in search_jobs(s, f, limit=50).jobs]

        within_30 = titles(travel_mode="car", travel_max=30)
        # Unknown places (only a region, or none) cannot be judged: always listed.
        assert set(within_30) == {"Skladník Kolín", "Skladník Středočeský kraj", "Skladník kdekoli"}
        assert "Skladník Pardubice" in titles(travel_mode="car", travel_max=60)
        assert "Skladník Pardubice" not in titles(travel_mode="bike", travel_max=60)
        assert "Skladník Pardubice" in titles(travel_mode="any", travel_max=60)
        by_time = titles(travel_mode="car", sort="travel")  # not filtered, only sorted
        assert by_time[:3] == ["Skladník Kolín", "Skladník Pardubice", "Skladník Praha"]
        defaults = {"at": KOLIN.key, "from": "Kolín", "max_min": "45", "mode": "x"}
        f = filters_from(QueryParams(""), defaults)
        assert (f.origin, f.travel_max, f.travel_mode, f.origin_label) == (
            KOLIN.key, 45, "any", "Kolín"
        )  # fmt: skip
        no_limit = filters_from(QueryParams("max_min=0"), {"at": KOLIN.key, "max_min": "45"})
        assert no_limit.travel_max is None

        times = commute.times(s, KOLIN, "07:00", [j.location_id for j in s.scalars(select(Job))])
    exact = [t for by_mode in times.values() for t in by_mode.values()]
    assert exact and all(flag for _, flag in exact)


def test_places_outside_the_imported_area_keep_estimates(sessions):
    data = FakeData(ready=True, version=2, area=Area(30, KOLIN.lat, KOLIN.lon))
    commute = CommuteService(sessions, data, FakeServer())
    commute.ensure(KOLIN, "07:00")
    wait_idle(commute)
    car = rows(sessions, "car")
    assert car["Skladník Kolín"][1] == 2  # inside: from MOTIS
    assert car["Skladník Praha"][1] == -2  # outside: an estimate, final for this data
    with sessions() as s:
        times = commute.times(s, KOLIN, "07:00", [j.location_id for j in s.scalars(select(Job))])
    assert not any(flag for loc in times.values() for m, (_, flag) in loc.items() if m == "car"
                   and _ is not None and _ > 60)  # fmt: skip


def test_old_start_points_are_dropped(sessions, monkeypatch):
    monkeypatch.setattr("adawork.routing.commute.IN_USE", 0)  # all asked for long ago
    home = Origin(50.0, 15.0, "Domov")
    commute = CommuteService(
        sessions, FakeData(), FakeServer(), keep=lambda s: [(home.key, "07:00")]
    )
    commute.ensure(home, "07:00")
    starts = [Origin(50.0 + i / 100, 15.1, f"Bod {i}") for i in range(KEEP_STARTS + 2)]
    for origin in starts:
        commute.ensure(origin, "07:00")
    with sessions() as s:
        kept = set(s.scalars(select(TravelSet.origin).distinct()))
        times = s.scalar(select(func.count()).select_from(TravelTime))
    assert kept == {home.key, *(o.key for o in starts[-KEEP_STARTS:])}
    assert times == len(kept) * 5 * 3  # five modes to the three places that can be judged
    commute.ensure(starts[0], "07:00")  # dropped: estimated again
    with sessions() as s:
        again = s.scalar(select(func.count()).select_from(TravelTime).join(TravelSet).where(
            TravelSet.origin == starts[0].key))  # fmt: skip
    assert again == 5 * 3

    monkeypatch.setattr("adawork.routing.commute.IN_USE", 60)
    commute.ensure(starts[0], "07:00")  # being listed now
    for i in range(KEEP_STARTS):
        commute.ensure(Origin(49.0 + i / 100, 15.2, f"Jiný {i}"), "07:00")
    with sessions() as s:
        assert starts[0].key in set(s.scalars(select(TravelSet.origin)))  # not dropped meanwhile


def test_requests_add_a_start_point_at_the_same_time(sessions):
    commute = CommuteService(sessions, FakeData(), FakeServer())
    barrier, errors = threading.Barrier(4), []

    def ask():
        barrier.wait()
        try:
            commute.ensure(KOLIN, "07:00")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=ask) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and len(rows(sessions, "car")) == 3


def test_reachable_area_from_estimates(sessions):
    commute = CommuteService(sessions, FakeData(), FakeServer())
    area = commute.area(KOLIN, ("car",), "07:00", 20)
    assert area["bucket"] == 30 and area["estimate"]
    minutes = [f["properties"]["m"] for f in area["features"]]
    assert min(minutes) == 5 and max(minutes) <= 30
    assert commute.area(KOLIN, ("car",), "07:00", 25) is area  # same range: cached


def test_departure_is_the_next_working_day():
    friday = datetime(2026, 10, 16, 15, 0)
    when = departure("07:30", friday)
    assert (when.weekday(), when.hour, when.minute) == (0, 7, 30) and when.tzinfo


def test_routes_from_a_plan():
    leg = {
        "mode": "REGIONAL_RAIL", "routeShortName": "S1", "headsign": "Praha",
        "from": {"name": "Kolín"}, "to": {"name": "Praha hl.n."},
        "startTime": "2026-10-15T05:12:00Z", "endTime": "2026-10-15T05:50:00Z",
        "duration": 2280, "distance": 60000.0,
        "legGeometry": {"points": "_p~iF~ps|U_ulLnnqC_mqNvxq`@", "precision": 5},
    }  # fmt: skip
    plan = {
        "itineraries": [{"duration": 2280, "startTime": leg["startTime"],
                         "endTime": leg["endTime"], "transfers": 0, "legs": [leg]}],
        "direct": [{"duration": 60000, "startTime": leg["startTime"], "legs": []}],
    }  # fmt: skip
    found = routes(plan, "transit")
    assert len(found) == 2  # walking all the way comes after the connections
    first = found[0]
    assert (first["minutes"], first["km"], first["legs"][0]["line"]) == (38, 60.0, "S1")
    assert first["legs"][0]["coords"][0] == [-120.2, 38.5]
    car = routes({"direct": [{"duration": 600, "startTime": leg["startTime"], "legs": []}]}, "car")
    assert car[0]["minutes"] == round(600 * CAR_FACTOR / 60)  # calibrated


def test_polyline():
    assert decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@", 5) == [
        [-120.2, 38.5], [-120.95, 40.7], [-126.453, 43.252]
    ]  # fmt: skip


def test_deep_links():
    when = datetime(2026, 10, 15, 7, 5)
    assert deeplinks.google((50.0, 15.2), (50.1, 14.4), "transit").endswith(
        "origin=50.0%2C15.2&destination=50.1%2C14.4&travelmode=transit"
    )
    assert "start=15.2%2C50.0&end=14.4%2C50.1&routeType=bike_road" in deeplinks.mapy(
        (50.0, 15.2), (50.1, 14.4), "bike"
    )
    assert deeplinks.mapy((50.0, 15.2), (50.1, 14.4), "transit") is None
    url = deeplinks.idos("Kolín", "Praha", when, arrive=True)
    assert "f=Kol%C3%ADn&t=Praha&date=15.10.2026&time=7%3A05&arr=true" in url


# --- Timetables ----------------------------------------------------------------------------------


def write_gtfs(path: Path) -> None:
    files = {
        "stops.txt": [["stop_id", "stop_name", "stop_lat", "stop_lon", "parent_station"],
                      ["A", "Kolín", "50.02", "15.21", "S"], ["A2", "Kolín", "50.02", "15.21", "S"],
                      ["S", "Kolín st.", "50.02", "15.21", ""],
                      ["B", "Praha", "50.08", "14.43", ""], ["C", "Ostrava", "49.83", "18.29", ""]],
        "trips.txt": [["route_id", "service_id", "trip_id", "block_id"],
                      ["R1", "W", "T1", "blk"], ["R2", "W", "T2", "blk"]],
        "stop_times.txt": [["trip_id", "stop_id", "stop_sequence"],
                           ["T1", "A", "1"], ["T1", "B", "2"], ["T2", "C", "1"], ["T2", "B", "2"]],
        "routes.txt": [["route_id", "route_short_name"], ["R1", "S1"], ["R2", "R2"]],
        "calendar.txt": [["service_id", "monday"], ["W", "1"]],
        "transfers.txt": [["from_stop_id", "to_stop_id", "transfer_type"],
                          ["A", "B", "4"], ["A", "B", "2"]],
        "agency.txt": [["agency_id"], ["X"]],
    }  # fmt: skip
    with zipfile.ZipFile(path, "w") as z:
        for name, table in files.items():
            buf = io.StringIO()
            csv.writer(buf, lineterminator="\n").writerows(table)
            z.writestr(name, buf.getvalue())


def read_gtfs(path: Path, name: str) -> list[list[str]]:
    with zipfile.ZipFile(path) as z:
        return list(csv.reader(io.StringIO(z.read(name).decode("utf-8"))))[1:]


def test_timetables_are_sanitized_and_clipped(tmp_path):
    source = tmp_path / "in.zip"
    write_gtfs(source)
    target = tmp_path / "out" / "timetable.zip"
    counts = gtfs.prepare(source, target)  # the whole country: only sanitized
    assert counts["trips.txt"] == 2
    assert all(row[3] == "" for row in read_gtfs(target, "trips.txt"))  # no in-seat transfers
    assert read_gtfs(target, "transfers.txt") == [["A", "B", "2"]]
    assert read_gtfs(target, "agency.txt") == [["X"]]  # copied as is

    gtfs.prepare(source, target, Area(30, 50.03, 15.20).contains)  # 30 km around Kolín
    assert [r[2] for r in read_gtfs(target, "trips.txt")] == ["T1"]
    # The trip stays whole (to Praha); the station and its other platform stay too.
    assert {r[0] for r in read_gtfs(target, "stops.txt")} == {"A", "A2", "S", "B"}
    assert [r[0] for r in read_gtfs(target, "routes.txt")] == ["R1"]


# --- Map and timetable data ----------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 14, 9, 0)

    def __call__(self) -> datetime:
        return self.now


class StoppedServer:
    def __init__(self) -> None:
        self.stops = 0

    @contextmanager
    def paused(self):
        self.stops += 1
        yield


def unchanged(request: httpx.Request) -> httpx.Response:
    return httpx.Response(304)


def routing_data(tmp_path, area, client=None, clock=None):
    client = client or (lambda: httpx.Client(transport=httpx.MockTransport(unchanged)))
    data = RoutingData(
        tmp_path / "routing", tmp_path / "vendor", StoppedServer(), lambda: area[0], client,
        clock=clock or Clock(),
    )  # fmt: skip
    steps = []

    def osm(*args):
        steps.append(("osmium", args[0]))
        Path(args[args.index("-o") + 1]).write_bytes(b"pbf")

    def run_import(area_, osm_path, timetable, target):
        steps.append(("import", area_.whole, osm_path.name, timetable.exists()))
        data.targets.append(target.name)
        target.mkdir(parents=True, exist_ok=True)
        (target / "made").write_text(str(len(steps)))  # which import made the data
        if data.fail_import:
            data.fail_import -= 1
            raise RuntimeError("import failed")

    data.targets = []

    data.fail_import = 0
    data._osmium = osm
    data._run_import = run_import
    return data, steps


def test_map_data_is_imported_for_the_area_this_pc_needs(tmp_path):
    def gtfs_site(request):
        if request.url.path.endswith(".pbf"):
            return httpx.Response(200, content=b"osm", headers={"ETag": '"o1"'})
        buf = tmp_path / "g.zip"
        write_gtfs(buf)
        return httpx.Response(200, content=buf.read_bytes())

    area = [None]  # a home area wanted, but no home yet
    clock = Clock()
    data, steps = routing_data(
        tmp_path, area, lambda: httpx.Client(transport=httpx.MockTransport(gtfs_site)), clock
    )
    assert not data.due() and data.look() == WAIT_FOR_HOME  # asked again soon
    area[0] = Area(150, 50.03, 15.20)
    assert data.due() and not data.ready
    ready = []
    data.on_ready = lambda: ready.append(data.ready)
    data.update()
    assert ready == [True]  # exact times start from home at once
    assert steps == [("osmium", "extract"), ("osmium", "renumber"),
                     ("import", False, "area.osm.pbf", True)]  # fmt: skip
    assert data.ready and data.version == 1 and data.area == area[0]
    config = data.config(area[0], Path("a.pbf"), Path("t.zip"))
    assert config["geocoding"] is False and config["tiles"]["profile"].endswith("background.lua")
    assert not data.due() and data.look() == LOOK_EVERY

    area[0] = Area(150, 50.05, 15.25)  # moved a few km: the same area
    assert not data.due()
    clock.now += GTFS_EVERY
    assert data.due()  # timetables a week old
    area[0] = Area()  # more RAM now: the whole country
    steps.clear()
    data.update()
    assert steps == [("import", True, "czech-republic-latest.osm.pbf", True)]
    assert data.config(area[0], Path("a"), Path("t"))["geocoding"] is True


def test_a_failed_import_keeps_the_previous_timetables(tmp_path):
    feeds = iter([b"v1", b"v2"])

    def site(request):
        if request.url.path.endswith(".pbf"):
            return (
                httpx.Response(304)
                if request.headers.get("If-None-Match")
                else httpx.Response(200, content=b"osm", headers={"ETag": '"o1"'})
            )
        buf = tmp_path / "g.zip"
        write_gtfs(buf)
        with zipfile.ZipFile(buf, "a") as z:
            z.writestr("feed_info.txt", next(feeds))
        return httpx.Response(200, content=buf.read_bytes())

    clock = Clock()
    data, steps = routing_data(
        tmp_path, [Area()], lambda: httpx.Client(transport=httpx.MockTransport(site)), clock
    )
    data.update()
    clock.now += GTFS_EVERY
    data.fail_import = 1
    with pytest.raises(RuntimeError):
        data.update()
    # Made beside the data in use, which keeps working; the inputs are those it came from.
    assert data.targets == ["motis", "motis.new"] and data.ready
    assert (data.data / "made").read_text() == "1" and not (data.root / "motis.new").exists()
    timetable = data.root / "input" / "timetable.zip"
    with zipfile.ZipFile(timetable) as z:
        assert z.read("feed_info.txt") == b"v1"
    assert data.version == 1 and data.status()["error"] == "import failed"
    clock.now += timedelta(hours=1)
    assert not data.due()  # that feed is not imported again: the next one is
    clock.now += GTFS_EVERY
    assert data.due()


def test_downloads_continue_where_they_stopped(tmp_path):
    etag = ['"e"']

    def site(request):
        if request.headers.get("Range") == "bytes=3-" and request.headers["If-Range"] == etag[0]:
            return httpx.Response(206, content=b"456", headers={"ETag": etag[0]})
        if request.headers.get("If-None-Match") == etag[0]:
            return httpx.Response(304)
        body = b"123456" if etag[0] == '"e"' else b"abcdef"
        return httpx.Response(200, content=body, headers={"ETag": etag[0]})

    client = httpx.Client(transport=httpx.MockTransport(site))
    path = tmp_path / "big.pbf"
    (tmp_path / "big.pbf.part").write_bytes(b"123")
    (tmp_path / "big.pbf.part.json").write_text(json.dumps({"etag": '"e"'}))
    assert fetch(client, "https://x/big.pbf", path) is True
    assert path.read_bytes() == b"123456"
    assert fetch(client, "https://x/big.pbf", path) is False  # unchanged
    assert json.loads((tmp_path / "big.pbf.meta.json").read_text())["etag"] == '"e"'
    assert not (tmp_path / "big.pbf.part.json").exists()

    # The file changed since the part was downloaded: it is downloaded whole again.
    etag[0] = '"f"'
    (tmp_path / "big.pbf.part").write_bytes(b"123")
    (tmp_path / "big.pbf.part.json").write_text(json.dumps({"etag": '"e"'}))
    assert fetch(client, "https://x/big.pbf", path) is True
    assert path.read_bytes() == b"abcdef"


def test_a_part_without_its_version_is_not_continued(tmp_path):
    def site(request):
        assert "Range" not in request.headers
        return httpx.Response(200, content=b"123456", headers={"ETag": '"e"'})

    path = tmp_path / "big.pbf"
    (tmp_path / "big.pbf.part").write_bytes(b"xyz")
    assert fetch(httpx.Client(transport=httpx.MockTransport(site)), "https://x/big.pbf", path)
    assert path.read_bytes() == b"123456"


def test_a_part_that_no_longer_fits_is_downloaded_again(tmp_path):
    def site(request):
        if "Range" in request.headers:  # the part is already whole (the app closed before use)
            return httpx.Response(416, headers={"Content-Range": "bytes */6"})
        return httpx.Response(200, content=b"123456", headers={"ETag": '"e"'})

    path = tmp_path / "big.pbf"
    (tmp_path / "big.pbf.part").write_bytes(b"123456")
    (tmp_path / "big.pbf.part.json").write_text(json.dumps({"etag": '"e"'}))
    assert fetch(httpx.Client(transport=httpx.MockTransport(site)), "https://x/big.pbf", path)
    assert path.read_bytes() == b"123456"


def test_a_new_import_is_swapped_in_when_done(tmp_path):
    clock = Clock()
    data, steps = routing_data(tmp_path, [Area()], clock=clock)
    (data.osm_download.parent).mkdir(parents=True)
    data.osm_download.write_bytes(b"osm")
    write_gtfs(data.gtfs_download)
    data.update()
    (data.data / "kept").write_text("x")  # what MOTIS keeps to skip unchanged steps
    data.state["osm_pending"] = True
    data.fail_import = 1
    with pytest.raises(RuntimeError):
        data.update()
    assert data.ready and (data.data / "made").read_text() == "1"  # still the old data
    clock.now += timedelta(hours=1)
    data.update()
    assert data.version == 2 and (data.data / "made").read_text() == "3"
    assert (data.data / "kept").exists()  # the import started from a copy
    assert not (data.root / "motis.new").exists() and not (data.root / "motis.old").exists()

    data.data.replace(data.root / "motis.old")  # the app ended between the two renames
    reloaded, _ = routing_data(tmp_path, [Area()], clock=clock)
    assert (reloaded.data / "made").read_text() == "3"


def test_data_from_a_failed_import_is_not_used(tmp_path):
    clock = Clock()
    data, steps = routing_data(tmp_path, [Area()], clock=clock)
    (data.osm_download.parent).mkdir(parents=True)
    data.osm_download.write_bytes(b"osm")
    write_gtfs(data.gtfs_download)
    data.update()
    assert data.ready and data.version == 1
    data._room_for_copy = lambda: False  # no disk room for a copy: imported in place
    data.state["osm_pending"] = True  # a new map, its import fails (or the app closes)
    data.fail_import = 1
    with pytest.raises(RuntimeError):
        data.update()
    assert not data.ready and data.status()["error"]
    reloaded, _ = routing_data(tmp_path, [Area()], clock=clock)
    assert not reloaded.ready  # also after a restart
    clock.now += timedelta(hours=1)
    assert data.due()
    data.update()
    assert data.ready and data.version == 2


# --- Start points --------------------------------------------------------------------------------

ADR_HEADER = (
    "Kód ADM;Kód obce;Název obce;Kód MOMC;Název MOMC;Kód obvodu Prahy;Název obvodu Prahy;"
    "Kód části obce;Název části obce;Kód ulice;Název ulice;Typ SO;Číslo domovní;"
    "Číslo orientační;Znak čísla orientačního;PSČ;Souřadnice Y;Souřadnice X;Platí Od"
)


def test_a_typed_address_becomes_a_start_point(tmp_path, municipalities):
    path = tmp_path / "adr.zip"
    rows = [
        "1;533165;Kolín;;;;;1;Kolín I;1;Politických vězňů;č.p.;105;;;28002;680000;1065000;x",
        "2;533165;Kolín;;;;;1;Kolín I;1;Politických vězňů;č.p.;106;;;28002;680100;1065000;x",
        "3;533165;Kolín;;;;;1;Kolín I;2;Kolínská;č.p.;12;;;28002;681000;1066000;x",
    ]
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "CSV/20260930_OB_533165_ADR.csv", "\n".join([ADR_HEADER, *rows]).encode("cp1250")
        )
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    with make_sessionmaker(engine)() as s:
        municipalities(s)
        places = PlaceResolver(s)
    engine.dispose()
    house = locate(places, path, "Politických vězňů 105, 280 02 Kolín")
    assert (house.label, house.precision) == ("Politických vězňů 105, Kolín", "address")
    street = locate(places, path, "Kolín, Politických vězňů")
    assert street.precision == "street" and street.lat != house.lat
    named = locate(places, path, "Kolínská 12, Kolín")  # a street named after the town
    assert (named.label, named.precision) == ("Kolínská 12, Kolín", "address")
    assert locate(places, path, "Kolín IV, Kolín").precision == "municipality"
    town = locate(places, None, "Kolín")
    assert (town.lat, town.lon, town.precision, town.key) == (
        50.03,
        15.2,
        "municipality",
        "50.0300,15.2000",
    )
    assert locate(places, path, "Nikde 5, 999 99 Nikde") is None
    assert parse_point("50.0281, 15.2005") == (50.0281, 15.2005)
    assert parse_point("10.0,10.0") is None  # not in Czechia


def test_counting_with_an_empty_table(sessions):
    with sessions() as s:
        assert s.scalar(select(func.count()).select_from(TravelTime)) == 0
        f = Filters(origin=KOLIN.key, travel_max=30)
        # No times yet for this start point: only places that cannot be judged are listed.
        assert {j.title for j in search_jobs(s, f).jobs} == {
            "Skladník Středočeský kraj", "Skladník kdekoli"
        }  # fmt: skip


# --- The MOTIS server ----------------------------------------------------------------------------

FAKE_MOTIS = """
import http.server, json, sys, yaml
from pathlib import Path

config = Path(sys.argv[sys.argv.index("-d") + 1]) / "config.yml"  # relative to the cwd
if Path("slow").exists():  # a big import loads for a while
    import time
    time.sleep(1.5)
port = yaml.safe_load(config.read_text())["server"]["port"]


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


http.server.HTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""


@pytest.fixture
def fake_motis(tmp_path, monkeypatch):
    """A MotisServer whose motis.exe is a small Python web server; the started processes."""
    import subprocess
    import sys

    from adawork.routing import motis as motis_module

    vendor, data = tmp_path / "vendor", tmp_path / "routing" / "motis"
    (vendor / "motis").mkdir(parents=True)
    (vendor / "motis" / "motis.exe").write_bytes(b"")
    data.mkdir(parents=True)
    (data / "config.yml").write_text("server: {}\n", encoding="utf-8")
    script = tmp_path / "fake_motis.py"
    script.write_text(FAKE_MOTIS, encoding="utf-8")
    started = []
    real_popen = subprocess.Popen

    def popen(args, **kw):
        started.append(args)
        return real_popen([sys.executable, str(script), *args[1:]], **kw)

    monkeypatch.setattr(motis_module.subprocess, "Popen", popen)
    server = motis_module.MotisServer(vendor, tmp_path / "motis.log", idle_seconds=0.4)
    yield server, data, started
    server.close()


def test_the_server_starts_on_use_and_stops_when_idle(fake_motis):
    server, data, started = fake_motis
    with server.use(data):
        assert server.running
    with server.use(data):  # still running: not started again
        pass
    assert len(started) == 1 and started[0][1:] == ["server", "-d", "motis"]  # relative
    deadline = time.monotonic() + 10
    while server.running and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not server.running  # idle for longer than idle_seconds


def test_an_import_waits_for_running_requests(fake_motis):
    from adawork.routing.motis import MotisUnavailable

    server, data, _ = fake_motis
    released = threading.Event()

    def request():
        with server.use(data):
            released.wait(5)

    user = threading.Thread(target=request)
    user.start()
    while not server.running:
        time.sleep(0.02)
    threading.Timer(0.3, released.set).start()
    began = time.monotonic()
    with server.paused():
        assert time.monotonic() - began >= 0.25 and not server.running  # after the request
        with pytest.raises(MotisUnavailable), server.use(data):
            pass
    user.join()
    with server.use(data):  # usable again after the import
        assert server.running


def test_a_slow_start_holds_back_only_those_who_want_the_server(fake_motis):
    from adawork.routing.motis import MotisUnavailable

    server, data, _ = fake_motis
    (data.parent / "slow").write_text("")
    user = threading.Thread(target=lambda: server.use(data).__enter__())
    user.start()
    deadline = time.monotonic() + 2
    while server._proc is None and time.monotonic() < deadline:
        time.sleep(0.01)  # started, now waiting for it to be ready
    assert server._proc is not None
    began = time.monotonic()
    with server._lock:  # what a pause and the idle check need
        assert time.monotonic() - began < 0.2
    user.join()
    server.close()
    assert not server.running
    with pytest.raises(MotisUnavailable), server.use(data):  # the app is ending
        pass


def test_a_server_that_cannot_start_is_unavailable(fake_motis):
    from adawork.routing.motis import MotisUnavailable

    server, data, _ = fake_motis
    (data / "config.yml").write_text("[not a mapping", encoding="utf-8")
    with pytest.raises(MotisUnavailable), server.use(data):
        pass


def test_transit_takes_the_faster_of_walking_and_riding_in_list_order():
    from adawork.routing.motis import MotisClient

    def answer(request):
        many = json.loads(request.content)["many"]
        street, transit = [], []
        for item in many:  # the point's latitude says what is known
            n = int(float(item.split(",")[0]))
            street.append({"duration": n + 600} if n % 2 else {})
            transit.append([{"duration": n + 300}] if n % 3 else [])
        return httpx.Response(200, json={"street_durations": street, "transit_durations": transit})

    client = MotisClient("http://m", httpx.Client(transport=httpx.MockTransport(answer)))
    points = [(float(n), 15.0) for n in range(1200)]  # several parallel chunks
    found = client.transit_to_many((50.0, 15.0), points, datetime(2026, 10, 15, 7, 0), 120)
    for n, seconds in enumerate(found):
        options = ([n + 600] if n % 2 else []) + ([n + 300] if n % 3 else [])
        assert seconds == (min(options) if options else None)


def test_import_progress_from_its_log(tmp_path):
    from adawork.routing.data import import_percent

    log_path = tmp_path / "import.log"
    log_path.write_bytes(
        b"osm: [=====     ]  50%\rtiles: [==        ]  20%\rosm: [==========] 100%"
    )
    assert import_percent(log_path) == 60  # the last of each task: (100 + 20) / 2
    assert import_percent(tmp_path / "missing.log") == 0
