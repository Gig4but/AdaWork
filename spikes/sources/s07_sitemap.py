# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "extruct"]
# ///
"""Sitemap discovery: index -> job sitemap (size, URL count, lastmod) -> sample detail pages.

Usage: uv run s07_sitemap.py <source_id> <sitemap_index_url> <job_sitemap_regex> <detail_regex> [n_details]
"""

import gzip
import re
import sys
from pathlib import Path

import extruct

sys.path.insert(0, str(Path(__file__).parent))
import polite

sid, index_url, job_sm_re, detail_re = sys.argv[1:5]
n_details = int(sys.argv[5]) if len(sys.argv) > 5 else 2
LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
LASTMOD = re.compile(r"<lastmod>\s*([^<\s]+)\s*</lastmod>")


def body(r):
    raw = r.content
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


polite.robots(index_url)
r = polite.get(index_url)
idx = body(r)
polite.save(sid, "sitemap_index.xml", idx)
locs = LOC.findall(idx)
print(f"index: {len(locs)} entries, is_index={'<sitemapindex' in idx}")
for loc in locs[:40]:
    print("   ", loc)

job_sms = [l for l in locs if re.search(job_sm_re, l)] if "<sitemapindex" in idx else [index_url]
print("job sitemaps:", job_sms)
urls: list[str] = []
for i, sm in enumerate(job_sms[:3]):
    if sm == index_url:
        text = idx
    else:
        rr = polite.get(sm)
        text = body(rr)
        polite.save(sid, f"sitemap_jobs_{i}.xml", text, limit=200_000)
    found = LOC.findall(text)
    mods = sorted(LASTMOD.findall(text))
    print(f"  {sm}: {len(text)} chars, {len(found)} urls, lastmod {mods[:1]}..{mods[-1:]}")
    urls += found
details = [u for u in urls if re.search(detail_re, u)]
print(f"detail-like urls: {len(details)} of {len(urls)}")
for j, u in enumerate(details[:n_details], 1):
    if not polite.allowed(u):
        print("robots disallows", u)
        continue
    rr = polite.get(u)
    html = rr.text
    polite.save(sid, f"detail_{j}.html", html)
    data = extruct.extract(
        html, base_url=str(rr.url), syntaxes=["json-ld", "microdata"], uniform=True, errors="log"
    )
    jp = polite.find_type(data["json-ld"], "JobPosting") + polite.find_type(
        data["microdata"], "JobPosting"
    )
    types = [d.get("@type") for d in data["json-ld"] + data["microdata"] if isinstance(d, dict)]
    print(f"  [{j}] {rr.url} -> types={types} JobPosting={len(jp)}")
    if jp:
        polite.save_json(sid, f"detail_{j}.jobposting.json", jp[0])
        print("     filled:", polite.filled_fields(jp[0]))
polite.summary()
