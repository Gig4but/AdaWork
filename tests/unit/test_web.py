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
        self.scope_changes = 0
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

    def scope_changed(self) -> None:
        self.scope_changes += 1

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
        routing_updates=False,
        employer_updates=False,
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
    assert "Všechny nabídky práce ve vašem okolí" in welcome
    assert re.search(r'value="system"[^>]*checked', welcome)
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
    assert r.headers["location"] == "/wizard/automation"
    assert client.get("/wizard/keys", follow_redirects=False).status_code == 303  # no step
    page = client.get("/wizard/automation").text
    assert 'href="/wizard/sources"' in page
    assert 'name="enabled" >' in page or 'name="enabled">' in page  # off by default
    assert 'value="12" checked' in page
    form = {"interval_hours": "12", "start_with_windows": "on", "notifications": "on"}
    r = client.post("/wizard/automation", data=form, follow_redirects=False)
    assert r.headers["location"] == "/wizard/map"
    page = client.get("/wizard/map").text
    assert "Stáhnout mapu" in page and "Přeskočit" in page  # nothing downloads unasked
    r = client.post("/wizard/map", data={"routing_area": "auto"}, follow_redirects=False)
    assert r.headers["location"] == "/wizard/employers"
    assert not app.state.services.settings.routing_download
    r = client.post("/wizard/map", data={"routing_area": "home", "download": "1"},
                    follow_redirects=False)  # fmt: skip
    assert r.headers["location"] == "/wizard/map"  # stays to show the progress
    page = client.get("/wizard/map").text
    assert "Stáhnout mapu" not in page and 'id="routing-status"' in page
    page = client.get("/wizard/employers").text
    assert re.search(r'name="auto_check"\s*>', page)  # off by default
    assert re.search(r'name="atmoskop"\s+checked', page)
    r = client.post("/wizard/employers", data={"auto_check": "on", "atmoskop": "on"},
                    follow_redirects=False)  # fmt: skip
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
    assert saved.routing_download and saved.routing_area == "home"
    assert saved.employers.auto_check and not saved.employers.mentions


def test_wizard_asks_for_no_keys_or_sign_ins_and_keeps_those_sources(app, client):
    svc = app.state.services
    svc.settings.wizard_completed = True
    svc.settings.sources.update(jooble=True, facebook=True)  # set on the Sources page
    page = client.get("/wizard/sources").text
    assert 'name="source_prace_cz"' in page and 'name="source_linkedin"' in page
    for hidden in ("jooble", "careerjet", "facebook", "instagram"):
        assert f'name="source_{hidden}"' not in page
    assert "Přihlásit se" not in page and "/settings/keys" not in page
    r = client.post("/wizard/sources", data={"source_mpsv": "on"}, follow_redirects=False)
    assert r.headers["location"] == "/wizard/automation"
    sources = svc.settings.sources
    assert sources["jooble"] and sources["facebook"] and not sources["prace_cz"]
    assert sources["instagram"] is False and sources["careerjet"] is False
    assert svc.runner.scope_changes == 1

    svc.settings.employers.google = True  # needs a key: set in Settings only
    page = client.get("/wizard/employers").text
    assert 'name="atmoskop"' in page and 'name="google"' not in page
    client.post("/wizard/employers", data={"atmoskop": "on"})
    assert svc.settings.employers.google and not svc.settings.employers.mentions
    assert 'name="google"' in client.get("/settings/employers").text


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
    assert r.text.count('class="job arrived"') == 5
    assert order(r.text, range(6, 11)) == [6, 7, 8, 9, 10]
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
    page = client.get("/").text  # no model yet: no box, a line that asks again
    assert 'name="question"' not in page and 'hx-get="/ask"' in page
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


