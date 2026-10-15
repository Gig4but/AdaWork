"""What the web says about an employer (PLAN §4.5): a few searches through ddgs ("{firma}
zkušenosti zaměstnanci", "{firma} recenze práce", "{firma} nevyplacená mzda"), then the best
pages read politely (robots.txt) for passages about working there. The AI summary works from
these passages; each keeps its page address, so every claim can link to its source.

ddgs sometimes answers "no results" for a moment: each search is retried with a pause. Only
results that name the employer are kept (small firms share names with others). Pages of the
sources read elsewhere (Atmoskop) or not allowed (Firmy.cz, Mapy.cz) are skipped; Indeed
and Facebook pages are only remembered as links.
"""

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from selectolax.lexbor import LexborHTMLParser

from adawork.employers.names import GENERIC, core_words
from adawork.net.polite import PoliteFetcher, SourceBlocked
from adawork.normalize.text import clean_spaces, fold

log = logging.getLogger(__name__)

QUERIES = ("{name} zkušenosti zaměstnanci", "{name} recenze práce", "{name} nevyplacená mzda")
RESULTS = 10  # per search
PAGES = 5  # pages read per employer
PASSAGES = 6  # per page
RETRY_WAITS = (5.0, 10.0)
NO_RESULTS = "No results found"  # ddgs's error for an empty answer (not a failure)
# Hosts not read: own sources, robots.txt says no, job listings (ads, not experiences).
SKIP_HOSTS = (
    "atmoskop.cz", "firmy.cz", "mapy.cz", "mapy.com", "indeed.com", "facebook.com",
    "instagram.com", "linkedin.com", "jobs.cz", "prace.cz", "jooble.org", "profesia.cz",
    "careerjet.cz", "startupjobs.cz", "jenprace.cz", "praceprovas.cz", "youtube.com",
    "or.justice.cz", "ares.gov.cz", "kurzy.cz", "rejstrik-firem.kurzy.cz", "podnikatel.cz",
    "detail.cz", "finstat.cz", "hlidacstatu.cz", "merk.cz", "zivefirmy.cz", "pracomat.cz",
    "flekni.cz",
)  # fmt: skip
WORK_WORDS = re.compile(
    r"zamestnan|zamestnav|\bprac[eiu]|\bpracov|brigad|vyplat|mzd|plat\b|plat[uy]|smen|sef|vedouc"
    r"|kolektiv|pohovor|zkusenost|recenz|hodnocen|benefit|prescas|vypoved|odmen"
)
WARNING_WORDS = re.compile(
    r"nevyplac|nevyplat|nezaplac|na cerno|bez smlouvy|zkusebni (den|smen)|podvod"
)


# Page furniture and ads, not experiences: cookie notes, "write a review", job offers,
# salary statistics of job sites.
BOILERPLATE = re.compile(
    r"cookies|zanechte (vase|svuj) cv|pridejte hodnoceni|napiste (sve|svou) hodnoceni"
    r"|zjistete si o teto spolecnosti|hodnotit muzete|pocet zamestnancu \d"
    r"|naplne prace|pozadujeme|nabizime:|posilejte zivotopis|poslete nam zivotopis"
    r"|nabizena mzda|nastupni mzd|aktualne nabizi|volnych pracovnich mist|nabidek prace"
    r"|napln\w* prace|prvni kontakt|zaslani zivotopis|volna mista|nabidk[uy] prace"
)


def boilerplate(text: str) -> bool:
    return bool(BOILERPLATE.search(fold(text)))


@dataclass
class Mention:
    url: str
    title: str
    snippet: str
    passages: list[str] = field(default_factory=list)


@dataclass
class Found:
    mentions: list[Mention] = field(default_factory=list)
    indeed_url: str | None = None
    facebook_url: str | None = None
    failed: bool = False  # the searches failed (not "no results"): what is stored stays


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").removeprefix("www.")


def _skipped(url: str) -> bool:
    host = _host(url)
    return any(host == h or host.endswith("." + h) for h in SKIP_HOSTS)


def _key_words(name: str) -> list[str]:
    """Without the legal form and country words ("Lidl Česká republika" -> lidl)."""
    words = core_words(name)
    return [w for w in words if w not in GENERIC and len(w) > 1] or [" ".join(words)]


