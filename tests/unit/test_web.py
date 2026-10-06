import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from adawork.config import Settings
from adawork.db.models import Job
from adawork.scraper.runner import Progress
from adawork.scraper.sources import mpsv
from adawork.scraper.store import JobStore
from adawork.web.app import create_app

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "mpsv"


class FakeRunner:
    def __init__(self, sessions, cache_dir) -> None:
        self.sessions = sessions
        self.progress = Progress()
        self.starts = 0
        self.fresh = False

    def start(self, fresh: bool = False) -> bool:
        if self.progress.running:
            return False
        self.starts += 1
        self.fresh = fresh
        self.progress = Progress(running=True, phase="download")
        return True


@pytest.fixture
def app(tmp_path):
    settings = Settings.load(tmp_path / "config.json")
    return create_app(
        settings,
        db_path=tmp_path / "t.db",
        cache_dir=tmp_path / "cache",
        runner_factory=FakeRunner,
    )


@pytest.fixture
def client(app):
    return TestClient(app, base_url="http://127.0.0.1")


def load_fixture_jobs(app) -> None:
    records = json.loads((FIXTURES / "volna-mista.first30.json").read_text(encoding="utf-8"))
    codebooks = mpsv.Codebooks.load_dir(FIXTURES / "codebooks")
    with app.state.services.sessions() as s:
        JobStore(s, "mpsv").add_all(mpsv.parse_record(r, codebooks) for r in records["polozky"])


def test_first_start_goes_through_wizard(app, client, tmp_path):
    assert client.get("/health").json()["status"] == "ok"
    first = client.get("/", follow_redirects=False)
    assert first.status_code == 303 and first.headers["location"] == "/wizard/welcome"
    welcome = client.get("/wizard/welcome").text
    assert "Vítejte" in welcome and re.search(r'value="system"[^>]*checked', welcome)
    assert welcome.count('name="theme"') == 8

    r = client.post(
        "/wizard/welcome", data={"language": "cs", "theme": "forest"}, follow_redirects=False
    )
    assert r.headers["location"] == "/wizard/profile"
    assert app.state.services.settings.theme == "forest"
    page = client.get("/wizard/profile").text
    assert "Odkud dojíždíte" in page and 'data-theme="light" data-scheme="forest"' in page
    assert 'href="/wizard/welcome" class="nav-button secondary" aria-current="page"' in page

    form = {
        "home_address": " Kolín ",
        "travel_mode": "transit",
        "max_minutes": "40",
        "kinds_of_work": "skladník, řidič ,",
    }
    r = client.post("/wizard/profile", data=form, follow_redirects=False)
    assert r.headers["location"] == "/wizard/done"
    done = client.get("/wizard/done").text
    assert "Automatické hledání je vypnuté" in done

    r = client.post("/wizard/done", follow_redirects=False)
    assert r.headers["location"] == "/"
    assert app.state.services.runner.starts == 1  # Start kicks off the MPSV download

    saved = Settings.load(tmp_path / "config.json")
    assert saved.wizard_completed
    assert saved.profile.home_address == "Kolín"
    assert saved.profile.travel_mode == "transit" and saved.profile.max_minutes == 40
    assert saved.profile.kinds_of_work == ["skladník", "řidič"]
    assert saved.automation.enabled is False


def test_wizard_rejects_bad_input(client):
    r = client.post("/wizard/profile", data={"max_minutes": "999"})
    assert r.status_code == 422
    r = client.post("/wizard/welcome", data={"language": "cs", "theme": "neon"})
    assert r.status_code == 422


def test_only_local_same_origin_requests(app, client):
    assert client.get("/health", headers={"Host": "attacker.example"}).status_code == 400
    evil = client.post("/scrape", headers={"Origin": "https://attacker.example"})
    assert evil.status_code == 403 and app.state.services.runner.starts == 0
    own = client.post("/scrape", headers={"Origin": "http://127.0.0.1"})
    assert own.status_code == 200 and app.state.services.runner.starts == 1


def test_english_ui(app, client):
    client.post("/wizard/welcome", data={"language": "en"})
    assert "Where you commute from" in client.get("/wizard/profile").text


def test_language_and_scheme_are_saved_when_picked(app, client):
    assert 'hx-post="/settings/appearance"' in client.get("/wizard/welcome").text
    r = client.post("/settings/appearance", data={"language": "en", "theme": "ocean"})
    assert r.headers["HX-Refresh"] == "true"  # the page comes back in the new language
    saved = Settings.load(app.state.services.settings._path)
    assert (saved.language, saved.theme) == ("en", "ocean")
    r = client.post("/settings/appearance", data={"language": "en", "theme": "plum"})
    assert "HX-Refresh" not in r.headers and app.state.services.settings.theme == "plum"
    assert client.post("/settings/appearance", data={"language": "de"}).status_code == 422


def test_last_update_is_shown_in_local_time(app, client):
    from datetime import UTC, datetime

    from adawork.db.models import Run

    finished = datetime(2026, 10, 9, 23, 30)  # stored as naive UTC
    with app.state.services.sessions() as s:
        s.add(Run(source_id="mpsv", started_at=finished, finished_at=finished, status="ok"))
        s.commit()
    local = finished.replace(tzinfo=UTC).astimezone()
    when = f"{local.day}. {local.month}. {local.year} {local:%H:%M}"
    assert f"poslední aktualizace {when}" in client.get("/status").text


