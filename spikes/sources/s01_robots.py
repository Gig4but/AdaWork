# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx"]
# ///
"""Fetch robots.txt of every planned source and save it as a fixture."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import polite

SITES = {
    "mpsv": "https://data.mpsv.cz",
    "prace_cz": "https://www.prace.cz",
    "jenprace": "https://www.jenprace.cz",
    "profesia": "https://www.profesia.cz",
    "startupjobs": "https://www.startupjobs.cz",
    "jobs_cz": "https://www.jobs.cz",
    "volnamista": "https://www.volnamista.cz",
    "bazos": "https://prace.bazos.cz",
    "hitprace": "https://www.hitprace.cz",
    "dobraprace": "https://www.dobraprace.cz",
    "jobsik": "https://www.jobsik.cz",
    "fajn_brigady": "https://www.fajn-brigady.cz",
    "indeed": "https://cz.indeed.com",
    "linkedin": "https://www.linkedin.com",
    "jooble": "https://cz.jooble.org",
    "careerjet": "https://www.careerjet.cz",
}

for sid, base in SITES.items():
    try:
        text, _ = polite.robots(base)
        polite.save(sid, "robots.txt", text)
        print(
            f"{sid}: {len(text)} chars, sitemaps: "
            f"{[l for l in text.splitlines() if l.lower().startswith('sitemap')][:5]}"
        )
    except Exception as e:
        print(f"{sid}: ERROR {e!r}")
