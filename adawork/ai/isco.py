"""Occupation codes (CZ-ISCO) for ads that come without one (PLAN §6.1).

Úřad práce labels every ad with a CZ-ISCO code, which gives tens of thousands of examples
"job title -> code". For a portal ad, the most similar labelled titles are found two ways:
- embeddings (multilingual-e5-small), which catch synonyms ("skladník" ~ "pracovník skladu");
- shared words, which catch exact trade names the small embedding model mixes up.
Their votes give a short list of 4-digit unit groups. When the vote is clear, the code is
taken directly; otherwise the AI model picks one from the list (it can only answer with a
listed code). The code then gives the job its categories (obory), like MPSV ads.
"""

import json
import logging
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from adawork.ai.embeddings import Embedder
from adawork.db.models import Job, JobSource
from adawork.normalize.text import fold

log = logging.getLogger(__name__)

NEIGHBOURS = 15
COMMON_WORD = 4000  # words in more titles than this carry no signal ("pracovník")
WORD = re.compile(r"[a-z0-9]{3,}")
# A clear vote: the leading group has this share of the combined score and the next one is
# far behind. Measured on the golden set (docs/spikes.md, M5).
CLEAR_SHARE = 0.6


@dataclass(frozen=True)
class Occupation:
    code: str
    name: str
    categories: tuple[str, ...]


@dataclass(frozen=True)
class Candidate:
    code: str  # 4-digit unit group
    name: str
    score: float
    examples: tuple[str, ...]  # a few labelled titles that voted for it


class IscoCodebook:
    def __init__(self, occupations: dict[str, Occupation]) -> None:
        self.occupations = occupations

    @classmethod
    def load(cls, path: Path) -> "IscoCodebook":
        items = json.loads(path.read_text(encoding="utf-8"))["polozky"]
        occupations = {}
        for item in items:
            categories = tuple(
                ref["id"].split("/", 1)[1] for ref in item.get("oboryCinnosti") or ()
            )
            occupations[item["kod"]] = Occupation(item["kod"], item["nazev"]["cs"], categories)
        return cls(occupations)

    def name(self, code: str) -> str:
        occ = self.occupations.get(code)
        return occ.name if occ else code

    def categories(self, code: str) -> list[str]:
        """Categories of the code, or of its closest parent that has some."""
        for size in range(len(code), 0, -1):
            occ = self.occupations.get(code[:size])
            if occ and occ.categories:
                return list(occ.categories)
        return []


def _words(title: str) -> set[str]:
    return set(WORD.findall(fold(title)))


