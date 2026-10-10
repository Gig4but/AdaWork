"""Facebook and Instagram reader (adawork/social) with a fake browser on hand-made pages."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from adawork.config import SocialSettings
from adawork.core import ParsedJob, ScrapeQuery
from adawork.db.models import Job, JobSource, SourceState
from adawork.db.session import make_engine, make_sessionmaker, migrate
from adawork.geo.places import PlaceResolver
from adawork.scraper.dedup import DedupIndex
from adawork.scraper.runner import ScrapeRunner, purge_expired
from adawork.scraper.store import JobStore
from adawork.social import browser
from adawork.social import posts as parse
from adawork.social.reader import (
    BLOCK_PAUSE,
    Facebook,
    Instagram,
    SocialState,
    run_session,
)

PAGES = Path(__file__).parents[1] / "fixtures" / "social"
NOON = datetime(2026, 10, 9, 12, 0)
QUERY = ScrapeQuery(kraje=["Středočeský kraj"], home="Kolín")


def page(name: str) -> str:
    return (PAGES / f"{name}.html").read_text(encoding="utf-8")


class FakeBrowser:
    """Serves hand-made pages by URL prefix; records what was opened."""

    def __init__(self, routes: dict[str, str]) -> None:
        self.routes, self.opened, self.closed = routes, [], False
        self._url, self._html = "", ""

    def goto(self, url: str) -> None:
        self.opened.append(url)
        self._url = url
        prefix = max((p for p in self.routes if url.startswith(p)), key=len, default=None)
        name = self.routes.get(prefix, "")
        if name.startswith("redirect:"):
            self._url, name = name[len("redirect:") :], ""
        self._html = page(name) if name else "<html><body></body></html>"

    def scroll(self) -> None: ...
    def expand(self) -> None: ...

    def html(self) -> str:
        return self._html

    def url(self) -> str:
        return self._url

    def close(self) -> None:
        self.closed = True


def quiet(source):
    source.sleep = lambda s: None
    return source


FB_ROUTES = {
    "https://www.facebook.com/groups/joins/": "facebook_joins",
    "https://www.facebook.com/search/groups/": "facebook_groups",
    "https://www.facebook.com/groups/brigadykolin/": "facebook_group",
    "https://www.facebook.com/marketplace/search/": "marketplace",
}


# --- parsing ---------------------------------------------------------------------------------


def test_facebook_feed_posts_without_comments_or_one_word_posts():
    posts = parse.facebook_feed(page("facebook_group"), "u", "Brigády Kolín a okolí")
    assert [p.post_id for p in posts] == ["1234567890123", "2222222222"]
    first = posts[0]
    assert first.text.startswith("Přijmeme skladníka na HPP do Kolína.")
    assert "Komentář" not in first.text
    assert first.url == "https://www.facebook.com/groups/brigadykolin/posts/1234567890123/"
    assert first.images == ["https://scontent.fprg1-1.fna.fbcdn.net/v/t39/poster.jpg"]


def test_groups_marketplace_and_instagram_pages():
    groups = {g.name: g for g in parse.facebook_groups(page("facebook_groups"))}
    assert groups["Brigády Kolín a okolí"].public is True
    assert groups["Práce Kolín - nabídky"].public is False
    assert groups["Práce Kolín - nabídky"].url == "https://www.facebook.com/groups/12345/"
    assert "Vaše skupiny" not in groups
    (item,) = parse.marketplace_items(page("marketplace"))
    assert item.post_id == "mp555666777" and item.text == "Brigáda na stavbě\n180 Kč\nKolín"
    assert parse.instagram_grid(page("instagram_tag")) == [
        "https://www.instagram.com/p/CxYz123_ab/",
        "https://www.instagram.com/reel/Rr987/",
    ]
    post = parse.instagram_post(page("instagram_post"), "https://www.instagram.com/p/CxYz123_ab/")
    assert post.text == "Hledáme posilu do pekárny v Kolíně! Ranní směny, 190 Kč/h. #pracekolin"
    assert post.posted_at == datetime(2026, 10, 8, 7, 30)
    assert post.images == ["https://scontent.cdninstagram.com/v/post.jpg"]


def test_blocks_verifications_and_expired_logins_stop_reading():
    with pytest.raises(parse.SocialBlocked):
        parse.check_page("https://www.facebook.com/groups/1/", page("facebook_blocked"))
    with pytest.raises(parse.SocialBlocked):
        parse.check_page("https://www.facebook.com/checkpoint/123/", "")
    with pytest.raises(parse.LoginNeeded):
        parse.check_page("https://www.instagram.com/accounts/login/?next=/explore/", "")
    parse.check_page("https://www.facebook.com/groups/1/", page("facebook_group"))


def test_a_post_quoting_a_block_message_does_not_stop_reading():
    feed = page("facebook_group")
    for text in parse._BLOCK_TEXTS:
        parse.check_page("https://www.facebook.com/groups/1/", feed.replace("ranní směny", text))
        parse.check_page(
            "https://www.instagram.com/p/x/", f"<html><body><article>{text}</article></body></html>"
        )
    dialog = '<div role="dialog"><h2>You’re temporarily blocked</h2></div><div role="feed">'
    with pytest.raises(parse.SocialBlocked):
        parse.check_page(
            "https://www.facebook.com/groups/1/", feed.replace('<div role="feed">', dialog)
        )
    with pytest.raises(parse.SocialBlocked):
        parse.check_page("https://www.instagram.com/", "<title>Try again later</title>")


# --- budget ----------------------------------------------------------------------------------


def test_sessions_only_in_daytime_twice_a_day_three_hours_apart():
    state = SocialState()
    assert state.may_start(NOON.replace(hour=6), 100) == "night"
    assert state.may_start(NOON, 100) is None
    state.sessions, state.last_session = 1, NOON.isoformat()
    assert state.may_start(NOON + timedelta(hours=1), 100) == "too_soon"
    assert state.may_start(NOON + timedelta(hours=4), 100) is None
    state.sessions = 2
    assert state.may_start(NOON + timedelta(hours=5), 100) == "daily_limit"
    assert state.may_start(NOON + timedelta(days=1), 100) is None  # a new day
    state.pause(NOON, "blocked")
    assert state.may_start(NOON + timedelta(days=6), 100) == "blocked"
    assert state.may_start(NOON + BLOCK_PAUSE + timedelta(hours=1), 100) is None


def test_a_facebook_session_reads_half_the_daily_budget_and_finds_groups():
    browser = FakeBrowser(FB_ROUTES)
    state = SocialState()
    settings = SocialSettings(facebook_per_day=10)
    result = run_session(
        quiet(Facebook()), lambda: browser, state, QUERY, settings, lambda i: False, lambda: NOON
    )
    assert browser.closed and state.sessions == 1
    assert [g["name"] for g in state.groups] == ["Brigády Kolín a okolí"]  # joined, job words
    assert [g["name"] for g in state.suggested] == ["Práce Kolín - nabídky"]  # private: suggested
    assert {p.post_id for p in result.posts} == {"1234567890123", "2222222222", "mp555666777"}
    assert state.read == 3 and result.stopped == ""
    # Nothing is clicked or sent: only pages are opened.
    assert not any("join" in u and "joins" not in u for u in browser.opened)
    # The group list is reused for a week; a second session the same day is too soon.
    assert run_session(
        Facebook(), lambda: browser, state, QUERY, settings, lambda i: False, lambda: NOON
    ) is None  # fmt: skip


def test_a_block_stops_the_session_keeps_what_was_read_and_pauses_a_week():
    routes = FB_ROUTES | {"https://www.facebook.com/marketplace/search/": "facebook_blocked"}
    state = SocialState(groups=[{"url": "https://www.facebook.com/groups/brigadykolin/",
                                 "name": "Brigády Kolín", "public": True}],
                        groups_at=datetime.now().isoformat())  # fmt: skip
    result = run_session(
        quiet(Facebook()),
        lambda: FakeBrowser(routes),
        state,
        QUERY,
        SocialSettings(),
        lambda i: False,
        lambda: NOON,
    )
    assert result.stopped == "blocked" and len(result.posts) == 2
    assert state.pause_reason == "blocked"
    assert datetime.fromisoformat(state.paused_until) == NOON + BLOCK_PAUSE

    expired = SocialState()
    result = run_session(
        quiet(Instagram()),
        lambda: FakeBrowser({"https://www.instagram.com/": "redirect:https://www.instagram.com/accounts/login/"}),
        expired, QUERY, SocialSettings(), lambda i: False, lambda: NOON,
    )  # fmt: skip
    assert result.stopped == "login" and expired.pause_reason == "login"


def test_instagram_reads_hashtags_near_home():
    ig = quiet(Instagram())
    assert ig.tags(QUERY, SocialSettings(instagram_tags=["#brigadyCZ"]))[:4] == [
        "brigadyCZ",
        "pracekolin",
        "brigadakolin",
        "nabidkapracekolin",
    ]
    browser = FakeBrowser({
        "https://www.instagram.com/explore/tags/": "instagram_tag",
        "https://www.instagram.com/p/": "instagram_post",
    })  # fmt: skip
    result = run_session(
        ig, lambda: browser, SocialState(), QUERY, SocialSettings(), lambda i: False, lambda: NOON
    )
    assert [p.post_id for p in result.posts] == ["CxYz123_ab"]  # the reel page has no caption


# --- storing ---------------------------------------------------------------------------------


@pytest.fixture
def sessions(tmp_path, municipalities):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    sessions = make_sessionmaker(engine)
    with sessions() as s:
        municipalities(s)
    yield sessions
    engine.dispose()


def test_runner_stores_posts_and_deletes_them_two_weeks_after_last_seen(sessions, tmp_path):
    fb = quiet(Facebook())
    runner = ScrapeRunner(
        sessions,
        tmp_path / "cache",
        geocode=False,
        sources=[fb],
        browser_factory=lambda platform: FakeBrowser(FB_ROUTES),
        source_choices=lambda: {"facebook": True},
        clock=lambda: NOON,
    )
    with sessions() as s:
        places, dedup = PlaceResolver(s), DedupIndex.load(s)
    assert runner.run_social(fb, places, dedup, QUERY).status == "ok"
    with sessions() as s:
        rows = s.execute(select(Job.title, Job.is_offer, Job.place_text)).all()
        by_title = {title: (offer, place) for title, offer, place in rows}
        assert by_title["Přijmeme skladníka na HPP do Kolína."] == (True, "Kolín")
        seeker = "Hledám práci jako řidička sk. B, Kolín a okolí, nejlépe ranní."
        assert by_title[seeker][0] is False
        state = SocialState.load(s.get(SourceState, "facebook").state)
        assert state.read == 3 and state.sessions == 1
    # The same hour again: too soon for another session.
    assert runner.run_social(fb, places, dedup, QUERY) is None

    # Two weeks later, unseen: the posts are deleted, not just expired.
    with sessions() as s:
        old = datetime.now() - timedelta(days=15)
        for src in s.scalars(select(JobSource).where(JobSource.source_id == "facebook")):
            src.last_seen_at = old
        s.commit()
        JobStore(s, "facebook").expire_unseen(14)
        assert purge_expired(s, "facebook") == 3
        assert s.scalar(select(Job.id)) is None


@pytest.mark.parametrize("on", [False, True])
def test_old_posts_are_deleted_while_the_platform_is_off_or_paused(sessions, tmp_path, on):
    with sessions() as s:
        store = JobStore(s, "facebook")
        store.add(ParsedJob("1", "Brigáda na stavbě", url="https://www.facebook.com/posts/1/"))
        store.commit()
        s.scalar(select(JobSource)).last_seen_at = datetime.now() - timedelta(days=15)
        paused = SocialState()
        paused.pause(NOON, "blocked")
        s.add(SourceState(source_id="facebook", state=paused.dump()))
        s.commit()

    def no_browser(platform):
        raise AssertionError("no session while off or paused")

    runner = ScrapeRunner(
        sessions,
        tmp_path / "cache",
        geocode=False,
        sources=[quiet(Facebook())],
        browser_factory=no_browser,
        source_choices=lambda: {"facebook": on},
        clock=lambda: NOON,
    )
    assert runner.start()
    runner.wait()
    assert runner.progress.errors == {}
    with sessions() as s:
        assert s.scalar(select(Job.id)) is None


def test_one_edge_window_per_profile(tmp_path):
    held = browser.hold_profile(browser.profile_dir(tmp_path, "facebook"))
    with pytest.raises(browser.ProfileBusy):
        browser.login(tmp_path, "facebook")
    browser.hold_profile(browser.profile_dir(tmp_path, "instagram")).release()
    held.release()
    browser.hold_profile(browser.profile_dir(tmp_path, "facebook")).release()


def test_logged_in_only_after_a_login_window_saw_the_session(tmp_path, monkeypatch):
    cookies = browser.profile_dir(tmp_path, "facebook") / "Default" / "Network" / "Cookies"
    cookies.parent.mkdir(parents=True)
    cookies.write_bytes(b"")
    assert not browser.has_session(tmp_path, "facebook")

    class Window:
        session = False

        def __init__(self, profile, visible=False): ...
        def goto(self, url): ...
        def close(self): ...

        def logged_in(self, platform):
            return Window.session

        def wait_closed_or(self, done, timeout):
            return done()

    monkeypatch.setattr(browser, "EdgeBrowser", Window)
    assert not browser.login(tmp_path, "facebook")
    assert not browser.has_session(tmp_path, "facebook")
    Window.session = True
    assert browser.login(tmp_path, "facebook") and browser.has_session(tmp_path, "facebook")
    Window.session = False
    assert not browser.login(tmp_path, "facebook")
    assert not browser.has_session(tmp_path, "facebook")


def test_a_busy_profile_skips_the_session_and_a_newer_pause_is_kept(sessions, tmp_path):
    fb = quiet(Facebook())

    def busy(platform):
        raise browser.ProfileBusy(platform)

    def pausing_elsewhere(platform):
        with sessions() as s:
            paused = SocialState.load(s.get(SourceState, "facebook").state)
            paused.pause(NOON, "blocked")
            s.get(SourceState, "facebook").state = paused.dump()
            s.commit()
        return FakeBrowser(FB_ROUTES)

    runner = ScrapeRunner(
        sessions,
        tmp_path / "cache",
        geocode=False,
        sources=[fb],
        browser_factory=busy,
        source_choices=lambda: {"facebook": True},
        clock=lambda: NOON,
    )
    with sessions() as s:
        places, dedup = PlaceResolver(s), DedupIndex.load(s)
    assert runner.run_social(fb, places, dedup, QUERY).status == "skipped"
    with sessions() as s:
        assert SocialState.load(s.get(SourceState, "facebook").state).sessions == 0

    runner.browser_factory = pausing_elsewhere
    assert runner.run_social(fb, places, dedup, QUERY).status == "ok"
    with sessions() as s:
        state = SocialState.load(s.get(SourceState, "facebook").state)
    assert state.sessions == 1 and state.pause_reason == "blocked"


def test_a_failed_session_is_still_counted(sessions, tmp_path):
    fb = quiet(Facebook())
    pages = []

    class Failing(FakeBrowser):
        def goto(self, url):
            pages.append(url)
            if len(pages) > 2:
                raise self.error("the window was closed")
            super().goto(url)

    runner = ScrapeRunner(
        sessions,
        tmp_path / "cache",
        geocode=False,
        sources=[fb],
        browser_factory=lambda platform: Failing(FB_ROUTES),
        source_choices=lambda: {"facebook": True},
        clock=lambda: NOON,
    )
    with sessions() as s:
        places, dedup = PlaceResolver(s), DedupIndex.load(s)
    Failing.error = browser.BrowserFailed  # the page or the window: what was read is kept
    assert runner.run_social(fb, places, dedup, QUERY).status == "failed"
    assert runner.progress.errors["facebook"] == "social.stopped.failed"

    Failing.error = RuntimeError  # anything else fails the run, but it still counts
    pages.clear()
    with sessions() as s:
        row = s.get(SourceState, "facebook")
        row.state = SocialState().dump()
        s.commit()
    with pytest.raises(RuntimeError):
        runner.run_social(fb, places, dedup, QUERY)
    with sessions() as s:
        state = SocialState.load(s.get(SourceState, "facebook").state)
    assert state.sessions == 1 and state.last_session
