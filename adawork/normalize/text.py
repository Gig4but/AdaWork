"""Rules that turn portal text into the app's values: plain text, salaries, contract types."""

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from html import unescape

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adawork.core import SalaryUnit

_BLOCK_TAGS = {
    "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table",
    "section", "article", "header", "footer", "blockquote", "pre", "dd", "dt", "hr",
}  # fmt: skip
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "button", "form"}


def fold(text: str) -> str:
    """Lowercase without diacritics, for matching ("Plný úvazek" -> "plny uvazek")."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def accented_words(user_text: str) -> list[str]:
    """Search words typed with diacritics ("řidič"), lowercased."""
    return [w for w in re.findall(r"\w+", user_text.casefold()) if fold(w) != w]


@lru_cache(maxsize=256)
def _word_starts(words: str) -> list[re.Pattern]:
    return [re.compile(rf"(?<!\w){re.escape(w)}", re.IGNORECASE) for w in words.split()]


def has_word_starts(text: str | None, words: str) -> int:
    """1 if every word in `words` (space-separated) starts a word in `text`, diacritics
    included: "řidič" matches "řidiči", not "řídící". Registered as an SQLite function."""
    if not words:
        return 1
    text = text or ""
    return int(all(p.search(text) for p in _word_starts(words)))


def clean_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def html_to_text(html: str | None) -> str:
    """Readable plain text: one line per paragraph or list item, bullets for list items."""
    if not html:
        return ""
    if "&lt;" in html and "<" not in html:  # escaped markup
        html = unescape(html)
    if "<" not in html:
        return clean_spaces(html)
    root = LexborHTMLParser(f"<div>{html}</div>").body
    return node_text(root) if root else ""


def node_text(node: LexborNode) -> str:
    parts: list[str] = []

    def walk(n: LexborNode) -> None:
        for child in n.iter(include_text=True):
            tag = child.tag
            if tag == "-text":
                parts.append(child.text_content or "")
            elif tag in _SKIP_TAGS or tag == "-comment":
                continue
            elif tag in _BLOCK_TAGS:
                parts.append("\n• " if tag == "li" else "\n")
                walk(child)
                parts.append("\n")
            else:
                walk(child)

    walk(node)
    lines = (clean_spaces(line) for line in "".join(parts).split("\n"))
    text = "\n".join(line for line in lines if line and line != "•")
    return text.strip()


@dataclass
class Salary:
    min: int | None
    max: int | None
    unit: SalaryUnit | None
    currency: str = "CZK"


_AMOUNT = r"\d{1,3}(?:[ .  ]\d{3})+|\d+"
_UNITS: tuple[tuple[SalaryUnit, re.Pattern[str]], ...] = (
    ("hour", re.compile(r"(?<![a-z])(?:hod|hour)|/h(?![a-z])")),
    ("month", re.compile(r"(?<![a-z])(?:mes|month)")),
    ("piece", re.compile(r"(?<![a-z])(?:kus|ukol)")),
)
_CURRENCIES = {"kc": "CZK", "czk": "CZK", "eur": "EUR", "€": "EUR", "usd": "USD", "$": "USD"}


def _number(text: str) -> int:
    return int(re.sub(r"\D", "", text))


def parse_salary(text: str | None) -> Salary | None:
    """Portal salary text: "30 000 – 35 000 Kč/měsíc", "od 70 000 Kč", "182 Kč/hod."."""
    if not text:
        return None
    folded = fold(clean_spaces(text))
    amounts = [_number(m) for m in re.findall(_AMOUNT, folded)]
    amounts = [a for a in amounts if a >= 50]  # skips "13. plat", "4 dny", "24/7"
    if not amounts:
        return None
    currency = next((code for key, code in _CURRENCIES.items() if key in folded), "CZK")
    unit: SalaryUnit | None = next((u for u, pattern in _UNITS if pattern.search(folded)), None)
    if unit is None:
        unit = "hour" if max(amounts) < 1000 else "month"
    if len(amounts) >= 2:
        low, high = sorted(amounts[:2])
        return Salary(low, high, unit, currency)
    if re.search(r"\b(az|max|do)\b", folded) and not re.search(r"\bod\b", folded):
        return Salary(None, amounts[0], unit, currency)
    return Salary(amounts[0], None, unit, currency)


# Regular expressions on folded text, each anchored at a word start.
_CONTRACT_WORDS = (
    (
        "full_time",
        (
            r"plne?m?\w* (i |nebo )?(zkracen\w* )?uvaz",
            r"hpp(?![a-z])",
            r"hlavni\w* pracovni\w* pomer",
            "full_time",
            "full-time",
            "full time",
        ),
    ),
    (
        "part_time",
        (
            r"zkracen\w* (pracovn\w* )?(uvaz|dob)",
            r"castecn\w* uvaz",
            r"polovicni\w* uvaz",
            "part_time",
            "part-time",
            "part time",
        ),
    ),
    ("dpp", (r"dohod\w* o provedeni", r"dpp(?![a-z])")),
    ("dpc", (r"dohod\w* o pracovni cinnosti", r"dpc(?![a-z])")),
    ("contractor", ("zivnost", r"ico(?![a-z])", "contractor", "osvc")),
    # Not "praxe" ("bez praxe" = no experience needed), not "interní" (internal).
    ("internship", ("staz", "internship", r"intern(?![a-z])")),
)


def contract_types(*texts: str | None) -> list[str]:
    """Contract codes named in portal labels or schema.org employmentType values."""
    folded = " ".join(fold(t) for t in texts if t)
    found = []
    for code, words in _CONTRACT_WORDS:
        if any(re.search(rf"(?<![a-z]){w}", folded) for w in words):
            found.append(code)
    return found


def split_places(text: str | None) -> list[str]:
    """ "Praha-Libeň, Středočeský" -> ["Praha-Libeň", "Středočeský"]."""
    if not text:
        return []
    return [p.strip() for p in re.split(r"[,;]| \+ ", text) if p.strip()]
