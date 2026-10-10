"""Coordinates from RÚIAN, the official Czech address register (ČÚZK open data).

One monthly ZIP (~63 MB) holds a CSV per municipality with every address point and its
S-JTSK coordinates. MPSV gives the RÚIAN address code for most workplaces, so we get exact
positions without geocoding; every other place falls back to its municipality centre.
"""

import csv
import io
import statistics
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import httpx
from pyproj import Transformer

from adawork.core import Precision
from adawork.net.http import download_if_changed
from adawork.normalize.text import fold

EXPORT_URL = "https://vdp.cuzk.gov.cz/vymenny_format/csv/{day:%Y%m%d}_OB_ADR_csv.zip"
FILE_NAME = "ruian_adresy.zip"


@lru_cache(maxsize=1)
def _to_wgs84() -> Transformer:
    return Transformer.from_crs("EPSG:5514", "EPSG:4326", always_xy=True)


def sjtsk_to_wgs84(y: float, x: float) -> tuple[float, float]:
    """RÚIAN's positive (Y, X) S-JTSK coordinates -> (lat, lon)."""
    lon, lat = _to_wgs84().transform(-y, -x)
    return lat, lon


def export_dates(today: date) -> list[date]:
    """The export is dated the last day of a month; try the newest two."""
    last_month_end = today.replace(day=1) - timedelta(days=1)
    before = last_month_end.replace(day=1) - timedelta(days=1)
    return [last_month_end, before]


def download(client: httpx.Client, directory: Path, today: date | None = None) -> Path:
    """Fetch the newest export (conditional GET). Returns the local ZIP path."""
    path = directory / FILE_NAME
    error: Exception | None = None
    for day in export_dates(today or date.today()):
        try:
            download_if_changed(client, EXPORT_URL.format(day=day), path)
            return path
        except httpx.HTTPError as exc:  # the newest month may not be published yet, or offline
            error = exc
    if path.exists():
        return path
    raise RuntimeError(f"RÚIAN export not available: {error}")


@dataclass
class Municipality:
    code: str
    name: str
    lat: float
    lon: float
    address_count: int
    postal_codes: frozenset[str] = frozenset()


@dataclass(frozen=True)
class PlaceQuery:
    """A place inside one municipality: a street (with an optional house number) or a part."""

    street: str | None = None  # folded street name
    number: str | None = None  # "245/24", "245" or "24"
    part: str | None = None  # folded name of a part, city district or Prague district


@dataclass
class PlaceMatch:
    lat: float
    lon: float
    precision: Precision  # address | street | part


_fold_name = lru_cache(maxsize=65536)(fold)  # street names repeat on every house


def _number_matches(number: str, row: dict[str, str]) -> bool:
    cp, co = row["Číslo domovní"], row["Číslo orientační"] + row["Znak čísla orientačního"]
    if "/" in number:
        first, second = number.split("/", 1)
        return first == cp and second.lower() == co.lower()
    return number.lower() in (cp, co.lower())


class RuianAddresses:
    def __init__(self, zip_path: Path) -> None:
        self.zip_path = zip_path
        self._zip = zipfile.ZipFile(zip_path)
        self._members = {
            name.rsplit("_OB_", 1)[1].split("_", 1)[0]: name
            for name in self._zip.namelist()
            if name.endswith("_ADR.csv")
        }

    def close(self) -> None:
        self._zip.close()

    def municipality_codes(self) -> list[str]:
        return list(self._members)

    def _rows(self, obec_code: str) -> Iterable[dict[str, str]]:
        member = self._members.get(obec_code)
        if member is None:
            return []
        raw = self._zip.read(member).decode("cp1250")
        return csv.DictReader(io.StringIO(raw), delimiter=";")

    def lookup(
        self,
        obec_code: str,
        adm_codes: set[str],
        places: dict[str, PlaceQuery] | None = None,
    ) -> tuple[dict[str, tuple[float, float]], dict[str, PlaceMatch], Municipality | None]:
        """(ADM code -> point of the wanted address points, key -> match of the `places`
        found, the municipality centre)."""
        points: dict[str, tuple[float, float]] = {}
        matches: dict[str, PlaceMatch] = {}
        ys, xs, name, postal_codes = [], [], None, set()
        by_street: dict[str, list[str]] = {}
        by_part: dict[str, list[str]] = {}
        places = places or {}
        for key, q in places.items():
            if q.street:
                by_street.setdefault(q.street, []).append(key)
            elif q.part:
                by_part.setdefault(q.part, []).append(key)
        hits: dict[str, tuple[list[float], list[float], bool]] = {}

        def hit(key: str, y: float, x: float, exact: bool) -> None:
            hit_ys, hit_xs, had_exact = hits.setdefault(key, ([], [], False))
            if exact and not had_exact:  # the house itself beats the street's middle
                hits[key] = ([y], [x], True)
            elif not had_exact:
                hit_ys.append(y)
                hit_xs.append(x)

        for row in self._rows(obec_code):
            try:
                y, x = float(row["Souřadnice Y"]), float(row["Souřadnice X"])
            except ValueError:
                continue  # a few address points have no coordinates yet
            ys.append(y)
            xs.append(x)
            name = name or row["Název obce"]
            if row["PSČ"]:
                postal_codes.add(row["PSČ"])
            if row["Kód ADM"] in adm_codes:
                points[row["Kód ADM"]] = sjtsk_to_wgs84(y, x)
            if by_street and row["Název ulice"]:
                for key in by_street.get(_fold_name(row["Název ulice"]), ()):
                    number = places[key].number
                    hit(key, y, x, bool(number) and _number_matches(number, row))
            if by_part:
                names = {row["Název části obce"], row["Název MOMC"], row["Název obvodu Prahy"]}
                for part in {_fold_name(n) for n in names if n}:
                    for key in by_part.get(part, ()):
                        hit(key, y, x, False)
        for key, (hit_ys, hit_xs, exact) in hits.items():
            lat, lon = sjtsk_to_wgs84(statistics.median(hit_ys), statistics.median(hit_xs))
            q = places[key]
            precision = "address" if exact else ("street" if q.street else "part")
            matches[key] = PlaceMatch(lat, lon, precision)
        if not ys:
            return points, matches, None
        # The median is robust against outlying settlements of large municipalities.
        lat, lon = sjtsk_to_wgs84(statistics.median(ys), statistics.median(xs))
        centre = Municipality(obec_code, name or "", lat, lon, len(ys), frozenset(postal_codes))
        return points, matches, centre
