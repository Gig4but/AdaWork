# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27"]
# ///
"""Atmoskop.cz spike.

- robots.txt allows company pages (/nazory-na-zamestnavatele/...); /graphql/ is disallowed.
- sitemap.xml lists ~37.7k company profile URLs "<id>-<webalized legal name>" -> local index
  that maps slugify(ARES obchodniJmeno) to a profile URL (no search API needed).
- Profile pages are Next.js; all data is in <script id="__NEXT_DATA__"> (Apollo cache).
  Overview: counts + satisfaction %, reviews tab: /hodnoceni, /hodnoceni/2, ... (10 per page).

Run: uv run spikes/employers/atmoskop_spike.py
"""

import collections
import json
import re
import time
import unicodedata
from pathlib import Path

import httpx

BASE = "https://www.atmoskop.cz"
FIX = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "employers" / "atmoskop"
FIX.mkdir(parents=True, exist_ok=True)
UA = "AdaWork/0.1.0 (local desktop job search; personal use)"
client = httpx.Client(
    headers={"User-Agent": UA, "Accept-Language": "cs"}, timeout=60, follow_redirects=True
)

# Apollo ROOT_QUERY keys that are big and irrelevant for reviews; dropped from fixtures.
DROP_PREFIXES = (
    "lmcFooter",
    "choicesForJobDescriptionListFilter",
    "profileVisualContentGalleryData",
    "jobDescriptionsList",
    "suitableForTiles",
    "enabledFeatures",
    "cookieNamesByCategoryForCurrentUser",
    "requestContextInformation",
)


def get(url: str) -> httpx.Response:
    r = client.get(url)
    time.sleep(2)
    return r


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def next_data(html: str) -> dict:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise ValueError("no __NEXT_DATA__")
    return json.loads(m.group(1))


def trimmed_fixture(html: str, data: dict) -> str:
    root = data["props"]["pageProps"]["initialApolloState"]["ROOT_QUERY"]
    for k in list(root):
        if k.startswith(DROP_PREFIXES):
            del root[k]
    title = re.search(r"<title>(.*?)</title>", html, re.S)
    return (
        f'<!DOCTYPE html><html lang="cs"><head><title>{title.group(1) if title else ""}</title></head>'
        "<body><!-- trimmed fixture: only __NEXT_DATA__ kept, large unrelated Apollo keys removed -->"
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data, ensure_ascii=False)}</script>'
        "</body></html>"
    )


def parse_profile(data: dict) -> dict:
    apollo = data["props"]["pageProps"]["initialApolloState"]
    root = apollo["ROOT_QUERY"]
    ov = next((v for k, v in root.items() if k.startswith("profileOverview(")), None) or {}
    prof = next((v for k, v in apollo.items() if k.startswith("AbstractProfile:")), {})
    out = {
        "id": prof.get("id"),
        "name": prof.get("name"),
        "address": prof.get("getAddress"),
        "field": prof.get("fieldOfBusinessTitle"),
        "employees": prof.get("numberOfEmployees"),
        "count_reviews": ov.get("countOfReviews"),
        "count_comments": ov.get("countOfComments"),
        "employer_pct": ov.get("employerSatisfaction"),
        "colleagues_pct": ov.get("colleaguesSatisfaction"),
        "boss_pct": ov.get("bossSatisfaction"),
        "praised_for": [
            (
                apollo.get(t["multiChoiceOption"]["__ref"], {}).get("questionnaireLabel")
                or t["multiChoiceOption"]["__ref"],
                t["numberOfEmployeesRecommending"],
            )
            for t in ov.get("top3SuitableFor") or []
        ],
    }
    text = next((v for k, v in root.items() if k.startswith("profileTextData(")), None)
    if text:
        ans = text["answers"]
        out["reviews_paginator"] = {
            k: ans["paginator"][k] for k in ("itemsCount", "currentPage", "lastPage")
        }
        out["reviews"] = []
        for ref in ans["currentPageItems"]:
            rv = apollo[ref["__ref"]]
            out["reviews"].append(
                {
                    "id": rv["id"],
                    "positive": rv.get("valueDiamond"),
                    "negative": rv.get("valueBulb"),
                    "date": rv.get("monthAndYearOfFinishFormatted"),
                    "added": rv.get("howLongAgoAdded"),
                    "current_employee": rv.get("currentlyEmployed"),
                    "tenure": rv.get("howLongWithCompanyLabel"),
                    "dept": rv.get("specializationLabel"),
                    "votes": rv.get("numberOfVotes"),
                    "employer_reply": rv.get("replyText"),
                }
            )
    return out


def main() -> None:
    r = get(f"{BASE}/robots.txt")
    (FIX / "robots.txt").write_text(r.text, encoding="utf-8")
    r = get(f"{BASE}/sitemap.xml")
    urls = re.findall(
        r"<loc>(https://www\.atmoskop\.cz/nazory-na-zamestnavatele/[^<]+)</loc>", r.text
    )
    print("sitemap bytes:", len(r.content), "company urls:", len(urls))
    (FIX / "sitemap_sample.xml").write_text(
        r.text[:3000] + "\n<!-- trimmed -->\n</urlset>\n", encoding="utf-8"
    )
    index: dict[str, list[str]] = collections.defaultdict(list)
    for u in urls:
        m = re.match(r".*/nazory-na-zamestnavatele/(\d+)-(.+)$", u)
        if m:
            index[m.group(2)].append(u)
    print(
        "distinct slugs:",
        len(index),
        "ambiguous slugs:",
        sum(1 for v in index.values() if len(v) > 1),
    )

    ares_names = [
        "Albert Česká republika, s.r.o.",
        "Lidl Česká republika s.r.o.",
        "Kaufland Česká republika v.o.s.",
        "Škoda Auto a.s.",
        "Penny Market s.r.o.",
        "ManpowerGroup s.r.o.",
        "Stavby Kohout s.r.o.",
    ]
    for n in ares_names:
        print(f"  {n!r:40} -> {slugify(n):40} {index.get(slugify(n))}")

    for slug_url, label in [
        (index[slugify("Penny Market s.r.o.")][0], "penny_market"),
        (
            index[slugify("ManpowerGroup s.r.o.")][0]
            if index.get(slugify("ManpowerGroup s.r.o."))
            else index[slugify("Albert Česká republika, s.r.o.")][0],
            "manpowergroup",
        ),
    ]:
        for tab in ("", "/hodnoceni"):
            r = get(slug_url + tab)
            print(label, tab or "/", r.status_code, len(r.content))
            data = next_data(r.text)
            parsed = parse_profile(data)
            print(
                json.dumps({k: v for k, v in parsed.items() if k != "reviews"}, ensure_ascii=False)
            )
            for rv in parsed.get("reviews", [])[:3]:
                print(
                    "   ",
                    rv["date"],
                    "|+",
                    (rv["positive"] or "")[:80],
                    "|-",
                    (rv["negative"] or "")[:80],
                )
            name = f"{label}{'_hodnoceni' if tab else '_overview'}.html"
            (FIX / name).write_text(trimmed_fixture(r.text, data), encoding="utf-8")
            (FIX / name.replace(".html", ".expected.json")).write_text(
                json.dumps(parsed, ensure_ascii=False, indent=1), encoding="utf-8"
            )


if __name__ == "__main__":
    main()
