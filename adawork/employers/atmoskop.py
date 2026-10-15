"""Atmoskop.cz employee reviews (PLAN §2.4, docs/spikes/employers.md).

Atmoskop has no IČO and no search we may use (robots.txt disallows /graphql/), so its weekly
sitemap is the index: profile URLs end in "<id>-<webalized legal name>", which matches the
ARES name written the same way. The reviews tab ("/hodnoceni") carries everything in its
`__NEXT_DATA__` JSON: satisfaction in % (employer, colleagues, boss; no stars), counts and the
first 10 confirmed reviews (what is good, what could be better). One request per employer.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from adawork.employers.names import name_key, slug
from adawork.net.polite import PoliteFetcher

PROFILE = re.compile(
    r"<loc>(https://www\.atmoskop\.cz/nazory-na-zamestnavatele/(\d+)-([^<]+))</loc>"
)
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
# "Suitable for" tags of a profile, as Atmoskop's codes.
SUITABLE = {
    "OPTION_RECOMMEND_STABLE": "stabilní práce",
    "OPTION_RECOMMEND_JUNIOR": "začátečníci",
    "OPTION_RECOMMEND_VARIOUS": "různorodá práce",
    "OPTION_RECOMMEND_LEARNING": "učení se novému",
    "OPTION_RECOMMEND_FLEXIBLE": "flexibilita",
    "OPTION_RECOMMEND_ATMOSPHERE": "dobrý kolektiv",
}


class SlugIndex:
    """Sitemap slugs -> profile URLs; reloaded when the weekly download changes."""

    def __init__(self, sitemap: Path) -> None:
        self.sitemap = sitemap
        self._mtime = 0.0
        self.by_slug: dict[str, str | None] = {}  # None: two profiles share the slug
        self.by_key: dict[str, str | None] = {}  # the slug without its legal form

    def fresh(self) -> "SlugIndex":
        mtime = self.sitemap.stat().st_mtime if self.sitemap.exists() else 0.0
        if mtime != self._mtime:
            by_slug: dict[str, str | None] = {}
            by_key: dict[str, str | None] = {}
            text = self.sitemap.read_text(encoding="utf-8") if mtime else ""
            for url, _id, s in PROFILE.findall(text):
                by_slug[s] = None if s in by_slug else url
                key = name_key(s.replace("-", " "))
                by_key[key] = None if key in by_key else url
            # Swapped whole (another thread may be looking up), and only once loaded.
            self.by_slug, self.by_key, self._mtime = by_slug, by_key, mtime
        return self

    def find(self, name: str) -> str | None:
        """The profile of a company by its ARES name; only an unambiguous match counts."""
        url = self.by_slug.get(slug(name))
        if url:
            return url
        return self.by_key.get(name_key(name)) or None


@dataclass
class Reviews:
    url: str
    employer_pct: int | None = None
    colleagues_pct: int | None = None
    boss_pct: int | None = None
    ratings: int = 0  # how many rated
    comments: int = 0  # how many wrote a review
    suitable_for: list[str] = field(default_factory=list)
    texts: list[dict] = field(default_factory=list)  # {"id", "plus", "minus", "date", "current"}

    def scores(self) -> dict:
        return {
            "employer": self.employer_pct, "colleagues": self.colleagues_pct,
            "boss": self.boss_pct, "comments": self.comments, "suitable_for": self.suitable_for,
        }  # fmt: skip


def parse(html: str, url: str) -> Reviews:
    m = NEXT_DATA.search(html)
    if not m:
        raise ValueError("Atmoskop page without __NEXT_DATA__")
    apollo = json.loads(m.group(1))["props"]["pageProps"]["initialApolloState"]
    root = apollo.get("ROOT_QUERY", {})
    overview = next((v for k, v in root.items() if k.startswith("profileOverview(")), None) or {}
    out = Reviews(
        url=url,
        employer_pct=overview.get("employerSatisfaction"),
        colleagues_pct=overview.get("colleaguesSatisfaction"),
        boss_pct=overview.get("bossSatisfaction"),
        ratings=overview.get("countOfReviews") or 0,
        comments=overview.get("countOfComments") or 0,
    )
    for tag in overview.get("top3SuitableFor") or []:
        ref = (tag.get("multiChoiceOption") or {}).get("__ref", "")
        code = apollo.get(ref, {}).get("questionnaireLabel") or ref
        if code in SUITABLE:
            out.suitable_for.append(SUITABLE[code])
    text = next((v for k, v in root.items() if k.startswith("profileTextData(")), None)
    for ref in ((text or {}).get("answers") or {}).get("currentPageItems") or []:
        review = apollo.get(ref.get("__ref", ""), {})
        plus = (review.get("valueDiamond") or "").strip()
        minus = (review.get("valueBulb") or "").strip()
        if plus or minus:
            out.texts.append(
                {
                    "id": review.get("id"),
                    "plus": _tidy(plus),
                    "minus": _tidy(minus),
                    "date": review.get("monthAndYearOfFinishFormatted") or "",
                    "current": bool(review.get("currentlyEmployed")),
                }
            )
    return out


def _tidy(text: str) -> str:
    """Atmoskop's texts put spaces before punctuation ("krám , dá se")."""
    text = re.sub(r"\s+([,.;:!?)])", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()[:2000]


def fetch(fetcher: PoliteFetcher, profile_url: str) -> Reviews | None:
    """The reviews tab of a profile; None when it is gone or robots.txt disallows it."""
    url = profile_url.rstrip("/") + "/hodnoceni"
    resp = fetcher.get(url)
    if resp is None:
        return None
    resp.raise_for_status()
    return parse(resp.text, profile_url)
