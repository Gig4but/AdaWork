# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "ijson"]
# ///
"""MPSV open data: HEAD/ETag, full download, schema survey with ijson, 30-record fixture."""

import json
import sys
import time
from collections import Counter
from pathlib import Path

import httpx
import ijson

sys.path.insert(0, str(Path(__file__).parent))
import polite

URL = "https://data.mpsv.cz/od/soubory/volna-mista/volna-mista.json"
dest = polite.DATA / "volna-mista.json"
polite.DATA.mkdir(exist_ok=True)

h = polite.client.head(URL)
meta = {k: h.headers.get(k) for k in ("content-length", "last-modified", "etag", "accept-ranges")}
print("HEAD", h.status_code, meta)

with polite.client.stream("GET", URL, headers={"If-None-Match": h.headers["etag"]}) as cond:
    meta["conditional_get_status"] = cond.status_code
print("conditional GET with ETag ->", meta["conditional_get_status"])
with polite.client.stream(
    "GET", URL, headers={"If-Modified-Since": h.headers["last-modified"]}
) as cond:
    meta["if_modified_since_status"] = cond.status_code
print("conditional GET with If-Modified-Since ->", meta["if_modified_since_status"])

if not dest.exists() or dest.stat().st_size != int(h.headers["content-length"]):
    t0 = time.monotonic()
    with (
        polite.client.stream("GET", URL, timeout=httpx.Timeout(60, read=300)) as r,
        dest.open("wb") as f,
    ):
        for chunk in r.iter_bytes(1 << 20):
            f.write(chunk)
    meta["download_seconds"] = round(time.monotonic() - t0, 1)
    print("downloaded in", meta["download_seconds"], "s")

top_keys = []
with dest.open("rb") as f:
    for prefix, event, value in ijson.parse(f):
        if prefix == "" and event == "map_key":
            top_keys.append(value)
meta["top_level_keys"] = top_keys

paths: Counter = Counter()
codebooks: Counter = Counter()
first: list = []
n = 0
kraj = Counter()


def walk(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else k
            if v not in (None, "", [], {}):
                paths[p] += 1
            if k == "id" and isinstance(v, str) and "/" in v:
                codebooks[v.split("/")[0]] += 1
            walk(v, p)
    elif isinstance(obj, list):
        for v in obj:
            walk(v, prefix + "[]")


t0 = time.monotonic()
with dest.open("rb") as f:
    for rec in ijson.items(f, "polozky.item", use_float=True):
        if n < 30:
            first.append(rec)
        walk(rec)
        n += 1
meta["records"] = n
meta["parse_seconds"] = round(time.monotonic() - t0, 1)
print("records:", n, "parse s:", meta["parse_seconds"])

polite.save_json("mpsv", "volna-mista.first30.json", {"polozky": first})
polite.save_json(
    "mpsv",
    "schema.json",
    {
        "meta": meta,
        "field_fill_counts": dict(sorted(paths.items())),
        "codebook_prefixes": dict(codebooks.most_common()),
    },
)
for p, c in sorted(paths.items()):
    print(f"{c:7d} {c / n:6.1%} {p}")
print(codebooks.most_common())
