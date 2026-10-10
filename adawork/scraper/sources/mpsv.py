"""MPSV open data: all job offers registered with Úřad práce ČR, every industry, daily dump.

Dataset: https://data.mpsv.cz/web/data/volna-mista-za-celou-cr (~40k records, 17.5 MB gzip).
Records reference codebooks by id ("Obec/554782"); we resolve the ones we display.
"""

import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, TypedDict

import httpx
import ijson

from adawork.core import ParsedJob, ParsedLocation, SalaryUnit, SourceKind
from adawork.net.http import download_if_changed, open_maybe_gzip
from adawork.scraper.store import JobStore

log = logging.getLogger(__name__)

SOURCE_ID = "mpsv"
# Bump when parse_record changes, so the next run re-imports even an unchanged dump.
PARSER_VERSION = 2
DUMP_URL = "https://data.mpsv.cz/od/soubory/volna-mista/volna-mista.json.gz"
CODEBOOK_URL = "https://data.mpsv.cz/od/soubory/ciselniky/{name}.json"
# Public detail page on the Úřad práce portal (verified 2026-10-06 in a browser).
DETAIL_URL = "https://up.gov.cz/volna-mista-v-cr#/volna-mista-detail/{id}"

CODEBOOKS = (
    "obce",
    "okresy",
    "kraje",
    "typy-mzdy",
    "pracovnepravni-vztahy",
    "smennosti",
    "vzdelani-detailni-kategorie",
    "vyhody-volneho-mista",
    "cz-isco",
    "obory-cinnosti-pro-vm",
)

_SALARY_UNIT: dict[str, SalaryUnit] = {"mesic": "month", "hod": "hour"}
_CONTRACT = {
    "plny": "full_time",
    "zkraceny": "part_time",
    "dpp": "dpp",
    "dpc": "dpc",
    "sluzebni": "service",
}
_EDUCATION = {
    "bezVzdel": "basic",
    "neuplZakl": "basic",
    "zaklPraktSkol": "basic",
    "nizsiStred": "basic",
    "nizsiStredOdbor": "basic",
    "stredOdbor": "vocational",
    "stredOdborVyuc": "vocational",
    "usoSMatVyuc": "maturita",
    "usv": "maturita",
    "usoSMat": "maturita",
    "konz": "higher_vocational",
    "vyssOdbor": "higher_vocational",
    "bakal": "bachelor",
    "vysoka": "master",
    "doktor": "doctoral",
}
_NO_VALUE = {"zdravi", "neurceno", "ne"}  # "healthy people", "unspecified", "not given"


class Codebooks:
    """id -> item dicts for the MPSV codebooks we need."""

    def __init__(self, books: dict[str, list[dict[str, Any]]]) -> None:
        self._by_id: dict[str, dict[str, Any]] = {}
        for items in books.values():
            for item in items:
                self._by_id[item["id"]] = item

    @classmethod
    def load_dir(cls, directory: Path) -> "Codebooks":
        books = {}
        for name in CODEBOOKS:
            path = directory / f"{name}.json"
            if path.exists():
                books[name] = json.loads(path.read_text(encoding="utf-8"))["polozky"]
        return cls(books)

    @classmethod
    def fetch(cls, client: httpx.Client, directory: Path) -> "Codebooks":
        for name in CODEBOOKS:
            download_if_changed(client, CODEBOOK_URL.format(name=name), directory / f"{name}.json")
        return cls.load_dir(directory)

    def okres_map(self) -> dict[str, tuple[str, str, str | None]]:
        """Municipality code -> (okres code, okres name, kraj name)."""
        result = {}
        for item in self._by_id.values():
            if item["id"].startswith("Obec/") and item.get("okres"):
                okres = self.get(item["okres"])
                if okres:
                    result[item["kod"]] = (
                        okres["kod"],
                        okres["nazev"]["cs"],
                        self.name(okres.get("kraj")),
                    )
        return result

    def get(self, ref: dict[str, Any] | str | None) -> dict[str, Any] | None:
        if not ref:
            return None
        return self._by_id.get(ref["id"] if isinstance(ref, dict) else ref)

    def name(self, ref: dict[str, Any] | str | None) -> str | None:
        item = self.get(ref)
        return item["nazev"]["cs"] if item else None

    def code(self, ref: dict[str, Any] | str | None) -> str | None:
        if not ref:
            return None
        ref_id = ref["id"] if isinstance(ref, dict) else ref
        return ref_id.partition("/")[2] or None


