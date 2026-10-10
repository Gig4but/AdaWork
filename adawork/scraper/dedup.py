"""Finds the same job offer published on several sources (PLAN §4.4 step 7).

Portals re-list each other's ads (prace.cz/jobs.cz, profesia, agencies, Úřad práce), each
under its own ID, title wording and URL. A new ad is a duplicate of an existing job when it
comes from another source and has the same place, the same employer and a matching title;
when only part of the title matches, the descriptions must agree too (when both are long
enough to compare). A job placed only by
district (MPSV: "okres Náchod, Trutnov") matches a job in one of those districts when the
titles match.
"""

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from adawork.core import ParsedJob
from adawork.db.models import Job, JobSource, Location
from adawork.normalize.text import fold

_LEGAL_FORMS = re.compile(
    r"\b(spol s r o|s r o|sro|a s|as|k s|v o s|z s|o p s|z u|s p|se|gmbh|ltd|inc|"
    r"odstepny zavod|o z|prispevkova organizace|p o|druzstvo)\b"
)
_STOP = {
    "a", "i", "v", "ve", "na", "do", "pro", "s", "se", "z", "ze", "k", "ke", "od", "az", "u",
    "the", "and", "of", "for", "in", "with", "nebo", "or", "m", "f", "h", "d", "ka", "ce",
    "ice", "yne", "nice", "kyne", "prace", "nabidka", "hledame", "pozice", "nabor", "kc",
    "praha", "brno", "ostrava", "plzen",
}  # fmt: skip
TITLE_SAME = 0.6  # Jaccard of title words: duplicate on its own
TITLE_CONTAINED = 0.8  # share of the shorter title's words found in the longer one
DESCRIPTION_SAME = 0.2  # Jaccard of word 3-grams, required when titles only overlap
LONG_DESCRIPTION = 300  # characters; shorter descriptions are not compared


def employer_key(name: str | None) -> str:
    if not name:
        return ""
    text = re.sub(r"[^a-z0-9]+", " ", fold(name))
    text = _LEGAL_FORMS.sub(" ", text)
    return " ".join(text.split())


def title_words(title: str) -> frozenset[str]:
    words = re.findall(r"[a-z0-9]+", fold(title))
    return frozenset(w for w in words if len(w) > 1 and w not in _STOP)


def _shingles(text: str) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9]+", fold(text[:3000]))
    return {tuple(words[i : i + 3]) for i in range(len(words) - 2)}


def jaccard(a: set | frozenset, b: set | frozenset) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def place_key(municipality_code: str | None, location_key: str | None, place_text: str | None):
    if municipality_code:
        return municipality_code
    if location_key:
        return location_key
    if place_text:
        return "t:" + fold(place_text.split(",")[0]).strip()
    return None


@dataclass
class Entry:
    """One known job, as far as dedup needs it."""

    job_id: int | None
    sources: set[str]
    place: str
    employer: str
    ico: str | None
    title: frozenset[str]
    okresy: frozenset[str] = frozenset()  # folded district names
    district_only: bool = False  # placed by district, not by municipality
    job: Job | None = None  # not yet flushed: no id
    description: str | None = None  # loaded only when needed

    @property
    def ref(self) -> int | Job:
        if self.job_id is None and self.job is not None and self.job.id is not None:
            self.job_id, self.job = self.job.id, None
        if self.job_id is not None:
            return self.job_id
        assert self.job is not None
        return self.job


