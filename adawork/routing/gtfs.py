"""Timetables (Spojenka GTFS) made ready for the MOTIS import.

Always sanitized: in-seat transfers are dropped (`block_id` blanked, transfers of type 4/5
removed). With them, MOTIS v2.11.3 logs "ERROR! NOT SORTED" in an endless loop and never
finishes (M0 spike: a 19 GB log in 12 minutes).

On machines that import only the home area, the feed is also clipped to the trips that stop at
least once within the area. Kept trips stay complete, so a train leaving the area still shows
its destination. Unknown files are copied unchanged.
"""

import csv
import io
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path

FLUSH = 1 << 20
FILTERED = {
    "stop_times.txt", "trips.txt", "stops.txt", "routes.txt", "calendar.txt",
    "calendar_dates.txt", "shapes.txt", "transfers.txt", "pathways.txt",
}  # fmt: skip


def _rows(archive: zipfile.ZipFile, name: str) -> Iterator[list[str]]:
    """The header, then the rows."""
    with archive.open(name) as raw:
        yield from csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))


def prepare(
    source: Path, target: Path, inside: Callable[[float, float], bool] | None = None
) -> dict[str, int]:
    """Writes the sanitized (and, with `inside`: (lat, lon) -> in the area, clipped) feed to
    `target`. Returns the row count of each filtered file."""
    counts: dict[str, int] = {}
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    with (
        zipfile.ZipFile(source) as src,
        zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=5) as out,
    ):
        names = set(src.namelist())

        rows = _rows(src, "stops.txt")
        header = next(rows)
        i_id, i_lat, i_lon = (header.index(k) for k in ("stop_id", "stop_lat", "stop_lon"))
        i_parent = header.index("parent_station") if "parent_station" in header else None
        clipped: set[str] = set()  # stops in the area
        parent_of: dict[str, str] = {}
        for r in rows:
            if i_parent is not None and r[i_parent]:
                parent_of[r[i_id]] = r[i_parent]
            if inside is None:
                continue
            try:
                if inside(float(r[i_lat]), float(r[i_lon])):
                    clipped.add(r[i_id])
            except ValueError:
                continue

        keep_trips: set[str] | None = None
        if inside is not None:
            rows = _rows(src, "stop_times.txt")
            header = next(rows)
            i_trip, i_stop = header.index("trip_id"), header.index("stop_id")
            keep_trips = {r[i_trip] for r in rows if r[i_stop] in clipped}

        used_stops: set[str] = set()
        services: set[str] = set()
        routes: set[str] = set()
        shapes: set[str] = set()

        def write(name: str, keep: Callable[[list[str], dict[str, int]], bool]) -> None:
            buffer = io.StringIO()
            writer = csv.writer(buffer, lineterminator="\n")
            rows = _rows(src, name)
            header = next(rows)
            writer.writerow(header)
            idx = {k: i for i, k in enumerate(header)}
            n = 0
            with out.open(name, "w") as file:
                for r in rows:
                    if keep(r, idx):
                        writer.writerow(r)
                        n += 1
                        if buffer.tell() > FLUSH:
                            file.write(buffer.getvalue().encode("utf-8"))
                            buffer.seek(0)
                            buffer.truncate()
                file.write(buffer.getvalue().encode("utf-8"))
            counts[name] = n

        def stop_time(r: list[str], idx: dict[str, int]) -> bool:
            if keep_trips is not None and r[idx["trip_id"]] not in keep_trips:
                return False
            used_stops.add(r[idx["stop_id"]])
            return True

        def trip(r: list[str], idx: dict[str, int]) -> bool:
            if keep_trips is not None and r[idx["trip_id"]] not in keep_trips:
                return False
            if "block_id" in idx:
                r[idx["block_id"]] = ""  # no in-seat transfers
            services.add(r[idx["service_id"]])
            routes.add(r[idx["route_id"]])
            if "shape_id" in idx and r[idx["shape_id"]]:
                shapes.add(r[idx["shape_id"]])
            return True

        write("stop_times.txt", stop_time)
        write("trips.txt", trip)
        for stop in list(used_stops):  # their stations, and those stations' other platforms
            parent = parent_of.get(stop)
            while parent and parent not in used_stops:
                used_stops.add(parent)
                parent = parent_of.get(parent)
        used_stops.update(c for c, p in parent_of.items() if p in used_stops)
        write("stops.txt", lambda r, idx: r[idx["stop_id"]] in used_stops)
        write("routes.txt", lambda r, idx: r[idx["route_id"]] in routes)
        for name in ("calendar.txt", "calendar_dates.txt"):
            if name in names:
                write(name, lambda r, idx: r[idx["service_id"]] in services)
        if "shapes.txt" in names:
            write("shapes.txt", lambda r, idx: r[idx["shape_id"]] in shapes)
        if "transfers.txt" in names:
            write(
                "transfers.txt",
                lambda r, idx: (
                    r[idx["from_stop_id"]] in used_stops
                    and r[idx["to_stop_id"]] in used_stops
                    and r[idx["transfer_type"]] not in ("4", "5")
                ),
            )
        if "pathways.txt" in names:
            write(
                "pathways.txt",
                lambda r, idx: (
                    r[idx["from_stop_id"]] in used_stops and r[idx["to_stop_id"]] in used_stops
                ),
            )
        for name in names - FILTERED:
            out.writestr(name, src.read(name))
    tmp.replace(target)
    return counts
