"""Official registers (PLAN §2.4, docs/spikes/employers.md): ARES (business register) and its
RES statistics by IČO in batches of 100, the ARES name search for the resolver, ISIR
(insolvency proceedings) only when ARES flags one, and the two daily lists kept by
downloads.py (unreliable VAT payers, licensed employment agencies) as local lookups.
"""

import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import httpx

from adawork.employers import downloads
from adawork.employers.names import name_key
from adawork.normalize.text import fold

ARES = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest"
BATCH = 100  # IČOs per ARES / RES call
ARES_GAP = 0.3  # seconds between ARES calls (MF may block above 500 a minute)
ISIR_URL = "https://isir.justice.cz:8443/isir_cuzk_ws/IsirWsCuzkService"
ISIR_NS = "http://isirws.cca.cz/types/"
# Legal forms (ARES "PravniForma") that do not hire through job ads: trade unions,
# foundations, owners' associations. Associations (spolky) sometimes do: a smaller penalty.
NOT_EMPLOYERS = frozenset({"707", "117", "118", "145"})
ASSOCIATIONS = frozenset({"706", "751"})
SOLE_TRADERS = frozenset({"101", "102", "105", "106", "107", "108", "424", "425"})
BANDS = {
    "110": "0", "120": "1–5", "130": "6–9", "210": "10–19", "220": "20–24", "230": "25–49",
    "240": "50–99", "310": "100–199", "320": "200–249", "330": "250–499", "340": "500–999",
    "410": "1 000–1 499", "420": "1 500–1 999", "430": "2 000–2 499", "440": "2 500–2 999",
    "450": "3 000–3 999", "460": "4 000–4 999", "470": "5 000–9 999", "510": "10 000+",
}  # fmt: skip


class TooMany(Exception):
    """ARES found more than 1000 subjects for the name."""


@dataclass
class Company:
    ico: str
    name: str
    legal_form: str = ""
    founded: date | None = None
    ended: date | None = None
    address: str = ""
    municipality: str = ""
    municipality_code: int | None = None
    dic: str | None = None
    registrations: dict[str, str] = field(default_factory=dict)
    nace: list[str] = field(default_factory=list)
    band: str | None = None  # RES employee band code
    nace_main: str | None = None  # RES main activity

    @property
    def active(self) -> bool:
        return self.ended is None

    @property
    def insolvency_flag(self) -> bool:
        """ARES says the insolvency register has a record (ISIR then tells which)."""
        return self.registrations.get("stavZdrojeIr", "NEEXISTUJICI") != "NEEXISTUJICI"

    @property
    def band_rank(self) -> int:
        codes = list(BANDS)
        return codes.index(self.band) + 1 if self.band in BANDS else 0


def _date(value) -> date | None:
    try:
        return date.fromisoformat(value[:10]) if value else None
    except ValueError:
        return None


def company(record: dict) -> Company:
    """An ARES record; `ico` is "" for one without a Czech IČO (some foreign subjects)."""
    seat = record.get("sidlo") or {}
    ico = record.get("ico") or ""
    return Company(
        ico=ico if re.fullmatch(r"\d{8}", ico) else "",
        name=record.get("obchodniJmeno") or "",
        legal_form=record.get("pravniForma") or "",
        founded=_date(record.get("datumVzniku")),
        ended=_date(record.get("datumZaniku")),
        address=seat.get("textovaAdresa") or "",
        municipality=seat.get("nazevObce") or "",
        municipality_code=seat.get("kodObce"),
        dic=record.get("dic"),
        registrations=record.get("seznamRegistraci") or {},
        nace=list(record.get("czNace") or record.get("czNace2008") or []),
    )


