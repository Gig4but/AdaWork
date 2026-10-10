import zipfile
from datetime import date

import httpx
import pytest
from sqlalchemy import select

from adawork.db.models import Location, Municipality
from adawork.db.session import make_engine, make_sessionmaker, migrate
from adawork.geo.geocode import geocode_locations, normalize_name, rebuild_municipalities
from adawork.geo.ruian import FILE_NAME, RuianAddresses, download, export_dates, sjtsk_to_wgs84

HEADER = (
    "Kód ADM;Kód obce;Název obce;Kód MOMC;Název MOMC;Kód obvodu Prahy;Název obvodu Prahy;"
    "Kód části obce;Název části obce;Kód ulice;Název ulice;Typ SO;Číslo domovní;"
    "Číslo orientační;Znak čísla orientačního;PSČ;Souřadnice Y;Souřadnice X;Platí Od"
)


def row(adm, obec, name, y, x):
    return f"{adm};{obec};{name};;;;;1;{name};;;č.p.;1;;;10000;{y};{x};2013-12-03T00:00:00"


@pytest.fixture
def ruian(tmp_path):
    path = tmp_path / "adr.zip"
    files = {
        "500020": [
            row("8053324", "500020", "Petrov nad Desnou", "559166.46", "1075134.09"),
            row("8053332", "500020", "Petrov nad Desnou", "559165.50", "1075119.88"),
            row("8053341", "500020", "Petrov nad Desnou", "558999.78", "1075011.93"),
            row("9999999", "500020", "Petrov nad Desnou", "", ""),  # no coordinates yet
        ],
        "537683": [row("18329012", "537683", "Poděbrady", "697700.00", "1044600.00")],
    }
    with zipfile.ZipFile(path, "w") as z:
        for code, rows in files.items():
            text = "\n".join([HEADER, *rows]) + "\n"
            z.writestr(f"CSV/20260930_OB_{code}_ADR.csv", text.encode("cp1250"))
    r = RuianAddresses(path)
    yield r
    r.close()


@pytest.fixture
def session(tmp_path):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    with make_sessionmaker(engine)() as s:
        yield s
    engine.dispose()


def test_coordinates_and_dates():
    lat, lon = sjtsk_to_wgs84(559166.46, 1075134.09)  # Petrov nad Desnou č.p. 1
    assert lat == pytest.approx(49.9975, abs=1e-3) and lon == pytest.approx(17.0204, abs=1e-3)
    assert export_dates(date(2026, 10, 6)) == [date(2026, 9, 30), date(2026, 8, 31)]
    assert export_dates(date(2026, 3, 1)) == [date(2026, 2, 28), date(2026, 1, 31)]
    assert normalize_name("Poděbrady") == "podebrady"


def test_download_falls_back_to_cached_export_when_offline(tmp_path):
    def offline(request):
        raise httpx.ConnectError("offline", request=request)

    client = httpx.Client(transport=httpx.MockTransport(offline))
    with pytest.raises(RuntimeError):
        download(client, tmp_path)
    (tmp_path / FILE_NAME).write_bytes(b"cached")
    assert download(client, tmp_path) == tmp_path / FILE_NAME


def test_lookup(ruian):
    assert sorted(ruian.municipality_codes()) == ["500020", "537683"]
    points, _, centre = ruian.lookup("500020", {"8053332", "9999999"})
    assert set(points) == {"8053332"}  # the point without coordinates is skipped
    assert centre.name == "Petrov nad Desnou" and centre.address_count == 3
    assert ruian.lookup("123456", set()) == ({}, {}, None)


def test_rebuild_and_geocode(session, ruian):
    okres_of = {"500020": ("3809", "Šumperk", "Olomoucký kraj")}
    assert rebuild_municipalities(session, ruian, okres_of) == 2
    petrov = session.get(Municipality, "500020")
    assert petrov.okres == "Šumperk" and petrov.name_normalized == "petrov nad desnou"

    session.add_all(
        [
            Location(key="ruian:8053324", precision="address", municipality_code="500020"),
            Location(key="ruian:1", precision="address", municipality_code="500020"),
            Location(key="obec:537683", precision="municipality", municipality_code="537683"),
            Location(key="okres:3809", precision="district"),
            Location(key="okres:9999", precision="district"),  # no municipalities known
        ]
    )
    session.commit()
    assert geocode_locations(session, ruian) == 4

    locs = {loc.key: loc for loc in session.scalars(select(Location))}
    exact = locs["ruian:8053324"]
    assert (exact.lat, exact.lon) == pytest.approx(sjtsk_to_wgs84(559166.46, 1075134.09))
    missing = locs["ruian:1"]
    assert missing.precision == "municipality"  # fell back to the municipality centre
    assert (missing.lat, missing.lon) == pytest.approx((petrov.lat, petrov.lon))
    assert locs["obec:537683"].lat == pytest.approx(50.15, abs=0.05)
    assert (locs["okres:3809"].lat, locs["okres:3809"].lon) == pytest.approx(
        (petrov.lat, petrov.lon)
    )
    assert locs["okres:9999"].lat is None
    assert geocode_locations(session, ruian) == 0  # nothing left to do
