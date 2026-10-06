# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx"]
# ///
"""Download the MPSV codebooks referenced by volna-mista.json, plus its JSON schema, as fixtures."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import polite

BASE = "https://data.mpsv.cz/od/soubory/ciselniky/"
PREFIX_TO_FILE = {
    "Okres": "okresy",
    "Obec": "obce",
    "Kraj": "kraje",
    "CastObce": "casti-obci",
    "TypStavebnihoObjektu": "typy-stavebniho-objektu",
    "VhodnostProTypZamestnance": "vhodnosti-pro-typ-zamestnance",
    "PracovnepravniVztah": "pracovnepravni-vztahy",
    "CzIsco": "cz-isco",
    "VzdelaniDetailniKategorie": "vzdelani-detailni-kategorie",
    "Smennost": "smennosti",
    "TypMzdy": "typy-mzdy",
    "ZverejnovatVpm": "zverejnovat-vpm",
    "KontaktniPracoviste": "kontaktni-pracoviste",
    "TypMistaVykonuPrace": "typy-mista-vykonu-prace",
    "MestskyObvodMestskaCast": "mestske-obvody-mestske-casti",
    "MestskyObvodVPraze": "mestske-obvody-v-praze",
    "VyhodyVolnehoMista": "vyhody-volneho-mista",
    "Dovednost": "dovednosti",
    "UrovenZnalostiJazyka": "urovne-znalosti-jazyka",
    "Jazyk": "jazyky",
    "VzdelaniKkov": "vzdelani-kkov",
}

report = {}
for prefix, name in PREFIX_TO_FILE.items():
    r = polite.get(BASE + name + ".json")
    ok = r.status_code == 200 and "json" in r.headers.get("content-type", "")
    entry = {"file": name + ".json", "status": r.status_code, "bytes": len(r.content)}
    if ok:
        data = r.json()
        items = data.get("polozky", [])
        entry["items"] = len(items)
        entry["first_item_keys"] = sorted(items[0].keys()) if items else []
        entry["id_prefix_matches"] = bool(items) and items[0].get("id", "").startswith(prefix + "/")
        if len(r.content) <= polite.MAX_FIXTURE:
            polite.save("mpsv", f"codebooks/{name}.json", r.content)
        else:
            polite.save_json("mpsv", f"codebooks/{name}.sample.json", {"polozky": items[:50]})
            entry["fixture"] = "sample of 50 items (full file > 1 MB)"
    report[prefix] = entry
    print(prefix, json.dumps(entry, ensure_ascii=False))

r = polite.get("https://data.mpsv.cz/od/soubory/volna-mista/volna-mista.schema.json")
polite.save("mpsv", "volna-mista.schema.json", r.content)
h = polite.client.head("https://data.mpsv.cz/od/soubory/volna-mista/volna-mista.json.gz")
report["_gzip_variant"] = {k: h.headers.get(k) for k in ("content-length", "last-modified", "etag")}
polite.save_json("mpsv", "codebooks/_report.json", report)
polite.summary()
