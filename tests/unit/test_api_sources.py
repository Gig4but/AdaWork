import base64
import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from adawork.config import Profile
from adawork.core import ScrapeQuery, utcnow
from adawork.db.models import Job, JobSource, Run, SavedSearch
from adawork.db.session import make_engine, make_sessionmaker, migrate
from adawork.geo.places import PlaceResolver
from adawork.scraper.api import searches
from adawork.scraper.runner import ScrapeRunner
from adawork.scraper.sources import careerjet, jooble
from adawork.search.saved import with_new_counts

KOLIN = Profile(home_address="Kolín", max_minutes=30, travel_mode="car")


def jooble_job(n: int, **extra) -> dict:
    return {
        "id": 900 + n,
        "title": f"Skladník {n}",
        "location": "Kolín, Středočeský kraj",
        "snippet": "Hledáme <b>skladníka</b>&nbsp;na ranní směny.",
        "salary": "28 000 - 32 000 Kč",
        "type": "Plný úvazek",
        "link": f"https://cz.jooble.org/desc/{900 + n}",
        "company": "ACME s.r.o.",
        "updated": "2026-10-10T00:00:00.0000000",
        "source": "prace.cz",
        **extra,
    }


class Jooble:
    def __init__(self, total: int = 3) -> None:
        self.total = total
        self.bodies: list[dict] = []
        self.status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.url.path == "/api/the-jooble-key"
        body = json.loads(request.content)
        self.bodies.append(body)
        if self.status != 200:
            return httpx.Response(self.status)
        start = (body["page"] - 1) * jooble.PAGE_SIZE
        jobs = [jooble_job(n) for n in range(start, min(self.total, start + jooble.PAGE_SIZE))]
        return httpx.Response(200, json={"totalCount": self.total, "jobs": jobs})


@pytest.fixture
def sessions(tmp_path, municipalities):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    sessions = make_sessionmaker(engine)
    with sessions() as s:
        municipalities(s)
    yield sessions
    engine.dispose()


def run_api(sessions, tmp_path, site, source, keys, profile=KOLIN):
    transport = httpx.MockTransport(site)
    runner = ScrapeRunner(
        sessions,
        tmp_path / "cache",
        lambda: httpx.Client(transport=transport),
        geocode=False,
        profile=lambda: profile,
        sources=[source],
        source_choices=lambda: {source.id: True},
        api_key=keys.get,
    )
    runner.start()
    runner.wait(60)
    return runner


def test_jooble_runs_only_with_a_key_and_stores_what_it_finds(sessions, tmp_path):
    site = Jooble(total=3)
    run_api(sessions, tmp_path, site, jooble.SOURCE, {})
    assert site.bodies == []  # no key, no request

    runner = run_api(sessions, tmp_path, site, jooble.SOURCE, {"jooble": "the-jooble-key"})
    assert runner.progress.phase == "done", runner.progress.errors
    assert runner.progress.results["jooble"]["new"] == 3
    # Near home, with the radius the API accepts above the 30 km reach by car.
    assert site.bodies == [
        {"keywords": "", "location": "Kolín", "page": 1, "ResultOnPage": 50, "radius": "40"}
    ]
    with sessions() as s:
        job = s.scalar(select(Job).join(JobSource).where(JobSource.external_id == "901"))
        assert job.title == "Skladník 1" and job.employer_name == "ACME s.r.o."
        assert job.description == "Hledáme skladníka na ranní směny."
        assert (job.salary_min, job.salary_max, job.salary_unit) == (28000, 32000, "month")
        assert job.location.municipality == "Kolín"
        assert job.sources[0].url == "https://cz.jooble.org/desc/901"
        assert job.sources[0].detail_at is None  # an excerpt: a portal's full text wins
        assert s.scalar(select(Run.status).where(Run.source_id == "jooble")) == "ok"

    # Not returned for two weeks: gone.
    site.total = 0
    with sessions() as s:
        for src in s.scalars(select(JobSource)):
            src.last_seen_at -= timedelta(days=15)
        s.commit()
    runner = run_api(sessions, tmp_path, site, jooble.SOURCE, {"jooble": "the-jooble-key"})
    assert runner.progress.results["jooble"]["removed"] == 3


def test_jooble_pages_until_the_end_within_its_request_budget(sessions, tmp_path):
    site = Jooble(total=120)
    profile = KOLIN.model_copy(update={"kinds_of_work": ["skladník", "řidič", "kuchař", "uklid"]})
    runner = run_api(sessions, tmp_path, site, jooble.SOURCE, {"jooble": "the-jooble-key"}, profile)
    pages = [(b["keywords"], b["page"]) for b in site.bodies]
    # 3 pages per search at most, 10 requests per run at most.
    assert pages[:4] == [("skladník", 1), ("skladník", 2), ("skladník", 3), ("řidič", 1)]
    assert len(pages) == jooble.SOURCE.max_requests
    assert runner.progress.results["jooble"]["new"] == 120  # the same ads for every search


