# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "selectolax"]
# ///
"""Read the 'found N offers' counters on unfiltered listing pages."""

import re
import sys
from pathlib import Path

from selectolax.lexbor import LexborHTMLParser

sys.path.insert(0, str(Path(__file__).parent))
import polite

for url in [
    "https://www.jobs.cz/prace/praha/",
    "https://www.jobs.cz/prace/?page=2",
    "https://www.hitprace.cz/prace/",
]:
    polite.robots(url)
    r = polite.get(url)
    t = " ".join(LexborHTMLParser(r.text).body.text(separator=" ").split())
    print(
        url,
        re.findall(r"[0-9][0-9  ]{1,}\s*(?:nabídek|nabídky|pozic)[^.]{0,30}", t)[:3],
        "cards:",
        r.text.count("SearchResultCard__title"),
        r.text.count("data-detail-url"),
    )
