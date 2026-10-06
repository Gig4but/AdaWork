# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Latency probe for a running MOTIS server (default http://127.0.0.1:8080; "localhost" adds ~2 s per request on Windows because IPv6 ::1 is tried first).

Usage: uv run queries.py [BASE_URL] [LABEL]

Writes trimmed example responses to samples/ (only when LABEL is "full") and prints a JSON
summary with latencies in milliseconds.
"""

import gzip
import json
import math
import random
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
LABEL = sys.argv[2] if len(sys.argv) > 2 else "full"
HOME = (49.1951, 16.6068)  # Brno, náměstí Svobody
# Thursday 2026-10-08 07:00 Europe/Prague (CEST, UTC+2)
DEPART = "2026-10-08T05:00:00Z"
SAMPLES = Path(__file__).parent / "samples"

# Fixed transit destinations: (name, lat, lon)
TRANSIT_DEST = [
    ("Brno-Bohunice, Univerzitní kampus", 49.1772, 16.5684),
    ("Blansko, centrum", 49.3631, 16.6444),
    ("Vyškov, náměstí", 49.2775, 16.9990),
    ("Hodonín, centrum", 48.8489, 17.1324),
    ("Praha, Václavské náměstí", 50.0814, 14.4280),
]


def many_points(n: int = 50, radius_km: float = 45.0) -> list[tuple[float, float]]:
    """Deterministic pseudo-random job locations around home, denser near the centre."""
    rnd = random.Random(42)
    pts = []
    for _ in range(n):
        r = radius_km * rnd.random() ** 1.5
        a = rnd.random() * 6.283185

        pts.append(
            (
                round(HOME[0] + r * math.sin(a) / 111.32, 5),
                round(HOME[1] + r * math.cos(a) / (111.32 * math.cos(math.radians(HOME[0]))), 5),
            )
        )
    return pts


def get(path: str, params: dict) -> tuple[float, int, object]:
    url = f"{BASE}{path}?{urllib.parse.urlencode(params, safe=';,:')}"
    # MOTIS answers 501 on /tiles unless the client accepts gzip.
    req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            body = r.read()
            status = r.status
            if r.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
    except urllib.error.HTTPError as e:
        body = e.read()
        status = e.code
    ms = (time.perf_counter() - t0) * 1000
    try:
        data = json.loads(body)
    except Exception:
        data = {"raw": body[:500].decode("utf-8", "replace"), "bytes": len(body)}
    return ms, status, data


def timed(path: str, params: dict, runs: int = 3) -> dict:
    lat = []
    status = data = None
    for _ in range(runs):
        ms, status, data = get(path, params)
        lat.append(ms)
    return {
        "first_ms": round(lat[0], 1),
        "warm_median_ms": round(statistics.median(lat[1:] or lat), 1),
        "status": status,
        "data": data,
    }


def trim(obj, depth=0, max_list=3):
    """Shorten long lists and polylines so the sample stays readable."""
    if isinstance(obj, list):
        out = [trim(x, depth + 1, max_list) for x in obj[:max_list]]
        if len(obj) > max_list:
            out.append(f"... {len(obj) - max_list} more")
        return out
    if isinstance(obj, dict):
        return {
            k: (
                v[:60] + "..."
                if isinstance(v, str) and len(v) > 80
                else trim(v, depth + 1, max_list)
            )
            for k, v in obj.items()
        }
    return obj


def save(name: str, data) -> None:
    if LABEL == "full":
        SAMPLES.mkdir(exist_ok=True)
        (SAMPLES / f"{name}.json").write_text(
            json.dumps(trim(data), ensure_ascii=False, indent=2), encoding="utf-8"
        )


def main() -> None:
    one = f"{HOME[0]};{HOME[1]}"
    many = ",".join(f"{a};{b}" for a, b in many_points())
    summary: dict = {"label": LABEL}

    for mode in ("CAR", "WALK", "BIKE"):
        res = timed(
            "/api/v1/one-to-many",
            {
                "one": one,
                "many": many,
                "mode": mode,
                "max": 7200,
                "maxMatchingDistance": 250,
                "arriveBy": "false",
            },
        )
        d = res.pop("data")
        found = (
            [x.get("duration") for x in d if isinstance(x, dict) and "duration" in x]
            if isinstance(d, list)
            else []
        )
        res.update(
            found=len(found), median_min=round(statistics.median(found) / 60, 1) if found else None
        )
        if not found:
            res["error"] = d
        summary[f"one_to_many_{mode}"] = res
        if mode == "CAR":
            save("one_to_many_car", d)

    res = timed(
        "/api/experimental/one-to-many-intermodal",
        {
            "one": one,
            "many": many,
            "time": DEPART,
            "maxTravelTime": 120,
            "maxMatchingDistance": 250,
            "arriveBy": "false",
        },
    )
    d = res.pop("data")
    # Response: {"street_durations": [{duration?}], "transit_durations": [[{duration, transfers}]]}
    found = []
    if isinstance(d, dict) and "transit_durations" in d:
        for street, transit in zip(d["street_durations"], d["transit_durations"]):
            cands = [street["duration"]] if "duration" in street else []
            cands += [t["duration"] for t in transit]
            if cands:
                found.append(min(cands))
    res.update(
        found=len(found), median_min=round(statistics.median(found) / 60, 1) if found else None
    )
    if not found:
        res["error"] = trim(d)
    summary["one_to_many_intermodal_TRANSIT"] = res
    save("one_to_many_intermodal", d)

    plans = []
    for i, (name, lat, lon) in enumerate(TRANSIT_DEST):
        res = timed(
            "/api/v6/plan",
            {
                "fromPlace": f"{HOME[0]},{HOME[1]}",
                "toPlace": f"{lat},{lon}",
                "time": DEPART,
                "numItineraries": 3,
            },
            runs=2,
        )
        d = res.pop("data")
        its = d.get("itineraries", []) if isinstance(d, dict) else []
        best = min(its, key=lambda x: x["duration"]) if its else None
        res.update(
            dest=name,
            itineraries=len(its),
            best_min=round(best["duration"] / 60) if best else None,
            best_legs=[
                f"{leg['mode']}:{leg.get('routeShortName') or leg.get('displayName') or ''}"
                for leg in best["legs"]
            ]
            if best
            else None,
        )
        if not its:
            res["error"] = trim(d)
        plans.append(res)
        if i == 1:
            save("plan_transit", d)
    summary["plan_transit"] = plans

    res = timed(
        "/api/v6/plan",
        {
            "fromPlace": f"{HOME[0]},{HOME[1]}",
            "toPlace": "49.3631,16.6444",
            "time": DEPART,
            "directModes": "CAR",
            "transitModes": "",
        },
        runs=2,
    )
    d = res.pop("data")
    res["direct"] = (
        [round(x["duration"] / 60) for x in d.get("direct", [])] if isinstance(d, dict) else d
    )
    summary["plan_car_direct"] = res
    save("plan_car_direct", d)

    for mins in (30, 60, 90):
        res = timed(
            "/api/v6/one-to-all",
            {
                "one": f"{HOME[0]},{HOME[1]}",
                "time": DEPART,
                "maxTravelTime": mins,
                "arriveBy": "false",
            },
            runs=2,
        )
        d = res.pop("data")
        res["reachable_stops"] = len(d.get("all", [])) if isinstance(d, dict) else None
        if not res["reachable_stops"]:
            res["error"] = trim(d)
        summary[f"one_to_all_{mins}min"] = res
        if mins == 30:
            save("one_to_all", d)

    res = timed("/api/v1/geocode", {"text": "Brno Kounicova", "language": "cs"})
    d = res.pop("data")
    res["results"] = len(d) if isinstance(d, list) else d
    summary["geocode"] = res
    save("geocode", d)

    res = timed("/api/v1/reverse-geocode", {"place": "49.1951,16.6068"})
    d = res.pop("data")
    res["results"] = len(d) if isinstance(d, list) else d
    summary["reverse_geocode"] = res
    save("reverse_geocode", d)

    ms, status, d = get("/tiles/10/559/350.mvt", {})
    summary["tile_z10"] = {
        "ms": round(ms, 1),
        "status": status,
        "bytes": d.get("bytes") if isinstance(d, dict) else None,
    }
    ms, status, d = get("/tiles/14/8947/5612.mvt", {})
    summary["tile_z14"] = {
        "ms": round(ms, 1),
        "status": status,
        "bytes": d.get("bytes") if isinstance(d, dict) else None,
    }

    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
