# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27", "zeep>=4.2"]
# ///
"""ISIR spike: ISIR_CUZK_WS SOAP service (getIsirWsCuzkData) queried by IČO.

Raw SOAP via httpx (preferred, no extra dependency) and the same call via zeep for comparison.
Limits from the official doc (Popis_WS_2_v1_13.pdf): <= 50 requests/min, <= 3000/day.

Run: uv run spikes/employers/isir_spike.py
"""

import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

URL = "https://isir.justice.cz:8443/isir_cuzk_ws/IsirWsCuzkService"
FIX = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "employers" / "isir"
FIX.mkdir(parents=True, exist_ok=True)

ENVELOPE = """<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:typ="http://isirws.cca.cz/types/">
 <soapenv:Header/>
 <soapenv:Body>
  <typ:getIsirWsCuzkDataRequest>
{fields}
  </typ:getIsirWsCuzkDataRequest>
 </soapenv:Body>
</soapenv:Envelope>"""


ORDER = [
    "ic",
    "rc",
    "druhVec",
    "bcVec",
    "rocnik",
    "nazevOsoby",
    "jmeno",
    "datumNarozeni",
    "maxPocetVysledku",
    "filtrAktualniRizeni",
    "vyhledatPresnouShoduJmen",
    "vyhledatBezDiakritiky",
    "maxRelevanceVysledku",
]


def call(**params: str) -> httpx.Response:
    # The XSD is a strict sequence: elements must follow ORDER and must not be empty.
    fields = "\n".join(f"   <{k}>{params[k]}</{k}>" for k in ORDER if params.get(k))
    body = ENVELOPE.format(fields=fields).encode("utf-8")
    r = httpx.post(
        URL,
        content=body,
        timeout=30,
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": '""',
            "User-Agent": "AdaWork-spike/0.1",
        },
    )
    time.sleep(2)
    return r


def parse(xml: bytes) -> dict:
    root = ET.fromstring(xml)
    out: dict = {"stav": None, "data": []}
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag == "stav":
            out["stav"] = {c.tag.split("}")[-1]: c.text for c in el}
        elif tag == "data":
            out["data"].append({c.tag.split("}")[-1]: c.text for c in el})
    return out


def main() -> None:
    # 1. clean company (Škoda Auto) - expect WS2 empty result
    r = call(ic="00177041", maxPocetVysledku="10")
    print("Skoda", r.status_code, parse(r.content))
    (FIX / "isir_00177041_none.xml").write_bytes(r.content)

    # 2. find some debtor by name prefix, then re-query by its IČO
    r = call(nazevOsoby="Stavby", filtrAktualniRizeni="T", maxPocetVysledku="5")
    res = parse(r.content)
    print("name 'Stavby'", r.status_code, res["stav"], len(res["data"]))
    ico = next((d.get("ic") for d in res["data"] if d.get("ic")), None)
    for d in res["data"][:5]:
        print(
            "   ",
            d.get("ic"),
            d.get("nazevOsoby"),
            d.get("druhStavKonkursu"),
            d.get("urlDetailRizeni"),
        )
    if ico:
        r = call(ic=ico, maxPocetVysledku="10")
        res = parse(r.content)
        print("by IC", ico, r.status_code, res["stav"])
        for d in res["data"]:
            print("   ", d)
        (FIX / f"isir_{ico}_debtor.xml").write_bytes(r.content)

    # 3. bad request: no parameter -> WS1
    r = call(maxPocetVysledku="5")
    print(
        "empty", r.status_code, parse(r.content)["stav"] if r.status_code == 200 else r.text[:300]
    )

    # 4. zeep variant
    try:
        from zeep import Client

        c = Client(URL + "?wsdl")
        resp = c.service.getIsirWsCuzkData(ic="00177041", maxPocetVysledku=5)
        print("zeep:", resp)
    except Exception as e:  # noqa: BLE001
        print("zeep failed:", type(e).__name__, e)


if __name__ == "__main__":
    main()