def facebook_page(url: str) -> str | None:
    """The page's address ("facebook.com/PennyCZ"), None for other kinds of Facebook URL."""
    first = urlsplit(url).path.strip("/").split("/")[0]
    if not re.fullmatch(r"[\w.\-]{3,}", first) or first.endswith(".php"):
        return None
    if first in ("groups", "marketplace", "events", "people", "watch", "reel", "share"):
        return None
    return f"https://www.facebook.com/{first}"


def names_it(text: str, name: str) -> bool:
    """Every word of the employer's name (without its legal form) starts a word of the
    text (Czech endings: "Lidlu")."""
    folded = " " + re.sub(r"[^a-z0-9]+", " ", fold(text)) + " "
    return all(f" {w}" in folded for w in _key_words(name))


def search(name: str, ddgs_text: Callable[..., list[dict]], sleep=time.sleep) -> Found:
    found = Found()
    seen: set[str] = set()
    errors = 0
    for template in QUERIES:
        query = template.format(name=f'"{name}"')
        results: list[dict] = []
        for wait in (*RETRY_WAITS, None):
            try:
                results = ddgs_text(query, region="cz-cs", max_results=RESULTS, backend="auto")
                break
            except Exception as exc:  # ddgs raises its own errors for empty answers
                log.info("ddgs %r: %s", query, exc)
                if wait is None:
                    errors += NO_RESULTS not in str(exc)  # rate limit, timeout, offline
                    break
                sleep(wait)
        for r in results or []:
            url, title, body = r.get("href") or "", r.get("title") or "", r.get("body") or ""
            if not url.startswith("http") or url in seen:
                continue
            seen.add(url)
            if not names_it(f"{title} {body} {url}", name):
                continue
            host = _host(url)
            if host.endswith("indeed.com") and "/cmp/" in url and not found.indeed_url:
                found.indeed_url = re.sub(r"(/cmp/[^/?#]+).*", r"\1/reviews", url)
            elif host.endswith("facebook.com") and not found.facebook_url:
                found.facebook_url = facebook_page(url)
            if _skipped(url):
                continue
            found.mentions.append(Mention(url, clean_spaces(title)[:200], clean_spaces(body)[:400]))
    found.failed = errors > 0 and not seen
    # Experiences and warnings first (by the words of the snippet), then as found.
    found.mentions.sort(key=lambda m: -_weight(m.title + " " + m.snippet))
    return found


def _weight(text: str) -> int:
    folded = fold(text)
    return 2 * len(WARNING_WORDS.findall(folded)) + min(len(WORK_WORDS.findall(folded)), 5)


def passages(html: str, name: str) -> list[str]:
    """Paragraphs about working there: they speak of work and (mostly) name the employer."""
    tree = LexborHTMLParser(html)
    for tag in ("script", "style", "noscript", "nav", "header", "footer", "aside", "form"):
        for node in tree.css(tag):
            node.decompose()
    body = tree.body
    if body is None:
        return []
    found: list[tuple[int, str]] = []
    blocks = [n for n in body.css("p, li, blockquote, dd")]
    blocks += [n for n in body.css("div") if n.css_first("p, div, li, blockquote") is None]
    for node in blocks:
        text = clean_spaces(node.text(separator=" "))
        if not 40 <= len(text) <= 1200 or boilerplate(text):
            continue
        folded = fold(text)
        work = len(WORK_WORDS.findall(folded))
        named = names_it(text, name)
        if not (named and work) and work < 2:  # one word about work: probably not about it
            continue
        weight = work + 3 * len(WARNING_WORDS.findall(folded)) + (2 if named else 0)
        found.append((weight, text))
    texts = list(dict.fromkeys(t for _, t in sorted(found, key=lambda x: -x[0])))
    kept = [t for t in texts if not any(t != o and t in o for o in texts)]  # no nested copies
    return kept[:PASSAGES]


def read_pages(found: Found, name: str, fetcher: PoliteFetcher) -> None:
    """Fills the passages of the first PAGES mentions; a page that fails is left as a link."""
    for m in found.mentions[:PAGES]:
        try:
            resp = fetcher.get(m.url)
        except SourceBlocked:
            continue
        except Exception as exc:
            log.info("mention %s not read: %s", m.url, exc)
            continue
        if resp is None or resp.status_code != 200:
            continue
        if "html" not in resp.headers.get("content-type", "html"):
            continue
        m.passages = passages(resp.text, name)
