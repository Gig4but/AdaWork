import json
from pathlib import Path

import pytest

from adawork.config import Profile
from adawork.db.session import make_engine, make_sessionmaker, migrate
from adawork.geo.places import PlaceResolver
from adawork.geo.scope import build_query
from adawork.scraper.structured import find_job_postings, posting_to_job

SOURCES = Path(__file__).parents[1] / "fixtures" / "sources"


@pytest.fixture
def places(tmp_path, municipalities):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    with make_sessionmaker(engine)() as s:
        municipalities(s)
        yield PlaceResolver(s)
    engine.dispose()


@pytest.mark.parametrize(
    ("text", "key", "precision"),
    [
        ("Praha", "obec:554782", "municipality"),
        ("Praha-Krč", "part:554782:krc", "part"),
        ("Praha 4", "part:554782:praha 4", "part"),
        ("Praha 10 - Strašnice", "part:554782:strasnice", "part"),
        ("Brno - Líšeň", "part:582786:lisen", "part"),
        ("Kolín IV", "part:533165:kolin iv", "part"),
        ("Příbram VI-Březové Hory", None, None),  # not in the test data
        ("Okres Kolín", "okres:9533", "district"),
        ("Praha-východ", "okres:9538", "district"),  # a district, not a part of Praha
        ("Brandýs nad Labem-Stará Boleslav", "obec:538094", "municipality"),
        ("Kraj Vysočina", "kraj:kraj vysocina", "region"),
        ("Středočeský", "kraj:stredocesky kraj", "region"),
    ],
)
def test_resolve_place_names(places, text, key, precision):
    loc = places.resolve(text)
    assert (loc.key, loc.precision) == (key, precision) if key else loc is None


def test_resolve_details(places):
    assert places.resolve("Celá ČR") is None and places.resolve("Velká Británie") is None
    praha = places.resolve("Praha")
    assert praha.kraj == "Hlavní město Praha" and (praha.lat, praha.lon) == (50.08, 14.43)
    # Same name twice: the postal code decides, else the region, else the bigger one.
    assert places.resolve("Lhota", postal_code="763 23").municipality_code == "555657"
    assert places.resolve("Lhota", region="Zlínský kraj").municipality_code == "555657"
    assert places.resolve("Lhota").municipality_code == "549240"
    assert places.resolve("Lhota", district="Zlín").municipality_code == "555657"
    assert places.resolve(None, postal_code="30100").municipality == "Plzeň"
    street = places.resolve("Plzeň", street="Sedláčkova 245/24", postal_code="30100")
    assert street.key == "addr:554791:sedlackova:245/24" and street.precision == "address"
    assert street.address_text == "Sedláčkova 245/24, Plzeň" and street.lat is None
    assert places.resolve("Praha-Krč").address_text == "Praha-Krč"
    assert places.resolve("Praha 4").address_text == "Praha 4"
    region = places.resolve("Kraj Vysočina")
    assert region.lat == pytest.approx(49.40) and region.municipality_code is None


def test_home_and_crawl_scope(places):
    assert places.find_home("Nádražní 5, 280 02 Kolín").name == "Kolín"
    assert places.find_home("Kolín").name == "Kolín"
    assert places.find_home("nowhere") is None
    car = Profile(home_address="Kolín", max_minutes=45, travel_mode="car")
    near = build_query(places, car).kraje
    assert near[0] == "Středočeský kraj" and "Pardubický kraj" in near
    assert "Jihomoravský kraj" not in near
    walk = Profile(home_address="Kolín", max_minutes=30, travel_mode="walk")
    assert build_query(places, walk).kraje == ["Středočeský kraj"]
    assert len(build_query(places, Profile()).kraje) == 14  # home unknown: everywhere
    for m in places.municipalities:
        m.kraj = None
    assert len(build_query(places, car).kraje) == 14


def detail(path: str) -> dict:
    return find_job_postings((SOURCES / path).read_text(encoding="utf-8"))[0]


def test_json_ld_postings(places):
    job = posting_to_job(detail("prace_cz/detail_1.html"), "dpgjza", "https://x", places)
    assert job.title.startswith("Vedoucí zdravotního úseku")
    assert job.employer_name == "Domov Sulická" and job.contract_types == ["full_time"]
    assert job.location.key == "addr:554782:sulicka:" and job.location.kraj == "Hlavní město Praha"
    assert job.expires_at.isoformat() == "2026-11-01T23:59:59"  # malformed in the source
    assert job.description.startswith("Hledáme zkušenou") and "\n• " not in job.description[:50]

    job = posting_to_job(detail("jenprace/detail_2.html"), "2tcexh", None, places)
    assert (job.salary_min, job.salary_max, job.salary_unit) == (35000, None, "month")
    assert job.location.municipality == "Plzeň" and job.location.precision == "address"
    assert job.extra["benefits_text"].startswith("finanční bonusy")


def test_microdata_posting(places):
    posting = detail("profesia/detail_2.html")
    assert posting["@type"] == "JobPosting" and posting["hiringOrganization"] == "ELMONTIA a.s."
    job = posting_to_job(posting, "5370489", None, places)
    assert job.contract_types == ["full_time"] and job.location is None  # abroad
    assert job.place_text.startswith("Velká Británie")
    assert job.posted_at.date().isoformat() == "2026-10-06"


def test_salary_units_from_json_ld(places):
    posting = {
        "@type": "JobPosting",
        "title": "Brigádník",
        "baseSalary": {"currency": "CZK", "value": {"value": 180, "unitText": "HOUR"}},
        "jobLocation": [{"address": {"addressLocality": "Kolín", "addressCountry": "CZ"}}],
    }
    job = posting_to_job(json.loads(json.dumps(posting)), "1", None, places)
    assert (job.salary_min, job.salary_unit) == (180, "hour")
    assert job.location.key == "obec:533165"
    posting["baseSalary"] = {"currency": "CZK", "value": {"minValue": 600000, "unitText": "YEAR"}}
    assert posting_to_job(posting, "1", None, places).salary_min == 50000
    posting["baseSalary"] = {"value": {"minValue": "35.000", "unitText": "MONTH"}}
    assert posting_to_job(posting, "1", None, places).salary_min == 35000


def test_address_with_country_object(places):
    country = {"@type": "Country", "name": "CZ"}
    posting = {
        "@type": "JobPosting",
        "title": "Skladník",
        "jobLocation": {"address": {"addressLocality": "Kolín", "addressCountry": country}},
    }
    assert posting_to_job(posting, "1", None, places).location.key == "obec:533165"


def test_jenprace_card_places(places):
    from adawork.scraper.sources.jenprace import card_location

    assert card_location(["Lhota", "Zlín"], places).municipality_code == "555657"
    assert card_location(["Krč", "Praha"], places).key == "part:554782:krc"
    assert card_location(["Chodov", "Praha"], places).key == "part:554782:chodov"
    assert card_location(["Nowhere", "Kolín"], places).municipality_code == "533165"
    assert card_location(["Plzeň"], places).municipality == "Plzeň"
