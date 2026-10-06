# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27"]
# ///
"""ARES RES batch lookup: POST a list of IČO to /ekonomicke-subjekty-res/vyhledat and read the
employee-count category + prevailing NACE for ranking name-search candidates.

Run: uv run spikes/employers/ares_res_batch.py
"""

import json
from pathlib import Path

import httpx

BASE = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest"
FIX = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "employers" / "ares"
icos = ["01464353", "22770348", "64945880", "71188576", "06283659"]
r = httpx.post(
    f"{BASE}/ekonomicke-subjekty-res/vyhledat",
    json={"ico": icos, "pocet": 100},
    headers={"User-Agent": "AdaWork-spike/0.1"},
    timeout=30,
)
print(r.status_code)
data = r.json()
print("keys:", list(data), "pocetCelkem:", data.get("pocetCelkem"))
for s in data.get("ekonomickeSubjekty", []):
    zs = s.get("zaznamy", [])
    z = next((z for z in zs if z.get("primarniZaznam")), zs[0] if zs else {})
    print(
        " ",
        s.get("icoId"),
        z.get("obchodniJmeno"),
        "| kat:",
        (z.get("statistickeUdaje") or {}).get("kategoriePoctuPracovniku"),
        "| nace:",
        z.get("czNacePrevazujici"),
        "| zanik:",
        z.get("datumZaniku"),
    )
(FIX / "res_batch_penny_candidates.json").write_text(
    json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
)
