# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27", "rapidfuzz>=3.9"]
# ///
"""ARES spike: lookup by IČO, name search with messy ad names, RES/VR detail, codebooks.

Run: uv run spikes/employers/ares_spike.py
Writes trimmed fixtures to tests/fixtures/employers/ares/.
"""

import json
import time
import unicodedata
from pathlib import Path

import httpx
from rapidfuzz import fuzz

BASE = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest"
ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "employers" / "ares"
FIX.mkdir(parents=True, exist_ok=True)
UA = "AdaWork-spike/0.1 (+desktop job search app; contact petrunyo)"
PAUSE = 1.5

client = httpx.Client(headers={"User-Agent": UA, "Accept": "application/json"}, timeout=30)


def save(name: str, data) -> None:
    (FIX / name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    for suffix in (" s.r.o.", " a.s.", " v.o.s.", " k.s.", " spol. s r.o.", ",", "."):
        s = s.replace(suffix, " ")
    return " ".join(s.split())


def get(path: str):
    r = client.get(f"{BASE}{path}")
    time.sleep(PAUSE)
    return r


def post(path: str, body: dict):
    r = client.post(f"{BASE}{path}", json=body)
    time.sleep(PAUSE)
    return r


def main() -> None:
    # 1. lookup by IČO
    r = get("/ekonomicke-subjekty/00177041")
    print(
        "GET by ICO",
        r.status_code,
        {
            k: v
            for k, v in r.headers.items()
            if "limit" in k.lower() or k.lower() in ("retry-after", "cache-control")
        },
    )
    skoda = r.json()
    save("subjekt_00177041_skoda.json", skoda)
    print(" keys:", list(skoda))
    print(" registrace:", skoda.get("seznamRegistraci"))

    r = get("/ekonomicke-subjekty/99999999")
    print("GET unknown ICO", r.status_code, r.text[:200])
    save("subjekt_not_found.json", r.json())

    # 2. RES (NACE, employee category) and VR (public register)
    r = get("/ekonomicke-subjekty-res/00177041")
    print("RES", r.status_code)
    res = r.json()
    save("res_00177041_skoda.json", res)
    for z in res.get("zaznamy", [])[:1]:
        print(
            " RES statistickeUdaje:",
            z.get("statistickeUdaje"),
            "nace prevaz:",
            z.get("czNacePrevazujici"),
            z.get("czNacePrevazujici2008"),
        )

    r = get("/ekonomicke-subjekty-vr/00177041")
    print("VR", r.status_code, len(r.content), "bytes")
    vr = r.json()
    zaznam = (vr.get("zaznamy") or [{}])[0]
    print(" VR zaznam keys:", list(zaznam))
    trimmed = {
        "icoId": vr.get("icoId"),
        "zaznamy": [
            {
                k: zaznam[k]
                for k in zaznam
                if k
                in (
                    "ico",
                    "obchodniJmeno",
                    "datumVzniku",
                    "datumZapisu",
                    "pravniForma",
                    "spisovaZnacka",
                    "adresy",
                    "insolvence",
                    "konkursy",
                    "stavSubjektu",
                    "zakladniKapital",
                    "cinnosti",
                    "primarniZaznam",
                    "pocetClenu",
                )
            }
        ],
    }
    save("vr_00177041_skoda_trimmed.json", trimmed)

    # 3. codebook for employee-count category
    r = post(
        "/ciselniky-nazevniky/vyhledat",
        {"kodCiselniku": "KategoriePoctuPracovniku", "zdrojCiselniku": "res"},
    )
    print("ciselnik KategoriePoctuPracovniku", r.status_code)
    if r.status_code == 200:
        save("ciselnik_kategorie_poctu_pracovniku.json", r.json())
        for c in r.json().get("ciselniky", []):
            for p in c.get("polozkyCiselniku", [])[:20]:
                print(
                    "  ",
                    p.get("kod"),
                    [n.get("nazev") for n in p.get("nazev", []) if n.get("kodJazyka") == "cs"],
                )
    else:
        print(r.text[:300])

    # 4. name search with messy ad names
    queries = [
        ("Albert", None),
        ("Albert Česká republika", None),
        ("Lidl ČR", None),
        ("Kaufland", None),
        ("Hospoda U Lípy", None),
        ("ŠKODA AUTO", None),
        ("Penny", None),
        ("Penny Market", None),
        ("Hospoda U Lípy", "Praha"),
    ]
    results = {}
    for name, place in queries:
        body = {"obchodniJmeno": name, "pocet": 20, "start": 0}
        if place:
            body["sidlo"] = {"textovaAdresa": place}
        r = post("/ekonomicke-subjekty/vyhledat", body)
        key = f"{name} @ {place}" if place else name
        if r.status_code != 200:
            print(f"SEARCH {key!r}: {r.status_code} {r.text[:300]}")
            results[key] = {
                "status": r.status_code,
                "body": r.json()
                if r.headers.get("content-type", "").startswith("application/json")
                else r.text[:500],
            }
            continue
        data = r.json()
        subs = data.get("ekonomickeSubjekty", [])
        print(f"SEARCH {key!r}: pocetCelkem={data.get('pocetCelkem')} returned={len(subs)}")
        scored = []
        for s in subs:
            nm = s.get("obchodniJmeno", "")
            score = fuzz.token_set_ratio(norm(name), norm(nm))
            active = s.get("datumZaniku") is None
            scored.append(
                (
                    score,
                    active,
                    s.get("ico"),
                    nm,
                    (s.get("sidlo") or {}).get("nazevObce"),
                    s.get("pravniForma"),
                    s.get("datumZaniku"),
                )
            )
        scored.sort(key=lambda t: (-t[0], not t[1]))
        for row in scored[:6]:
            print("   ", row)
        results[key] = {
            "pocetCelkem": data.get("pocetCelkem"),
            "top": [
                {
                    "ico": s.get("ico"),
                    "obchodniJmeno": s.get("obchodniJmeno"),
                    "obec": (s.get("sidlo") or {}).get("nazevObce"),
                    "pravniForma": s.get("pravniForma"),
                    "datumZaniku": s.get("datumZaniku"),
                }
                for s in subs[:10]
            ],
        }
    save("search_results_summary.json", results)

    # save one raw search response (small)
    r = post("/ekonomicke-subjekty/vyhledat", {"obchodniJmeno": "Penny Market", "pocet": 3})
    save("search_penny_market_raw.json", r.json())

    # 5. limits: large pocet / start
    r = post("/ekonomicke-subjekty/vyhledat", {"obchodniJmeno": "Albert", "pocet": 1000})
    print(
        "pocet=1000:",
        r.status_code,
        r.text[:200] if r.status_code != 200 else len(r.json().get("ekonomickeSubjekty", [])),
    )
    r = post(
        "/ekonomicke-subjekty/vyhledat", {"obchodniJmeno": "Albert", "pocet": 100, "start": 1000}
    )
    print(
        "start=1000:",
        r.status_code,
        r.text[:300] if r.status_code != 200 else len(r.json().get("ekonomickeSubjekty", [])),
    )
    r = post("/ekonomicke-subjekty/vyhledat", {"obchodniJmeno": "s.r.o.", "pocet": 10})
    print(
        "very broad query:",
        r.status_code,
        r.text[:300] if r.status_code != 200 else r.json().get("pocetCelkem"),
    )
    # search with sidlo.kodObce only (Kolín = 533165) and name
    r = post(
        "/ekonomicke-subjekty/vyhledat",
        {"obchodniJmeno": "Albert", "sidlo": {"kodObce": 554782}, "pocet": 10},
    )
    print(
        "Albert + kodObce Praha:",
        r.status_code,
        r.text[:300] if r.status_code != 200 else r.json().get("pocetCelkem"),
    )


if __name__ == "__main__":
    main()