def iter_records(path: Path) -> Iterator[dict[str, Any]]:
    """Stream records from the (gzipped) dump without loading 187 MB into memory."""
    with open_maybe_gzip(path) as f:
        yield from ijson.items(f, "polozky.item", use_float=True)


def _cs(value: dict[str, str] | None) -> str | None:
    return value.get("cs") if value else None


def _datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value[:10]) if value else None


def _format_address(adresa: dict[str, Any], municipality: str | None) -> str | None:
    """Czech convention: "Na Valech 57/27, Poděbrady", or "Tvrdonice 701" without a street."""
    number = adresa.get("cisloDomovni")
    if number and adresa.get("cisloOrientacni"):
        number = f"{number}/{adresa['cisloOrientacni']}"
    street = (adresa.get("ulice") or {}).get("nazev")
    if street:
        line = f"{street} {number}" if number else street
        return f"{line}, {municipality}" if municipality else line
    if municipality:
        return f"{municipality} {number}" if number else municipality
    return None


class _Area(TypedDict):
    municipality: str | None
    municipality_code: str | None
    okres: str | None
    kraj: str | None


def _location(rec: dict[str, Any], cb: Codebooks) -> tuple[ParsedLocation | None, str | None]:
    misto = rec.get("mistoVykonuPrace") or {}

    def area(obec_ref, okres_ref=None) -> _Area:
        obec = cb.get(obec_ref)
        okres = cb.get(okres_ref or (obec or {}).get("okres"))
        return {
            "municipality": obec["nazev"]["cs"] if obec else None,
            "municipality_code": obec["kod"] if obec else None,
            "okres": okres["nazev"]["cs"] if okres else None,
            "kraj": cb.name((okres or {}).get("kraj")),
        }

    for pracoviste in misto.get("pracoviste") or []:
        adresa = pracoviste.get("adresa") or {}
        a = area(adresa.get("obec"), adresa.get("okres"))
        address = _format_address(adresa, a["municipality"])
        ruian = adresa.get("kodAdresnihoMista")
        if ruian:
            loc = ParsedLocation(
                key=f"ruian:{ruian}", precision="address", address_text=address, **a
            )
        elif a["municipality_code"]:
            loc = ParsedLocation(
                key=f"obec:{a['municipality_code']}", precision="municipality", **a
            )
        else:
            continue
        return loc, address or a["municipality"]

    if misto.get("obec"):
        a = area(misto["obec"])
        if a["municipality_code"]:
            loc = ParsedLocation(
                key=f"obec:{a['municipality_code']}", precision="municipality", **a
            )
            return loc, misto.get("adresaText") or a["municipality"]

    okresy = [cb.get(o) for o in misto.get("okresy") or []]
    okresy = [o for o in okresy if o]
    if okresy:
        codes = sorted(o["kod"] for o in okresy)
        names = ", ".join(o["nazev"]["cs"] for o in okresy)
        kraje = {cb.name(o.get("kraj")) for o in okresy}
        loc = ParsedLocation(
            key="okres:" + ",".join(codes),
            precision="district",
            okres=names,
            kraj=kraje.pop() if len(kraje) == 1 else None,
        )
        return loc, f"okres {names}"

    return None, misto.get("adresaText")


def _codes(cb: Codebooks, refs, mapping: dict[str, str] | None = None) -> list[str]:
    """Codebook references -> app codes, without empty/"unspecified" values or duplicates."""
    out: list[str] = []
    for ref in refs or []:
        code = cb.code(ref)
        if not code or code in _NO_VALUE:
            continue
        code = mapping.get(code, code) if mapping else code
        if code not in out:
            out.append(code)
    return out