def test_settings_end_with_the_license_texts(client, monkeypatch):
    from adawork import licenses

    files = (
        licenses.LicenseFile("notices", "THIRD-PARTY-NOTICES.txt", text="all of them"),
        licenses.LicenseFile("python", "demo-1.0 / LICENSE", text="<b>MIT</b> & co"),
    )
    monkeypatch.setattr(licenses, "files", lambda: files)
    settings = client.get("/settings").text
    assert settings.index('href="/licenses"') > settings.index("Záznamy (logy)")  # at the end
    page = client.get("/licenses").text
    assert "Přehled" in page and "Balíčky Pythonu" in page and "demo-1.0 / LICENSE" in page
    assert 'hx-get="/licenses/1"' in page
    assert client.get("/licenses/1").text == "<pre>&lt;b&gt;MIT&lt;/b&gt; &amp; co</pre>"
    assert client.get("/licenses/2").status_code == 404
    assert client.get("/licenses/-1").status_code == 422


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
        "/settings/map",
        "/settings/employers",
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


def test_the_kinds_of_work_fill_the_search(app, client):
    app.state.services.settings.wizard_completed = True
    store_jobs(app, fixture_jobs()[:10])
    app.state.services.settings.profile.kinds_of_work = ["skladník", "řidič"]
    page = client.get("/").text
    assert 'name="q" value="skladník, řidič"' in page
    assert 'name="q" value=""' in client.get("/?q=").text  # cleared by the user


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
    assert r.headers["location"] == "/?q=&sort=date&at="  # as counted: no start point
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


# --- Commute (M8) --------------------------------------------------------------------------------


def commute_jobs(app, municipalities) -> dict[str, int]:
    """Jobs in Kolín (home), Pardubice (~41 km) and Praha (~55 km); title -> id."""
    from adawork.core import ParsedJob, ParsedLocation

    places = {"Kolín": (50.031, 15.201), "Pardubice": (50.04, 15.78), "Praha": (50.08, 14.43)}
    with app.state.services.sessions() as s:
        municipalities(s)
        store = JobStore(s, "x")
        for name, (lat, lon) in places.items():
            location = ParsedLocation(
                key=f"t:{name}", precision="address", lat=lat, lon=lon, municipality=name
            )
            store.add(ParsedJob(external_id=name, title=f"Skladník {name}", location=location))
        store.commit()
        return {j.title: j.id for j in s.scalars(select(Job))}


def map_imported_once(app) -> None:
    """The map was imported, but the data is being imported again: times are estimates."""
    app.state.services.settings.routing_download = True
    app.state.services.routing.state.update(version=1, usable=False)


def test_travel_times_are_estimates_until_the_map_is_downloaded(app, client, municipalities):
    settings = app.state.services.settings
    settings.wizard_completed = True
    settings.profile.home_address, settings.profile.max_minutes = "Kolín", 45
    ids = commute_jobs(app, municipalities)
    page = client.get("/").text
    assert "odhad vzdušnou čarou" in page and 'href="/settings/map">Stáhnout mapu' in page
    assert 'name="at" value="50.0300,15.2000"' in page and 'value="travel"' in page
    assert "Skladník Kolín" in page and "Skladník Praha" not in page  # ~55 km: over 45 min
    assert 'class="tt est"' in page
    wide = client.get("/results", params={"at": "50.0300,15.2000", "max_min": "120"}).text
    assert "Skladník Praha" in wide
    params = {"at": "50.0300,15.2000", "from": "Kolín", "mode": "car"}
    window = client.get(f"/commute/job/{ids['Skladník Pardubice']}", params=params).text
    assert "Odhadem asi" in window and 'href="/settings/map">Stáhnout mapu' in window

    assert "Nestaženo" in client.get("/settings").text
    page = client.get("/settings/map").text
    assert "Stáhnout mapu" in page and "Aktualizovat teď" not in page
    r = client.post("/settings/map/update", follow_redirects=False)
    assert r.headers["location"] == "/settings/map" and not settings.routing_download
    r = client.post("/settings/map", data={"routing_area": "auto", "download": "1"},
                    follow_redirects=False)  # fmt: skip
    assert settings.routing_download and r.headers["location"] == "/settings/map?saved=1"
    page = client.get("/settings/map").text
    assert "Stáhnout mapu" not in page and "Aktualizovat teď" in page
    assert "Zatím nejsou připravené" in page and 'hx-trigger="every 3s"' in page
    assert "odhad vzdušnou čarou" in client.get("/").text  # until the import is done


