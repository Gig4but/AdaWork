"""Maps the kinds of work the user typed to occupations (CZ-ISCO), so a vague phrase still
finds the right ads: "ucitelka ve skolce" -> 2342 preschool teachers, "družinářka" -> 2359.

Each phrase is looked up like an ad title (similar Úřad práce titles vote for unit groups).
When the vote is clear, its group is taken. Otherwise the model picks the groups that fit
from that short list, so it can only answer with real occupations; without a model only a
dominant group is taken. The result is stored and matched in `adawork.search.kinds`.
"""

import re
from typing import TYPE_CHECKING

from adawork.ai.client import BadAnswer
from adawork.normalize.text import clean_spaces, fold

if TYPE_CHECKING:
    from adawork.ai.client import LlmClient
    from adawork.ai.isco import IscoIndex

LIKELY_SHARE = 0.5  # without a model: the top group's share of the vote to take it
MAX_PICKS = 3

SYSTEM = """Uživatel napsal vlastními slovy, jakou práci hledá (může psát bez diakritiky, \
hovorově nebo jen obor či místo). Vyber ze seznamu 1–3 profese, které takovou práci \
opravdu nabízejí, nejlepší první. Vrať JSON."""


def resolve(index: "IscoIndex", phrase: str, client: "LlmClient | None" = None) -> dict:
    """{"codes": unit groups, "search": a plain title or "", "ai": whether a model chose}."""
    found = index.lookup(phrase)
    candidates = found.candidates
    winner = found.clear_winner()
    if winner:
        codes = [winner.code]
    elif client is not None and candidates:
        codes = _pick(client, phrase, candidates)
    else:
        total = sum(c.score for c in candidates)
        best = candidates[0] if candidates else None
        codes = [best.code] if best and total > 0 and best.score >= LIKELY_SHARE * total else []
    first = next((c for c in candidates if codes and c.code == codes[0]), None)
    titles = [plain_title(t) for t in first.examples] if first else []
    # "Učitel MŠ" over "Učitel": the qualifier is often what the user meant.
    title = max(titles, key=lambda t: (min(len(t.split()), 4), not t.isupper()), default="")
    a, b = fold(title), fold(phrase)
    search = title if title and a not in b and b not in a else ""
    return {"codes": codes, "search": search, "ai": client is not None or winner is not None}


def _pick(client: "LlmClient", phrase: str, candidates) -> list[str]:
    lines = [f"Hledá: {phrase[:100]}", "", "Profese:"]
    for c in candidates:
        lines.append(
            f"{c.code} {c.name}" + (f" (např. {', '.join(c.examples[:2])})" if c.examples else "")
        )
    offered = [c.code for c in candidates]
    schema = {
        "type": "object",
        "properties": {
            "profese": {
                "type": "array",
                "items": {"enum": offered},
                "minItems": 1,
                "maxItems": MAX_PICKS,
            }
        },
        "required": ["profese"],
        "additionalProperties": False,
    }
    reply = client.chat(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n".join(lines)}],
        schema=schema,
        max_tokens=60,
    )
    picked = reply.data.get("profese") if isinstance(reply.data, dict) else None
    if not isinstance(picked, list):
        raise BadAnswer(f"unexpected answer: {str(reply.data)[:200]}")
    picked = list(dict.fromkeys(c for c in picked if c in offered))
    if not picked:
        return []
    # Small models fill the list with unrelated groups (a carpenter for "družinářka"); the
    # further picks are kept only within the first one's major group.
    return [picked[0], *(c for c in picked[1:] if c[0] == picked[0][0])]


def plain_title(title: str) -> str:
    """A title usable as a search: "Vychovatelka/vychovatel školní družiny" -> "Vychovatelka
    školní družiny", "UČITEL / UČITELKA MŠ" -> "UČITEL MŠ", "skladník/ce (m+ž)" -> "skladník"."""
    text = re.sub(r"\([^)]*\)", " ", title)
    text = re.sub(r"\s*/\s*\w+", "", text)
    return clean_spaces(re.split(r",|\s-\s", text, maxsplit=1)[0]).strip(" -")
