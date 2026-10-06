# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27"]
# ///
"""ARES follow-up: how the name search tokenizes (diacritics, abbreviations, legal-form
suffixes), the textovaAdresa filter, and the RES search (which carries the employee-count
category used for ranking).

Run: uv run spikes/employers/ares_search_variants.py
"""

import json
import time
from pathlib import Path

import httpx

BASE = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest"
FIX = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "employers" / "ares"
client = httpx.Client(headers={"User-Agent": "AdaWork-spike/0.1"}, timeout=30)


def search(path: str, body: dict) -> dict | None:
    r = client.post(f"{BASE}{path}", json=body)
    time.sleep(1.5)
    if r.status_code != 200:
        print("   ", r.status_code, r.text[:200])
        return None
    return r.json()


def show(label: str, body: dict, path: str = "/ekonomicke-subjekty/vyhledat") -> dict | None:
    data = search(path, body)
    if data is None:
        print(f"{label}: error")
        return None
    subs = data.get("ekonomickeSubjekty", [])
    print(f"{label}: pocetCelkem={data.get('pocetCelkem')}")
    for s in subs[:5]:
        print(
            "    ",
            s.get("ico"),
            s.get("obchodniJmeno"),
            "|",
            (s.get("sidlo") or {}).get("nazevObce"),
            s.get("pravniForma"),
        )
    return data


def main() -> None:
    show("Skoda Auto (no diacritics)", {"obchodniJmeno": "Skoda Auto", "pocet": 5})
    show("Lidl", {"obchodniJmeno": "Lidl", "pocet": 10})
    show("Lidl Česká republika", {"obchodniJmeno": "Lidl Česká republika", "pocet": 5})
    show("Penny Market s.r.o.", {"obchodniJmeno": "Penny Market s.r.o.", "pocet": 5})
    show("Penny Market, s. r. o.", {"obchodniJmeno": "Penny Market, s. r. o.", "pocet": 5})
    show("Kaufl (prefix?)", {"obchodniJmeno": "Kaufl", "pocet": 5})
    show("U Lípy", {"obchodniJmeno": "U Lípy", "pocet": 10})
    show(
        "Albert + textovaAdresa Kolín",
        {"obchodniJmeno": "Albert", "sidlo": {"textovaAdresa": "Kolín"}, "pocet": 5},
    )
    show(
        "Albert + pravniForma 112", {"obchodniJmeno": "Albert", "pravniForma": ["112"], "pocet": 10}
    )
    # RES search: carries statistickeUdaje.kategoriePoctuPracovniku -> rank big employers first
    data = search(
        "/ekonomicke-subjekty-res/vyhledat", {"obchodniJmeno": "Penny Market", "pocet": 10}
    )
    if data:
        print("RES search keys:", list(data))
        rows = data.get("ekonomickeSubjekty") or data.get("zaznamy") or []
        for s in rows[:10]:
            z = s.get("zaznamy", [s])[0] if "zaznamy" in s else s
            print(
                "    ",
                s.get("icoId") or z.get("ico"),
                z.get("obchodniJmeno"),
                (z.get("statistickeUdaje") or {}).get("kategoriePoctuPracovniku"),
                z.get("czNacePrevazujici"),
            )
        (FIX / "res_search_penny_market.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=1)[:60000], encoding="utf-8"
        )


if __name__ == "__main__":
    main()
