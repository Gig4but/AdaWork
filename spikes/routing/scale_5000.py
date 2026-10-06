# /// script
# requires-python = ">=3.12"
# ///
"""One request with 5000 destinations per mode (POST), the M8 target case.

Note: POST one-to-many takes "lat;lon", POST one-to-many-intermodal takes "lat,lon".
"""
import json, math, random, sys, time, urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
HOME = (49.1951, 16.6068)
rnd = random.Random(3)
many = []
for _ in range(5000):
    r = 80 * rnd.random() ** 1.5; a = rnd.random() * 2 * math.pi
    many.append(f"{HOME[0] + r * math.sin(a) / 111.32:.5f};{HOME[1] + r * math.cos(a) / (111.32 * math.cos(math.radians(HOME[0]))):.5f}")
for mode, mx in (("CAR", 7200), ("BIKE", 7200), ("WALK", 7200)):
    body = json.dumps({"one": f"{HOME[0]};{HOME[1]}", "many": many, "mode": mode, "max": mx,
                       "maxMatchingDistance": 250, "arriveBy": False}).encode()
    t0 = time.perf_counter()
    with urllib.request.urlopen(urllib.request.Request(f"{BASE}/api/v1/one-to-many", data=body,
                                headers={"Content-Type": "application/json"}), timeout=600) as r:
        d = json.loads(r.read())
    print(f"{mode} n=5000 max={mx}: {(time.perf_counter() - t0) * 1000:.0f} ms, found={sum('duration' in x for x in d)}", flush=True)
body = json.dumps({"one": f"{HOME[0]},{HOME[1]}", "many": [m.replace(";", ",") for m in many], "time": "2026-10-08T05:00:00Z", "maxTravelTime": 120,
                   "maxMatchingDistance": 250, "arriveBy": False}).encode()
t0 = time.perf_counter()
with urllib.request.urlopen(urllib.request.Request(f"{BASE}/api/experimental/one-to-many-intermodal", data=body,
                            headers={"Content-Type": "application/json"}), timeout=600) as r:
    d = json.loads(r.read())
n = sum(1 for s, t in zip(d["street_durations"], d["transit_durations"]) if "duration" in s or t)
print(f"TRANSIT intermodal n=5000 max=120min: {(time.perf_counter() - t0) * 1000:.0f} ms, found={n}", flush=True)
