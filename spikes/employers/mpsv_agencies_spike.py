# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27"]
# ///
"""MPSV agency register spike: download agentury-prace.json (open data, daily update),
report size/fields/IČO coverage and show how to build an IČO -> licence index.

Run: uv run spikes/employers/mpsv_agencies_spike.py
"""

import collections
import datetime as dt
import json
import time
from pathlib import Path

import httpx

URL = "https://data.mpsv.cz/od/soubory/agentury-prace/agentury-prace.json"
FIX = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "employers" / "mpsv_agencies"
FIX.mkdir(parents=True, exist_ok=True)

t = time.time()
r = httpx.get(URL, timeout=120, headers={"User-Agent": "AdaWork-spike/0.1"})
print(
    r.status_code,
    len(r.content),
    "bytes",
    f"{time.time() - t:.1f}s",
    "Last-Modified:",
    r.headers.get("last-modified"),
    "ETag:",
    r.headers.get("etag"),
)
data = r.json()
print("top-level keys:", list(data))
items = data["polozky"]
print("agencies:", len(items))
print("item keys:", list(items[0]))
print("povoleni keys:", list(items[0]["povoleni"][0]))

today = dt.date.today().isoformat()
no_ico = sum(1 for i in items if not i.get("ico"))
by_ico = collections.defaultdict(list)
for i in items:
    if i.get("ico"):
        by_ico[i["ico"]].append(i)
print(
    "without IČO:",
    no_ico,
    "| distinct IČO:",
    len(by_ico),
    "| IČO with >1 record:",
    sum(1 for v in by_ico.values() if len(v) > 1),
)
n_perm = collections.Counter()
for i in items:
    for p in i["povoleni"]:
        n_perm["valid" if (p.get("platnostDo") or "9999") >= today else "expired"] += 1
        n_perm["platnostDo=null"] += p.get("platnostDo") is None
        n_perm["zamestnavaniOsob"] += bool(p.get("zamestnavaniOsob"))
print("permits:", dict(n_perm))
print(
    "druhyPraci samples:",
    collections.Counter(
        p["druhyPraci"]["cs"] if p.get("druhyPraci") else None for i in items for p in i["povoleni"]
    ).most_common(5),
)


def licence(ico: str) -> dict | None:
    recs = by_ico.get(ico.zfill(8))
    if not recs:
        return None
    perms = [p for rec in recs for p in rec["povoleni"]]
    until = max((p.get("platnostDo") or "9999-12-31") for p in perms)
    return {
        "nazev": recs[0]["nazev"],
        "valid_until": until,
        "zamestnavani": any(p.get("zamestnavaniOsob") for p in perms),
    }


for ico in ("29813158", "00177041"):
    print("lookup", ico, licence(ico))
# well-known agencies by name
for name in ("ManpowerGroup", "Grafton", "Randstad", "Adecco"):
    hits = [(i["ico"], i["nazev"]) for i in items if name.lower() in i["nazev"].lower()][:3]
    print(name, hits)

(FIX / "agentury_prace_sample.json").write_text(
    json.dumps({"polozky": items[:3]}, ensure_ascii=False, indent=1), encoding="utf-8"
)
