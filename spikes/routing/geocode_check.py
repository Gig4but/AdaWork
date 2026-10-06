# /// script
# requires-python = ">=3.12"
# ///
"""Top MOTIS geocoder hit for typical free-text Czech addresses from job ads."""
import json, sys, time, urllib.parse, urllib.request
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
Q = ["Kounicova 67a, Brno", "Kounicova 67a, 602 00 Brno", "Masarykova 12, Brno", "Brno, Hlinky 102",
     "Vídeňská 89, Brno-Štýřice", "Pražská 1, Praha", "nám. Míru 9, Zlín", "Hodonín", "Kuřim",
     "Lidická 1880/4, Praha 2", "Tř. Kosmonautů 1, Olomouc", "Průmyslová 1, Modřice"]
for q in Q:
    t0 = time.perf_counter()
    d = json.loads(urllib.request.urlopen(f"{BASE}/api/v1/geocode?" + urllib.parse.urlencode({"text": q, "language": "cs"})).read())
    ms = (time.perf_counter() - t0) * 1000
    if d:
        x = d[0]
        area = ", ".join(a["name"] for a in x.get("areas", []) if a.get("default") or a.get("adminLevel") in (8, 10))
        print(f"{ms:5.0f} ms | {q:32} -> {x['type']:8} {x['name']} {x.get('houseNumber','')} [{area}] {x['lat']:.5f},{x['lon']:.5f}")
    else:
        print(f"{ms:5.0f} ms | {q:32} -> NO RESULT")
