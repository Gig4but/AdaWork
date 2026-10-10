import json
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import select

from adawork.db.models import Job, Location
from adawork.db.session import make_engine, make_sessionmaker, migrate
from adawork.normalize.vocab import EDUCATION_LEVEL
from adawork.scraper.sources import mpsv
from adawork.scraper.store import JobStore
from adawork.search.service import FACETS, Filters, search_jobs

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "mpsv"


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    engine = make_engine(tmp_path_factory.mktemp("db") / "t.db")
    migrate(engine)
    records = json.loads((FIXTURES / "volna-mista.first30.json").read_text(encoding="utf-8"))
    codebooks = mpsv.Codebooks.load_dir(FIXTURES / "codebooks")
    with make_sessionmaker(engine)() as s:
        JobStore(s, "mpsv").add_all(mpsv.parse_record(r, codebooks) for r in records["polozky"])
        yield s
    engine.dispose()


def ids(result):
    return {j.id for j in result.jobs}


def all_jobs(session, f=None):
    return search_jobs(session, f or Filters(), limit=1000).jobs


def test_list_filters_match_any_selected_value(session):
    jobs = all_jobs(session)
    for name in ("categories", "contract_types", "shifts", "benefits", "suitable_for"):
        values = sorted({v for j in jobs for v in getattr(j, name)})
        assert values, name
        chosen = values[:2]
        expected = {j.id for j in jobs if set(getattr(j, name)) & set(chosen)}
        result = search_jobs(session, Filters(**{name: chosen}), limit=1000)
        assert ids(result) == expected and result.total == len(expected), name


def test_salary_education_region_and_sort(session):
    jobs = all_jobs(session)
    rich = search_jobs(session, Filters(salary_min=35000), limit=1000).jobs
    assert rich and all((j.salary_month_max or j.salary_month_min) >= 35000 for j in rich)

    basic = search_jobs(session, Filters(education="basic"), limit=1000).jobs
    assert basic and all(j.education_level in (None, 1) for j in basic)
    assert len(basic) < len(jobs)

    kraj = next(j.location.kraj for j in jobs if j.location and j.location.kraj)
    in_kraj = search_jobs(session, Filters(kraj=[kraj]), limit=1000).jobs
    assert in_kraj and all(j.location.kraj == kraj for j in in_kraj)

    by_salary = search_jobs(session, Filters(sort="salary"), limit=5).jobs
    pays = [j.salary_month_max or j.salary_month_min or 0 for j in by_salary]
    assert pays == sorted(pays, reverse=True)


def brute_force_facets(session, f: Filters) -> dict[str, dict[str, int]]:
    """Reference counts: one search per facet value, with that facet's own filter replaced."""
    jobs = all_jobs(session)
    out = {}
    for name in FACETS:
        if name == "kraj":
            values = {j.location.kraj for j in jobs if j.location and j.location.kraj}
        elif name == "education":
            values = set(EDUCATION_LEVEL)
        elif name == "sources":
            values = {src.source_id for j in jobs for src in j.sources}
        else:
            values = {v for j in jobs for v in getattr(j, name)}
        counts = {}
        for v in values:
            if name == "education":
                g = replace(f, education=None)
                n = sum(1 for j in all_jobs(session, g) if j.education == v)
            elif name == "sources":
                g = replace(f, sources_off=[])
                n = sum(1 for j in all_jobs(session, g) if v in {s.source_id for s in j.sources})
            else:
                n = search_jobs(session, replace(f, **{name: [v]}), limit=0).total
            if n:
                counts[v] = n
        out[name] = counts
    return out


@pytest.mark.parametrize(
    "f",
    [
        Filters(),
        Filters(categories=["13"]),
        Filters(categories=["8", "14"], contract_types=["full_time"], salary_min=25000),
        Filters(education="vocational", shifts=["jednoSm"]),
        Filters(categories=["13"], sources_off=["mpsv"]),
    ],
)
def test_facets_match_brute_force(session, f):
    result = search_jobs(session, f, facets=True)
    assert {k: dict(v) for k, v in result.facets.items()} == brute_force_facets(session, f)


def test_full_text_combines_with_filters(session):
    plain = search_jobs(session, "pokojska", limit=1000)
    assert plain.total >= 1
    loc = plain.jobs[0].location
    assert isinstance(loc, Location)
    narrowed = search_jobs(session, Filters(q="pokojska", kraj=[loc.kraj]), limit=1000)
    assert 1 <= narrowed.total <= plain.total
    assert search_jobs(session, Filters(q="pokojska", kraj=["neexistuje"])).total == 0


def test_facet_cache_notices_jobs_swapping_status(tmp_path):
    engine = make_engine(tmp_path / "swap.db")
    migrate(engine)
    records = json.loads((FIXTURES / "volna-mista.first30.json").read_text(encoding="utf-8"))
    codebooks = mpsv.Codebooks.load_dir(FIXTURES / "codebooks")
    with make_sessionmaker(engine)() as s:
        JobStore(s, "mpsv").add_all(mpsv.parse_record(r, codebooks) for r in records["polozky"])
        jobs = s.scalars(select(Job).order_by(Job.id)).all()
        a = jobs[0]
        b = next(j for j in jobs[1:-1] if set(j.categories) != set(a.categories))
        b.status = "expired"
        s.commit()
        search_jobs(s, Filters(), facets=True)  # fills the cache

        a.status, b.status = "expired", "active"  # one expires, one returns unchanged
        s.commit()
        result = search_jobs(s, Filters(), facets=True)
        assert {k: dict(v) for k, v in result.facets.items()} == brute_force_facets(s, Filters())
    engine.dispose()


def test_facet_cache_notices_a_change_within_one_clock_tick(tmp_path):
    engine = make_engine(tmp_path / "tick.db")
    migrate(engine)
    records = json.loads((FIXTURES / "volna-mista.first30.json").read_text(encoding="utf-8"))
    codebooks = mpsv.Codebooks.load_dir(FIXTURES / "codebooks")
    sessions = make_sessionmaker(engine)
    with sessions() as s:
        JobStore(s, "mpsv").add_all(mpsv.parse_record(r, codebooks) for r in records["polozky"])
    with sessions() as s:
        search_jobs(s, Filters(), facets=True)  # fills the cache
    with sessions() as s:
        job = s.scalars(select(Job).order_by(Job.id)).first()
        job.contract_types = ["new-type"]  # updated_at stays: the same clock tick
        s.commit()
    with sessions() as s:
        result = search_jobs(s, Filters(), facets=True)
        assert dict(result.facets["contract_types"])["new-type"] == 1
    engine.dispose()


def test_sources_switched_off_hide_their_jobs(session):
    every = search_jobs(session, Filters()).total
    assert every and search_jobs(session, Filters(sources_off=["prace_cz"])).total == every
    assert search_jobs(session, Filters(sources_off=["mpsv"])).total == 0