def test_travel_time_from_home_before_the_map_data_is_ready(app, client, municipalities):
    settings = app.state.services.settings
    settings.wizard_completed = True
    settings.profile.home_address, settings.profile.max_minutes = "Kolín", 45
    ids = commute_jobs(app, municipalities)
    map_imported_once(app)
    page = client.get("/").text
    # The bar starts from the profile; times are estimates until the import is done.
    assert 'name="at" value="50.0300,15.2000"' in page and 'value="Kolín"' in page
    assert "Doba cesty je zatím odhad" in page
    assert "Skladník Kolín" in page and "Skladník Praha" not in page  # ~55 km: over 45 min
    assert 'class="tt est"' in page and "odhad" in page
    wide = client.get("/results", params={"at": "50.0300,15.2000", "max_min": "120"}).text
    assert "Skladník Praha" in wide
    off = client.get("/results", params={"at": "50.0300,15.2000", "max_min": "0"}).text
    assert "Skladník Praha" in off  # no limit
    params = {"at": "50.0300,15.2000", "sort": "travel", "max_min": "120"}
    by_time = client.get("/results", params=params).text
    assert order(by_time, [ids["Skladník Kolín"], ids["Skladník Pardubice"]]) == [
        ids["Skladník Kolín"], ids["Skladník Pardubice"]
    ]  # fmt: skip

    window = client.get(f"/commute/job/{ids['Skladník Pardubice']}",
                        params={"at": "50.0300,15.2000", "from": "Kolín", "mode": "car",
                                "arrive": ""}).text  # fmt: skip
    assert "Jak se tam dostanu" in window and "Odhadem asi" in window
    assert "mapy.com" in window and "google.com/maps" in window
    assert "Spojenka" in window  # timetable licence and map attribution
    no_start = client.get(f"/commute/job/{ids['Skladník Praha']}", params={"at": ""}).text
    assert "Nejdřív zadejte, odkud dojíždíte" in no_start and "Odhadem" not in no_start

    area = client.get("/commute/area", params={"at": "50.0300,15.2000", "mode": "car",
                                               "max_min": "20"}).json()  # fmt: skip
    assert area["estimate"] and area["bucket"] == 30 and area["features"]
    assert client.get("/commute/locate", params={"text": "Kolín"}).json()["at"] == "50.0300,15.2000"
    assert client.get("/commute/locate", params={"text": "Nikde"}).status_code == 404
    assert "Kolín" in client.get("/commute/places", params={"from": "Kol"}).text
    style = client.get("/map/style.json", follow_redirects=False)
    assert style.status_code == 307 and "openfreemap" in style.headers["location"]  # online map

    assert "Zatím nejsou připravené" in client.get("/settings/map").text
    r = client.post("/settings/map", data={"routing_area": "home"}, follow_redirects=False)
    assert r.headers["location"] == "/settings/map?saved=1" and settings.routing_area == "home"


def test_the_bar_takes_a_saved_profile(app, client, municipalities):
    app.state.services.settings.wizard_completed = True
    commute_jobs(app, municipalities)
    form = {"home_address": "Kolín", "travel_mode": "car", "max_minutes": "60",
            "depart_time": "06:30"}  # fmt: skip
    client.post("/settings/profile", data=form)
    assert client.get("/commute/defaults").json() == {
        "from": "Kolín", "at": "50.0300,15.2000", "mode": "car", "max_min": "60",
        "depart": "06:30",
    }  # fmt: skip
    client.post("/settings/profile", data=form | {"home_address": "Nikde"})
    assert client.get("/commute/defaults").json()["at"] == ""  # not found: no start point
    client.post("/settings/profile", data=form | {"home_address": ""})
    assert client.get("/commute/defaults").json() == {
        "from": "", "at": "", "mode": "any", "max_min": "0", "depart": "07:00",
    }  # fmt: skip
    assert "/commute/defaults" in client.get("/static/commute.js").text


