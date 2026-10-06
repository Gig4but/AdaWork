"""Portal place text -> a location: "Praha-Krč", "Brno-střed", "Plzeň" + "Sedláčkova 245/24",
"Kraj Vysočina". Uses the municipalities table (built from RÚIAN), so it works offline.
"""

import math
import re
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from adawork.core import ParsedLocation
from adawork.db.models import Municipality
from adawork.normalize.text import clean_spaces, fold

# Straight-line km that can be covered in a minute at best (the commute filter and the regions
# to read). M8 check from Kolín: car 1.3 km/min misses 10 of 13 013 places within two hours,
# transit 1.2 km/min none.
REACH_KM_PER_MINUTE = {"car": 1.3, "transit": 1.2, "walk": 0.09, "bike": 0.3}

KRAJE = (
    "Hlavní město Praha",
    "Středočeský kraj",
    "Jihočeský kraj",
    "Plzeňský kraj",
    "Karlovarský kraj",
    "Ústecký kraj",
    "Liberecký kraj",
    "Královéhradecký kraj",
    "Pardubický kraj",
    "Kraj Vysočina",
    "Jihomoravský kraj",
    "Olomoucký kraj",
    "Moravskoslezský kraj",
    "Zlínský kraj",
)
PRAHA_CODE = "554782"

# Words that mean "no particular place" in Czechia.
_NOWHERE = {"ceska republika", "cela cr", "cr", "czech republic", "cesko", "home office",
            "prace z domova", "z domova", "remote", "zahranici", "dle dohody"}  # fmt: skip
_STREET = re.compile(r"^(?P<street>.*?\D)\s*(?P<number>\d+[a-zA-Z]?(?:/\d+[a-zA-Z]?)?)?$")


def _kraj_aliases() -> dict[str, str]:
    aliases = {}
    for name in KRAJE:
        folded = fold(name)
        aliases[folded] = name
        core = folded.replace(" kraj", "").replace("kraj ", "")
        aliases[core] = name  # "stredocesky", "vysocina"
        aliases[f"{core} kraj"] = name
    return aliases  # "Praha" itself resolves to the municipality before regions are tried


@dataclass
class _Muni:
    code: str
    name: str
    okres_code: str | None
    okres: str | None
    kraj: str | None
    lat: float
    lon: float
    address_count: int
    postal_codes: frozenset[str]


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