def parse_record(rec: dict[str, Any], cb: Codebooks) -> ParsedJob:
    location, place_text = _location(rec, cb)
    employer = rec.get("zamestnavatel") or {}
    kontakt = rec.get("prvniKontaktSeZamestnavatelem") or {}
    komu = kontakt.get("komuSeHlasit") or {}
    kde = kontakt.get("kdeSeHlasit") or {}
    person = " ".join(
        p for p in (komu.get("jmeno"), komu.get("prijmeni")) if p and p.strip() not in ("", "-")
    )
    contact = {
        "name": person or None,
        "email": komu.get("email") or kde.get("email"),
        "phone": komu.get("telefon") or kde.get("telefon"),
        "where": kde.get("mistoKontaktu"),
    }
    flags = [
        "cizinecMimoEu",
        "modraKarta",
        "zamestnaneckaKarta",
        "azylant",
        "statniSpravaSamosprava",
        "souhlasAgenturyAgentura",
        "souhlasAgenturyUzivatel",
    ]
    extra = {
        "reference_number": rec.get("referencniCislo"),
        "hours_per_week": rec.get("pocetHodinTydne"),
        "employer_website": rec.get("urlAdresa"),
        "workplaces": len((rec.get("mistoVykonuPrace") or {}).get("pracoviste") or []),
        "isco_name": cb.name(rec.get("profeseCzIsco")),
        "end_date": rec.get("terminUkonceniPracovnihoPomeru"),
        "changed_at": rec.get("datumZmeny"),
    } | {f: rec[f] for f in flags if rec.get(f)}

    portal_id = str(rec["portalId"])
    salary_unit = cb.code(rec.get("typMzdy"))
    return ParsedJob(
        external_id=portal_id,
        url=DETAIL_URL.format(id=portal_id),
        title=(_cs(rec.get("pozadovanaProfese")) or extra["isco_name"] or "").strip(),
        description=(_cs(rec.get("upresnujiciInformace")) or "").strip(),
        employer_name=employer.get("nazev"),
        employer_ico=employer.get("ico"),
        place_text=place_text,
        location=location,
        salary_min=rec.get("mesicniMzdaOd") or None,
        salary_max=rec.get("mesicniMzdaDo") or None,
        salary_unit=_SALARY_UNIT.get(salary_unit or ""),
        contract_types=_codes(cb, rec.get("pracovnePravniVztahy"), _CONTRACT),
        shifts=_codes(cb, [rec.get("smennost")]),
        education=_EDUCATION.get(cb.code(rec.get("minPozadovaneVzdelani")) or ""),
        categories=_codes(cb, (cb.get(rec.get("profeseCzIsco")) or {}).get("oboryCinnosti")),
        suitable_for=_codes(cb, rec.get("vhodnostiPracovnihoMista")),
        benefits=_codes(cb, [v.get("vyhoda") for v in rec.get("vyhodyVolnehoMista") or []]),
        isco_code=cb.code(rec.get("profeseCzIsco")),
        positions_count=rec.get("pocetMist"),
        start_date=_date(rec.get("terminZahajeniPracovnihoPomeru")),
        posted_at=_datetime(rec.get("datumVlozeni")),
        expires_at=_datetime(rec.get("expirace")),
        contact={k: v for k, v in contact.items() if v},
        extra={k: v for k, v in extra.items() if v not in (None, [], "")},
    )


@dataclass
class DumpRead:
    skipped: bool = False  # the same file as at the last import: nothing to do
    notes: list[str] = field(default_factory=list)  # what the run's row tells
    state: dict[str, Any] = field(default_factory=dict)  # what the next run compares with


class MpsvSource:
    """Úřad práce: every open job in Czechia as one file a day, read before the portals."""

    id = SOURCE_ID
    label = "Úřad práce"
    kind: SourceKind = "portal"
    default_enabled = True
    needs_consent = False

    def read(
        self,
        client: httpx.Client,
        directory: Path,
        store: JobStore,
        previous: dict[str, Any],
        progress: Callable[[str, int], None] = lambda phase, n: None,
    ) -> DumpRead:
        """Downloads the dump (when changed) and stores its offers; offers no longer in it
        expire, unless most known ones are missing (a broken dump)."""
        progress("download", 0)
        codebooks = Codebooks.fetch(client, directory / "codebooks")
        dump = download_if_changed(
            client, DUMP_URL, directory / "volna-mista.json.gz", keep_gzip=True
        )
        stat = dump.path.stat()
        fingerprint = f"{stat.st_size}:{stat.st_mtime_ns}"
        if previous.get("dump") == fingerprint and previous.get("parser_version") == PARSER_VERSION:
            return DumpRead(skipped=True)
        progress("import", 0)
        imported = skipped = 0
        for record in iter_records(dump.path):
            try:
                job = parse_record(record, codebooks)
            except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
                skipped += 1  # one odd record must not stop the whole import
                if skipped == 1:
                    log.warning("mpsv: unreadable record skipped: %r", exc)
                continue
            store.add(job)
            imported += 1
            progress("import", imported)
        if not imported:
            raise ValueError("no readable job offers in the MPSV dump")
        store.commit()
        notes = [f"{skipped} unreadable records skipped"] if skipped else []
        if store.mostly_unseen():
            notes.append("most known offers missing from the dump: none expired")
        else:
            store.expire_missing()
        return DumpRead(notes=notes, state={"dump": fingerprint, "parser_version": PARSER_VERSION})


SOURCE = MpsvSource()