class IscoIndex:
    def __init__(self, codebook: IscoCodebook, embedder: Embedder) -> None:
        self.codebook = codebook
        self.embedder = embedder
        self.titles: list[str] = []
        self.codes: list[str] = []
        self.vectors = np.zeros((0, 384), dtype=np.float32)
        self._words: list[set[str]] = []
        self._inverted: dict[str, list[int]] = defaultdict(list)

    def __len__(self) -> int:
        return len(self.titles)

    @staticmethod
    def examples(session: Session, codebook: IscoCodebook) -> list[tuple[str, str]]:
        """(title, 5-digit code) pairs: every distinct Úřad práce title, plus the codebook's
        own occupation names so codes without ads can still be found."""
        stmt = (
            select(Job.title, Job.isco_code)
            .join(JobSource, JobSource.job_id == Job.id)
            .where(JobSource.source_id == "mpsv", Job.isco_code.is_not(None))
        )
        pairs = {(fold(t).strip(), c): (t.strip(), c) for t, c in session.execute(stmt) if c}
        for occ in codebook.occupations.values():
            if len(occ.code) == 5:
                pairs.setdefault((fold(occ.name), occ.code), (occ.name, occ.code))
        return sorted(pairs.values())

    def build(self, examples: Iterable[tuple[str, str]], cache: Path | None = None) -> None:
        """Embeds the titles; vectors of titles seen before are reused from `cache`."""
        pairs = list(examples)
        titles = [t for t, _ in pairs]
        known: dict[str, np.ndarray] = {}
        if cache and cache.exists():
            try:
                with np.load(cache, allow_pickle=False) as data:
                    known = dict(zip(data["titles"].tolist(), data["vectors"], strict=True))
            except (OSError, ValueError, KeyError) as exc:
                log.warning("ISCO cache unreadable, rebuilding: %s", exc)
        missing = sorted({t for t in titles if t not in known})
        if missing:
            log.info("Embedding %d new job titles", len(missing))
            for title, vector in zip(missing, self.embedder.embed(missing), strict=True):
                known[title] = vector.astype(np.float16)
        self.titles = titles
        self.codes = [c for _, c in pairs]
        self.vectors = (
            np.stack([known[t] for t in titles]).astype(np.float32)
            if titles
            else (np.zeros((0, 384), dtype=np.float32))
        )
        if cache and missing:
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(".tmp.npz")
            np.savez(tmp, titles=np.array(titles), vectors=self.vectors.astype(np.float16))
            tmp.replace(cache)
        self._words = [_words(t) for t in titles]
        self._inverted = defaultdict(list)
        for i, words in enumerate(self._words):
            for w in words:
                self._inverted[w].append(i)

    def _votes(self, title: str, vector: np.ndarray) -> dict[str, list[tuple[float, int]]]:
        """4-digit group -> [(similarity, example index)] from both neighbour searches."""
        votes: dict[str, list[tuple[float, int]]] = defaultdict(list)
        if not self.titles:
            return votes
        sims = self.vectors @ vector
        for i in np.argpartition(-sims, min(NEIGHBOURS, len(sims) - 1))[:NEIGHBOURS]:
            votes[self.codes[i][:4]].append((float(sims[i]), int(i)))
        query = _words(title)
        overlap: dict[int, int] = defaultdict(int)
        for w in query:
            hits = self._inverted.get(w, ())
            if len(hits) < COMMON_WORD:
                for i in hits:
                    overlap[i] += 1
        scored = sorted(
            ((n / len(query | self._words[i]), i) for i, n in overlap.items()), reverse=True
        )[:NEIGHBOURS]
        for score, i in scored:
            votes[self.codes[i][:4]].append((score, i))
        return votes

    def lookup(self, title: str, vector: np.ndarray | None = None, limit: int = 8) -> "Lookup":
        if vector is None:
            vector = np.asarray(self.embedder.embed([title])[0])
        votes = self._votes(title, vector)
        ranked = sorted(votes.items(), key=lambda kv: -sum(s for s, _ in kv[1]))[:limit]
        candidates = []
        for code, hits in ranked:
            hits = sorted(hits, reverse=True)
            examples = tuple(dict.fromkeys(self.titles[i] for _, i in hits))[:3]
            candidates.append(
                Candidate(code, self.codebook.name(code), sum(s for s, _ in hits), examples)
            )
        return Lookup(candidates, votes, self.codes)

    def lookup_many(self, titles: list[str]) -> list["Lookup"]:
        """Same as `lookup` for many titles, with one batched embedding call."""
        vectors = self.embedder.embed(titles) if titles else []
        return [self.lookup(t, v) for t, v in zip(titles, vectors, strict=True)]


@dataclass
class Lookup:
    candidates: list[Candidate]
    votes: dict[str, list[tuple[float, int]]]
    codes: list[str]

    def clear_winner(self) -> Candidate | None:
        total = sum(c.score for c in self.candidates)
        if not self.candidates or total <= 0:
            return None
        best = self.candidates[0]
        return best if best.score / total >= CLEAR_SHARE else None

    def detail_code(self, group: str) -> str:
        """The most common 5-digit occupation within the chosen group among the neighbours."""
        counts: dict[str, float] = defaultdict(float)
        for score, i in self.votes.get(group, ()):
            counts[self.codes[i]] += score
        return max(counts, key=counts.__getitem__) if counts else group
