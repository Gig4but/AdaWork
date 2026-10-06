# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27"]
# ///
"""Unreliable VAT payer spike: ADIS rozhraniCRPDPH SOAP service (Ministry of Finance).

Operations tested:
- getStatusNespolehlivyPlatce (<= 100 DIČ per call, DIČ without the "CZ" prefix)
- getStatusNespolehlivySubjektRozsirenyV2 (adds name, address, subject type incl. "nespolehlivá osoba")
- getSeznamNespolehlivyPlatce (full list of unreliable payers; measured size)

Run: uv run spikes/employers/vat_spike.py
"""

import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

URL = "https://adisrws.mfcr.cz/dpr/axis2/services/rozhraniCRPDPH.rozhraniCRPDPHSOAP"
NS = "http://adis.mfcr.cz/rozhraniCRPDPH/"
FIX = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "employers" / "vat"
FIX.mkdir(parents=True, exist_ok=True)


def envelope(element: str, dics: list[str]) -> bytes:
    inner = "".join(f"<roz:dic>{d}</roz:dic>" for d in dics)
    return (
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        f'xmlns:roz="{NS}"><soapenv:Header/><soapenv:Body>'
        f"<roz:{element}>{inner}</roz:{element}>"
        "</soapenv:Body></soapenv:Envelope>"
    ).encode()


def call(op: str, element: str, dics: list[str], url: str = URL) -> httpx.Response:
    r = httpx.post(
        url,
        content=envelope(element, dics),
        timeout=120,
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": NS + op,
            "User-Agent": "AdaWork-spike/0.1",
        },
    )
    time.sleep(2)
    return r


def summarize(xml: bytes) -> None:
    root = ET.fromstring(xml)
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag == "status":
            print("   status:", el.attrib)
        elif tag in ("statusPlatceDPH", "statusSubjektu"):
            name = el.find(f"{{{NS}}}nazevSubjektu")
            accounts = len(el.findall(f".//{{{NS}}}ucet"))
            print(
                "   ",
                el.attrib,
                "|",
                name.text if name is not None else "",
                "| accounts:",
                accounts,
            )


def main() -> None:
    # Škoda Auto (CZ00177041), Penny Market (CZ64945880), Stavby Kohout (in insolvency, DPH ZANIKLY),
    # 12345678 = nonexistent
    dics = ["00177041", "64945880", "01493248", "12345678"]
    r = call("getStatusNespolehlivyPlatce", "StatusNespolehlivyPlatceRequest", dics)
    print("getStatusNespolehlivyPlatce", r.status_code, len(r.content), "bytes")
    summarize(r.content)
    # trim the published bank accounts list to keep the fixture small
    trimmed = re.sub(
        rb"(<[^>]*zverejneneUcty>)(.*?)(</[^>]*zverejneneUcty>)",
        lambda m: m.group(1) + m.group(2)[:400] + b"<!-- trimmed -->" + m.group(3),
        r.content,
        flags=re.S,
    )
    (FIX / "status_nespolehlivy_platce.xml").write_bytes(trimmed)

    r = call(
        "getStatusNespolehlivySubjektRozsirenyV2",
        "StatusNespolehlivySubjektRozsirenyV2Request",
        dics,
    )
    print("getStatusNespolehlivySubjektRozsirenyV2", r.status_code, len(r.content), "bytes")
    if r.status_code == 200:
        summarize(r.content)
        trimmed = re.sub(
            rb"(<[^>]*zverejneneUcty>)(.*?)(</[^>]*zverejneneUcty>)",
            lambda m: m.group(1) + m.group(2)[:400] + b"<!-- trimmed -->" + m.group(3),
            r.content,
            flags=re.S,
        )
        (FIX / "status_subjekt_rozsireny_v2.xml").write_bytes(trimmed)
    else:
        print(r.text[:500])

    # whole list of unreliable payers: how big is it?
    t = time.time()
    r = call("getSeznamNespolehlivyPlatce", "SeznamNespolehlivyPlatceRequest", [])
    print(
        "getSeznamNespolehlivyPlatce",
        r.status_code,
        len(r.content),
        "bytes",
        f"{time.time() - t:.1f}s",
    )
    if r.status_code == 200:
        root = ET.fromstring(r.content)
        items = [e for e in root.iter() if e.tag.endswith("statusPlatceDPH")]
        st = next(e for e in root.iter() if e.tag.endswith("status"))
        print("   status:", st.attrib, "count:", len(items))
        if items:
            print("   first:", items[0].attrib)
            ano = [e.attrib["dic"] for e in items if e.attrib.get("nespolehlivyPlatce") == "ANO"][
                :3
            ]
            print("   sample ANO dic:", ano)
            # fixture: header + first 3 entries
            head = r.content[: r.content.find(b"<", r.content.find(b"statusPlatceDPH") + 1500)]
            (FIX / "seznam_nespolehlivy_platce_head.xml").write_bytes(head[:3000])
            if ano:
                r2 = call("getStatusNespolehlivyPlatce", "StatusNespolehlivyPlatceRequest", ano[:1])
                print("   single unreliable payer:")
                summarize(r2.content)
                (FIX / "status_unreliable_payer.xml").write_bytes(r2.content[:4000])
    else:
        print(r.text[:500])


if __name__ == "__main__":
    main()
