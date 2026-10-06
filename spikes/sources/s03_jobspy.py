# /// script
# requires-python = ">=3.12"
# dependencies = ["python-jobspy"]
# ///
"""python-jobspy: Indeed CZ and LinkedIn guest search, field fill-rate survey.

Both sites disallow this in robots.txt (docs/spikes/sources.md §3); kept as a record only.
Output goes to the git-ignored data/ folder: the ads hold real contact data."""

import json
import sys
import time
import traceback
from importlib.metadata import version
from pathlib import Path

from jobspy import scrape_jobs

FIX = Path(__file__).resolve().parent / "data"
print("python-jobspy", version("python-jobspy"))

RUNS = [
    ("indeed", "skladník", "Praha", {}),
    ("indeed", "účetní", "Brno", {}),
    ("linkedin", "skladník", "Praha, Czechia", {"linkedin_fetch_description": True}),
    ("linkedin", "účetní", "Brno, Czechia", {"linkedin_fetch_description": False}),
]

report = []
for site, term, loc, extra in RUNS:
    t0 = time.monotonic()
    entry = {"site": site, "term": term, "location": loc, **extra}
    try:
        df = scrape_jobs(
            site_name=[site],
            search_term=term,
            location=loc,
            results_wanted=10,
            country_indeed="Czech Republic",
            hours_old=24 * 14,
            verbose=1,
            **extra,
        )
        entry["rows"] = len(df)
        entry["fill"] = {c: int(df[c].notna().sum()) for c in df.columns}
        recs = json.loads(df.to_json(orient="records", date_format="iso", force_ascii=False))
        out = FIX / ("indeed" if site == "indeed" else "linkedin")
        out.mkdir(parents=True, exist_ok=True)
        slug = "skladnik" if term.startswith("sklad") else "ucetni"
        (out / f"jobspy_{slug}.json").write_text(
            json.dumps(recs, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        entry["error"] = repr(e)
        traceback.print_exc()
    entry["seconds"] = round(time.monotonic() - t0, 1)
    print(json.dumps(entry, ensure_ascii=False))
    report.append(entry)
    time.sleep(5)

(FIX / "indeed" / "jobspy_report.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)
