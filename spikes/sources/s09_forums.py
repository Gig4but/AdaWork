# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "selectolax"]
# ///
"""Verify candidate public forums/boards with job-offer sections: loads without login, robots, content."""

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from selectolax.lexbor import LexborHTMLParser

sys.path.insert(0, str(Path(__file__).parent))
import polite

CANDIDATES = [
    ("hyperinzerce_prace", "HyperInzerce – Práce, zaměstnání", "https://prace.hyperinzerce.cz/"),
    ("hyperinzerce_brigady", "HyperInzerce – Brigády", "https://brigady.hyperinzerce.cz/"),
    (
        "avizo_volna_mista",
        "Avízo.cz – Práce / Volná místa",
        "https://www.avizo.cz/prace/volna-mista/",
    ),
    ("avizo_brigady", "Avízo.cz – Práce / Brigády", "https://www.avizo.cz/prace/brigady/"),
    (
        "annonce_nabidky",
        "Annonce.cz – Nabízím práci, dám vydělat",
        "https://www.annonce.cz/nabizim-praci-dam-vydelat.html",
    ),
    (
        "annonce_brigady",
        "Annonce.cz – Brigády, práce doma, au-pair",
        "https://www.annonce.cz/brigady-prace-doma-au-pair.html",
    ),
    (
        "bazar_cz",
        "Bazar.cz – Přivýdělek, brigáda, práce",
        "https://www.bazar.cz/privydelek-brigada-prace/",
    ),
    ("cbazar_prace", "cBazar.cz – Práce", "https://prace.cbazar.cz/"),
    (
        "mimibazar_prace",
        "Mimibazar.cz – Práce, přivýdělek",
        "https://www.mimibazar.cz/inzeraty-nove-prace-privydelek/",
    ),
    (
        "emimino_zamestnani",
        "eMimino.cz – diskuse Zaměstnání",
        "https://www.emimino.cz/diskuse/zamestnani/",
    ),
    ("okoun_prace", "Okoun.cz – klub Práce", "https://www.okoun.cz/boards/prace"),
    ("sbazar", "Sbazar.cz – Práce (control: robots)", "https://www.sbazar.cz/"),
]
KEYWORDS = re.compile(
    r"\b(nabízím|nabízíme|hledáme|přijmeme|brigád|práce|pracovník|mzda|Kč/h)", re.I
)
LOGIN = re.compile(
    r"(přihlaste se pro zobrazení|musíte být přihlášen|pro zobrazení se přihlaste|login required)",
    re.I,
)

results = []
for sid, name, url in CANDIDATES:
    host = urlsplit(url).netloc
    row = {"id": sid, "name": name, "url": url}
    try:
        polite.robots(url)
        row["robots_allows"] = polite.allowed(url)
        if not row["robots_allows"]:
            print(f"{sid}: robots disallows")
            results.append(row)
            continue
        r = polite.get(url)
        row["status"] = r.status_code
        row["final_url"] = str(r.url)
        h = LexborHTMLParser(r.text)
        t = h.css_first("title")
        row["title"] = " ".join(t.text().split())[:120] if t else None
        body = " ".join((h.body.text(separator=" ") if h.body else "").split())
        row["keyword_hits"] = len(KEYWORDS.findall(body))
        row["login_wall"] = bool(LOGIN.search(body))
        row["cloudflare"] = "cf-ray" in r.headers and ("Just a moment" in r.text)
        row["bytes"] = len(r.content)
        if r.status_code == 200 and row["keyword_hits"] > 5:
            polite.save("forums", f"{sid}.html", r.text, limit=300_000)
    except Exception as e:
        row["error"] = repr(e)[:200]
    print(json.dumps(row, ensure_ascii=False))
    results.append(row)

polite.save_json("forums", "_verification.json", results)
polite.summary()
