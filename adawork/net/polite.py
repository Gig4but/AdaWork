"""Polite fetching for portal pages (PLAN §4.2): robots.txt, a pause between requests to the
same host, retries with backoff, and stopping a source that is being refused.
"""

import logging
import random
import threading
import time
from collections.abc import Callable
from urllib.parse import urljoin, urlsplit

import httpx

from adawork.net.robots import RobotsRules

log = logging.getLogger(__name__)

ROBOTS_AGENT = "adawork"  # product token of USER_AGENT
DEFAULT_DELAY = 1.5  # seconds between requests to one host, unless robots.txt asks for more
MAX_DELAY = 300.0  # a site asking for a longer Crawl-delay is not crawled at all
MAX_REDIRECTS = 5
RETRY_WAITS = (5.0, 20.0)  # after a timeout or a 5xx answer
_CHALLENGE_MARKERS = ("cf-chl", "challenge-platform", "<title>Just a moment")
_LOGIN_PATHS = ("/authwall", "/login", "/checkpoint", "/uas/login")


class SourceBlocked(Exception):
    """The site refuses us (403/429 or a bot challenge). Stop this source for now."""


class Stopped(Exception):
    """`stop` is set (the app is closing): the search ends at its next request."""


def is_challenge(response: httpx.Response) -> bool:
    if response.status_code not in (403, 429, 503):
        return False
    head = response.text[:5000]
    return any(marker in head for marker in _CHALLENGE_MARKERS)


def is_login_wall(url: str) -> bool:
    """LinkedIn sends a guest it refuses to a login page instead of the page asked for."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    linkedin = host == "linkedin.com" or host.endswith(".linkedin.com")
    return linkedin and parts.path.startswith(_LOGIN_PATHS)


class PoliteFetcher:
    def __init__(
        self,
        client: httpx.Client,
        delay: float = DEFAULT_DELAY,
        sleep: Callable[[float], object] | None = None,  # None: waits that `stop` ends early
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.delay = delay
        self.sleep = sleep
        self.clock = clock
        self.stop = threading.Event()  # the runner sets its own: one for every fetcher
        self.requests = 0
        self._robots: dict[str, RobotsRules] = {}
        self._last: dict[str, float] = {}

    def robots(self, url: str) -> RobotsRules:
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host not in self._robots:
            self._robots[host] = self._load_robots(host)
        return self._robots[host]

    def _follow(
        self, resp: httpx.Response, obey_robots: bool = True, min_delay: float | None = None
    ) -> httpx.Response | None:
        """Where a redirect leads, hop by hop: each hop must be allowed by the robots.txt of
        its host and waits for that host's turn. The final URL is the result's `url`, the
        redirects are in its `history`."""
        history = []
        while resp.is_redirect:
            if len(history) == MAX_REDIRECTS:
                raise httpx.TooManyRedirects(f"redirect loop at {resp.url}", request=resp.request)
            history.append(resp)
            url = urljoin(str(resp.url), resp.headers["Location"])
            if is_login_wall(url):
                raise SourceBlocked(f"login wall at {url}")
            if obey_robots and not self.allowed(url):
                log.info("robots.txt disallows %s (redirected from %s)", url, history[0].url)
                return None
            resp = self._request(url, min_delay)
        resp.history = history
        if resp.status_code in (404, 410):
            return None
        resp.raise_for_status()
        return resp

    def _load_robots(self, host: str) -> RobotsRules:
        try:
            resp = self._request(f"{host}/robots.txt")
            for _ in range(MAX_REDIRECTS):  # RFC 9309: follow at least five redirects
                if not resp.is_redirect:
                    break
                resp = self._request(urljoin(str(resp.url), resp.headers["Location"]))
        except httpx.HTTPError as exc:
            log.warning("robots.txt of %s unavailable (%s): not crawling it", host, exc)
            return RobotsRules.disallow_all()
        if resp.status_code >= 500:
            return RobotsRules.disallow_all()  # RFC 9309: server error means "assume disallowed"
        if resp.status_code >= 300:
            return RobotsRules.allow_all()  # no robots.txt (or redirects without end)
        rules = RobotsRules.parse(resp.text, ROBOTS_AGENT)
        if rules.crawl_delay and rules.crawl_delay > MAX_DELAY:
            log.warning(
                "%s asks for %s s between requests: not crawling it", host, rules.crawl_delay
            )
            return RobotsRules.disallow_all()  # rather not at all than faster than asked
        return rules

    def allowed(self, url: str) -> bool:
        return self.robots(url).allowed(url)

    def get(
        self, url: str, obey_robots: bool = True, min_delay: float | None = None
    ) -> httpx.Response | None:
        """GET a page; None when robots.txt disallows it or the page is gone (404/410).

        `obey_robots=False` only for a source the user switched on knowing that its
        robots.txt disallows us (LinkedIn). Raises SourceBlocked when the site refuses us,
        httpx.HTTPError when it keeps failing.
        """
        if obey_robots and not self.allowed(url):
            log.info("robots.txt disallows %s", url)
            return None
        resp = self._request(url, min_delay)
        if resp.status_code in (404, 410):
            return None
        if resp.is_redirect:
            return self._follow(resp, obey_robots, min_delay)
        resp.raise_for_status()
        return resp

    def _request(self, url: str, min_delay: float | None = None) -> httpx.Response:
        for attempt in range(len(RETRY_WAITS) + 1):
            if self.stop.is_set():
                raise Stopped(url)
            self._wait_turn(url, min_delay)
            try:
                resp = self.client.get(url)
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt == len(RETRY_WAITS):
                    raise
                self._pause(RETRY_WAITS[attempt])
                continue
            if is_challenge(resp):
                raise SourceBlocked(f"bot challenge at {url}")
            if resp.status_code in (403, 429, 999):  # 999: LinkedIn's refusal
                raise SourceBlocked(f"HTTP {resp.status_code} at {url}")
            if resp.status_code >= 500 and attempt < len(RETRY_WAITS):
                self._pause(RETRY_WAITS[attempt])
                continue
            return resp
        raise AssertionError("unreachable")

    def _wait_turn(self, url: str, min_delay: float | None = None) -> None:
        host = urlsplit(url).netloc
        rules = self._robots.get(f"{urlsplit(url).scheme}://{host}")
        delay = max(self.delay, min_delay or 0)
        if rules and rules.crawl_delay:
            delay = min(max(delay, rules.crawl_delay), MAX_DELAY)
        if host in self._last:
            wait = self._last[host] + delay * random.uniform(1.0, 1.3) - self.clock()
            if wait > 0:
                self._pause(wait)
        self._last[host] = self.clock()
        self.requests += 1

    def _pause(self, seconds: float) -> None:
        (self.sleep or self.stop.wait)(seconds)
        if self.stop.is_set():
            raise Stopped("stopped while waiting")
