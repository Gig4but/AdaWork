import io
import json
import re
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import openpyxl
import pytest
from conftest import make_ai
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from adawork.config import Settings
from adawork.db.models import Job, JobSource, SavedSearch
from adawork.scraper.runner import Progress
from adawork.scraper.sources import SWITCHABLE, mpsv
from adawork.scraper.store import JobStore
from adawork.search.service import start_batch
from adawork.secrets import MemorySecrets
from adawork.social import browser as social_browser
from adawork.web.app import create_app
from adawork.web.routes import maps, search

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources" / "mpsv"


class FakeRunner:
    def __init__(self, sessions, cache_dir, **kwargs) -> None:
        self.sessions = sessions
        self.progress = Progress()
        self.starts = 0
        self.restarts = 0
        self.fresh = False
        self.started_at = None
        self.api_key = kwargs.get("api_key")
        self.focus = None

    def start(self, fresh: bool = False) -> bool:
        if self.progress.running:
            return False
        self.starts += 1
        self.fresh = fresh
        self.progress = Progress(running=True, phase="download")
        self.started_at = datetime(2026, 10, 13, 8, 0)
        return True

    def start_over(self) -> None:
        self.restarts += 1
        self.progress = Progress()
        self.start(fresh=True)

    def last_start(self):
        return self.started_at