def test_the_bar_takes_the_wizard_profile_before_the_towns_are_known(app, client, municipalities):
    app.state.services.settings.wizard_completed = True
    form = {"home_address": "Kolín", "travel_mode": "car", "max_minutes": "60",
            "depart_time": "06:30"}  # fmt: skip
    client.post("/wizard/profile", data=form)
    page = client.get("/").text  # the first search has not filled the towns yet
    assert re.search(r'<input name="from"[^>]*value="Kolín"', page)
    assert 'name="at" value=""' in page
    assert '<option value="car" selected>' in page
    assert re.search(r'name="max_min"[^>]*value="60"', page)
    assert 'name="depart" value="06:30"' in page
    empty = client.get("/results", headers={"HX-Request": "true"}).text
    assert "Zatím tu nejsou žádné nabídky" in empty  # no start point yet: not a filter
    with app.state.services.sessions() as s:
        municipalities(s)
    no_start = "Zadejte, odkud dojíždíte"
    htmx = {"HX-Request": "true"}
    form = {"from": "Kolín", "at": "", "mode": "car"}  # the bar as the page has it
    found = client.get("/results", params=form, headers=htmx).text
    assert no_start not in found
    assert 'id="origin" hidden data-at="50.0300,15.2000" data-from="Kolín"' in found
    by_time = client.get("/", params=form | {"sort": "travel"}).text
    assert 'value="travel" selected' in by_time
    assert 'name="at" value="50.0300,15.2000"' in client.get("/").text
    cleared = form | {"from": ""}
    assert no_start in client.get("/results", params=cleared, headers=htmx).text


def test_saving_what_is_searched_for_restarts_a_running_search(app, client):
    svc = app.state.services
    svc.settings.wizard_completed = True
    client.post("/settings/profile", data={"home_address": "Kolín"})
    client.post("/sources", data={"source_mpsv": "on"})
    client.post("/settings/keys", data={"key_jooble": ""})
    client.post("/wizard/profile", data={"home_address": "Kolín"})
    assert svc.runner.scope_changes == 4


def test_profile_keeps_the_departure_time(app, client):
    app.state.services.settings.wizard_completed = True
    client.post("/settings/profile", data={"home_address": "Kolín", "depart_time": "06:15"})
    assert app.state.services.settings.profile.depart_time == "06:15"
    client.post("/settings/profile", data={"home_address": "Kolín", "depart_time": "25:00"})
    assert app.state.services.settings.profile.depart_time == "06:15"  # invalid: kept
    assert 'name="depart_time" value="06:15"' in client.get("/settings/profile").text


def test_the_travel_note_and_saved_searches_follow_the_start_point(app, client, municipalities):
    app.state.services.settings.wizard_completed = True
    commute_jobs(app, municipalities)
    map_imported_once(app)
    page = client.get("/").text
    assert "Zadejte, odkud dojíždíte" in page and 'value="travel" selected' not in page
    near = {"at": "50.0300,15.2000", "from": "Kolín", "max_min": "30", "sort": "date"}
    results = client.get("/results", params=near, headers={"HX-Request": "true"}).text
    assert 'id="commute-state" class="muted" hx-swap-oob="true"' in results
    assert "Doba cesty je zatím odhad" in results

    from adawork.core import ParsedJob, ParsedLocation

    client.post("/saved", data={**near, "view": "list", "saved_name": "Kolín"})
    with app.state.services.sessions() as s:  # a new place near the start, found later
        store = JobStore(s, "x")
        location = ParsedLocation(key="t:Velim", precision="address", lat=50.06,
                                  lon=15.15, municipality="Velim")  # fmt: skip
        store.add(ParsedJob(external_id="Velim", title="Skladník Velim", location=location))
        store.commit()
    assert "nové: 1" in client.get("/saved").text

    # Saved without a start point: counted and opened without the one from the profile.
    app.state.services.settings.profile.home_address = "Kolín"
    client.post("/saved", data={**near, "at": "", "from": "", "saved_name": "Všude"})
    with app.state.services.sessions() as s:
        saved = s.scalar(select(SavedSearch).where(SavedSearch.name == "Všude"))
    assert "at=&" in saved.query or saved.query.endswith("at=")
    page = client.get(f"/saved/{saved.id}").text
    assert 'name="at" value=""' in page


