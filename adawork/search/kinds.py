"""The kinds of work the user typed and the occupations (CZ-ISCO groups) they were mapped to
(by `adawork.ai.wanted`). A job is wanted when its occupation is one of those groups, or
when each word of a phrase starts a word of its title (before the job has an occupation).

The mapping is stored per phrase, with a plain job title that keyword-searched portals
(hitprace.cz, LinkedIn) are asked for besides the phrase itself.
"""

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from adawork.db.models import SourceState
from adawork.normalize.text import fold

STATE_ID = "kinds"  # SourceState row: phrase -> picked groups and a search title


def phrases(kinds_of_work: list[str]) -> list[str]:
    return list(dict.fromkeys(k.strip() for k in kinds_of_work if k.strip()))


def load(session: Session) -> dict[str, dict]:
    state = session.get(SourceState, STATE_ID)
    return dict(state.state or {}) if state else {}


def store(session: Session, resolved: dict[str, dict]) -> None:
    state = session.get(SourceState, STATE_ID)
    if state is None:
        state = SourceState(source_id=STATE_ID, enabled=False)
        session.add(state)
    state.state = resolved


def searches(session: Session, kinds_of_work: list[str]) -> list[str]:
    """Plain titles for the phrases that are not ordinary job titles themselves."""
    resolved = load(session)
    found = (resolved.get(p, {}).get("search") for p in phrases(kinds_of_work))
    return list(dict.fromkeys(s for s in found if s))


@dataclass(frozen=True)
class Wanted:
    words: tuple[tuple[str, ...], ...] = ()  # each phrase's folded words
    codes: tuple[str, ...] = ()  # unit groups (or shorter prefixes)

    @classmethod
    def load(cls, session: Session, kinds_of_work: list[str]) -> "Wanted":
        wanted = phrases(kinds_of_work)
        resolved = load(session)
        codes = (c for p in wanted for c in resolved.get(p, {}).get("codes", ()))
        return cls(tuple(_words(p) for p in wanted), tuple(dict.fromkeys(codes)))

    def matches(self, title: str, isco_code: str | None) -> bool:
        if not self.words:
            return True  # nothing chosen: every kind of work
        if isco_code and self.codes and isco_code.startswith(self.codes):
            return True
        starts = _words(title)
        return any(all(any(t.startswith(w) for t in starts) for w in ws) for ws in self.words)


def _words(text: str) -> tuple[str, ...]:
    """Folded words; a phrase matches a title when each of its words starts a title word
    ("řidič" -> "Řidiči MHD", but "it" not -> "kvalita")."""
    return tuple(re.findall(r"\w+", fold(text)))
