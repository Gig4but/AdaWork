# /// script
# requires-python = ">=3.12"
# ///
"""How one-to-many CAR latency scales with `max` and the number of destinations."""

import json, math, random, sys, time, urllib.request, urllib.parse

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
HOME = (49.1951, 16.6068)


def pts(n, radius_km=60.0):
    rnd = random.Random(7)
    out = []
    for _ in range(n):
        r = radius_km * rnd.random() ** 1.5
        a = rnd.random() * 2 * math.pi
        out.append(
            f"{HOME[0] + r * math.sin(a) / 111.32:.5f};{HOME[1] + r * math.cos(a) / (111.32 * math.cos(math.radians(HOME[0]))):.5f}"
        )
    return out


def run(mode, mx, n, post=False):
    many = pts(n)
    t0 = time.perf_counter()
    if post:
        body = json.dumps(
            {
                "one": f"{HOME[0]};{HOME[1]}",
                "many": many,
                "mode": mode,
                "max": mx,
                "maxMatchingDistance": 250,
                "arriveBy": False,
            }
        ).encode()
        req = urllib.request.Request(
            f"{BASE}/api/v1/one-to-many", data=body, headers={"Content-Type": "application/json"}
        )
    else:
        q = urllib.parse.urlencode(
            {
                "one": f"{HOME[0]};{HOME[1]}",
                "many": ",".join(many),
                "mode": mode,
                "max": mx,
                "maxMatchingDistance": 250,
                "arriveBy": "false",
            },
            safe=";,",
        )
        req = urllib.request.Request(f"{BASE}/api/v1/one-to-many?{q}")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            d = json.loads(r.read())
            st = r.status
    except urllib.error.HTTPError as e:
        d = e.read()[:200]
        st = e.code
    ms = (time.perf_counter() - t0) * 1000
    found = (
        sum(1 for x in d if isinstance(x, dict) and "duration" in x) if isinstance(d, list) else d
    )
    print(
        f"{mode:5} max={mx:5} n={n:4} post={post!s:5} status={st} {ms:7.0f} ms found={found}",
        flush=True,
    )


for mx in (1800, 3600, 7200):
    run("CAR", mx, 50)
run("CAR", 3600, 128)
run("CAR", 3600, 128, post=True)
run("CAR", 3600, 1000, post=True)
run("BIKE", 3600, 128)
run("WALK", 3600, 128)