@dataclass
class DedupIndex:
    load_description: Callable[[int], str] = lambda job_id: ""
    _blocks: dict[tuple[str, str], list[Entry]] = field(default_factory=lambda: defaultdict(list))
    _by_ico: dict[tuple[str, str], list[Entry]] = field(default_factory=lambda: defaultdict(list))
    _by_okres: dict[tuple[str, str], list[Entry]] = field(default_factory=lambda: defaultdict(list))
    size: int = 0

    @classmethod
    def load(cls, session: Session, sessions: Callable[[], Session] | None = None) -> "DedupIndex":
        """Index of all active jobs (~40k rows read once per run).

        `sessions`: where descriptions are read later, when `session` may be closed.
        """

        def description(job_id: int) -> str:
            if sessions is None:
                return session.scalar(select(Job.description).where(Job.id == job_id)) or ""
            with sessions() as s:
                return s.scalar(select(Job.description).where(Job.id == job_id)) or ""

        index = cls(load_description=description)
        sources = (
            select(JobSource.job_id, func.group_concat(JobSource.source_id).label("ids"))
            .where(JobSource.status == "active")
            .group_by(JobSource.job_id)
            .subquery()
        )
        rows = session.execute(
            select(
                Job.id,
                Job.title,
                Job.employer_name,
                Job.employer_ico,
                Job.place_text,
                Location.municipality_code,
                Location.key,
                Location.okres,
                sources.c.ids,
            )
            .join(sources, sources.c.job_id == Job.id)
            .outerjoin(Location, Location.id == Job.location_id)
            .where(Job.status == "active")
        )
        for r in rows:
            entry = index.entry(r.title, r.employer_name, r.employer_ico, r.place_text,
                                r.municipality_code, r.key, r.okres,
                                set(r.ids.split(",")))  # fmt: skip
            if entry:
                entry.job_id = r.id
                index.add(entry)
        return index

    @staticmethod
    def entry(title, employer_name, ico, place_text, municipality_code, location_key, okres,
              sources):  # fmt: skip
        place = place_key(municipality_code, location_key, place_text)
        employer = employer_key(employer_name)
        if not place or not (employer or ico):
            return None  # too little to compare safely
        okresy = frozenset(fold(o).strip() for o in (okres or "").split(",") if o.strip())
        district_only = not municipality_code and (location_key or "").startswith("okres:")
        return Entry(None, sources, place, employer, ico, title_words(title or ""), okresy,
                     district_only)  # fmt: skip

    @classmethod
    def entry_for(cls, parsed: ParsedJob, source_id: str) -> Entry | None:
        loc = parsed.location
        return cls.entry(
            parsed.title,
            parsed.employer_name,
            parsed.employer_ico,
            parsed.place_text,
            loc.municipality_code if loc else None,
            loc.key if loc else None,
            loc.okres if loc else None,
            {source_id},
        )

    def add(self, entry: Entry) -> None:
        if entry.employer:
            self._blocks[(entry.place, entry.employer.split()[0])].append(entry)
        if entry.ico:
            self._by_ico[(entry.place, entry.ico)].append(entry)
        if entry.employer:
            for okres in entry.okresy:
                self._by_okres[(okres, entry.employer.split()[0])].append(entry)

    def find(self, parsed: ParsedJob, source_id: str) -> Entry | None:
        new = self.entry_for(parsed, source_id)
        if new is None or not new.title:
            return None
        candidates: dict[int, Entry] = {}
        if new.employer:
            for e in self._blocks.get((new.place, new.employer.split()[0]), ()):
                candidates[id(e)] = e
        if new.ico:
            for e in self._by_ico.get((new.place, new.ico), ()):
                candidates[id(e)] = e
        district: dict[int, Entry] = {}  # same district, one of the two placed by district only
        if new.employer:
            for okres in new.okresy:
                for e in self._by_okres.get((okres, new.employer.split()[0]), ()):
                    if id(e) not in candidates and (e.district_only or new.district_only):
                        district[id(e)] = e

        best, best_score = None, 0.0
        for e in [*candidates.values(), *district.values()]:
            if source_id in e.sources or not self._same_employer(new, e):
                continue
            if id(e) in district:
                same = jaccard(new.title, e.title)
                score = same if same >= TITLE_SAME else 0.0
            else:
                score = self._title_score(new, e, parsed.description)
            if score > best_score:
                best, best_score = e, score
        return best

    @staticmethod
    def _same_employer(a: Entry, b: Entry) -> bool:
        if a.ico and b.ico:
            return a.ico == b.ico
        if a.employer == b.employer:
            return True
        short, long = sorted((set(a.employer.split()), set(b.employer.split())), key=len)
        return bool(short) and short <= long and max(map(len, short)) >= 3

    def _title_score(self, new: Entry, old: Entry, description: str) -> float:
        if not old.title:
            return 0.0
        same = jaccard(new.title, old.title)
        if same >= TITLE_SAME:
            return 1.0 + same
        shorter = min(new.title, old.title, key=len)
        contained = len(new.title & old.title) / len(shorter)
        if contained < TITLE_CONTAINED:
            return 0.0
        if old.description is None and old.job_id is not None:
            old.description = self.load_description(old.job_id)
        old_text = old.description or (old.job.description if old.job else "") or ""
        if len(description) >= LONG_DESCRIPTION and len(old_text) >= LONG_DESCRIPTION:
            overlap = jaccard(_shingles(description), _shingles(old_text))
            return overlap + contained if overlap >= DESCRIPTION_SAME else 0.0
        return contained
