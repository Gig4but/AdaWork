import gzip
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from adawork.db.models import Job, Location, Run
from adawork.db.session import make_engine, make_sessionmaker, migrate
from adawork.scraper.runner import ScrapeRunner
from adawork.scraper.sources import mpsv
from adawork.search.service import search_jobs

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "mpsv"
RECORDS = json.loads((FIXTURES / "volna-mista.first30.json").read_text(encoding="utf-8"))["polozky"]


@pytest.fixture(scope="module")
def codebooks():
    return mpsv.Codebooks.load_dir(FIXTURES / "codebooks")


@pytest.fixture
def sessions(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    migrate(engine)
    yield make_sessionmaker(engine)
    engine.dispose()


def by_id(portal_id):
    return next(r for r in RECORDS if r["portalId"] == portal_id)


def test_parse_municipality_record(codebooks):
    job = mpsv.parse_record(by_id(67299562), codebooks)
    assert job.title == "Pokojská"
    assert job.employer_name == "Evelimax, s.r.o." and job.employer_ico == "10946128"
    assert job.salary_min == 22500 and job.salary_max is None and job.salary_unit == "month"
    assert job.contract_types == ["full_time"] and job.shifts == ["jednoSm"]
    assert job.education == "basic"  # "Základní + praktická škola"
    assert job.categories == ["8", "13", "14"]  # as shown on up.gov.cz for this ad
    assert job.suitable_for == []  # "zdravi" (healthy people) carries no information
    assert job.isco_code == "91123"
    assert job.place_text == "Praha"
    assert job.location.key == "obec:554782" and job.location.precision == "municipality"
    assert job.location.kraj == "Hlavní město Praha"
    assert job.contact["email"] == "evelimax.services@gmail.com"
    assert job.extra["cizinecMimoEu"] is True
    assert job.posted_at.year == 2026


def test_parse_every_fixture_record(codebooks):
    jobs = [mpsv.parse_record(r, codebooks) for r in RECORDS]
    assert all(j.title for j in jobs)
    assert len({j.external_id for j in jobs}) == len(jobs)
    precisions = {j.location.precision for j in jobs if j.location}
    assert {"address", "municipality", "district"} <= precisions
    address = next(j for j in jobs if j.location and j.location.precision == "address")
    assert address.location.key.startswith("ruian:") and address.location.municipality


def make_transport(records, etag='"v1"'):
    calls = {"dump": 0, "not_modified": 0}
    dump = gzip.compress(json.dumps({"polozky": records}).encode("utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == mpsv.DUMP_URL:
            if request.headers.get("If-None-Match") == etag:
                calls["not_modified"] += 1
                return httpx.Response(304)
            calls["dump"] += 1
            # Like the real server: a .gz file sent with Content-Encoding: gzip.
            headers = {"ETag": etag, "Content-Encoding": "gzip"}
            return httpx.Response(200, stream=httpx.ByteStream(dump), headers=headers)
        name = url.rsplit("/", 1)[1]
        return httpx.Response(200, content=(FIXTURES / "codebooks" / name).read_bytes())

    return httpx.MockTransport(handler), calls


def run_once(sessions, tmp_path, records, etag='"v1"', phase="done"):
    transport, calls = make_transport(records, etag)
    runner = ScrapeRunner(
        sessions, tmp_path / "cache", lambda: httpx.Client(transport=transport), geocode=False
    )
    assert runner.start()
    runner.wait(60)
    assert runner.progress.phase == phase, runner.progress.message
    return calls


def test_import_update_and_expire(sessions, tmp_path):
    run_once(sessions, tmp_path, RECORDS)
    with sessions() as s:
        assert s.scalar(select(func.count(Job.id))) == 30
        assert s.scalar(select(func.count(Location.id))) > 0
        assert search_jobs(s, "pokojska").total >= 1

    # Same ETag: download skipped, nothing re-imported.
    calls = run_once(sessions, tmp_path, RECORDS)
    assert calls == {"dump": 0, "not_modified": 1}

    # New dump: one record changed, one disappeared.
    changed = [dict(r) for r in RECORDS[:-1]]
    changed[0] = changed[0] | {"pozadovanaProfese": {"cs": "Pokojská / uklízečka"}}
    run_once(sessions, tmp_path, changed, etag='"v2"')
    with sessions() as s:
        runs = s.scalars(select(Run).order_by(Run.id)).all()
        assert [r.status for r in runs] == ["ok", "skipped", "ok"]
        assert (runs[2].new_count, runs[2].updated_count, runs[2].removed_count) == (0, 1, 1)
        assert s.scalar(select(func.count(Job.id)).where(Job.status == "active")) == 29
        assert search_jobs(s, "uklizecka").total == 1
        gone = s.scalar(select(Job).where(Job.status == "expired"))

    run_once(sessions, tmp_path, [*changed, RECORDS[-1]], etag='"v3"')
    with sessions() as s:
        back = s.get(Job, gone.id)
        assert back.status == "active" and back.updated_at > gone.updated_at
        last = s.scalars(select(Run).order_by(Run.id.desc())).first()
        assert (last.new_count, last.updated_count, last.removed_count) == (0, 0, 0)


def test_a_fresh_search_finds_everything_again(sessions, tmp_path):
    transport, calls = make_transport(RECORDS)
    runner = ScrapeRunner(sessions, tmp_path / "cache", lambda: httpx.Client(transport=transport))

    def run(fresh=False):
        assert runner.start(fresh=fresh)
        runner.wait(60)
        assert runner.progress.phase == "done", runner.progress.message

    run()
    run()
    assert not runner.progress.cleared
    run(fresh=True)
    assert runner.progress.cleared and calls["dump"] == 1  # imported from the cached file
    with sessions() as s:
        last = s.scalars(select(Run).order_by(Run.id.desc())).first()
        assert last.status == "ok" and last.new_count == 30
        assert s.scalar(select(func.count(Job.id))) == 30


def test_failed_import_is_retried_with_the_same_dump(sessions, tmp_path, monkeypatch):
    run_once(sessions, tmp_path, RECORDS[:10])

    def disk_full(record, codebooks):
        raise OSError("disk full")

    with monkeypatch.context() as m:
        m.setattr(mpsv, "parse_record", disk_full)
        transport, _ = make_transport(RECORDS, etag='"v2"')
        runner = ScrapeRunner(
            sessions,
            tmp_path / "cache",
            lambda: httpx.Client(transport=transport),
            geocode=False,
        )
        runner.start()
        runner.wait(60)
        assert runner.progress.phase == "failed"

    calls = run_once(sessions, tmp_path, RECORDS, etag='"v2"')
    assert calls == {"dump": 0, "not_modified": 1}  # the cached file is imported again
    with sessions() as s:
        runs = s.scalars(select(Run).order_by(Run.id)).all()
        assert [r.status for r in runs] == ["ok", "failed", "ok"]
        assert s.scalar(select(func.count(Job.id))) == 30


def test_unreadable_records_are_skipped(sessions, tmp_path, codebooks):
    odd = [RECORDS[0] | {"pozadovanaProfese": "Pokojská"}, {"referencniCislo": "no portalId"}]
    run_once(sessions, tmp_path, [*RECORDS[:10], *odd, *RECORDS[10:]])
    with sessions() as s:
        run = s.scalar(select(Run))
        assert (run.status, run.new_count) == ("ok", 30)
        assert run.error == "2 unreadable records skipped"
    assert codebooks.code("Obec") is None  # a reference without its code part


def test_a_broken_dump_expires_nothing(sessions, tmp_path):
    run_once(sessions, tmp_path, RECORDS)
    run_once(sessions, tmp_path, [], etag='"v2"', phase="failed")
    run_once(sessions, tmp_path, RECORDS[:10], etag='"v3"')  # 20 of 30 offers missing
    with sessions() as s:
        assert s.scalar(select(func.count(Job.id)).where(Job.status == "active")) == 30
        runs = s.scalars(select(Run).order_by(Run.id)).all()
        assert [r.status for r in runs] == ["ok", "failed", "ok"]
        assert "none expired" in runs[2].error

    run_once(sessions, tmp_path, RECORDS[3:], etag='"v4"')  # 3 of 30 gone: that happens
    with sessions() as s:
        assert s.scalar(select(func.count(Job.id)).where(Job.status == "expired")) == 3


def test_address_format():
    with_street = {"ulice": {"nazev": "Na Valech"}, "cisloDomovni": 57, "cisloOrientacni": "27"}
    assert mpsv._format_address(with_street, "Poděbrady") == "Na Valech 57/27, Poděbrady"
    assert mpsv._format_address({"cisloDomovni": 701}, "Tvrdonice") == "Tvrdonice 701"
    assert mpsv._format_address({}, "Praha") == "Praha"
    assert mpsv._format_address({}, None) is None


def test_store_derives_filter_fields(sessions, codebooks):
    from adawork.scraper.store import JobStore

    hourly = mpsv.parse_record(by_id(67299562), codebooks)
    hourly.external_id, hourly.salary_min, hourly.salary_unit = "h1", 180, "hour"
    bogus = mpsv.parse_record(by_id(67299562), codebooks)
    bogus.external_id, bogus.salary_min, bogus.salary_unit = "h2", 139380, "hour"
    with sessions() as s:
        JobStore(s, "test").add_all([hourly, bogus])
        jobs = {j.sources[0].external_id: j for j in s.scalars(select(Job))}
        assert jobs["h1"].salary_month_min == 180 * 160
        assert jobs["h2"].salary_month_min == 139380  # implausible hourly rate = monthly
        assert jobs["h1"].education_level == 1 and jobs["h1"].categories == ["8", "13", "14"]


def test_a_dropped_connection_is_tried_again_but_not_an_offline_one():
    from adawork.net.http import retry_dropped

    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise httpx.RemoteProtocolError("peer closed connection")
        return "file"

    assert retry_dropped(flaky, "x", waits=(0, 0)) == "file" and len(calls) == 3

    def broken():
        calls.append(1)
        raise httpx.ReadError("connection reset")

    calls.clear()
    with pytest.raises(httpx.ReadError):
        retry_dropped(broken, "x", waits=(0,))
    assert len(calls) == 2  # once more after the pause, then the error

    def offline():
        calls.append(1)
        raise httpx.ConnectError("refused")

    calls.clear()
    with pytest.raises(httpx.ConnectError):
        retry_dropped(offline, "x", waits=(0, 0))
    assert len(calls) == 1
