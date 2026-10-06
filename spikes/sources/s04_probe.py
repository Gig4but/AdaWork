# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "extruct"]
# ///
"""Probe listing/detail pages: save HTML, report JSON-LD/microdata types, __NEXT_DATA__, links.

Usage: uv run s04_probe.py <source_id> <name> <url> [link-regex]
"""

import re
import sys
from pathlib import Path

import extruct

sys.path.insert(0, str(Path(__file__).parent))
import polite

sid, name, url = sys.argv[1:4]
link_re = sys.argv[4] if len(sys.argv) > 4 else None

polite.robots(url)
print("robots allows:", polite.allowed(url))
r = polite.get(url)
html = r.text
print("final url:", r.url)
print(
    "server:",
    r.headers.get("server"),
    "| cf-ray:",
    r.headers.get("cf-ray"),
    "| set-cookie names:",
    [c.split("=")[0] for c in r.headers.get_list("set-cookie")][:8],
)
polite.save(sid, name, html)

data = extruct.extract(
    html,
    base_url=str(r.url),
    syntaxes=["json-ld", "microdata", "opengraph"],
    uniform=True,
    errors="log",
)
for syn, items in data.items():
    types = [i.get("@type") for i in items if isinstance(i, dict)]
    print(f"{syn}: {len(items)} items, types={types[:15]}")
jp = polite.find_type(data["json-ld"], "JobPosting") + polite.find_type(
    data["microdata"], "JobPosting"
)
print("JobPosting count:", len(jp))
if jp:
    print("JobPosting[0] filled fields:", polite.filled_fields(jp[0]))
nd = polite.next_data(html)
print(
    "__NEXT_DATA__:",
    bool(nd),
    (list(nd.get("props", {}).get("pageProps", {}).keys())[:20] if nd else ""),
)
for marker in (
    "__NUXT__",
    "__INITIAL_STATE__",
    "__APOLLO_STATE__",
    "window.__",
    "data-props",
    "application/json",
):
    if marker in html:
        print("marker present:", marker)
print(
    "title:",
    re.search(r"<title[^>]*>(.*?)</title>", html, re.S).group(1).strip()[:150]
    if "<title" in html
    else None,
)
if link_re:
    links = list(dict.fromkeys(re.findall(link_re, html)))
    print(f"links matching {link_re!r}: {len(links)}")
    for l in links[:12]:
        print("  ", l)
