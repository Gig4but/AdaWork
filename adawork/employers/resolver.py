"""Who is the employer: an ad's employer name -> IČO (PLAN §4.5).

The service tries the cheap ways first (the IČO in the ad, names learned from ads that carry
one, the curated brand table, the agency register). This module does the rest: ARES name
search with a few variants, then ranking of the candidates:
- name similarity without the legal form (token_sort_ratio: token_set_ratio scores every
  name that merely contains the query as 100, e.g. the shop's trade union);
- legal form (unions, associations and foundations do not advertise jobs; sole traders
  rarely under a company-like name), still active;
- size (RES employee band) and main activity fitting the job's category;
- registered seat in the job's municipality (a bonus only: chains sit in Prague).

A clear winner resolves the name; close candidates go to the AI (with the ad text) or are
shown on the card as "nejisté přiřazení" for the user to pick.
"""

from dataclasses import dataclass, field

from rapidfuzz import fuzz

from adawork.employers.names import core_words, legal_form_in, variants, words
from adawork.employers.official import (
    ASSOCIATIONS,
    NOT_EMPLOYERS,
    SOLE_TRADERS,
    Ares,
    Company,
    TooMany,
)

MAX_QUERIES = 4
MAX_HITS = 200  # more hits than this: ordered by IČO, the right one may be missing
SURE = 0.85  # score of a clear winner...
MARGIN = 0.08  # ...ahead of the next one by this much...
SAME_NAME = 0.92  # ...and named (almost) the same: "Muzeum Policie ČR" is not "Policie ČR"
CANDIDATE = 0.6  # lower scores are not worth showing
SHOWN = 5
# Job category (normalize/vocab.CATEGORIES) -> CZ-NACE divisions that fit it.
_NACE = {
    "2": "49 50 51 52 53", "3": "64 65 66", "4": "58 59 60 61 62 63", "6": "90 91 92 93",
    "8": "45 46 47 55 56 79", "9": "80 84", "10": "41 42 43", "11": "72",
    "12": "85", "13": " ".join(f"{n:02}" for n in range(5, 34)),
    "14": "55 56 77 78 79 80 81 82 94 95 96", "15": "86 87 88", "16": "01 02 03",
}  # fmt: skip
NACE_FIT = {k: tuple(v.split()) for k, v in _NACE.items()}
AGENCY_NACE = "78"  # employment activities: agencies advertise every kind of job


@dataclass
class AdHints:
    """What the ads say besides the name: helps tell apart companies of similar names."""

    municipality_code: int | None = None
    categories: tuple[str, ...] = ()


@dataclass
class Candidate:
    company: Company
    score: float
    similarity: float = 0.0

    def as_dict(self) -> dict:
        c = self.company
        return {
            "ico": c.ico, "name": c.name, "score": round(self.score, 2),
            "seat": c.municipality, "band": c.band, "active": c.active,
        }  # fmt: skip


@dataclass
class Resolution:
    ico: str | None  # None: not resolved
    confidence: float = 0.0
    candidates: list[Candidate] = field(default_factory=list)
    method: str = "ares"  # ares | uncertain | none

    @property
    def companies(self) -> dict[str, Company]:
        return {c.company.ico: c.company for c in self.candidates}


def similarity(query: str, name: str) -> float:
    """Of the names without legal forms and country words ("Foxconn Czech Republic" =
    "FOXCONN CZ s.r.o."); the same legal form too adds a little."""
    a, b = " ".join(core_words(query)), " ".join(core_words(name))
    if not a or not b:
        return 0.0
    score = fuzz.token_sort_ratio(a, b) / 100
    if a == b and legal_form_in(query):
        whole = " ".join(words(query)) == " ".join(words(name))
        score += 0.05 if whole else 0.0  # the same legal form too
    return min(score, 1.0)


def best_similarity(queries: list[str], name: str) -> float:
    """To the variant of the ad's name that fits best (the one that found it)."""
    return max((similarity(q, name) for q in queries), default=0.0)


def score(queries: list[str], c: Company, ctx: AdHints) -> float:
    s = best_similarity(queries, c.name)
    if c.legal_form in NOT_EMPLOYERS:
        s -= 0.5
    elif c.legal_form in ASSOCIATIONS | SOLE_TRADERS and not legal_form_in(queries[0]):
        s -= 0.1  # rarely, unless the ad writes the legal form
    if not c.active:
        s -= 0.3
    s += 0.1 * min(c.band_rank, 19) / 19
    if c.nace_main:
        division = c.nace_main[:2]
        fits = division == AGENCY_NACE or any(
            division in NACE_FIT.get(cat, ()) for cat in ctx.categories
        )
        s += 0.05 if fits else 0.0
    if ctx.municipality_code and c.municipality_code == ctx.municipality_code:
        s += 0.05
    return s


def rank(queries: list[str], companies: list[Company], ctx: AdHints) -> list[Candidate]:
    ranked = [
        Candidate(c, score(queries, c, ctx), best_similarity(queries, c.name)) for c in companies
    ]
    ranked.sort(key=lambda c: (-c.score, c.company.ico))
    return ranked


def decide(
    ranked: list[Candidate], whole: list[str] | None = None, ctx: AdHints | None = None
) -> Resolution:
    """`whole`: the queries with every word of the ad's name. A company named like a shorter
    one only ("U Lípy s.r.o." for "Hospoda U Lípy") is sure only with its seat in the ad's town."""
    shown = [c for c in ranked if c.score >= CANDIDATE][:SHOWN]
    if not shown:
        return Resolution(None, 0.0, [], "none")
    best = shown[0]
    runner_up = shown[1].score if len(shown) > 1 else 0.0
    sure = best.score >= SURE and best.similarity >= SAME_NAME and best.score - runner_up >= MARGIN
    if sure and whole is not None and best_similarity(whole, best.company.name) < SAME_NAME:
        town = ctx.municipality_code if ctx else None
        sure = town is not None and best.company.municipality_code == town
    if sure:
        return Resolution(best.company.ico, min(best.score, 1.0), shown, "ares")
    return Resolution(None, min(best.score, 1.0), shown, "uncertain")


def resolve(
    ares: Ares, name: str, ctx: AdHints, places: frozenset[str] = frozenset()
) -> Resolution:
    """At most MAX_QUERIES ARES searches, stopping at the first variant with 1–200 hits.
    `places`: municipality names (folded), dropped from a name in one of the variants."""
    pool: dict[str, Company] = {}
    used: list[str] = []
    for query in variants(name, places)[:MAX_QUERIES]:
        used.append(query)
        try:
            total, found = ares.search(query, MAX_HITS)
        except TooMany:
            continue
        if not found:
            continue
        for c in found:
            pool.setdefault(c.ico, c)
        if total <= MAX_HITS:
            break
    if not pool:
        return Resolution(None, 0.0, [], "none")
    # Size and activity only for the names that could be it (one RES call).
    likely = sorted(pool.values(), key=lambda c: -best_similarity(used, c.name))[:40]
    ares.add_statistics([c for c in likely if c.band is None])
    size = len(core_words(name))
    whole = [q for q in used if len(core_words(q)) >= size]
    return decide(rank(used, likely, ctx), whole, ctx)
