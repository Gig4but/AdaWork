"""Facebook and Instagram job posts, read in the user's own logged-in Edge (PLAN §4.3).

**Off by default.** Switched on only after the risk warning and the user's confirmation; the
user logs in once in a visible window (browser.login).

Limits (no safe number exists, §4.3.1):
- at most `per_day` posts a day (100 Facebook / 50 Instagram by default), read in at most two
  sessions a day of ≤ 15 minutes, in daytime, at least 3 hours apart, whatever the scrape
  interval is;
- irregular pauses of 5–30 s between pages; one tab; reading only: no likes, comments,
  follows, joins or messages (opening "See more" of a long post is the only click);
- a block, a verification request or "going too fast" stops the session and pauses the
  platform for 7 days; an expired login pauses it until the user logs in again.

What is read:
- Facebook: job groups the user is a member of, public job groups found by "práce {town}" and
  "brigády {town}" (private ones are only suggested, never joined), groups the user added, and
  Marketplace searches (Marketplace uses the location set in the user's Facebook);
- Instagram: hashtags such as #prace{town}, #brigada{town} and ones the user added.

Posts become jobs of the source "facebook" or "instagram"; a post that is not an offer (people
looking for work) is kept but not listed. Social posts carry personal data, so they are
deleted 14 days after they were last seen (§12) and stay on this computer.
"""

import logging
import math
import random
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Literal
from urllib.parse import quote

from adawork.config import SocialSettings
from adawork.core import ParsedJob, ScrapeQuery, SourceKind
from adawork.geo.places import PlaceResolver
from adawork.normalize.offers import not_offer_reason
from adawork.normalize.text import clean_spaces, fold, slugify
from adawork.social import posts as parse
from adawork.social.browser import Browser, BrowserFailed
from adawork.social.posts import JOB_WORDS, LoginNeeded, Post, SocialBlocked

log = logging.getLogger(__name__)

DAY_HOURS = (8, 21)  # local time in which sessions may start
SESSIONS_PER_DAY = 2
SESSION_GAP = timedelta(hours=3)
SESSION_SECONDS = 15 * 60
PAUSE_SECONDS = (5.0, 30.0)
BLOCK_PAUSE = timedelta(days=7)
GROUPS_FRESH = timedelta(days=7)  # how long the found group list is reused
MAX_GROUPS = 12  # groups read per session at most
SCROLLS = 4  # screens per group feed at most
KEEP_DAYS = 14
TITLE_LENGTH = 90


@dataclass
class SocialState:
    """Kept in the source's SourceState.state."""

    day: str = ""  # local date the counters belong to
    read: int = 0  # posts read that day
    sessions: int = 0
    last_session: str | None = None  # ISO local time
    paused_until: str | None = None  # ISO local time
    pause_reason: str = ""  # "blocked" | "login" | ""
    groups: list[dict] = field(default_factory=list)  # found job groups: url, name, public
    groups_at: str | None = None
    suggested: list[dict] = field(default_factory=list)  # private groups to join by hand

    @classmethod
    def load(cls, data: dict | None) -> "SocialState":
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in (data or {}).items() if k in known})

    def dump(self) -> dict:
        return asdict(self)

    def for_today(self, now: datetime) -> "SocialState":
        today = now.date().isoformat()
        if self.day != today:
            self.day, self.read, self.sessions = today, 0, 0
        return self

    def may_start(self, now: datetime, per_day: int) -> str | None:
        """None when a session may start now, else the reason it may not."""
        self.for_today(now)
        if self.paused_until and datetime.fromisoformat(self.paused_until) > now:
            return self.pause_reason or "paused"
        if not DAY_HOURS[0] <= now.hour < DAY_HOURS[1]:
            return "night"
        if self.sessions >= SESSIONS_PER_DAY or self.read >= per_day:
            return "daily_limit"
        if self.last_session and now - datetime.fromisoformat(self.last_session) < SESSION_GAP:
            return "too_soon"
        return None

    def pause(self, now: datetime, reason: str) -> None:
        until = now + BLOCK_PAUSE if reason == "blocked" else now + timedelta(days=3650)
        self.paused_until, self.pause_reason = until.isoformat(), reason

    def keep_pause(self, loaded: dict | None, latest: dict | None) -> None:
        """Takes the pause saved elsewhere since `loaded` was read (`latest`), unless this
        state paused or lifted it itself."""
        before, now = SocialState.load(loaded), SocialState.load(latest)
        if (self.paused_until, self.pause_reason) == (before.paused_until, before.pause_reason):
            self.paused_until, self.pause_reason = now.paused_until, now.pause_reason