class Ares:
    # The gap between calls is kept across instances: the background pass and opened cards
    # each make their own, and ARES counts them all.
    _last = 0.0
    _turn = threading.Lock()

    def __init__(self, client: httpx.Client, sleep=time.sleep, clock=time.monotonic) -> None:
        self.client = client
        self.sleep, self.clock = sleep, clock

    def _post(self, path: str, body: dict) -> dict:
        with Ares._turn:
            wait = Ares._last + ARES_GAP - self.clock()
            if wait > 0:
                self.sleep(wait)
            Ares._last = self.clock()
        resp = self.client.post(
            ARES + path, json=body, headers={"Accept": "application/json"}, timeout=60
        )
        if resp.status_code == 400 and "PRILIS_MNOHO" in resp.text:
            raise TooMany(body.get("obchodniJmeno"))
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        return resp.json()

    def companies(self, icos: list[str]) -> dict[str, Company]:
        """ARES records with RES statistics, by IČO (missing IČOs are left out)."""
        found: dict[str, Company] = {}
        for i in range(0, len(icos), BATCH):
            chunk = icos[i : i + BATCH]
            body = {"ico": chunk, "start": 0, "pocet": BATCH}
            for record in self._post("/ekonomicke-subjekty/vyhledat", body).get(
                "ekonomickeSubjekty", []
            ):
                c = company(record)
                if c.ico:
                    found[c.ico] = c
            self.add_statistics([found[i] for i in chunk if i in found])
        return found

    def add_statistics(self, companies: list[Company]) -> None:
        """Employee band and main activity from RES (one call per 100)."""
        for i in range(0, len(companies), BATCH):
            chunk = {c.ico: c for c in companies[i : i + BATCH] if c.ico}
            if not chunk:
                continue
            body = {"ico": list(chunk), "start": 0, "pocet": BATCH}
            data = self._post("/ekonomicke-subjekty-res/vyhledat", body)
            for item in data.get("ekonomickeSubjekty", []):
                records = item.get("zaznamy") or []
                record = next((r for r in records if r.get("primarniZaznam")), None)
                record = record or (records[0] if records else {})
                key = item.get("icoId") or record.get("ico")
                c = chunk.get(key) if key else None
                if c is not None:
                    c.band = (record.get("statistickeUdaje") or {}).get("kategoriePoctuPracovniku")
                    c.nace_main = record.get("czNacePrevazujici")

    def search(self, name: str, limit: int = 200) -> tuple[int, list[Company]]:
        """(how many subjects ARES has under the name, the first `limit`). ARES matches all
        words, ignores case and diacritics, and orders by IČO, not by relevance."""
        body = {"obchodniJmeno": name, "start": 0, "pocet": limit}
        data = self._post("/ekonomicke-subjekty/vyhledat", body)
        records = data.get("ekonomickeSubjekty") or []
        found = [c for c in map(company, records) if c.ico]  # foreign ones may have no IČO
        return data.get("pocetCelkem", len(records)), found


# --- ISIR -------------------------------------------------------------------------------------

_ISIR_ENVELOPE = (
    '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
    f'xmlns:typ="{ISIR_NS}"><soapenv:Body><typ:getIsirWsCuzkDataRequest>'
    "<ic>{ico}</ic><maxRelevanceVysledku>2</maxRelevanceVysledku>"
    "</typ:getIsirWsCuzkDataRequest></soapenv:Body></soapenv:Envelope>"
)


# ISIR's state codes ("VYRIZENA", "K-PO ZRUSENI") by their start, as plain-text keys.
_ISIR_STATES = (
    ("nevyrizen", "pending"),
    ("moratorium", "moratorium"),
    ("upadek", "bankrupt"),
    ("konkurs", "konkurs"),
    ("oddluzeni", "debt_relief"),
    ("reorganiz", "reorganization"),
    ("vyrizen", "ended"),
    ("pravomocn", "ended"),
    ("odskrtnut", "ended"),
    ("zrusen", "cancelled"),
    ("k-po zrusen", "cancelled"),
    ("mylny", "error"),
)


def insolvency_state(state: str) -> str | None:
    """The i18n key ("employer.isir.<key>") of an ISIR state code; None for an unknown one."""
    folded = fold(state).strip()
    return next((key for start, key in _ISIR_STATES if folded.startswith(start)), None)


