import threading
import time
from pathlib import Path

import httpx
import pytest

from adawork.net.polite import PoliteFetcher, SourceBlocked, Stopped
from adawork.net.robots import RobotsRules
from adawork.normalize.text import contract_types, html_to_text, parse_salary, split_places

SOURCES = Path(__file__).parents[1] / "fixtures" / "sources"


def robots(name: str) -> RobotsRules:
    return RobotsRules.parse((SOURCES / name / "robots.txt").read_text(encoding="utf-8"), "adawork")


def test_robots_of_the_portals_we_crawl():
    jenprace = robots("jenprace")
    assert jenprace.allowed("https://www.jenprace.cz/nabidky/praha?page=2")
    assert jenprace.allowed("https://www.jenprace.cz/nabidka/2tcexh/operator")
    assert not jenprace.allowed("https://www.jenprace.cz/nabidky/praha?page_dbox=2")  # wildcard
    assert not jenprace.allowed("https://www.jenprace.cz/data/x.json")
    prace = robots("prace_cz")
    assert prace.allowed("https://www.prace.cz/nabidky/praha/?page=3")
    assert not prace.allowed("https://www.prace.cz/hledat/?q=skladnik")
    profesia = robots("profesia")
    assert profesia.allowed("https://www.profesia.cz/prace/praha/?page_num=2")
    assert profesia.allowed("https://www.profesia.cz/prace/manpowergroup/O5370601")
    assert not robots("volnamista").allowed("https://www.volnamista.cz/")


def test_robots_rules():
    text = """
User-agent: Googlebot
Disallow: /

User-agent: *
User-agent: AdaWork
Disallow: /private
Allow: /private/ok
Disallow: /*.pdf$
Crawl-delay: 4
Sitemap: https://example.cz/sitemap.xml
"""
    rules = RobotsRules.parse(text, "adawork")
    assert rules.allowed("https://example.cz/jobs")
    assert not rules.allowed("https://example.cz/private/x")
    assert rules.allowed("https://example.cz/private/ok/1")  # longer Allow wins
    assert not rules.allowed("https://example.cz/a/b.pdf")
    assert rules.allowed("https://example.cz/a/b.pdf?x=1")  # "$" anchors the end
    assert rules.crawl_delay == 4 and rules.sitemaps == ["https://example.cz/sitemap.xml"]
    assert not RobotsRules.parse(text, "googlebot").allowed("https://example.cz/jobs")


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def fetcher(handler, clock: Clock | None = None) -> PoliteFetcher:
    clock = clock or Clock()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return PoliteFetcher(client, delay=2.0, sleep=clock.sleep, clock=lambda: clock.now)


def test_polite_fetcher_waits_and_obeys_robots():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /secret\n")
        if request.url.path == "/gone":
            return httpx.Response(404)
        return httpx.Response(200, text="ok")

    clock = Clock()
    f = fetcher(handler, clock)
    assert f.get("https://a.cz/page").text == "ok"
    assert f.get("https://a.cz/secret") is None  # never requested
    assert f.get("https://a.cz/gone") is None
    assert f.requests == 3  # robots.txt, /page, /gone
    assert len(clock.slept) == 2 and all(2.0 <= s <= 2.6 for s in clock.slept)


def test_stop_ends_the_wait_for_a_turn_and_later_requests():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="ok"))
    f = PoliteFetcher(httpx.Client(transport=transport), delay=60.0)
    assert f.get("https://a.cz/1", obey_robots=False).text == "ok"
    threading.Timer(0.2, f.stop.set).start()
    started = time.monotonic()
    with pytest.raises(Stopped):
        f.get("https://a.cz/2", obey_robots=False)  # its turn is a minute away
    assert time.monotonic() - started < 10
    with pytest.raises(Stopped):
        f.get("https://a.cz/3", obey_robots=False)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429),
        httpx.Response(999),  # LinkedIn
        httpx.Response(403, text="Forbidden"),
        httpx.Response(503, text="<html><title>Just a moment...</title>"),
    ],
)
def test_refusal_stops_the_source(response):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404) if request.url.path == "/robots.txt" else response

    with pytest.raises(SourceBlocked):
        fetcher(handler).get("https://a.cz/page")


def test_refused_robots_txt_stops_the_source():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403) if request.url.path == "/robots.txt" else httpx.Response(200)

    with pytest.raises(SourceBlocked):
        fetcher(handler).get("https://a.cz/page")


def test_login_wall_stops_linkedin():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/jobs":
            return httpx.Response(302, headers={"Location": "/authwall?trk=guest"})
        if request.url.path == "/moved":
            return httpx.Response(301, headers={"Location": "/jobs-new"})
        return httpx.Response(200, text="ok")

    clock = Clock()
    f = fetcher(handler, clock)
    with pytest.raises(SourceBlocked):
        f.get("https://cz.linkedin.com/jobs", obey_robots=False, min_delay=5)
    # Other redirects of an opt-in source skip robots.txt and keep its pace.
    assert f.get("https://cz.linkedin.com/moved", obey_robots=False, min_delay=5).text == "ok"
    assert f.requests == 3 and all(5 <= s <= 6.5 for s in clock.slept)
    assert f.get("https://a.cz/authwall").text == "ok"  # elsewhere just a page


