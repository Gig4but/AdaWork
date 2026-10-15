"""AI summary of what employees and the web say about an employer (PLAN §4.5).

The local model gets numbered texts, grouped: what employees praise and what they criticize
(the two halves of each Atmoskop review, kept apart) and passages of web pages, which may
back only warnings. It writes
short points: what people praise, what they criticize, and warnings (unpaid wages, work
without a contract, an unpaid trial shift, fraud). Every point ends with the numbers of the
texts it comes from, so the card links each point to its sources.

The model must not invent, and a small one does (M9: "práce na černo" from a text about a
changed job title), so every point is checked against its sources and dropped when:
- it cites nothing, or only texts that do not exist;
- most of its words are not in the cited texts (in a list "A, B, C", each part on its own:
  the parts no source says are left out, so are the sources that say none of it);
- it praises only with complaints, or criticizes only with praise;
- it is a warning, but neither it nor its sources speak of one of the warning cases.
Customer reviews (Google) are about the shop, not the job, and are not summarized.
"""

import hashlib
import json
import math
import re
from dataclasses import dataclass

from adawork.ai.client import BadAnswer, LlmClient, Timing
from adawork.normalize.text import fold

PROMPT_VERSION = 5
MAX_ITEMS = 30  # numbered texts given to the model
ITEM_CHARS = 350
POINTS = 4
BACKED = 0.6  # of a point's words must be in its sources
SYSTEM = """Jsi pečlivý redaktor. Dostaneš očíslované texty o zaměstnavateli: co zaměstnanci \
chválí, co kritizují a úryvky z webu. Napiš česky krátké body vlastními slovy (nejvýš 10 slov): \
co lidé chválí (jen z chvály), co kritizují (jen z kritiky) a varování (z čehokoli). \
Varování je jen nevyplacená nebo krácená mzda, neproplacené přesčasy, práce na černo nebo bez \
smlouvy, neplacený zkušební den, podvod; jiná kritika do varování nepatří. Spoj, co říká víc \
textů. Piš jen to, co v textech opravdu stojí. Každý bod končí čísly textů, např. \
"Dobrý kolektiv [1,3]". Když pro část nic není, nech ji prázdnou. Vrať JSON."""
GROUPS = {"plus": "Co zaměstnanci chválí:", "minus": "Co zaměstnanci kritizují:",
          "text": "Z webu a diskusí (jen pro varování):"}  # fmt: skip
WARNING = re.compile(
    r"nevyplac|nevyplat|nezaplac|neproplac|nedoplat|nebyl\w* (vy|za|pro)placen|kracen"
    r"|zadrz\w* mzd|na cerno|bez smlouv"
    r"|bez pracovni smlouv|zkusebni (den|smen)|neplacen\w* (zkusebn|prescas|smen)|podvod|okrad"
)
_CITES = re.compile(r"\s*\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\]\s*$")
_STOP = frozenset({
    "jsou", "bylo", "byla", "jsem", "jsme", "byli", "mají", "máme", "není", "nejsou", "které",
    "který", "která", "také", "jako", "když", "aby", "ale", "nebo", "protože", "jejich", "této",
    "tento", "firma", "firmě", "firmy", "práce", "práci", "zaměstnanci", "lidé", "velmi", "hodně",
    "často", "vždy", "dobrý", "dobrá", "dobré", "špatný", "špatná", "špatné", "možnost",
})  # fmt: skip


@dataclass
class Item:
    source: str  # atmoskop | web
    url: str
    text: str
    kind: str = "text"  # plus | minus (the halves of a review) | text


@dataclass
class Summary:
    praise: list[dict]  # {"text", "sources": [item numbers]}
    criticism: list[dict]
    warnings: list[dict]
    dropped: int  # points not backed by their sources
    timing: Timing | None = None

    def as_dict(self) -> dict:
        return {
            "praise": self.praise, "criticism": self.criticism,
            "warnings": self.warnings, "dropped": self.dropped,
        }  # fmt: skip


def schema() -> dict:
    point = {"type": "string", "maxLength": 160, "x-cites": True}
    part = {"type": "array", "items": point, "maxItems": POINTS}
    return {
        "type": "object",
        "properties": {"praise": part, "criticism": part, "warnings": {**part, "maxItems": 3}},
        "required": ["praise", "criticism", "warnings"],
    }


def numbered(items: list[Item]) -> str:
    lines, group = [], None
    for i, it in enumerate(items, 1):
        if it.kind != group:
            group = it.kind
            lines.append(("\n" if lines else "") + GROUPS[group])
        lines.append(f"[{i}] {it.text[:ITEM_CHARS]}")
    return "\n".join(lines)


