"""Gives locations coordinates: exact RÚIAN address points, else municipality centres."""

import unicodedata
from collections import defaultdict
from collections.abc import Callable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from adawork.db.models import Location, Municipality
from adawork.geo.ruian import RuianAddresses


def normalize_name(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip()


def rebuild_municipalities(
    session: Session,
    ruian: RuianAddresses,
    okres_of: dict[str, tuple[str, str, str | None]] | None = None,
    progress: Callable[[int], None] | None = None,
) -> int:
    """Recompute every municipality centre. `okres_of`: obec code -> (okres code, okres, kraj)."""
    okres_of = okres_of or {}
    session.execute(delete(Municipality))
    count = 0
    for code in ruian.municipality_codes():
        _, m = ruian.lookup(code, set())
        if m is None:
            continue
        okres_code, okres, kraj = okres_of.get(code, (None, None, None))
        session.add(
            Municipality(
                code=m.code,
                name=m.name,
                name_normalized=normalize_name(m.name),
                okres_code=okres_code,
                okres=okres,
                kraj=kraj,
                lat=m.lat,
                lon=m.lon,
                address_count=m.address_count,
            )
        )
        count += 1
        if progress and count % 250 == 0:
            progress(count)
    session.commit()
    return count


def geocode_locations(session: Session, ruian: RuianAddresses) -> int:
    """Fill in coordinates for locations that have none. Returns how many were updated."""
    pending = session.scalars(select(Location).where(Location.lat.is_(None))).all()
    by_obec: dict[str, list[Location]] = defaultdict(list)
    districts: list[Location] = []
    for loc in pending:
        if loc.municipality_code:
            by_obec[loc.municipality_code].append(loc)
        elif loc.key.startswith("okres:"):
            districts.append(loc)

    updated = 0
    for obec_code, locations in by_obec.items():
        wanted = {loc.key.split(":", 1)[1] for loc in locations if loc.key.startswith("ruian:")}
        found, centre = ruian.lookup(obec_code, wanted)
        for loc in locations:
            adm = loc.key.split(":", 1)[1] if loc.key.startswith("ruian:") else None
            if adm in found:
                loc.lat, loc.lon = found[adm]
            elif centre is not None:
                loc.lat, loc.lon = centre.lat, centre.lon
                if loc.precision == "address":
                    loc.precision = "municipality"  # address point unknown to RÚIAN
            else:
                continue
            updated += 1

    for loc in districts:
        codes = loc.key.split(":", 1)[1].split(",")
        rows = session.execute(
            select(Municipality.lat, Municipality.lon, Municipality.address_count).where(
                Municipality.okres_code.in_(codes)
            )
        ).all()
        weight = sum(r.address_count for r in rows)
        if weight:
            loc.lat = sum(r.lat * r.address_count for r in rows) / weight
            loc.lon = sum(r.lon * r.address_count for r in rows) / weight
            updated += 1

    session.commit()
    return updated