def test_server_errors_are_retried_and_robots_errors_mean_disallowed():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(502) if len(calls) < 3 else httpx.Response(200, text="ok")

    assert fetcher(handler).get("https://a.cz/page").text == "ok"

    def broken_robots(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    assert fetcher(broken_robots).get("https://b.cz/page") is None


def test_redirects_are_followed_hop_by_hop():
    moves = {
        "a.cz/old": "/new",
        "a.cz/away": "https://b.cz/page",
        "a.cz/sneaky": "https://b.cz/private",
        "a.cz/loop": "/loop",
        "c.cz/robots.txt": "https://www.c.cz/robots.txt",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        where = request.url.host + request.url.path
        if where in moves:
            return httpx.Response(301, headers={"Location": moves[where]})
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
        return httpx.Response(200, text=where)

    clock = Clock()
    f = fetcher(handler, clock)
    resp = f.get("https://a.cz/old")
    assert resp.text == "a.cz/new" and str(resp.url) == "https://a.cz/new"
    assert [str(r.url) for r in resp.history] == ["https://a.cz/old"]
    assert f.requests == 3 and len(clock.slept) == 2  # robots.txt, /old, then /new in its turn
    assert f.get("https://a.cz/away").text == "b.cz/page"  # after b.cz's robots.txt
    assert f.get("https://a.cz/sneaky") is None  # b.cz disallows it
    with pytest.raises(httpx.TooManyRedirects):
        f.get("https://a.cz/loop")
    assert f.get("https://c.cz/private") is None  # robots.txt found at www.c.cz


def test_crawl_delay_is_honoured_or_the_site_left_alone():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            delay = {"slow.cz": 60, "slowest.cz": 600}[request.url.host]
            return httpx.Response(200, text=f"User-agent: *\nCrawl-delay: {delay}\n")
        return httpx.Response(200, text="ok")

    clock = Clock()
    f = fetcher(handler, clock)
    assert f.get("https://slow.cz/a").text == f.get("https://slow.cz/b").text == "ok"
    assert 60 <= clock.slept[-1] <= 78
    assert f.get("https://slowest.cz/a") is None  # rather not at all than faster than asked


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("30 000 – 35 000 Kč/měsíc", (30000, 35000, "month", "CZK")),
        ("od 70 000 Kč", (70000, None, "month", "CZK")),
        ("až 45 000 Kč", (None, 45000, "month", "CZK")),
        ("182 Kč/hod.", (182, None, "hour", "CZK")),
        ("Od 3 000 EUR/měsíc", (3000, None, "month", "EUR")),
        ("mzda až 35.000 + odměny", (None, 35000, "month", "CZK")),
        ("30 000 Kč/měsíc, odměny dle dohody", (30000, None, "month", "CZK")),
        ("od 35 000 Kč, dle dohody", (35000, None, "month", "CZK")),
        ("150 Kč/h", (150, None, "hour", "CZK")),
        ("Plat neuveden", None),
    ],
)
def test_parse_salary(text, expected):
    salary = parse_salary(text)
    got = salary and (salary.min, salary.max, salary.unit, salary.currency)
    assert got == expected


def test_contract_types_and_places():
    assert contract_types("Plný úvazek, Zkrácený úvazek") == ["full_time", "part_time"]
    assert contract_types("FULL_TIME", "CONTRACTOR") == ["full_time", "contractor"]
    assert contract_types("Dohoda o provedení práce") == ["dpp"]
    assert contract_types("Práce bez praxe") == []
    assert split_places("Praha-Libeň, Středočeský") == ["Praha-Libeň", "Středočeský"]


def test_html_to_text():
    html = "<h2>O nás</h2><p>Jsme <strong>malý</strong> tým.</p><ul><li>A</li><li>B</li></ul>"
    assert html_to_text(html) == "O nás\nJsme malý tým.\n• A\n• B"
    assert html_to_text("&lt;p&gt;Escaped&lt;/p&gt;") == "Escaped"
    assert html_to_text("plain   text") == "plain text"


def test_opt_in_source_skips_robots_and_goes_slower():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        return httpx.Response(200, text="ok")

    clock = Clock()
    f = fetcher(handler, clock)
    assert f.get("https://li.cz/jobs") is None  # robots.txt says no
    assert f.get("https://li.cz/jobs", obey_robots=False, min_delay=5).text == "ok"
    assert f.get("https://li.cz/jobs", obey_robots=False, min_delay=5).text == "ok"
    assert 5 <= clock.slept[-1] <= 6.5