def test_search_page_and_results(app, client):
    app.state.services.settings.wizard_completed = True
    load_fixture_jobs(app)
    page = client.get("/")
    assert page.status_code == 200
    assert "Aktivní nabídky: 30" in page.text
    assert "automatické hledání vypnuto" in page.text
    assert "Pokojská" in page.text

    partial = client.get("/results", params={"q": "pokojska"}).text
    assert "Pokojská" in partial and "Evelimax" in partial
    assert "22 500 Kč/měsíc" in partial
    assert "up.gov.cz/volna-mista-v-cr#/volna-mista-detail/67299562" in partial
    assert "<html" not in partial  # fragment only


def test_the_kinds_of_work_fill_the_search(app, client):
    app.state.services.settings.wizard_completed = True
    app.state.services.settings.profile.kinds_of_work = ["skladník", "řidič"]
    assert 'name="q" value="skladník, řidič"' in client.get("/").text
    assert 'name="q" value=""' in client.get("/?q=").text  # cleared by the user


def test_list_is_numbered_with_the_last_found_first(app, client):
    app.state.services.settings.wizard_completed = True
    load_fixture_jobs(app)
    page = client.get("/").text
    assert 'id="job-list"' in page and page.count('class="job"') == 30
    with app.state.services.sessions() as s:
        newest = s.scalar(select(func.max(Job.id)))
    assert page.index(f'data-id="{newest}"') < page.index(f'data-id="{newest - 1}"')


def test_new_finds_go_on_top_while_a_search_runs(app, client):
    app.state.services.settings.wizard_completed = True
    records = json.loads((FIXTURES / "volna-mista.first30.json").read_text(encoding="utf-8"))
    codebooks = mpsv.Codebooks.load_dir(FIXTURES / "codebooks")
    jobs = [mpsv.parse_record(r, codebooks) for r in records["polozky"]]

    def store(batch):
        with app.state.services.sessions() as s:
            JobStore(s, "mpsv").add_all(batch)

    store(jobs[:20])
    assert "/results/new" not in client.get("/").text  # no search running
    app.state.services.runner.progress = Progress(running=True, phase="import")
    page = client.get("/").text
    assert "since=20&cleared=0" in page

    r = client.get("/results/new?since=20&cleared=0")
    assert "since=20" in r.text and "job-list" not in r.text  # nothing new yet
    store(jobs[20:25])
    r = client.get("/results/new?since=20&cleared=0")
    assert 'hx-swap-oob="afterbegin:#job-list"' in r.text and r.text.count('class="job"') == 5
    assert "since=25" in r.text and "Nalezené nabídky: 25" in r.text

    store(jobs[25:])  # with words to rank by, new finds belong anywhere: the list reloads
    r = client.get("/results/new?q=a&since=25&cleared=0")
    assert r.headers["HX-Trigger"] == "jobs-updated" and r.text == ""

    app.state.services.runner.progress.cleared = True  # old jobs deleted: ids start again
    r = client.get("/results/new?since=30&cleared=0")
    assert r.headers["HX-Trigger"] == "jobs-updated"

    app.state.services.runner.progress = Progress(phase="done")
    assert client.get("/results/new?since=30&cleared=1").text == ""  # the poll stops


def test_a_job_opens_with_its_whole_text(app, client):
    app.state.services.settings.wizard_completed = True
    load_fixture_jobs(app)
    with app.state.services.sessions() as s:
        text = "Úvod.\n" + "slovo " * 100
        s.execute(update(Job).where(Job.id == 1).values(description=text))
        s.commit()
    page = client.get("/").text
    assert 'href="/jobs/1" class="job-open"' in page and "Celý text" in page
    assert 'id="job-detail"' in page and 'id="to-top"' in page
    r = client.get("/jobs/1", headers={"HX-Request": "true"})
    assert "<html" not in r.text and "Pokojská" in r.text
    assert text.strip() in r.text and "up.gov.cz" in r.text
    assert "<html" in client.get("/jobs/1").text  # opened as a page of its own
    boosted = {"HX-Request": "true", "HX-Boosted": "true"}  # from the page beside the search
    assert "<html" in client.get("/jobs/1", headers=boosted).text
    assert client.get("/jobs/999999").status_code == 404


def test_scrape_button_starts_and_polls(app, client):
    app.state.services.settings.wizard_completed = True
    runner = app.state.services.runner
    r = client.post("/scrape")
    assert runner.starts == 1 and not runner.fresh
    assert 'hx-trigger="every 1s"' in r.text
    assert r.headers["HX-Trigger"] == "jobs-updated"  # the list starts asking for new finds
    page = client.get("/").text  # both buttons, also while a search runs
    assert 'hx-post="/scrape"' in page and 'hx-post="/scrape?fresh=1"' in page
    client.post("/scrape")
    assert runner.starts == 1  # not again while one runs
    runner.progress = Progress()
    client.post("/scrape?fresh=1")
    assert runner.fresh

    app.state.services.runner.progress = Progress(
        running=False, phase="done", results={"mpsv": {"new": 5, "updated": 1, "removed": 2}}
    )
    r = client.get("/scrape/progress")
    assert r.headers["HX-Trigger"] == "jobs-updated"
    assert "5 nových, 1 změněných, 2 již neplatných" in r.text
