# /// script
# requires-python = ">=3.12"
# dependencies = ["osmium>=4.0"]
# ///
"""Clip an .osm.pbf to a circle around a point, keeping ways and relations complete.

Usage: uv run clip_osm.py IN.osm.pbf OUT.osm.pbf LAT LON RADIUS_KM

Nodes inside the circle are kept, plus every way and relation that touches them; the
BackReferenceWriter then adds the out-of-circle nodes those ways need, so road geometry at
the border stays intact.
"""

import math
import sys
import time

import osmium


def main() -> None:
    src, dst, lat0, lon0, radius_km = sys.argv[1], sys.argv[2], *map(float, sys.argv[3:6])
    dlat = radius_km / 111.32
    dlon = radius_km / (111.32 * math.cos(math.radians(lat0)))
    cos0 = math.cos(math.radians(lat0))
    r2 = radius_km * radius_km

    def inside(lat: float, lon: float) -> bool:
        if abs(lat - lat0) > dlat or abs(lon - lon0) > dlon:
            return False
        dy = (lat - lat0) * 111.32
        dx = (lon - lon0) * 111.32 * cos0
        return dx * dx + dy * dy <= r2

    t0 = time.perf_counter()
    tracker = osmium.IdTracker()
    counts = {"n": 0, "w": 0, "r": 0}
    with osmium.BackReferenceWriter(dst, ref_src=src, overwrite=True, remove_tags=True) as writer:
        for obj in osmium.FileProcessor(src):
            if obj.is_node():
                loc = obj.location
                if loc.valid() and inside(loc.lat, loc.lon):
                    writer.add(obj)
                    tracker.add_node(obj.id)
                    counts["n"] += 1
            elif obj.is_way():
                if tracker.contains_any_references(obj):
                    writer.add(obj)
                    tracker.add_way(obj.id)
                    counts["w"] += 1
            elif obj.is_relation():
                if tracker.contains_any_references(obj):
                    writer.add(obj)
                    counts["r"] += 1
        print(f"filter pass done in {time.perf_counter() - t0:.0f} s, kept {counts}", flush=True)
    print(f"total {time.perf_counter() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()