def inputs_key(items: list[Item]) -> str:
    """Changes when the texts change: the summary is then written again."""
    raw = json.dumps([(it.url, it.kind, it.text[:ITEM_CHARS]) for it in items], ensure_ascii=False)
    return hashlib.sha256(f"{PROMPT_VERSION}:{raw}".encode()).hexdigest()[:32]


def summarize(client: LlmClient, name: str, items: list[Item]) -> Summary:
    """`items`: praise first, then criticism, then the web (as `Reviews.items` gives them),
    at most MAX_ITEMS; they are numbered in this order."""
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"Zaměstnavatel: {name}\n\n{numbered(items)}"},
    ]
    reply = client.chat(messages, schema=schema(), max_tokens=700)
    data = reply.data
    if not isinstance(data, dict):
        raise BadAnswer("summary is not an object")
    summary = check(data, items)
    summary.timing = reply.timing
    return summary


def _stem_list(text: str) -> list[str]:
    """The words that carry meaning, by their first five letters, in order."""
    words = re.findall(r"\w+", text.casefold())
    return [fold(w)[:5] for w in words if len(w) >= 4 and w not in _STOP]


def _stems(text: str) -> set[str]:
    return set(_stem_list(text))


def backed(point: str, cited: list[str]) -> bool:
    """Most of the point's words appear in one of its sources, close together: "neplacená
    mzda" is not backed by "mzda pod průměrem, neplacené přesčasy"."""
    words = _stems(point)
    if not words:
        return False
    need = math.ceil(BACKED * len(words))
    for text in cited:
        positions = [(i, w) for i, w in enumerate(_stem_list(text)) if w in words]
        span = len(words) + 1  # the words of the point, give or take one in between
        for start, _ in positions:
            near = {w for i, w in positions if start <= i <= start + span}
            if len(near) >= need:
                return True
    return False


# Web passages back only warnings: a page has no side (a complaint read as praise, M9) and
# often speaks of other firms or of nothing in particular (job-site furniture).
ALLOWED = {"praise": ("plus",), "criticism": ("minus",), "warnings": ("plus", "minus", "text")}
_LABEL = re.compile(r"^(chvál\w*|kritiz\w*|varování)\s*:\s*", re.I)


def _fragment_backed(fragment: str, cited: list[str]) -> bool:
    """ "Nic." says nothing; "potřeba oh." was cut off mid-word: neither is kept."""
    last = re.findall(r"\w+", fragment)[-1:] or [""]
    return (len(last[0]) > 2 or last[0].isdigit()) and backed(fragment, cited)


def verify(part: str, text: str, cited: list[Item]) -> tuple[str, list[Item]] | None:
    """The point as far as its sources say it: sources of the wrong kind are left out (a
    complaint does not back praise), so are the parts of a list ("A, B, C") that no source
    says. None when nothing is left."""
    if "[" in text:  # citations inside the point: the model lost the format
        return None
    text = _LABEL.sub("", text)  # "Chvalí: ..." repeats the section's title
    cited = [it for it in cited if it.kind in ALLOWED[part]]
    if part == "warnings":
        if not WARNING.search(fold(text)):
            return None
        cited = [it for it in cited if WARNING.search(fold(it.text))]
    texts = [it.text[:ITEM_CHARS] for it in cited]
    if not texts:
        return None
    fragments = [f.strip() for f in re.split(r"[,;]\s+", text.rstrip(" .")) if f.strip()]
    kept = [f for f in fragments if _fragment_backed(f, texts)]
    if part == "warnings":  # each part of a warning is one
        kept = [f for f in kept if WARNING.search(fold(f))]
    if not kept:
        return None
    used = [it for it in cited if any(_fragment_backed(f, [it.text[:ITEM_CHARS]]) for f in kept)]
    return ", ".join(kept) + ".", used or cited


def check(data: dict, items: list[Item]) -> Summary:
    parts: dict[str, list[dict]] = {}
    dropped = 0
    for part in ("praise", "criticism", "warnings"):
        kept = []
        for raw in data.get(part) or []:
            m = _CITES.search(raw) if isinstance(raw, str) else None
            numbers = sorted({int(n) for n in re.findall(r"\d+", m.group(1))}) if m else []
            numbers = [n for n in numbers if 1 <= n <= len(items)]
            found = (
                verify(part, raw[: m.start()].strip(" .;-"), [items[n - 1] for n in numbers])
                if m and numbers
                else None
            )
            if found is None:
                dropped += 1
                continue
            text, used = found
            sources = [n for n in numbers if items[n - 1] in used]
            if all(fold(text) != fold(k["text"]) for k in kept):
                kept.append({"text": text[0].upper() + text[1:], "sources": sources})
        parts[part] = kept
    return Summary(parts["praise"], parts["criticism"], parts["warnings"], dropped)
