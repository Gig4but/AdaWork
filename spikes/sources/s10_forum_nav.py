# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "selectolax"]
# ///
"""Find the real job-section URLs of candidate boards from their homepage navigation."""

import re
import sys
from pathlib import Path
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

sys.path.insert(0, str(Path(__file__).parent))
import polite

HOMES = sys.argv[1:] or [
    "https://www.emimino.cz/",
    "https://www.modrykonik.cz/",
    "https://www.annonce.cz/",
    "https://www.bazar.cz/",
    "https://www.okoun.cz/",
    "https://www.abclinuxu.cz/",
    "https://www.mimibazar.cz/",
    "https://www.agrobazar.cz/",
    "https://www.zena-in.cz/",
    "https://www.hlidacky.cz/",
    "https://www.sbazar.cz/",
]
WANT = re.compile(
    r"(práce|prace|zaměstn|zamestn|brigád|brigad|job|kariér|diskus|fórum|forum|inzer)", re.I
)

for home in HOMES:
    try:
        polite.robots(home)
        r = polite.get(home)
    except Exception as e:
        print(home, "ERROR", repr(e)[:150])
        continue
    h = LexborHTMLParser(r.text)
    seen = set()
    hits = []
    for a in h.css("a[href]"):
        href = urljoin(str(r.url), a.attributes.get("href") or "")
        txt = " ".join(a.text().split())[:50]
        if (WANT.search(href) or WANT.search(txt)) and href not in seen:
            seen.add(href)
            hits.append(f"{txt!r} -> {href}")
    print(f"== {home} [{r.status_code}] final={r.url} links={len(hits)}")
    for x in hits[:25]:
        print("    ", x)