class PlaceResolver:
    def __init__(self, session: Session) -> None:
        self._by_name: dict[str, list[_Muni]] = defaultdict(list)
        self._by_psc: dict[str, list[_Muni]] = defaultdict(list)
        self.municipalities: list[_Muni] = []
        for m in session.scalars(select(Municipality)):
            muni = _Muni(
                m.code, m.name, m.okres_code, m.okres, m.kraj, m.lat, m.lon, m.address_count,
                frozenset(m.postal_codes.split()),
            )  # fmt: skip
            self.municipalities.append(muni)
            self._by_name[m.name_normalized].append(muni)
            for psc in muni.postal_codes:
                self._by_psc[psc].append(muni)
        self._kraje = _kraj_aliases()
        self._okresy = {fold(m.okres) for m in self.municipalities if m.okres}
        self._kraj_centres: dict[str, tuple[float, float]] = {}

    def __bool__(self) -> bool:
        return bool(self.municipalities)

    def resolve(
        self,
        text: str | None,
        street: str | None = None,
        postal_code: str | None = None,
        region: str | None = None,
        district: str | None = None,
    ) -> ParsedLocation | None:
        """One place: `text` is a municipality ("Praha-Krč", "Brno - Líšeň") or a region.

        `postal_code`, `region` and `district` pick among municipalities of the same name.
        """
        text = clean_spaces(text or "")
        psc = re.sub(r"\D", "", postal_code or "") or None
        if not text and not psc:
            return None
        folded = fold(text)
        if folded in _NOWHERE:
            return None

        if folded in self._okresy and folded not in self._by_name:
            return self._district(text)  # "Praha-východ", "Brno-venkov": not "Praha" + part
        found = self._municipality(text, psc, region, district)
        if found is None and psc and len(psc) == 5:
            candidates = self._by_psc.get(psc)
            if candidates:
                found = (max(candidates, key=lambda m: m.address_count), None)
        if found is None:
            if folded.startswith("okres "):
                return self._district(text[6:].strip())
            kraj = self._kraje.get(folded)
            return self._region(kraj) if kraj else None

        muni, part = found
        base = {
            "municipality": muni.name,
            "municipality_code": muni.code,
            "okres": muni.okres,
            "kraj": muni.kraj,
        }
        street = clean_spaces(street or "")
        m = _STREET.match(street) if street else None
        if m and len(m["street"].strip(" ,.")) >= 3:
            name, number = m["street"].strip(" ,."), m["number"] or ""
            return ParsedLocation(
                key=f"addr:{muni.code}:{fold(name)}:{number}",
                precision="address",
                address_text=f"{name} {number}".strip() + f", {muni.name}",
                **base,
            )
        if part:
            return ParsedLocation(
                key=f"part:{muni.code}:{fold(part)}",
                precision="part",
                address_text=part if part.startswith(muni.name) else f"{muni.name}-{part}",
                **base,
            )
        return ParsedLocation(
            key=f"obec:{muni.code}", precision="municipality", lat=muni.lat, lon=muni.lon, **base
        )

    def _municipality(
        self, text: str, psc: str | None, region: str | None, district: str | None = None
    ) -> tuple[_Muni, str | None] | None:
        """(municipality, part or None). Tries "Brandýs nad Labem-Stará Boleslav" whole first."""
        if not text:
            return None
        if muni := self._pick(fold(text), psc, region, district):
            return muni, None
        # "Praha 4", "Plzeň 3": a numbered city district.
        m = re.match(r"^(.*?)\s+(\d{1,2})$", text)
        if m and (muni := self._pick(fold(m[1]), psc, region)):
            return muni, f"{muni.name} {m[2]}"
        # "Kolín IV", "Příbram VI-Březové Hory": RÚIAN names of town parts.
        m = re.match(r"^(.*?)\s+[IVXL]+\b", text)
        if m and (muni := self._pick(fold(m[1]), psc, region)):
            return muni, text
        # "Praha-Krč", "Ostrava - Poruba", "Praha 10 - Strašnice": the part after a dash.
        pieces = re.split(r"\s*-\s*", text)
        for i in range(len(pieces) - 1, 0, -1):
            head, tail = "-".join(pieces[:i]), " ".join(pieces[i:])
            head = re.sub(r"\s+\d{1,2}$", "", head)
            if muni := self._pick(fold(head), psc, region):
                return muni, tail
        return None

    def _pick(
        self, folded: str, psc: str | None, region: str | None, district: str | None = None
    ) -> _Muni | None:
        candidates = self._by_name.get(folded)
        if not candidates:
            return None
        kraj = self._kraje.get(fold(region)) if region else None
        hints = (
            lambda c: psc is not None and psc in c.postal_codes,
            lambda c: district is not None and fold(c.okres or "") == fold(district),
            lambda c: kraj is not None and c.kraj == kraj,
        )
        for matches in hints:  # the most precise hint that narrows the choice wins
            if same := [c for c in candidates if matches(c)]:
                candidates = same
                break
        return max(candidates, key=lambda c: c.address_count)

    def _district(self, name: str) -> ParsedLocation | None:
        members = [m for m in self.municipalities if fold(m.okres or "") == fold(name)]
        if not members or not members[0].okres_code:
            return None
        first = members[0]
        return ParsedLocation(
            key=f"okres:{first.okres_code}", precision="district", okres=first.okres,
            kraj=first.kraj,
        )  # fmt: skip

    def _region(self, kraj: str) -> ParsedLocation:
        if kraj not in self._kraj_centres:
            members = [m for m in self.municipalities if m.kraj == kraj]
            weight = sum(m.address_count for m in members) or 1
            self._kraj_centres[kraj] = (
                sum(m.lat * m.address_count for m in members) / weight,
                sum(m.lon * m.address_count for m in members) / weight,
            )
        lat, lon = self._kraj_centres[kraj]
        return ParsedLocation(
            key=f"kraj:{fold(kraj)}", precision="region", kraj=kraj, lat=lat, lon=lon
        )

    def find_home(self, address: str) -> _Muni | None:
        """The municipality in a free-text home address ("Nádražní 5, 280 02 Kolín")."""
        psc = re.search(r"\b(\d{3})\s?(\d{2})\b", address)
        pieces = [re.sub(r"\d{3}\s?\d{2}|\d+", "", p) for p in address.split(",")]
        for piece in reversed(pieces):
            found = self._municipality(clean_spaces(piece), psc and psc[1] + psc[2], None)
            if found:
                return found[0]
        return None

    def kraje_within(self, lat: float, lon: float, km: float) -> list[str]:
        """Regions with a town (>= 200 addresses) within `km` of the point, nearest first."""
        nearest: dict[str, float] = {}
        for m in self.municipalities:
            if m.kraj and m.address_count >= 200:
                d = distance_km(lat, lon, m.lat, m.lon)
                if d <= km and d < nearest.get(m.kraj, math.inf):
                    nearest[m.kraj] = d
        return sorted(nearest, key=nearest.__getitem__)


class SharedPlaces:
    """The app's one PlaceResolver, read once the municipalities are in (the first search
    fills them): `places()`."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions
        self._places: PlaceResolver | None = None

    def __call__(self) -> PlaceResolver:
        if not self._places:  # none yet, or read before the first search filled the table
            with self.sessions() as s:
                self._places = PlaceResolver(s)
        return self._places