@pytest.fixture
def app(tmp_path):
    settings = Settings.load(tmp_path / "config.json")
    autostart = []
    app = create_app(
        settings,
        db_path=tmp_path / "t.db",
        cache_dir=tmp_path / "cache",
        runner_factory=FakeRunner,
        ai_factory=lambda st, sessions, cache: make_ai(tmp_path, st, sessions),
        data_dir=tmp_path / "data",
        secrets=MemorySecrets(),
        autostart_sync=autostart.append,
        refreshes=(),
    )
    app.state.autostart = autostart  # what the Run value was set to, in order
    return app


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

    assert 'href="/wizard/profile"' in welcome
    # In a form: htmx sends the picked language and scheme only from one.
    assert re.search(r'<form>\s*<div hx-post="/settings/appearance"', welcome)
    client.post("/settings/appearance", data={"language": "cs", "theme": "forest"})
    assert app.state.services.settings.theme == "forest"
    page = client.get("/wizard/profile").text
    assert "Odkud dojíždíte" in page and 'data-theme="light" data-scheme="forest"' in page
    assert "nav-button" not in page  # only the wizard until it is done

    form = {
        "home_address": " Kolín ",
        "travel_mode": "transit",
        "max_minutes": "40",
        "kinds_of_work": "skladník, řidič ,",
    }
    r = client.post("/wizard/profile", data=form, follow_redirects=False)
    assert r.headers["location"] == "/wizard/ai"
    page = client.get("/wizard/ai").text
    assert "Automaticky – nejlepší pro tento počítač" in page
    assert "Tento počítač: 16 GB RAM · AMD Radeon(TM) Graphics" in page
    assert 'value="auto" checked' in page and "Doporučeno" in page
    r = client.post("/wizard/ai", data={"choice": "auto"}, follow_redirects=False)
    assert r.headers["location"] == "/wizard/sources"
    assert app.state.services.ai.manager.downloads_started == 1  # the model downloads meanwhile
    page = client.get("/wizard/sources").text
    assert 'name="source_prace_cz" checked' in page
    assert 'name="source_linkedin" >' in page or 'name="source_linkedin">' in page  # off

    # LinkedIn without ticking the risk box stays off.
    portals = {"source_mpsv": "on", "source_prace_cz": "on", "source_jenprace": "on"}
    r = client.post("/wizard/sources", data=portals | {"source_linkedin": "on"})
    assert "zaškrtnete, že rozumíte riziku" in r.text
    assert app.state.services.settings.sources == {}
    assert 'name="source_linkedin" checked' in r.text  # the choices made are kept
    assert 'name="source_profesia" checked' not in r.text
    r = client.post(
        "/wizard/sources",
        data=portals | {"source_linkedin": "on", "consent_linkedin": "on"},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/wizard/keys"
    page = client.get("/wizard/keys").text
    assert "Klíč Jooble" in page and "https://jooble.org/api/about" in page
    r = client.post("/wizard/keys", data={"key_jooble": ""}, follow_redirects=False)
    assert r.headers["location"] == "/wizard/automation"  # no key: nothing changes
    page = client.get("/wizard/automation").text
    assert 'name="enabled" >' in page or 'name="enabled">' in page  # off by default
    assert 'value="12" checked' in page
    form = {"interval_hours": "12", "start_with_windows": "on", "notifications": "on"}
    r = client.post("/wizard/automation", data=form, follow_redirects=False)
    assert r.headers["location"] == "/wizard/done"
    done = client.get("/wizard/done").text
    assert "Automatické hledání je vypnuté" in done

    r = client.post("/wizard/done", follow_redirects=False)
    assert r.headers["location"] == "/"
    page = client.get("/").text
    assert 'href="/settings" class="nav-button icon-only"' in page
    assert 'href="/wizard/welcome"' not in page  # opened again from Settings only
    assert app.state.services.runner.starts == 1  # Start kicks off the MPSV download
    assert app.state.autostart == [False]  # no start with Windows while checks are off

    saved = Settings.load(tmp_path / "config.json")
    assert saved.wizard_completed
    assert saved.profile.home_address == "Kolín"
    assert saved.profile.travel_mode == "transit" and saved.profile.max_minutes == 40
    assert saved.profile.kinds_of_work == ["skladník", "řidič"]
    chosen = {"mpsv", "prace_cz", "jenprace", "linkedin"}  # ticked in the form above
    assert saved.sources == {source_id: source_id in chosen for source_id, _ in SWITCHABLE}
    assert saved.automation.enabled is False


def test_facebook_and_instagram_need_consent_then_offer_a_login(app, client):
    app.state.services.settings.wizard_completed = True
    page = client.get("/sources").text
    assert "Více o rizicích" in page and "trvale" in page  # the warning is always shown
    assert "Přihlásit se" not in page  # only once switched on

    r = client.post("/sources", data={"source_mpsv": "on", "source_facebook": "on"})
    assert "Facebook se zapne, až zaškrtnete" in r.text
    assert not app.state.services.settings.sources.get("facebook")
    assert 'name="consent_facebook" checked' not in r.text  # Save again must not consent
    r = client.post("/sources", data={"source_mpsv": "on", "source_facebook": "on"})
    assert "Facebook se zapne, až zaškrtnete" in r.text
    form = {"source_linkedin": "on", "consent_linkedin": "on", "source_facebook": "on"}
    r = client.post("/sources", data=form)
    assert "Facebook se zapne" in r.text and 'name="consent_linkedin" checked' in r.text

    form = {
        "source_mpsv": "on",
        "source_facebook": "on",
        "consent_facebook": "on",
        "facebook_per_day": "500",
        "facebook_groups": "https://m.facebook.com/groups/brigadykolin/\nnot a link",
        "instagram_tags": "#brigadabrno prace_kolin",
    }
    assert client.post("/sources", data=form, follow_redirects=False).status_code == 303
    social = app.state.services.settings.social
    assert (
        app.state.services.settings.sources["facebook"]
        and not app.state.services.settings.sources["instagram"]
    )
    assert social.facebook_per_day == 200  # at most 200, with a warning in the form
    assert social.facebook_groups == ["https://www.facebook.com/groups/brigadykolin"]
    assert social.instagram_tags == ["brigadabrno", "prace_kolin"]
    page = client.get("/sources").text
    assert "Přihlásit se" in page and "Nepřihlášeno" in page
    assert "Nepřihlášeno" in client.get("/social/facebook/status").text
    assert client.post("/social/instagram/login").status_code == 404  # switched off


def test_a_login_while_the_app_reads_says_so(app, client, tmp_path):
    app.state.services.settings.wizard_completed = True
    app.state.services.settings.sources["facebook"] = True
    held = social_browser.hold_profile(social_browser.profile_dir(tmp_path / "data", "facebook"))
    try:
        client.post("/social/facebook/login")
        for thread in threading.enumerate():
            if thread.name == "login-facebook":
                thread.join(10)
    finally:
        held.release()
    assert "Aplikace právě čte" in client.get("/social/facebook/status").text


def test_sources_page_and_indeed_link(app, client, municipalities):
    app.state.services.settings.wizard_completed = True
    assert "Zdroje nabídek" in client.get("/sources").text
    r = client.post("/sources", data={"source_mpsv": "on"}, follow_redirects=False)
    assert r.headers["location"] == "/sources?saved=1"
    assert app.state.services.settings.sources["prace_cz"] is False
    assert "Uloženo" in client.get("/sources?saved=1").text

    with app.state.services.sessions() as s:
        municipalities(s)
    app.state.services.settings.profile.home_address = "Kolín"
    app.state.services.settings.profile.max_minutes = 30
    r = client.get("/out/indeed", params={"q": "skladník"}, follow_redirects=False)
    assert r.headers["location"] == (
        "https://cz.indeed.com/jobs?q=skladn%C3%ADk&l=Kol%C3%ADn&sort=date&radius=50"
    )
    places = app.state.services.places()
    app.state.services.settings.profile.home_address = "Nádražní 5, 999 99 Nikde"
    r = client.get("/out/indeed", params={"q": "skladník"}, follow_redirects=False)
    assert r.headers["location"] == "https://cz.indeed.com/jobs?q=skladn%C3%ADk&l=&sort=date"
    assert app.state.services.places() is places  # the municipalities are read once
    assert "/out/indeed" in client.get("/").text


def test_linkedin_needs_consent_ticked_by_the_user(app, client):
    app.state.services.settings.wizard_completed = True
    form = {"source_mpsv": "on", "source_linkedin": "on"}
    r = client.post("/sources", data=form, follow_redirects=False)
    assert r.status_code == 200 and 'name="source_linkedin" checked' in r.text
    # The refused form comes back without the consent ticked, so saving it again fails too.
    assert 'name="consent_linkedin" checked' not in r.text
    r = client.post("/sources", data=form, follow_redirects=False)
    assert r.status_code == 200 and app.state.services.settings.sources.get("linkedin") is not True
    r = client.post("/sources", data=form | {"consent_linkedin": "on"}, follow_redirects=False)
    assert r.headers["location"] == "/sources?saved=1"
    assert app.state.services.settings.sources["linkedin"] is True
    assert 'name="consent_linkedin" checked' in client.get("/sources").text


def test_wizard_rejects_bad_input(client):
    r = client.post("/wizard/profile", data={"max_minutes": "999"})
    assert r.status_code == 422
    r = client.post("/settings/appearance", data={"language": "cs", "theme": "neon"})
    assert r.status_code == 422


def test_scraped_links_are_web_addresses_only(app, client):
    from adawork.db.models import JobSource

    app.state.services.settings.wizard_completed = True
    load_fixture_jobs(app)
    with app.state.services.sessions() as s:
        s.execute(update(JobSource).values(url="javascript:alert(1)"))
        s.commit()
        location_id = s.query(Job.location_id).filter(Job.location_id.is_not(None)).first()[0]
    for page in ("/results", f"/map/location/{location_id}"):
        text = client.get(page).text
        assert "javascript:" not in text and 'href="#"' in text


def test_only_local_same_origin_requests(app, client):
    assert client.get("/health", headers={"Host": "attacker.example"}).status_code == 400
    evil = client.post("/scrape", headers={"Origin": "https://attacker.example"})
    assert evil.status_code == 403 and app.state.services.runner.starts == 0
    own = client.post("/scrape", headers={"Origin": "http://127.0.0.1"})
    assert own.status_code == 200 and app.state.services.runner.starts == 1


def test_english_ui(app, client):
    client.post("/settings/appearance", data={"language": "en"})
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

    with app.state.services.sessions() as s:
        s.execute(update(Job).values(contract_types=[], shifts=[], benefits=["strav"]))
        s.commit()
    assert "Podnikové stravování" in client.get("/results").text


def test_the_kinds_of_work_fill_the_search(app, client):
    app.state.services.settings.wizard_completed = True
    app.state.services.settings.profile.kinds_of_work = ["skladník", "řidič"]
    assert 'name="q" value="skladník, řidič"' in client.get("/").text
    assert 'name="q" value=""' in client.get("/?q=").text  # cleared by the user


def fixture_jobs():
    records = json.loads((FIXTURES / "volna-mista.first30.json").read_text(encoding="utf-8"))
    codebooks = mpsv.Codebooks.load_dir(FIXTURES / "codebooks")
    return [mpsv.parse_record(r, codebooks) for r in records["polozky"]]


def store_jobs(app, batch) -> None:
    with app.state.services.sessions() as s:
        JobStore(s, "mpsv").add_all(batch)


def new_batch(app) -> None:
    with app.state.services.sessions() as s:
        start_batch(s)


def order(page: str, ids) -> list[int]:
    return sorted(ids, key=lambda i: page.index(f'id="job-{i}"'))


def test_list_is_in_found_order_the_last_search_first(app, client):
    app.state.services.settings.wizard_completed = True
    jobs = fixture_jobs()
    store_jobs(app, jobs[:10])
    new_batch(app)
    store_jobs(app, jobs[10:20])
    page = client.get("/").text
    assert 'id="job-list"' in page and page.count('class="job"') == 20
    assert order(page, range(1, 21)) == [*range(11, 21), *range(1, 11)]
    assert page.count('class="new-mark"') == 10  # the last search's finds
    assert page.index('class="new-mark"') < page.index('id="job-1"')
    with app.state.services.sessions() as s:
        start_batch(s, 0)  # started over: everything was found again, nothing is new
    assert 'class="new-mark"' not in client.get("/").text


def test_jobs_can_be_hidden_and_brought_back(app, client):
    app.state.services.settings.wizard_completed = True
    load_fixture_jobs(app)
    page = client.get("/").text
    assert page.count('class="hide secondary outline"') == 30 and "Skryto" not in page
    for job_id in (29, 30):
        r = client.post(f"/jobs/{job_id}/hide", data={"sort": "relevance"})
    assert 'id="hidden-bar"' in r.text and 'hx-swap-oob="true"' in r.text
    assert "Skryto: 2" in r.text and "Nalezené nabídky: 28" in r.text
    page = client.get("/results").text
    assert 'id="job-30"' not in page and 'id="job-29"' not in page and "Skryto: 2" in page

    r = client.post("/hidden/restore")  # the last hidden comes back first
    assert r.headers["HX-Trigger"] == "jobs-updated" and "Skryto: 1" in r.text
    page = client.get("/results").text
    assert 'id="job-30"' in page and 'id="job-29"' not in page
    r = client.post("/hidden/restore?every=1")
    assert "Skryto" not in r.text and 'id="job-29"' in client.get("/results").text
    assert client.post("/jobs/999999/hide").status_code == 404


def test_a_running_search_adds_its_finds_in_found_order(app, client, monkeypatch):
    monkeypatch.setattr(search, "PAGE_SIZE", 10)
    app.state.services.settings.wizard_completed = True
    jobs = fixture_jobs()

    def poll(since, shown, more=False, qs=""):
        data = {"shown": ",".join(map(str, shown)), "more": "1" if more else ""}
        return client.post(f"/results/new?{qs}since={since}&cleared=0", data=data)

    assert "/results/new" not in client.get("/").text  # no search running
    new_batch(app)  # the first search: the list is empty
    app.state.services.runner.progress = Progress(running=True, phase="import")
    assert "since=0&cleared=0" in client.get("/").text
    r = poll(0, [])
    assert "since=0" in r.text and "hx-swap-oob" not in r.text  # nothing new yet

    store_jobs(app, jobs[:5])  # put at the end, in the order found
    r = poll(0, [])
    assert 'hx-swap-oob="beforeend:#job-list"' in r.text
    assert order(r.text, range(1, 6)) == [1, 2, 3, 4, 5] and "since=5" in r.text
    assert "Nalezené nabídky: 5" in r.text
    store_jobs(app, jobs[5:15])  # fills the page; the rest is behind "more"
    r = poll(5, range(1, 6))
    assert r.text.count('class="job"') == 5 and order(r.text, range(6, 11)) == [6, 7, 8, 9, 10]
    assert 'class="secondary outline more"' in r.text and "since=15" in r.text

    with app.state.services.sessions() as s:  # no longer offered: drops out of the list
        s.execute(update(Job).where(Job.id == 2).values(status="expired"))
        s.commit()
    store_jobs(app, jobs[15:16])  # belongs on a page not loaded yet
    r = poll(15, range(1, 11), more=True)
    assert '<div id="job-2" hx-swap-oob="delete">' in r.text
    assert 'class="job"' not in r.text and "since=16" in r.text

    new_batch(app)  # the next search: its finds go above the jobs found before
    store_jobs(app, jobs[16:19])
    shown = [1, *range(3, 11)]
    r = poll(16, shown, more=True)
    assert 'hx-swap-oob="beforebegin:#job-1"' in r.text
    assert order(r.text, range(17, 20)) == [17, 18, 19] and "since=19" in r.text
    page = client.get("/").text
    assert order(page, [3, 1, 19, 18, 17]) == [17, 18, 19, 1, 3]

    store_jobs(app, jobs[19:])  # with words to rank by too, above the jobs found before
    r = poll(19, shown, qs="q=praha&")
    assert "HX-Trigger" not in r.headers and 'hx-swap-oob="beforebegin:#job-1"' in r.text
    assert 'id="job-24"' in r.text and 'id="job-21"' not in r.text  # Praha only
    assert '<div id="job-4" hx-swap-oob="delete">' in r.text  # Poděbrady
    r = poll(19, shown, qs="sort=date&")  # another order: new finds belong anywhere, reload
    assert r.headers["HX-Trigger"] == "jobs-updated" and r.text == ""

    app.state.services.runner.progress.cleared = True  # old jobs deleted: ids start again
    assert poll(30, shown).headers["HX-Trigger"] == "jobs-updated"
    app.state.services.runner.progress = Progress(phase="done")
    r = client.post("/results/new?since=30&cleared=1")
    assert r.text == ""  # the poll stops


def test_starting_over_empties_the_list_at_once(app, client):
    app.state.services.settings.wizard_completed = True
    load_fixture_jobs(app)
    for progress in (
        Progress(running=True, phase="listing", restarting=True),  # the running one stops
        Progress(running=True, phase="clear"),
    ):
        app.state.services.runner.progress = progress
        r = client.get("/results", headers={"HX-Request": "true"})
        assert 'class="job' not in r.text and "since=0&cleared=0" in r.text


def test_a_refresh_dims_the_listed_jobs_until_checked(app, client):
    app.state.services.settings.wizard_completed = True
    load_fixture_jobs(app)
    checked_since = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=1)
    app.state.services.runner.progress = Progress(
        running=True, phase="check", checked_since=checked_since, checking=("mpsv",)
    )
    with app.state.services.sessions() as s:  # one was checked already
        s.execute(update(JobSource).where(JobSource.job_id == 2).values(last_seen_at=checked_since))
        s.commit()
    page = client.get("/").text
    assert 'class="job unchecked" id="job-1"' in page and 'class="job" id="job-2"' in page
    r = client.post("/results/new?since=30&cleared=0", data={"shown": "1,2,3"})
    assert 'data-unchecked="1,3"' in r.text
    app.state.services.runner.progress.checking = ()  # an Úřad práce ad has no page to check
    assert 'class="job unchecked"' not in client.get("/").text


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
    client.post("/scrape?fresh=1")  # starts over: the running one stops for it
    assert runner.restarts == 1 and runner.starts == 2 and runner.fresh

    app.state.services.runner.progress = Progress(
        running=False, phase="done", results={"mpsv": {"new": 5, "updated": 1, "removed": 2}}
    )
    r = client.get("/scrape/progress")
    assert r.headers["HX-Trigger"] == "jobs-updated"
    assert "5 nových, 1 změněných, 2 již neplatných" in r.text