def test_a_refused_key_fails_the_source_not_the_run(sessions, tmp_path):
    site = Jooble()
    site.status = 403
    runner = run_api(sessions, tmp_path, site, jooble.SOURCE, {"jooble": "the-jooble-key"})
    assert "403" in runner.progress.errors["jooble"]
    assert "the-jooble-key" not in runner.progress.errors["jooble"]  # the key is in the URL
    with sessions() as s:
        assert s.scalar(select(Run.status).where(Run.source_id == "jooble")) == "failed"


def test_careerjet_asks_with_the_key_and_reads_salaries(sessions, tmp_path):
    asked: list[httpx.Request] = []

    def site(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        if request.url.params["page"] == "2":
            return httpx.Response(200, json={"type": "JOBS", "hits": 2, "pages": 2, "jobs": []})
        jobs = [
            {
                "title": "Řidič VZV",
                "company": "Logistika a.s.",
                "date": "Tue, 07 Oct 2026 12:00:00 GMT",
                "description": "Práce na <b>směny</b>",
                "locations": "Kolín",
                "salary": "",
                "salary_min": 420000,
                "salary_max": 480000,
                "salary_currency_code": "CZK",
                "salary_type": "Y",
                "url": "https://jobviewtrack.com/v2/cs-cz/job/abc?track=1",
            },
            {"title": "Bez odkazu", "locations": "Kolín"},
        ]
        return httpx.Response(200, json={"type": "JOBS", "hits": 2, "pages": 2, "jobs": jobs})

    runner = run_api(sessions, tmp_path, site, careerjet.SOURCE, {"careerjet": "cj-key-1234"})
    assert runner.progress.results["careerjet"]["new"] == 1, runner.progress.errors
    first = asked[0]
    auth = base64.b64decode(first.headers["Authorization"].split()[1]).decode()
    assert auth == "cj-key-1234:"
    params = first.url.params
    assert params["locale_code"] == "cs_CZ" and params["location"] == "Kolín"
    assert params["radius"] == "39" and params["user_ip"] and "AdaWork" in params["user_agent"]
    assert len(asked) == 2  # both pages of the answer
    with sessions() as s:
        job = s.scalar(select(Job).join(JobSource).where(JobSource.source_id == "careerjet"))
        assert job.title == "Řidič VZV" and job.description == "Práce na směny"
        assert (job.salary_min, job.salary_max, job.salary_unit) == (35000, 40000, "month")
        assert f"{job.posted_at:%Y-%m-%d %H:%M}" == "2026-10-07 12:00"


def test_careerjet_stops_at_an_ambiguous_place(sessions):
    def site(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"type": "LOCATIONS", "locations": ["Kolín", "Köln"]})

    client = httpx.Client(transport=httpx.MockTransport(site))
    with sessions() as s:
        places = PlaceResolver(s)
    query = ScrapeQuery(kraje=["Středočeský kraj"], home="Kolín", radius_km=30)
    assert list(careerjet.SOURCE.search(client, "k" * 10, query, places)) == []


def test_without_a_home_every_region_is_asked():
    query = ScrapeQuery(kraje=["Středočeský kraj", "Hlavní město Praha"], keywords=["řidič"])
    assert searches(query) == [
        ("řidič", "Středočeský kraj", None),
        ("řidič", "Hlavní město Praha", None),
    ]
    assert jooble.radius(30) == "40" and jooble.radius(500) == "80"


def test_the_last_search_start_is_known_also_from_older_runs(sessions, tmp_path):
    runner = ScrapeRunner(sessions, tmp_path, geocode=False, sources=())
    assert runner.last_start() is None
    earlier = utcnow() - timedelta(hours=3)
    with sessions() as s:
        s.add(Run(source_id="prace_cz", started_at=earlier, status="ok"))
        s.commit()
    assert runner.last_start() == earlier  # a run from before the start was recorded
    runner.start()
    runner.wait(30)
    assert runner.last_start() > earlier


def test_jobs_found_again_after_a_clear_are_not_new_to_saved_searches(sessions, tmp_path):
    site = Jooble(total=5)
    runner = run_api(sessions, tmp_path, site, jooble.SOURCE, {"jooble": "the-jooble-key"})
    with sessions() as s:
        s.add(SavedSearch(name="Sklad", query="q=skladník", seen_up_to=5, created_at=utcnow()))
        s.commit()
    site.total = 2
    runner.start(fresh=True)  # deletes the jobs: their ids are given out again
    runner.wait(60)
    site.total = 4
    runner.start()
    runner.wait(60)
    with sessions() as s:
        assert [n for _, n in with_new_counts(s)] == [2]