def test_times_of_saved_start_points_are_kept(app, client, municipalities, monkeypatch):
    monkeypatch.setattr("adawork.routing.commute.IN_USE", 0)  # all asked for long ago
    from adawork.db.models import TravelSet
    from adawork.routing.commute import KEEP_STARTS

    app.state.services.settings.wizard_completed = True
    commute_jobs(app, municipalities)
    map_imported_once(app)
    client.post("/saved", data={"at": "50.0300,15.2000", "max_min": "30", "saved_name": "Kolín"})
    for i in range(KEEP_STARTS + 2):  # other start points tried since
        client.get("/results", params={"at": f"50.{i + 10:02d}00,15.0000", "max_min": "30"})
    with app.state.services.sessions() as s:
        origins = set(s.scalars(select(TravelSet.origin)))
    assert "50.0300,15.2000" in origins and len(origins) == KEEP_STARTS + 1


def test_other_sites_cannot_start_work_through_the_app(app, client):
    app.state.services.settings.wizard_completed = True
    cross = {"Sec-Fetch-Site": "cross-site"}
    assert (
        client.get("/commute/area", params={"at": "50.0300,15.2000"}, headers=cross).status_code
        == 403
    )
    assert client.get("/", headers=cross).status_code == 403
    assert client.get("/static/app.css", headers=cross).status_code == 200
    assert client.get("/", headers={"Sec-Fetch-Site": "none"}).status_code == 200  # typed in


# --- M9: employer cards -------------------------------------------------------------------------


def employer_jobs(app):
    """Two jobs: Penny (checked, Atmoskop 62 %, an AI summary) and an uncertain pub."""
    from adawork.core import ParsedJob
    from adawork.db.models import Employer, EmployerReview, EmployerSummary

    now = datetime(2026, 10, 14, 12, 0)
    with app.state.services.sessions() as s:
        store = JobStore(s, "x")
        store.add(
            ParsedJob(
                external_id="p",
                title="Prodavač Penny",
                employer_name="Penny Market s.r.o.",
                employer_ico="64945880",
            )
        )
        store.add(ParsedJob(external_id="u", title="Číšník", employer_name="Hospoda U Lípy"))
        store.commit()
        penny = Employer(
            ico="64945880",
            name="Penny Market s.r.o.",
            match="ad",
            dic="CZ64945880",
            founded=datetime(1996, 1, 1).date(),
            employee_band="470",
            checked_at=now,
            satisfaction=62,
            reviews_at=now,
            mentions_at=now,
            updated_at=now,
            links={"atmoskop": "https://www.atmoskop.cz/nazory-na-zamestnavatele/1-penny"},
        )
        pub = Employer(
            name="Hospoda U Lípy",
            match="uncertain",
            updated_at=now,
            candidates=[
                {
                    "ico": "12345679",
                    "name": "Hostinec u Lípy, s.r.o.",
                    "seat": "Praha",
                    "band": "120",
                },
            ],
        )
        s.add_all([penny, pub])
        s.flush()
        s.add(
            EmployerReview(
                employer_id=penny.id,
                source="atmoskop",
                rating=62,
                scale="percent",
                count=861,
                scores={"colleagues": 71, "boss": 62, "suitable_for": []},
                texts=[{"plus": "Dobrý kolektiv", "minus": "Přesčasy", "date": "2025"}],
                url="https://www.atmoskop.cz/nazory-na-zamestnavatele/1-penny",
                fetched_at=now,
            )
        )
        s.add(
            EmployerSummary(
                employer_id=penny.id,
                inputs="x",
                model="test",
                prompt_version=1,
                created_at=now,
                summary={
                    "praise": [{"text": "Dobrý kolektiv.", "sources": [1]}],
                    "criticism": [],
                    "warnings": [],
                },
                sources=[{"source": "atmoskop", "url": "https://www.atmoskop.cz/r"}],
            )
        )
        for job in s.scalars(select(Job)):
            job.employer_id = penny.id if job.title.startswith("Prodavač") else pub.id
        penny.jobs_count, pub.jobs_count = 1, 1
        s.commit()
        return penny.id, pub.id