@dataclass
class SessionResult:
    posts: list[Post] = field(default_factory=list)
    pages: int = 0
    stopped: Literal["", "blocked", "login", "layout", "failed"] = ""


class SocialSource:
    id: str
    label: str
    kind: SourceKind = "social"
    default_enabled = False
    needs_consent = True  # Meta's terms forbid it; the account may be restricted

    def __init__(
        self,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.sleep = sleep
        self.clock = clock

    def per_day(self, settings: SocialSettings) -> int:
        raise NotImplementedError

    def read(
        self,
        browser: Browser,
        state: SocialState,
        query: ScrapeQuery,
        settings: SocialSettings,
        budget: int,
        known: Callable[[str], bool],
        result: SessionResult,
    ) -> None:
        """Adds the posts read to `result`, also when a block stops it midway."""
        raise NotImplementedError

    # --- shared steps ----------------------------------------------------------------------

    def _pause(self) -> None:
        self.sleep(random.uniform(*PAUSE_SECONDS))

    def _open(self, browser: Browser, url: str, result: SessionResult) -> str:
        browser.goto(url)
        result.pages += 1
        html = browser.html()
        parse.check_page(browser.url(), html)
        return html

    def _out_of_time(self, started: float) -> bool:
        return self.clock() - started > SESSION_SECONDS

    def to_job(self, post: Post, places: PlaceResolver) -> ParsedJob:
        first = clean_spaces(post.text.strip().split("\n", 1)[0])
        title = (
            first if len(first) <= TITLE_LENGTH else first[:TITLE_LENGTH].rsplit(" ", 1)[0] + "…"
        )
        job = ParsedJob(
            external_id=post.post_id,
            title=title,
            url=post.url,
            description=post.text.strip(),
            posted_at=post.posted_at,
            images=post.images,
        )
        if post.where:
            job.extra["where"] = post.where
            job.place_text, job.location = place_in(post.where, places)
        if reason := not_offer_reason(job.title, job.description):
            job.extra["not_offer"] = reason
        return job


def place_in(name: str, places: PlaceResolver):
    """A municipality named in a group name or hashtag: "Brigády Kolín a okolí" -> Kolín."""
    words = [w for w in name.replace("#", " ").replace("-", " ").split() if len(w) > 2]
    for size in (3, 2, 1):
        for i in range(len(words) - size + 1):
            candidate = " ".join(words[i : i + size])
            if fold(candidate) in ("prace", "brigady", "brigada", "okoli", "nabidka", "prace a"):
                continue
            loc = places.resolve(candidate)
            if loc is not None and loc.precision in ("municipality", "part", "district"):
                return candidate, loc
    return None, None


def home_words(query: ScrapeQuery) -> list[str]:
    """Places to search for: the home town, when the profile has one."""
    return [query.home] if query.home else []


class Facebook(SocialSource):
    id = "facebook"
    label = "Facebook"

    def per_day(self, settings: SocialSettings) -> int:
        return settings.facebook_per_day

    def find_groups(self, browser, state, query, result) -> None:
        """Job groups the user is in, and public ones near home; private ones are suggested."""
        html = self._open(browser, parse.FACEBOOK + "/groups/joins/", result)
        joined = [g for g in parse.facebook_groups(html) if JOB_WORDS.search(g.name)]
        found = {g.url: {"url": g.url, "name": g.name, "public": True} for g in joined}
        suggested = {}
        for town in home_words(query):
            for words in ("práce", "brigády"):
                self._pause()
                url = f"{parse.FACEBOOK}/search/groups/?q={quote(f'{words} {town}')}"
                for g in parse.facebook_groups(self._open(browser, url, result)):
                    if not JOB_WORDS.search(g.name) or g.url in found:
                        continue
                    entry = {"url": g.url, "name": g.name, "public": g.public}
                    if g.public:
                        found[g.url] = entry
                    elif g.public is False:
                        suggested[g.url] = entry
        state.groups = list(found.values())[:30]
        state.suggested = list(suggested.values())[:20]
        state.groups_at = datetime.now().isoformat(timespec="seconds")

    def read(self, browser, state, query, settings, budget, known, result) -> None:
        started = self.clock()
        fresh = (
            state.groups_at
            and datetime.now() - datetime.fromisoformat(state.groups_at) < GROUPS_FRESH
        )
        if not fresh:
            self.find_groups(browser, state, query, result)
        groups = [{"url": u, "name": ""} for u in settings.facebook_groups] + state.groups
        random.shuffle(groups)
        targets = [
            (g["url"].rstrip("/") + "/?sorting_setting=CHRONOLOGICAL", g["name"]) for g in groups
        ]
        targets = targets[:MAX_GROUPS]
        for words in ("brigáda", "práce"):
            targets.append((f"{parse.FACEBOOK}/marketplace/search/?query={quote(words)}", ""))
        seen_any = False
        for url, name in targets:
            if len(result.posts) >= budget or self._out_of_time(started):
                break
            self._pause()
            html = self._open(browser, url, result)
            if "/marketplace/" in url:
                found = parse.marketplace_items(html)
                seen_any = seen_any or bool(found)
                self._take(found, result, budget, known)
                continue
            for _ in range(SCROLLS):
                browser.expand()
                found = parse.facebook_feed(browser.html(), url, name)
                seen_any = seen_any or bool(found)
                if not self._take(found, result, budget, known) and found:
                    break  # only posts we know already: the rest is older
                if len(result.posts) >= budget or self._out_of_time(started):
                    break
                browser.scroll()
                self._pause()
        if not seen_any and result.pages > 1:
            result.stopped = "layout"

    @staticmethod
    def _take(found: list[Post], result: SessionResult, budget: int, known) -> int:
        have = {p.post_id for p in result.posts}
        new = [p for p in found if p.post_id not in have and not known(p.post_id)]
        result.posts.extend(new[: budget - len(result.posts)])
        return len(new)


class Instagram(SocialSource):
    id = "instagram"
    label = "Instagram"

    def per_day(self, settings: SocialSettings) -> int:
        return settings.instagram_per_day

    def tags(self, query: ScrapeQuery, settings: SocialSettings) -> list[str]:
        tags = [t.strip().lstrip("#") for t in settings.instagram_tags if t.strip()]
        for town in home_words(query):
            slug = slugify(town).replace("-", "")
            tags += [f"prace{slug}", f"brigada{slug}", f"nabidkaprace{slug}"]
        return list(dict.fromkeys(tags + ["nabidkaprace", "brigada", "hledamezamestnance"]))

    def read(self, browser, state, query, settings, budget, known, result) -> None:
        started = self.clock()
        seen_any = False
        for tag in self.tags(query, settings):
            if len(result.posts) >= budget or self._out_of_time(started):
                break
            self._pause()
            html = self._open(browser, f"{parse.INSTAGRAM}/explore/tags/{quote(tag)}/", result)
            links = parse.instagram_grid(html)
            seen_any = seen_any or bool(links)
            for link in links[:15]:
                code = parse.post_id(link)
                if not code or known(code) or any(p.post_id == code for p in result.posts):
                    continue
                if len(result.posts) >= budget or self._out_of_time(started):
                    break
                self._pause()
                post = parse.instagram_post(self._open(browser, link, result), link, f"#{tag}")
                if post is not None and JOB_WORDS.search(post.text):
                    result.posts.append(post)
        if not seen_any and result.pages > 1:
            result.stopped = "layout"


def run_session(
    source: SocialSource,
    browser_factory: Callable[[], Browser],
    state: SocialState,
    query: ScrapeQuery,
    settings: SocialSettings,
    known: Callable[[str], bool],
    now: Callable[[], datetime] = datetime.now,
) -> SessionResult | None:
    """One reading session if the budget allows it now; None when it does not."""
    per_day = source.per_day(settings)
    if state.may_start(now(), per_day) is not None:
        return None
    budget = min(per_day - state.read, math.ceil(per_day / SESSIONS_PER_DAY))
    browser = browser_factory()
    state.sessions += 1
    state.last_session = now().isoformat(timespec="seconds")
    result = SessionResult()
    try:
        source.read(browser, state, query, settings, budget, known, result)
    except LoginNeeded:
        result.stopped = "login"
        state.pause(now(), "login")
    except SocialBlocked as exc:
        log.warning("%s stopped: %s", source.id, exc)
        result.stopped = "blocked"
        state.pause(now(), "blocked")
    except BrowserFailed as exc:  # the posts read so far are kept
        log.warning("%s stopped: %s", source.id, exc)
        result.stopped = "failed"
    finally:
        browser.close()
    state.read += len(result.posts)
    return result


SOURCES = (Facebook(), Instagram())
