"""Shared helpers for the M0 source spike: polite fetching, robots.txt checks, fixture saving.

Not production code. The real HTTP layer lives in adawork/ (PLAN.md section 4.2).
"""

from __future__ import annotations

import json
import re
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "sources"
DATA = Path(__file__).resolve().parent / "data"

# The app's honest User-Agent. docs/spikes/sources.md was measured with a browser-like one.
UA = "AdaWork/0.1.0 (local desktop job search; personal use)"
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.6",
}
MIN_DELAY = 2.5
MAX_FIXTURE = 950_000

_last_hit: dict[str, float] = {}
_robots: dict[str, urllib.robotparser.RobotFileParser] = {}
_counts: dict[str, int] = {}

client = httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30.0, http2=False)


def _wait(host: str) -> None:
    last = _last_hit.get(host)
    if last is not None:
        gap = time.monotonic() - last
        if gap < MIN_DELAY:
            time.sleep(MIN_DELAY - gap)
    _last_hit[host] = time.monotonic()
    _counts[host] = _counts.get(host, 0) + 1


def robots(base: str) -> tuple[str, urllib.robotparser.RobotFileParser]:
    parts = urlsplit(base)
    host = parts.netloc
    url = f"{parts.scheme}://{host}/robots.txt"
    _wait(host)
    r = client.get(url)
    text = r.text if r.status_code == 200 else ""
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(text.splitlines())
    _robots[host] = rp
    return text, rp


def allowed(url: str) -> bool:
    host = urlsplit(url).netloc
    rp = _robots.get(host)
    if rp is None:
        return True
    return rp.can_fetch(UA, url) and rp.can_fetch("*", url)


def get(url: str, *, check_robots: bool = True, **kw) -> httpx.Response:
    if check_robots and not allowed(url):
        raise PermissionError(f"robots.txt disallows {url}")
    host = urlsplit(url).netloc
    _wait(host)
    r = client.get(url, **kw)
    print(f"  GET {url} -> {r.status_code} {len(r.content)}B {r.headers.get('content-type', '')}")
    return r


def save(source_id: str, name: str, content: str | bytes, limit: int = MAX_FIXTURE) -> Path:
    d = FIXTURES / source_id
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        data = content.encode("utf-8")
    else:
        data = content
    if len(data) > limit:
        data = data[:limit]
        print(f"  (trimmed {name} to {limit} bytes)")
    p.write_bytes(data)
    return p


def save_json(source_id: str, name: str, obj) -> Path:
    return save(source_id, name, json.dumps(obj, ensure_ascii=False, indent=2, default=str))


LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I
)


def ld_json(html: str) -> list:
    out = []
    for m in LD_RE.finditer(html):
        raw = m.group(1).strip()
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            try:
                out.append(json.loads(raw.replace("\n", " ").replace("\t", " ")))
            except json.JSONDecodeError as e:
                out.append({"_parse_error": str(e), "_raw": raw[:500]})
    return out


def find_type(obj, typ: str) -> list[dict]:
    found = []
    if isinstance(obj, dict):
        t = obj.get("@type")
        if t == typ or (isinstance(t, list) and typ in t):
            found.append(obj)
        for v in obj.values():
            found.extend(find_type(v, typ))
    elif isinstance(obj, list):
        for v in obj:
            found.extend(find_type(v, typ))
    return found


def filled_fields(d: dict, prefix: str = "") -> list[str]:
    out = []
    for k, v in d.items():
        if v in (None, "", [], {}):
            continue
        key = f"{prefix}{k}"
        if isinstance(v, dict) and k not in ("description",):
            out.extend(filled_fields(v, key + "."))
        else:
            out.append(key)
    return out


NEXT_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def next_data(html: str):
    m = NEXT_RE.search(html)
    return json.loads(m.group(1)) if m else None


def summary() -> None:
    print("request counts per host:", _counts)