def insolvency(client: httpx.Client, ico: str) -> list[dict]:
    """Insolvency proceedings of the IČO: [{"state", "court", "case", "since", "until", "url"}].
    Empty when there are none (ISIR answers WS2)."""
    resp = client.post(
        ISIR_URL,
        content=_ISIR_ENVELOPE.format(ico=ico).encode(),
        headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '""'},
        timeout=60,
    )
    resp.raise_for_status()
    try:
        return parse_insolvency(resp.content)
    except ET.ParseError as exc:  # a SyntaxError: callers handle ValueError
        raise ValueError(f"ISIR answer for {ico} is not XML: {exc}") from exc


def parse_insolvency(xml: bytes) -> list[dict]:
    root = ET.fromstring(xml)
    found = []
    for data in root.iter("data"):

        def get(tag: str, data=data) -> str:
            return (data.findtext(tag) or "").strip()

        case = f"{get('druhVec')} {get('bcVec')}/{get('rocnik')}".strip()
        found.append(
            {
                "state": get("druhStavKonkursu"),
                "court": get("nazevOrganizace"),
                "case": case if case != "/" else "",
                "since": get("datumPmZahajeniUpadku").rstrip("Z") or None,
                "until": get("datumPmUkonceniUpadku").rstrip("Z") or None,
                "url": get("urlDetailRizeni"),
            }
        )
    return found


# --- local lists ------------------------------------------------------------------------------


@dataclass
class Agency:
    name: str
    temp_staffing: bool  # licensed to hire people out (agenturní zaměstnávání)
    until: date | None  # None: no end date (most permits)


class Lists:
    """The daily VAT and agency lists from the cache folder, reloaded when they change."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._stamp: tuple = ()
        self.vat: dict[str, str] = {}  # DIČ without "CZ" -> published unreliable since
        self.agencies: dict[str, Agency] = {}  # IČO -> licence
        self.agency_names: dict[str, str] = {}  # name key -> IČO

    def fresh(self) -> "Lists":
        files = [self.directory / downloads.VAT_FILE, self.directory / downloads.AGENCIES_FILE]
        stamp = tuple(f.stat().st_mtime if f.exists() else 0 for f in files)
        if stamp != self._stamp:
            self._load(*files)
            self._stamp = stamp  # after loading: a file that failed to load is tried again
        return self

    @property
    def stamp(self) -> str:
        return ",".join(str(int(s)) for s in self._stamp)

    def _load(self, vat_file: Path, agencies_file: Path) -> None:
        if vat_file.exists():
            data = json.loads(vat_file.read_text(encoding="utf-8"))
            self.vat = {k.removeprefix("CZ"): v for k, v in data.get("payers", {}).items()}
        if agencies_file.exists():
            data = json.loads(agencies_file.read_text(encoding="utf-8"))
            agencies: dict[str, Agency] = {}
            names: dict[str, str] = {}
            for item in data.get("polozky", []):
                ico = (item.get("ico") or "").strip()
                if not ico:
                    continue
                permits = item.get("povoleni") or []
                temp = any(p.get("zamestnavaniOsob") for p in permits)
                ends = [d for p in permits if (d := _date(p.get("platnostDo")))]
                until = max(ends) if ends and len(ends) == len(permits) else None
                known = agencies.get(ico)
                if known:  # one IČO, several records: permits merged
                    temp = temp or known.temp_staffing
                    until = (
                        None if known.until is None or until is None else max(until, known.until)
                    )
                agencies[ico] = Agency(item.get("nazev") or "", temp, until)
                names.setdefault(name_key(item.get("nazev")), ico)
            # Swapped whole: another thread may be reading them (a card's check).
            self.agencies, self.agency_names = agencies, names

    def unreliable_since(self, dic: str | None) -> str | None:
        """When the VAT payer was published as unreliable; None when it is not."""
        if not dic:
            return None
        return self.vat.get(dic.removeprefix("CZ"))
