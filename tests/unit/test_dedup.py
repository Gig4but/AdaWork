from datetime import timedelta

import pytest
from sqlalchemy import select

from adawork.core import ParsedJob, ParsedLocation
from adawork.db.models import Job, JobSource, table
from adawork.db.session import make_engine, make_sessionmaker, migrate
from adawork.scraper.dedup import DedupIndex, employer_key, title_words
from adawork.scraper.store import _GAP_FIELDS, _JOB_FIELDS, _SALARY_FIELDS, JobStore

LONG = (
    "Hledáme skladníka do našeho moderního logistického centra v Kolíně. Náplní práce je "
    "příjem a výdej zboží, kontrola dodávek, práce se skenerem a obsluha VZV. Nabízíme "
    "stabilní zázemí, třináctou mzdu, stravenky a příspěvek na dopravu. Nástup možný ihned. "
    "Pracuje se ve dvousměnném provozu od pondělí do pátku, víkendy volné. "
)
OTHER = (
    "Do kanceláře v Kolíně přijmeme skladníka pro evidenci materiálu v ERP, inventury a "
    "komunikaci s dodavateli. Požadujeme maturitu, pečlivost a znalost Excelu. Práce na "
    "jednu směnu v kanceláři, home office jeden den v týdnu, pět týdnů dovolené. "
    "Výhodou je zkušenost se systémem SAP a řidičský průkaz skupiny B. "
)


def job(ext, title="Skladník", employer="ACME Logistics s.r.o.", place="533165", **kw):
    loc = ParsedLocation(key=f"obec:{place}", precision="municipality", municipality_code=place)
    return ParsedJob(external_id=ext, title=title, employer_name=employer, location=loc, **kw)


def later(session, store_factory):
    """Windows clocks tick coarsely: let earlier sightings lie clearly before the next run."""
    for source in session.scalars(select(JobSource)):
        source.last_seen_at -= timedelta(minutes=1)
    session.commit()
    return store_factory()


@pytest.fixture
def session(tmp_path):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    with make_sessionmaker(engine)() as s:
        yield s
    engine.dispose()


def test_every_parsed_field_has_a_column():
    columns = set(table(Job).columns.keys())
    assert set(_JOB_FIELDS) <= columns
    assert set(_GAP_FIELDS) | set(_SALARY_FIELDS) <= set(_JOB_FIELDS)


def test_normalized_keys():
    assert employer_key("ACME Logistics, s.r.o.") == employer_key("Acme logistics s. r. o.")
    assert employer_key("Letiště Praha, a. s.") == "letiste praha"
    assert title_words("Skladník/skladnice (m/ž) - Praha") == {"skladnik", "skladnice"}
    assert title_words("Operátor/ka výroby") == {"operator", "vyroby"}


def test_index_finds_duplicates_from_other_sources_only():
    index = DedupIndex()
    first = DedupIndex.entry_for(job("1", description=LONG), "mpsv")
    first.job_id = 1
    index.add(first)
    assert index.find(job("2", "Skladník / skladnice (m/ž)"), "jenprace") is first
    assert index.find(job("2", employer="ACME Logistics"), "jenprace") is first
    assert index.find(job("2"), "mpsv") is None  # same source: a separate ad
    assert index.find(job("2", place="554782"), "jenprace") is None  # another town
    assert index.find(job("2", employer="Beta s.r.o."), "jenprace") is None
    assert index.find(job("2", "Účetní"), "jenprace") is None


def test_district_level_jobs_match_jobs_in_their_districts():
    index = DedupIndex()
    district = ParsedLocation(key="okres:3602,3605", precision="district", okres="Náchod, Trutnov")
    mpsv = ParsedJob("1", "Strojník pásového / kolového rypadla (ž/m)", employer_name="Votroubek",
                     location=district)  # fmt: skip
    entry = DedupIndex.entry_for(mpsv, "mpsv")
    entry.job_id = 1
    index.add(entry)
    in_trutnov = ParsedLocation(key="obec:579025", precision="municipality",
                                municipality_code="579025", okres="Trutnov")  # fmt: skip
    portal = ParsedJob("2", "Strojník pásového/kolového rypadla", employer_name="Votroubek s.r.o.",
                       location=in_trutnov)  # fmt: skip
    assert index.find(portal, "jenprace") is entry
    portal.title = "Strojník"  # a partial title is not enough at district level
    assert index.find(portal, "jenprace") is None
    portal.title, portal.location.okres = "Strojník kolového rypadla", "Jičín"
    assert index.find(portal, "jenprace") is None


def test_partly_matching_titles_need_matching_descriptions():
    index = DedupIndex(load_description=lambda job_id: LONG)
    entry = DedupIndex.entry_for(job("1", "Skladník"), "mpsv")
    entry.job_id = 1
    index.add(entry)
    longer = "Skladník – ranní směna, logistické centrum"
    assert index.find(job("2", longer, description=LONG + " Volejte."), "prace_cz") is entry
    assert index.find(job("2", longer, description=OTHER), "prace_cz") is None
    assert index.find(job("2", longer), "prace_cz") is entry  # too short to compare


def test_store_merges_fills_gaps_and_expires_by_source(session):
    mpsv = JobStore(session, "mpsv")
    mpsv.add_all([job("m1", description="Krátký popis.", employer_ico="12345678")])
    portal = JobStore(session, "prace_cz", dedup=mpsv.dedup)
    portal.add_all(
        [
            job(
                "p1",
                "Skladník/skladnice",
                description=LONG,
                salary_min=32000,
                salary_unit="month",
            ),
            job("p2", "Účetní"),
        ]
    )
    assert (portal.stats.new, portal.stats.merged) == (1, 1)

    merged = session.scalar(select(Job).where(Job.title == "Skladník"))
    assert sorted(s.source_id for s in merged.sources) == ["mpsv", "prace_cz"]
    assert merged.salary_min == 32000 and merged.salary_month_min == 32000  # filled from portal
    assert merged.description == LONG  # the primary's was too short
    assert merged.employer_ico == "12345678"

    # The primary source updates without wiping what the portal filled in.
    mpsv = JobStore(session, "mpsv")
    mpsv.add_all([job("m1", "Skladník", description="Nový krátký popis.")])
    session.refresh(merged)
    assert merged.salary_min == 32000 and merged.description == "Nový krátký popis."

    # MPSV no longer offers it, the portal still does: the job stays, the portal takes over.
    mpsv = later(session, lambda: JobStore(session, "mpsv"))
    mpsv.commit()
    assert mpsv.expire_missing() == 0
    session.refresh(merged)
    primary = {s.source_id: (s.is_primary, s.status) for s in merged.sources}
    assert merged.status == "active"
    assert primary == {"mpsv": (False, "expired"), "prace_cz": (True, "active")}

    portal = later(session, lambda: JobStore(session, "prace_cz"))
    portal.touch("p2")
    assert portal.expire_missing() == 1  # p1 is gone from every source now
    session.refresh(merged)
    assert merged.status == "expired"
    assert session.scalar(select(Job.status).where(Job.title == "Účetní")) == "active"
    assert session.scalar(select(JobSource.status).where(JobSource.external_id == "p2")) == (
        "active"
    )
