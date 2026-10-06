# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "selectolax"]
# ///
"""Total-ad counts on unfiltered listing pages, plus a company-branded jobs.cz page."""

import re
import sys
from pathlib import Path

from selectolax.lexbor import LexborHTMLParser

sys.path.insert(0, str(Path(__file__).parent))
import polite

PAGES = [
    ("jobs_cz", "listing_all.html", "https://www.jobs.cz/prace/"),
    ("prace_cz", "listing_all.html", "https://www.prace.cz/nabidky/"),
    ("jenprace", "listing_praha.html", "https://www.jenprace.cz/nabidky/praha"),
]
for sid, name, url in PAGES:
    polite.robots(url)
    r = polite.get(url)
    polite.save(sid, name, r.text)
    text = " ".join(LexborHTMLParser(r.text).body.text(separator=" ").split())
    print(
        sid,
        re.findall(r"(?:Našli jsme|Nalezeno|nalezen\w*)\s*[0-9  ]+\s*\w+", text)[:3],
        re.findall(r"[0-9][0-9  ]{2,}\s+(?:nabídek|nabídky|pozic)", text)[:3],
    )