def test_employer_card_from_the_job_list(app, client):
    app.state.services.settings.wizard_completed = True
    penny, pub = employer_jobs(app)
    page = client.get("/").text
    assert f'class="link employer-open" data-employer="{penny}"' in page
    assert '<span class="emp-badge mid"' in page and "62 %" in page  # Atmoskop satisfaction
    assert 'id="employer-body"' in page and "/static/employer.js" in page

    card = client.get(f"/employer/{penny}").text
    assert "IČO 64945880" in card and "Bez insolvence" in card and "Spolehlivý plátce DPH" in card
    assert "5 000–9 999 zaměstnanců" in card and "861 hodnocení" in card
    assert 'href="https://www.atmoskop.cz/r"' in card and ">[1]</a>" in card  # cited point
    assert "Shrnutí od AI" in card and f'href="/?q=&employer={penny}"' in card
    assert "ares.gov.cz/ekonomicke-subjekty?ico=64945880" in card
    assert 'hx-trigger="load delay:2s"' not in card  # nothing being fetched in tests

    unsure = client.get(f"/employer/{pub}").text
    assert "Nejisté přiřazení" in unsure and "Hostinec u Lípy, s.r.o." in unsure
    assert client.get("/employer/99999").status_code == 200  # gone: a short note
    assert client.get(f"/employer/{2**64}").status_code == 422  # not a number SQLite keeps

    # While a refresh runs, the card asks for the step line only; at the end, the card.
    app.state.services.employers.refreshing[penny] = "reviews"
    step = client.get(f"/employer/{penny}", params={"poll": "1"})
    assert 'id="employer-step"' in step.text and "IČO" not in step.text
    assert "HX-Retarget" not in step.headers
    del app.state.services.employers.refreshing[penny]
    done = client.get(f"/employer/{penny}", params={"poll": "1"})
    assert "IČO 64945880" in done.text and done.headers["HX-Retarget"] == "#employer-body"


def test_employer_match_corrected_by_ico(app, client):
    app.state.services.settings.wizard_completed = True
    penny, pub = employer_jobs(app)
    for typo in ("123", "64945881"):  # not an IČO; a wrong check digit
        r = client.post(f"/employer/{pub}/correct", data={"ico": typo})
        assert 'aria-invalid="true"' in r.text
    r = client.post(f"/employer/{pub}/correct", data={"ico": "6494 5880"})
    assert "IČO 64945880" in r.text and r.headers["HX-Trigger"] == "jobs-updated"
    with app.state.services.sessions() as s:
        assert {j.employer_id for j in s.scalars(select(Job))} == {penny}


def test_employer_filters_in_the_panel(app, client):
    app.state.services.settings.wizard_completed = True
    penny, _ = employer_jobs(app)
    panel = client.get("/facets", params={"employer": penny}).text
    assert "Jen nabídky firmy <strong>Penny Market s.r.o.</strong>" in panel
    assert 'name="emp_min"' in panel and 'name="emp_ok"' in panel
    results = client.get("/results", params={"emp_min": "60"}, headers={"HX-Request": "true"})
    assert "Prodavač Penny" in results.text and "Číšník" not in results.text
    page = client.get("/", params={"sort": "employer"}).text
    assert 'value="employer" selected' in page
    gone = client.get("/facets", params={"employer": 99999}).text  # merged away since
    assert "(už není v seznamu)" in gone and "zrušit" in gone


def test_google_key_and_employer_sources_in_settings(app, client):
    app.state.services.settings.wizard_completed = True
    page = client.get("/settings/keys").text
    assert "Klíč Google Maps (hodnocení zákazníků)" in page and "0 z 950" in page
    client.post("/settings/keys", data={"key_google_places": "AIza-test-1234"})
    assert app.state.services.secrets.get("google_places") == "AIza-test-1234"
    assert "google_places" not in app.state.services.settings.sources  # not a job source
    r = client.post("/settings/employers", data={"atmoskop": "on"}, follow_redirects=False)
    assert r.headers["location"] == "/settings/employers?saved=1"
    cfg = app.state.services.settings.employers
    assert cfg.atmoskop and not cfg.mentions and not cfg.google and not cfg.auto_check
    assert "Zaměstnavatelů v nabídkách" in client.get("/settings/employers").text
    assert "při otevření karty · Atmoskop" in client.get("/settings").text
    client.post("/settings/employers", data={"auto_check": "on", "atmoskop": "on"})
    assert cfg.auto_check and "automaticky · Atmoskop" in client.get("/settings").text
