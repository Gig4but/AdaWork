# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx"]
# ///
"""Check whether Indeed CZ's public HTML search (robots-allowed) is reachable with plain HTTP."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import polite

url = "https://cz.indeed.com/jobs?q=skladn%C3%ADk&l=Praha"
polite.robots(url)
print("robots allows:", polite.allowed(url))
r = polite.get(url)
print("server:", r.headers.get("server"), "cf-ray:", r.headers.get("cf-ray"))
print(
    "challenge markers:",
    [
        m
        for m in (
            "Just a moment",
            "cf-chl",
            "challenge-platform",
            "captcha",
            "mosaic-provider-jobcards",
        )
        if m in r.text
    ],
)
polite.save("indeed", "html_search_attempt.html", r.text, limit=200_000)

import json
import re

m = re.search(
    r'window\.mosaic\.providerData\["mosaic-provider-jobcards"\]\s*=\s*(\{.*?\});\s*\n',
    r.text,
    re.S,
)
if m:
    data = json.loads(m.group(1))
    results = data.get("metaData", {}).get("mosaicProviderJobCardsModel", {}).get("results", [])
    print("jobcards results:", len(results))
    if results:
        print("result keys:", sorted(results[0].keys()))
    polite.save_json("indeed", "html_search_jobcards.json", {"results": results[:15]})
    tier = data.get("metaData", {}).get("mosaicProviderJobCardsModel", {}).get("tierSummaries")
    print("tierSummaries:", json.dumps(tier, ensure_ascii=False)[:300])
