from datetime import UTC, datetime

import pytest

from adawork.db.models import Job, JobSource
from adawork.db.session import backup, make_engine, make_sessionmaker, migrate
from adawork.search.service import fts_query, search_jobs


@pytest.fixture
def session(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    migrate(engine)
    with make_sessionmaker(engine)() as s:
        yield s
    engine.dispose()


def test_backup_copies_the_database(tmp_path, session):
    add_job(session, "Skladník")
    copy = backup(session.get_bind())
    assert copy == tmp_path / "test.db.bak"
    with make_sessionmaker(make_engine(copy))() as s:
        assert [j.title for j in s.query(Job)] == ["Skladník"]


def test_no_backup_when_the_schema_is_current(tmp_path):
    engine = make_engine(tmp_path / "new.db")
    migrate(engine)  # a new database
    migrate(engine)  # already current
    engine.dispose()
    assert not (tmp_path / "new.db.bak").exists()


def add_job(session, title, description="", employer="", place="", status="active", ext=None):
    now = datetime.now(UTC)
    job = Job(
        title=title,
        description=description,
        employer_name=employer,
        place_text=place,
        status=status,
        content_hash="x",
        first_seen_at=now,
        last_seen_at=now,
        updated_at=now,
        posted_at=now,
    )
    job.sources.append(
        JobSource(source_id="test", external_id=ext or title, first_seen_at=now, last_seen_at=now)
    )
    session.add(job)
    session.commit()
    return job


def test_fts_query_is_safe():
    assert fts_query("Skladník  Brno") == '"skladník"* AND "brno"*'
    assert fts_query('NOT "x" OR (') == '"not"* AND "x"* AND "or"*'
    assert fts_query("  -- ") is None
    assert fts_query("skladník, , řidič C") == '("skladník"*) OR ("řidič"* AND "c"*)'


def test_search_ignores_diacritics_and_matches_prefix(session):
    add_job(session, "Účetní", place="Brno")
    add_job(session, "Skladník/skladnice", place="Kolín")
    assert [j.title for j in search_jobs(session, "ucetni").jobs] == ["Účetní"]
    assert [j.title for j in search_jobs(session, "sklad kolin").jobs] == ["Skladník/skladnice"]
    assert search_jobs(session, "sklad brno").total == 0


def test_title_ranks_above_description(session):
    add_job(session, "Prodavač", description="Hledáme řidiče na rozvoz", ext="a")
    add_job(session, "Řidič sk. C", description="Rozvoz zboží", ext="b")
    assert search_jobs(session, "řidič").jobs[0].title == "Řidič sk. C"
    add_job(session, "Řídící pracovník výroby", description="Vedení týmu.", ext="manager")
    titles = {j.title for j in search_jobs(session, "řidič").jobs}
    assert "Řídící pracovník výroby" not in titles and "Prodavač" in titles  # "řidiče"
    assert "Řídící pracovník výroby" in {j.title for j in search_jobs(session, "ridic").jobs}


def test_phrases_separated_by_commas_are_alternatives(session):
    add_job(session, "Skladník", ext="a")
    add_job(session, "Řidič sk. C", ext="b")
    add_job(session, "Řídící pracovník", ext="c")
    add_job(session, "Kuchař", ext="d")
    titles = {j.title for j in search_jobs(session, "sklad, řidič").jobs}
    assert titles == {"Skladník", "Řidič sk. C"}  # diacritics count in their own phrase


def test_fts_follows_updates_and_deletes(session):
    job = add_job(session, "Kuchař")
    job.title = "Pizzař"
    session.commit()
    assert search_jobs(session, "kuchar").total == 0
    assert search_jobs(session, "pizzar").total == 1
    session.delete(job)
    session.commit()
    assert search_jobs(session, "pizzar").total == 0


def test_empty_query_lists_active_jobs(session):
    add_job(session, "A")
    add_job(session, "B", status="expired")
    result = search_jobs(session, "")
    assert result.total == 1 and result.jobs[0].title == "A"


def test_equal_ranks_page_without_repeats(session):
    for i in range(12):
        add_job(session, "Skladník", ext=f"s{i}")
    pages = [search_jobs(session, "skladnik", limit=5, offset=o).jobs for o in (0, 5, 10)]
    ids = [j.id for page in pages for j in page]
    assert ids == sorted(ids, reverse=True) and len(set(ids)) == 12
