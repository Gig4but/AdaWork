"""A typed address -> a start point for travel times ("Nádražní 5, 280 02 Kolín", "Kolín").

The house comes from RÚIAN when the street and number are known, the middle of the street when
only the street is; otherwise the municipality centre. Works offline, also on PCs where the
routing data has no geocoder (PLAN §4.6).
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from adawork.geo import ruian
from adawork.geo.places import PlaceResolver
from adawork.normalize.text import clean_spaces, fold

_POSTAL = re.compile(r"\b\d{3}\s?\d{2}\b")
_STREET = re.compile(
    r"^(?P<street>.*?[^\W\d][^\d]*?)\s*(?P<number>\d+[a-zA-Z]?(?:/\d+[a-zA-Z]?)?)?$"
)


@dataclass(frozen=True)
class Origin:
    lat: float
    lon: float
    label: str
    precision: str = "address"  # address | street | municipality | point

    @property
    def key(self) -> str:
        """Equal for points ~10 m apart: the travel-time cache key."""
        return f"{self.lat:.4f},{self.lon:.4f}"

    @property
    def point(self) -> tuple[float, float]:
        return (self.lat, self.lon)


def parse_point(text: str) -> tuple[float, float] | None:
    """Typed coordinates ("50.0281,15.2005") -> (lat, lon) inside Czechia, else None."""
    m = re.fullmatch(r"\s*(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)\s*", text or "")
    if not m:
        return None
    lat, lon = float(m[1]), float(m[2])
    return (lat, lon) if 48.4 <= lat <= 51.2 and 11.9 <= lon <= 19.0 else None


def _street_piece(address: str, municipality: str) -> str | None:
    """The street and house number in an address: the piece that is not the town or PSČ."""
    town = fold(municipality)
    for piece in address.split(","):
        piece = clean_spaces(_POSTAL.sub("", piece))
        if len(re.sub(r"[\d\s/]", "", piece)) < 3:
            continue
        rest = fold(piece).removeprefix(town)
        if rest != fold(piece) and not rest[:1].isalpha():  # "Kolín IV", "Praha 4", not "Kolínská"
            continue
        return piece
    return None


def locate(places: PlaceResolver, ruian_zip: Path | None, address: str) -> Origin | None:
    """The point of a typed address; None if not even the town is known."""
    address = clean_spaces(address)
    muni = places.find_home(address) if address else None
    if muni is None:
        return None
    piece = _street_piece(address, muni.name)
    m = _STREET.match(piece) if piece else None
    if m and ruian_zip and ruian_zip.exists():
        street, number = m["street"].strip(" ,."), m["number"] or ""
        query = ruian.PlaceQuery(street=fold(street), number=number or None)
        addresses = ruian.RuianAddresses(ruian_zip)
        try:
            _, matches, _ = addresses.lookup(muni.code, set(), {"home": query})
        finally:
            addresses.close()
        match = matches.get("home")
        if match is not None:
            label = f"{street} {number}".strip() + f", {muni.name}"
            return Origin(match.lat, match.lon, label, match.precision)
    return Origin(muni.lat, muni.lon, muni.name, "municipality")


class Locator:
    """Start points of typed addresses, remembered: the same address is asked on every page."""

    def __init__(self, places: Callable[[], PlaceResolver], addresses: Path) -> None:
        self.places = places
        self.addresses = addresses  # RÚIAN address points (geo/ruian.py), once downloaded
        self._found: dict[str, Origin] = {}

    def locate(self, text: str) -> Origin | None:
        """A typed address, or "lat,lon" from a click on the map."""
        text = text.strip()[:200]
        if not text:
            return None
        if point := parse_point(text):
            return Origin(point[0], point[1], text, "point")
        if text in self._found:
            return self._found[text]
        found = locate(self.places(), self.addresses, text)
        # A town centre only because the addresses are not downloaded yet is asked again.
        if found is not None and (found.precision != "municipality" or self.addresses.exists()):
            if len(self._found) >= 200:
                self._found.clear()
            self._found[text] = found
        return found
