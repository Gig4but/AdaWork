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

from adawork.net.http import download_if_changed

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

    def lookup(self, obec_code: str, adm_codes: set[str]) -> tuple[dict, Municipality | None]:
        """Coordinates of the wanted address points plus the municipality centre."""
        found: dict[str, tuple[float, float]] = {}
        ys, xs, name = [], [], None
        for row in self._rows(obec_code):
            try:
                y, x = float(row["Souřadnice Y"]), float(row["Souřadnice X"])
            except ValueError:
                continue  # a few address points have no coordinates yet
            ys.append(y)
            xs.append(x)
            name = name or row["Název obce"]
            if row["Kód ADM"] in adm_codes:
                found[row["Kód ADM"]] = sjtsk_to_wgs84(y, x)
        if not ys:
            return found, None
        # The median is robust against outlying settlements of large municipalities.
        lat, lon = sjtsk_to_wgs84(statistics.median(ys), statistics.median(xs))
        return found, Municipality(obec_code, name or "", lat, lon, len(ys))
