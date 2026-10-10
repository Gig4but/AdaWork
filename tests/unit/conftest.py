"""Shared test data."""

import pytest

from adawork.db.models import Municipality
from adawork.geo.geocode import normalize_name

# code, name, okres, kraj, lat, lon, addresses, postal codes
MUNICIPALITIES = [
    ("554782", "Praha", "Hlavní město Praha", "Hlavní město Praha", 50.08, 14.43, 300000,
     "10000 11000 14000 14200 15000"),
    ("554791", "Plzeň", "Plzeň-město", "Plzeňský kraj", 49.74, 13.37, 40000, "30100 32600"),
    ("586846", "Jihlava", "Jihlava", "Kraj Vysočina", 49.40, 15.59, 15000, "58601"),
    ("582786", "Brno", "Brno-město", "Jihomoravský kraj", 49.19, 16.61, 80000, "60200 62800"),
    ("533165", "Kolín", "Kolín", "Středočeský kraj", 50.03, 15.20, 9000, "28002"),
    ("538094", "Brandýs nad Labem-Stará Boleslav", "Praha-východ", "Středočeský kraj",
     50.19, 14.66, 7000, "25001"),
    ("549240", "Lhota", "Kladno", "Středočeský kraj", 50.12, 14.02, 300, "27301"),
    ("555657", "Lhota", "Zlín", "Zlínský kraj", 49.09, 17.69, 250, "76323"),
    ("560251", "Chodov", "Sokolov", "Karlovarský kraj", 50.24, 12.75, 5000, "35735"),
    ("574121", "Pardubice", "Pardubice", "Pardubický kraj", 50.04, 15.78, 20000, "53002"),
]  # fmt: skip


def add_municipalities(session) -> None:
    for code, name, okres, kraj, lat, lon, count, pscs in MUNICIPALITIES:
        session.add(
            Municipality(
                code=code, name=name, name_normalized=normalize_name(name),
                okres_code=f"9{code[:3]}",
                okres=okres, kraj=kraj, lat=lat, lon=lon, address_count=count,
                postal_codes=pscs,
            )
        )  # fmt: skip
    session.commit()


@pytest.fixture
def municipalities():
    """Adds a few real municipalities (with made-up centres) to a session."""
    return add_municipalities
