"""Official lists for the employer checks (PLAN §4.5), kept fresh in the background (M7) so
an employer card can be checked offline later. Each download is small or conditional:
- unreliable VAT payers (Ministry of Finance, ~0.5 MB): daily, stored as DIČ -> date;
- the MPSV register of employment agencies (4 MB JSON, ETag): daily;
- the Atmoskop sitemap (profile slugs to find an employer's reviews, ETag): weekly.

docs/spikes/employers.md has the details.
"""

import json
import os
import xml.etree.ElementTree as ET
from datetime import timedelta
from pathlib import Path

import httpx

from adawork.net.http import Refresh, download_if_changed
from adawork.net.polite import PoliteFetcher

VAT_URL = "https://adisrws.mfcr.cz/dpr/axis2/services/rozhraniCRPDPH.rozhraniCRPDPHSOAP"
VAT_NS = "http://adis.mfcr.cz/rozhraniCRPDPH/"
VAT_FILE = "nespolehlivi-platci.json"
AGENCIES_URL = "https://data.mpsv.cz/od/soubory/agentury-prace/agentury-prace.json"
AGENCIES_FILE = "agentury-prace.json"
ATMOSKOP_SITEMAP = "https://www.atmoskop.cz/sitemap.xml"
ATMOSKOP_FILE = "atmoskop-sitemap.xml"


def unreliable_vat_payers(client: httpx.Client, directory: Path) -> int:
    """Downloads the list of unreliable VAT payers; returns how many there are."""
    body = (
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        f'xmlns:roz="{VAT_NS}"><soapenv:Body><roz:SeznamNespolehlivyPlatceRequest/>'
        "</soapenv:Body></soapenv:Envelope>"
    )
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": VAT_NS + "getSeznamNespolehlivyPlatce",
    }
    resp = client.post(VAT_URL, content=body.encode(), headers=headers, timeout=120)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    status = root.find(f".//{{{VAT_NS}}}status")
    # statusCode 2 during the daily outage (0:00–0:10): tried again later.
    if status is None or status.get("statusCode") != "0":
        text = status.get("statusText") if status is not None else "no status"
        raise ValueError(f"VAT register answered: {text}")
    payers = {
        e.get("dic"): e.get("datumZverejneniNespolehlivosti")
        for e in root.iter(f"{{{VAT_NS}}}statusPlatceDPH")
        if e.get("dic") and e.get("nespolehlivyPlatce") == "ANO"
    }
    if not payers:
        raise ValueError("VAT register sent an empty list")
    data = {"generated": status.get("odpovedGenerovana"), "payers": payers}
    _write(directory / VAT_FILE, json.dumps(data, separators=(",", ":")).encode())
    return len(payers)


def agency_register(client: httpx.Client, directory: Path) -> bool:
    """Downloads the agency register when it changed; returns whether it did."""
    download = download_if_changed(client, AGENCIES_URL, directory / AGENCIES_FILE)
    if download.changed:
        try:
            with download.path.open(encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or not isinstance(data.get("polozky"), list):
                raise ValueError("agency register without a list of agencies")
        except ValueError:
            _discard(download.path)
            raise
    return download.changed


def atmoskop_sitemap(client: httpx.Client, directory: Path) -> bool:
    """Downloads the Atmoskop sitemap when it changed (and robots.txt allows it)."""
    if not PoliteFetcher(client).allowed(ATMOSKOP_SITEMAP):
        raise PermissionError("robots.txt of atmoskop.cz disallows the sitemap")
    download = download_if_changed(client, ATMOSKOP_SITEMAP, directory / ATMOSKOP_FILE)
    if download.changed:
        with download.path.open("rb") as f:
            valid = b"<urlset" in f.read(4096)
        if not valid:
            _discard(download.path)
            raise ValueError("Atmoskop sitemap is not a list of pages")
    return download.changed


def refreshes(directory: Path) -> tuple[Refresh, ...]:
    return (
        Refresh("vat", timedelta(days=1), lambda c: unreliable_vat_payers(c, directory)),
        Refresh("agencies", timedelta(days=1), lambda c: agency_register(c, directory)),
        Refresh("atmoskop", timedelta(days=7), lambda c: atmoskop_sitemap(c, directory)),
    )


def _discard(path: Path) -> None:
    """A download that is not what was expected: without the file, the next try downloads
    it again instead of being told it has not changed."""
    path.unlink(missing_ok=True)


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, path)