def test_filters_from_url_and_facets_panel(app, client):
    app.state.services.settings.wizard_completed = True
    load_fixture_jobs(app)
    params = [("categories", "14"), ("salary_min", "25000"), ("sort", "salary"), ("bogus", "x")]
    r = client.get("/results", params=params, headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert r.headers["HX-Push-Url"].startswith("/?categories=14&salary_min=25000")
    assert 'hx-get="/facets?categories=14' in r.text  # filter counts refresh after results

    page = client.get("/", params=params)
    assert '<option value="salary" selected>' in page.text

    panel = client.get("/facets", params=params).text
    assert 'name="categories" value="14" checked' in panel
    assert 'name="source_off" value="mpsv" disabled' in panel  # every source on
    assert 'name="source_off" value="prace_cz" disabled' in panel  # no jobs from it yet
    assert 'value="linkedin"' not in panel  # off unless the user turns it on
    assert panel.index("Zdroje") < panel.index("Typ úvazku") < panel.index("Obor")
    assert panel.index("Obor") < panel.index("Kraj")
    off = client.get("/facets", params=[*params, ("source_off", "mpsv")]).text
    assert 'name="source_off" value="mpsv" >' in off
    r = client.get("/results", params={"source_off": "mpsv"}, headers={"HX-Request": "true"})
    assert r.headers["HX-Push-Url"] == "/?source_off=mpsv" and 'class="job"' not in r.text
    assert "Služby" in panel and "Plný úvazek" in panel
    assert '<option value="25000" selected>' in panel


def test_malformed_filters_are_ignored(app, client):
    app.state.services.settings.wizard_completed = True
    r = client.get("/", params={"salary_min": "abc", "sort": "drop table", "posted_days": "-1"})
    assert r.status_code == 200
    for value in ("²", "9" * 30, "1" * 5000):
        r = client.get("/results", params={"salary_min": value, "posted_days": value})
        assert r.status_code == 200


def test_empty_database_is_not_blamed_on_filters(client):
    r = client.get("/results?q=&sort=date", headers={"HX-Request": "true"})
    assert "Zatím tu nejsou žádné nabídky" in r.text
    r = client.get("/results?q=xyz", headers={"HX-Request": "true"})
    assert "Těmto filtrům neodpovídá" in r.text


def test_map_points_follow_filters(app, client, monkeypatch):
    from adawork.db.models import Location

    app.state.services.settings.wizard_completed = True
    load_fixture_jobs(app)
    with app.state.services.sessions() as s:  # coordinates normally come from RÚIAN
        for i, loc in enumerate(s.query(Location)):
            loc.lat, loc.lon = 50.0 + i / 100, 14.0 + i / 100
        s.commit()

    everything = client.get("/map/points").json()
    assert everything["type"] == "FeatureCollection"
    total = sum(f["properties"]["count"] for f in everything["features"])
    assert total == 30
    lon, lat = everything["features"][0]["geometry"]["coordinates"]
    assert 14 <= lon < 15 and 50 <= lat < 51

    some = client.get("/map/points", params={"q": "pokojska"}).json()
    assert 1 <= sum(f["properties"]["count"] for f in some["features"]) < total

    loc_id = some["features"][0]["properties"]["id"]
    popup = client.get(f"/map/location/{loc_id}", params={"q": "pokojska"}).text
    assert "Pokojská" in popup and "<html" not in popup

    busiest = max(everything["features"], key=lambda f: f["properties"]["count"])
    count = busiest["properties"]["count"]
    monkeypatch.setattr(maps, "POPUP_SIZE", 2)
    popup = client.get(f"/map/location/{busiest['properties']['id']}").text
    assert popup.count("<li>") == 2 and f"další nabídky: {count - 2}" in popup

    page = client.get("/", params={"view": "map"}).text
    assert 'document.body.dataset.view = "map"' in page and 'id="map"' in page


def test_ai_page_saves_the_choice_and_starts_the_download(app, client):
    app.state.services.settings.wizard_completed = True
    page = client.get("/ai").text
    assert "Umělá inteligence" in page and "integrovaná grafika" in page
    assert "Jednoduchý (Qwen3-VL 2B" in page and "Poběží plynule" in page
    form = {"choice": "simple", "model": "", "server_url": "javascript:alert(1)"}
    r = client.post("/ai", data=form, follow_redirects=False)
    assert r.headers["location"] == "/ai?saved=1"
    ai = app.state.services.settings.ai
    assert ai.choice == "simple" and ai.server_url == ""  # only http(s) URLs are kept
    assert app.state.services.ai.manager.downloads_started == 1
    client.post("/ai", data={"choice": "simple", "model": "gemma-4-e2b", "server_url": ""})
    assert app.state.services.settings.ai.choice == "gemma-4-e2b"  # "Pokročilé" wins
    assert "Stahuji" not in client.get("/ai/status").text


def test_ask_in_a_sentence(app, client, monkeypatch):
    from contextlib import contextmanager

    from adawork.ai.client import Reply, Timing

    app.state.services.settings.wizard_completed = True
    page = client.get("/").text  # no model yet: the box is disabled and asks again
    assert 'name="question" disabled' in page and 'hx-get="/ask"' in page
    assert "AI zatím není připravená" in page
    r = client.post("/ask", data={"question": "sklad u Kolína"})
    assert "model není stažený" in r.text

    class Answer:
        def chat(self, messages, schema=None, max_tokens=0, temperature=0):
            data = {"slova": "sklad", "obor": [], "kraj": ["Středočeský kraj"], "uvazek": [],
                    "smeny": ["Jednosměnný provoz"], "mzda_od": 30000, "vzdelani": None,
                    "dni": None}  # fmt: skip
            return Reply(data, Timing(100, 0.1, 30, 1.0, 1.1))

    @contextmanager
    def session(vision=False, priority=None):
        yield Answer()

    manager = app.state.services.ai.manager
    monkeypatch.setattr(manager, "session", session)
    monkeypatch.setattr(type(manager), "ready", property(lambda self: True))
    row = client.get("/ask").text  # the disabled box turns on without reloading the page
    assert 'name="question" maxlength' in row and 'hx-get="/ask"' not in row
    r = client.post("/ask", data={"question": "sklad u Kolína, jen ranní, aspoň 30 tisíc"})
    assert r.headers["HX-Redirect"] == (
        "/?q=sklad&shifts=jednoSm&kraj=St%C5%99edo%C4%8Desk%C3%BD+kraj&salary_min=30000"
    )


def test_downloaded_models_are_listed_and_deleted_only_on_request(app, client):
    from test_ai import fake_download

    app.state.services.settings.wizard_completed = True
    manager = app.state.services.ai.manager
    fake_download(manager, "qwen3-vl-2b", "gemma-4-e2b")
    manager.state.active = "gemma-4-e2b"
    page = client.get("/ai").text
    assert "Stažené modely" in page and "používá se" in page
    assert 'name="model_id" value="qwen3-vl-2b"' in page
    assert 'name="model_id" value="gemma-4-e2b"' not in page  # in use: no delete button
    client.post("/ai/delete", data={"model_id": "gemma-4-e2b"})
    client.post("/ai/delete", data={"model_id": "qwen3-vl-2b"})
    assert manager.downloaded() == {"gemma-4-e2b"}


def test_manual_import_of_text_and_of_a_photo(app, client):
    from io import BytesIO

    from PIL import Image

    app.state.services.settings.wizard_completed = True
    assert "Vložit inzerát" in client.get("/add").text
    assert "Vložte text inzerátu nebo obrázek" in client.post("/add", data={"text": " "}).text

    r = client.post("/add", data={"text": "Přijmeme skladníka do Kolína\nMzda 32 000 Kč"})
    assert "Uloženo" in r.text and "Přijmeme skladníka do Kolína" in r.text

    photo = BytesIO()
    Image.new("RGB", (800, 600), "white").save(photo, format="PNG")
    r = client.post("/add", files={"picture": ("plakat.png", photo.getvalue(), "image/png")})
    assert "Inzerát z obrázku" in r.text and "/images/" in r.text
    bad = client.post("/add", files={"picture": ("x.png", b"not an image", "image/png")})
    assert "Soubor není obrázek" in bad.text
    with app.state.services.sessions() as s:
        jobs = s.query(Job).all()
        assert sorted(j.title for j in jobs) == [
            "Inzerát z obrázku",
            "Přijmeme skladníka do Kolína",
        ]
        pictured = next(j for j in jobs if j.images)
        assert pictured.extra["title_from_picture"] and pictured.images[0].status == "stored"
        image_id = pictured.images[0].id
    assert client.get(f"/images/{image_id}").headers["content-type"] == "image/jpeg"
    assert client.get("/images/999").status_code == 404


# --- M7: settings, keys, saved searches, export ---------------------------------------------


def test_automatic_checks_are_switched_in_settings_without_a_restart(app, client):
    app.state.services.settings.wizard_completed = True
    page = client.get("/settings/automation").text
    assert "Automaticky hledat nové nabídky" in page and 'value="12" checked' in page
    assert "automatické hledání vypnuto" in client.get("/status").text

    form = {"enabled": "on", "interval_hours": "6", "start_with_windows": "on"}
    r = client.post("/settings/automation", data=form, follow_redirects=False)
    assert r.headers["location"] == "/settings/automation?saved=1"
    auto = app.state.services.settings.automation
    assert auto.enabled and auto.interval_hours == 6
    assert app.state.autostart == [True]  # starts with Windows while the checks are on
    # Never searched: due now (the scheduler thread starts it).
    assert app.state.services.scheduler.next_check() is not None
    app.state.services.runner.start()
    started = app.state.services.runner.started_at
    assert app.state.services.scheduler.next_check() == started + timedelta(hours=6)

    client.post("/settings/pause")
    assert "pozastaveno do" in client.get("/status").text
    assert "Pokračovat" in client.get("/settings/automation").text
    client.post("/settings/resume")
    assert "další hledání" in client.get("/status").text

    form = {"interval_hours": "24"}  # switched off: no start with Windows
    client.post("/settings/automation", data=form)
    assert not auto.enabled and app.state.autostart[-1] is False
    assert app.state.services.scheduler.next_check() is None


def test_settings_list_the_wizard_steps_in_order(app, client):
    app.state.services.settings.wizard_completed = True
    page = client.get("/settings").text
    assert 'href="/wizard/welcome"' in page and "Spustit průvodce znovu" in page
    catalog = page.split('class="catalog"')[1].split("</ul>")[0]
    pages = re.findall(r'<li><a href="([^"]+)"', catalog)
    assert pages == [
        "/settings/appearance",
        "/settings/profile",
        "/ai",
        "/sources",
        "/settings/keys",
        "/settings/automation",
    ]
    assert "Čeština · Podle Windows" in page and "domov nezadán" in page
    for href in pages:
        assert 'href="/settings" class="back' in client.get(href).text
    assert client.get("/settings/nothing", follow_redirects=False).status_code == 303

    assert 'hx-post="/settings/appearance"' in client.get("/settings/appearance").text
    profile = app.state.services.settings.profile
    profile.kinds_of_work = ["skladník"]  # asked by the wizard only
    assert 'name="kinds_of_work"' not in client.get("/settings/profile").text
    form = {"home_address": "Kolín", "travel_mode": "car", "max_minutes": "30"}
    r = client.post("/settings/profile", data=form, follow_redirects=False)
    assert r.headers["location"] == "/settings/profile?saved=1"
    assert (profile.home_address, profile.travel_mode, profile.max_minutes) == ("Kolín", "car", 30)
    assert profile.kinds_of_work == ["skladník"]
    assert "Kolín · Auto · nejvýš 30 min" in client.get("/settings").text
    assert client.post("/settings/profile", data={"max_minutes": "999"}).status_code == 422


def test_api_keys_go_to_the_credential_store_and_switch_their_source(app, client):
    app.state.services.settings.wizard_completed = True
    assert "chybí klíč" in client.get("/sources").text
    r = client.post("/settings/keys", data={"key_jooble": "bad key!"})
    assert "Klíč Jooble vypadá neplatně" in r.text
    assert app.state.services.secrets.get("jooble") is None

    r = client.post(
        "/settings/keys", data={"key_jooble": " 1234-abcd-5678 "}, follow_redirects=False
    )
    assert r.headers["location"] == "/settings/keys?saved=1"
    assert app.state.services.secrets.get("jooble") == "1234-abcd-5678"
    assert app.state.services.settings.sources["jooble"] is True
    assert "1234-abcd-5678" not in Path(app.state.services.settings._path).read_text(
        encoding="utf-8"
    )
    assert app.state.services.runner.api_key("jooble") == "1234-abcd-5678"
    page = client.get("/settings/keys").text
    assert "klíč uložen (…5678)" in page and "1234-abcd-5678" not in page
    assert "Jooble" in client.get("/settings").text  # listed as saved in the catalog

    client.post("/settings/keys", data={"key_jooble": "", "remove_jooble": "on"})
    assert app.state.services.secrets.get("jooble") is None
    assert app.state.services.settings.sources["jooble"] is False


def test_saved_searches_count_new_matches_until_opened(app, client):
    app.state.services.settings.wizard_completed = True
    jobs = fixture_jobs()
    store_jobs(app, jobs[:10])
    assert "Zatím žádná" in client.get("/saved").text

    form = {"q": "", "sort": "date", "view": "list", "saved_name": "Všechno"}
    r = client.post("/saved", data=form)
    assert "Všechno" in r.text and "nové:" not in r.text
    with app.state.services.sessions() as s:
        saved = s.scalar(select(SavedSearch))
        assert saved.query == "q=&sort=date" and saved.notify
    store_jobs(app, jobs[10:13])
    assert "nové: 3" in client.get("/saved").text

    r = client.get(f"/saved/{saved.id}", follow_redirects=False)
    assert r.headers["location"] == "/?q=&sort=date"
    assert "nové:" not in client.get("/saved").text  # opened: seen

    r = client.post(f"/saved/{saved.id}/notify")
    assert "(vypnuto)" in r.text
    client.post(f"/saved/{saved.id}/delete")
    assert "Zatím žádná" in client.get("/saved").text

    r = client.post("/saved", data={"q": "pokojská"})  # no name typed: the words
    assert "pokojská" in r.text


def test_the_list_downloads_as_csv_and_excel(app, client):
    app.state.services.settings.wizard_completed = True
    load_fixture_jobs(app)
    with app.state.services.sessions() as s:
        s.execute(update(Job).where(Job.id == 1).values(title="=HYPERLINK(1)"))
        s.execute(update(Job).where(Job.id == 2).values(employer_name="\t=1+1"))
        s.commit()
    r = client.get("/export.csv")
    assert r.headers["content-disposition"].startswith('attachment; filename="adawork-')
    text = r.content.decode("utf-8-sig")
    lines = text.splitlines()
    assert lines[0].startswith("Pozice;Zaměstnavatel;Místo;Kraj;Mzda")
    assert len(lines) == 31 and "'=HYPERLINK(1)" in text and "'\t=1+1" in text
    assert "up.gov.cz/volna-mista-v-cr" in text

    r = client.get("/export.xlsx", params={"q": "pokojska"})
    sheet = openpyxl.load_workbook(io.BytesIO(r.content)).active
    rows = list(sheet.iter_rows(values_only=True))
    assert rows[0][0] == "Pozice" and len(rows) == 2 and rows[1][0] == "POKOJSKÁ/-Ý"
    assert client.get("/export.pdf").status_code == 404
