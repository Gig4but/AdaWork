# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Clip a GTFS zip to trips that stop at least once within RADIUS_KM of a point.

Usage: uv run clip_gtfs.py IN.zip OUT.zip LAT LON RADIUS_KM

Kept trips stay complete (all their stops), so a train leaving the area still shows its
destination. Only the standard files MOTIS reads are filtered; unknown files are copied.

In-seat transfers are always dropped (block_id blanked, transfers.txt rows with
transfer_type 4/5 removed): with the Spojenka feed, MOTIS v2.11.3 (nigiri "write_seated")
logs "ERROR! NOT SORTED" in an endless loop (19 GB log in 12 min) and never finishes.
Use a huge radius (e.g. 5000) to only apply this fix without clipping.
"""

import csv
import io
import math
import sys
import time
import zipfile


def rows(zf: zipfile.ZipFile, name: str):
    with zf.open(name) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        reader = csv.reader(text)
        header = next(reader)
        yield header
        yield from reader


def main() -> None:
    src, dst, lat0, lon0, radius_km = sys.argv[1], sys.argv[2], *map(float, sys.argv[3:6])
    cos0 = math.cos(math.radians(lat0))
    t0 = time.perf_counter()
    zin = zipfile.ZipFile(src)
    names = set(zin.namelist())

    it = rows(zin, "stops.txt")
    h = next(it)
    i_id, i_lat, i_lon = h.index("stop_id"), h.index("stop_lat"), h.index("stop_lon")
    i_par = h.index("parent_station") if "parent_station" in h else None
    inside: set[str] = set()
    parent_of: dict[str, str] = {}
    for r in it:
        try:
            dy = (float(r[i_lat]) - lat0) * 111.32
            dx = (float(r[i_lon]) - lon0) * 111.32 * cos0
        except ValueError:
            continue
        if dx * dx + dy * dy <= radius_km * radius_km:
            inside.add(r[i_id])
        if i_par is not None and r[i_par]:
            parent_of[r[i_id]] = r[i_par]

    it = rows(zin, "stop_times.txt")
    h = next(it)
    i_trip, i_stop = h.index("trip_id"), h.index("stop_id")
    keep_trips = {r[i_trip] for r in it if r[i_stop] in inside}
    print(
        f"stops inside: {len(inside)}, trips kept: {len(keep_trips)} ({time.perf_counter() - t0:.0f} s)",
        flush=True,
    )

    used_stops: set[str] = set()
    keep_services: set[str] = set()
    keep_routes: set[str] = set()
    keep_shapes: set[str] = set()

    zout = zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=5)

    def write(name: str, predicate) -> None:
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        it2 = rows(zin, name)
        header = next(it2)
        w.writerow(header)
        idx = {k: i for i, k in enumerate(header)}
        n = 0
        with zout.open(name, "w") as out:
            for r in it2:
                if predicate(r, idx):
                    w.writerow(r)
                    n += 1
                    if buf.tell() > 1 << 20:
                        out.write(buf.getvalue().encode("utf-8"))
                        buf.seek(0)
                        buf.truncate()
            out.write(buf.getvalue().encode("utf-8"))
        print(f"  {name}: {n} rows", flush=True)

    def st_pred(r, idx):
        if r[idx["trip_id"]] in keep_trips:
            used_stops.add(r[idx["stop_id"]])
            return True
        return False

    def trip_pred(r, idx):
        if r[idx["trip_id"]] in keep_trips:
            if "block_id" in idx:
                r[idx["block_id"]] = ""
            keep_services.add(r[idx["service_id"]])
            keep_routes.add(r[idx["route_id"]])
            if "shape_id" in idx and r[idx["shape_id"]]:
                keep_shapes.add(r[idx["shape_id"]])
            return True
        return False

    write("stop_times.txt", st_pred)
    write("trips.txt", trip_pred)
    for s in list(used_stops):
        p = parent_of.get(s)
        while p and p not in used_stops:
            used_stops.add(p)
            p = parent_of.get(p)
    # Children of kept stations (entrances, pathway nodes, sibling platforms) stay too.
    used_stops.update(c for c, par in parent_of.items() if par in used_stops)
    write("stops.txt", lambda r, idx: r[idx["stop_id"]] in used_stops)
    write("routes.txt", lambda r, idx: r[idx["route_id"]] in keep_routes)
    for name in ("calendar.txt", "calendar_dates.txt"):
        if name in names:
            write(name, lambda r, idx: r[idx["service_id"]] in keep_services)
    if "shapes.txt" in names:
        write("shapes.txt", lambda r, idx: r[idx["shape_id"]] in keep_shapes)
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
    done = {
        "stop_times.txt",
        "trips.txt",
        "stops.txt",
        "routes.txt",
        "calendar.txt",
        "calendar_dates.txt",
        "shapes.txt",
        "transfers.txt",
        "pathways.txt",
    }
    for name in names - done:
        zout.writestr(name, zin.read(name))
    zout.close()
    print(f"total {time.perf_counter() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()
