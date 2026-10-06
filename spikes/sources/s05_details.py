# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "extruct"]
# ///
"""Fetch detail pages, save HTML + extracted structured data, report filled JobPosting fields.

Usage: uv run s05_details.py <source_id> <url> [<url> ...]
"""

import sys
from pathlib import Path

import extruct

sys.path.insert(0, str(Path(__file__).parent))
import polite

sid, *urls = sys.argv[1:]
polite.robots(urls[0])
for i, url in enumerate(urls, 1):
    if not polite.allowed(url):
        print("robots disallows", url)
        continue
    r = polite.get(url)
    html = r.text
    polite.save(sid, f"detail_{i}.html", html)
    data = extruct.extract(
        html, base_url=str(r.url), syntaxes=["json-ld", "microdata"], uniform=True, errors="log"
    )
    jp = polite.find_type(data["json-ld"], "JobPosting") + polite.find_type(
        data["microdata"], "JobPosting"
    )
    types = [d.get("@type") for d in data["json-ld"] + data["microdata"] if isinstance(d, dict)]
    print(f"[{i}] final={r.url} types={types} JobPosting={len(jp)}")
    nd = polite.next_data(html)
    if nd:
        polite.save_json(sid, f"detail_{i}.next_data.json", nd)
        print(
            "    __NEXT_DATA__ pageProps keys:",
            list(nd.get("props", {}).get("pageProps", {}).keys()),
        )
    if jp:
        polite.save_json(sid, f"detail_{i}.jobposting.json", jp[0])
        print("    filled:", polite.filled_fields(jp[0]))
polite.summary()
