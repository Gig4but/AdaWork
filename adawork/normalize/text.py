"""Rules that turn portal text into the app's values: plain text, salaries, contract types."""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from html import unescape

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adawork.core import SalaryUnit

_BLOCK_TAGS = {
    "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table",
    "section", "article", "header", "footer", "blockquote", "pre", "dd", "dt", "hr",
}  # fmt: skip
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "button", "form"}
_LEADING_SYMBOLS = re.compile(r"^[^\w(]+")
_DATE = re.compile(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})")


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


def clean_title(text: str | None) -> str:
    """ "⭐Vedoucí směny" -> "Vedoucí směny": without the emoji or symbols titles start with."""
    return _LEADING_SYMBOLS.sub("", clean_spaces(text or ""))


def czech_date(text: str | None) -> date | None:
    """The first date written "7. 10. 2026" in a text."""
    m = _DATE.search(text or "")
    if not m:
        return None
    try:
        return date(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        return None


def card_date(text: str | None) -> datetime | None:
    """ "Dnes", "Včera" or "7. 10. 2026", as listing cards show the day an ad was posted."""
    folded = fold(text or "")
    if folded == "dnes":
        day = date.today()
    elif folded == "vcera":
        day = date.today() - timedelta(days=1)
    else:
        day = czech_date(folded)
    return datetime.combine(day, time()) if day else None


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


# A line of ad text that states the pay: an amount in Kč and a pay word. Bonuses, meal
# vouchers or allowances name amounts that are not the wage, often on the wage's line
# ("Nástupní bonus 10 000 Kč, mzda 30 000 Kč"), so the line is read clause by clause.
_PAY_LINE = re.compile(r"\d[\d . ]*(?:,-)?\s*(?:kc|czk)\b|\d\s*(?:tis|k)\b.*kc")
_PAY_WORD = re.compile(r"mzd|plat|vydel|odmen|prijem|hrub|cist|/\s*(?:hod|h|mes)|za hodinu|mesicne")
_WAGE_WORD = re.compile(r"(?<![a-z])(?:mzd|plat|odmen|hodinov)")  # not "příplatky"
_NOT_PAY = re.compile(
    r"naborov|bonus|premi|priplat|prispev|stravenk|odstupn|provize|zaloh|benefit|kurz|cena|poplat"
    r"|doporuc|stabilizac|vernost|jednoraz"
)
# Not before "do"/"až"/",-": "od 25 000, do 30 000 Kč" and "30 000,- Kč" are one amount.
_CLAUSE = re.compile(r";|\(|\)|\s\+\s|\s(?:a|plus)\s|,(?!\s*(?:-|do\b|az\b))\s")
_MONTH_RANGE, _HOUR_RANGE = (8_000, 500_000), (80, 3_000)
_THOUSANDS = re.compile(r"(\d+(?:[,.]\d)?)\s*(?:tis\w*\.?|k\b)", re.IGNORECASE)  # "35 tis."


# "32-38k", "30 – 35 tis.": the word applies to both numbers.
_THOUSANDS_RANGE = re.compile(
    r"(\d+(?:[,.]\d)?)\s*[-–]\s*(\d+(?:[,.]\d)?)\s*(?:tis\w*\.?|k\b)", re.IGNORECASE
)


def _range(m: re.Match) -> str:
    low, high = (round(float(g.replace(",", ".")) * 1000) for g in m.groups())
    return f"{low} - {high} "


def _thousands(m: re.Match) -> str:
    return str(round(float(m.group(1).replace(",", ".")) * 1000)) + " "


def salary_in_text(text: str | None) -> Salary | None:
    """The pay an ad states in its text ("Mzda 32 000 – 38 000 Kč měsíčně", "180 Kč/hod."),
    for ads without a structured salary. Only the first clear pay line counts."""
    for line in (text or "").splitlines():
        folded = fold(clean_spaces(line))
        if not _PAY_WORD.search(folded):
            continue
        clauses = [
            c
            for c in _CLAUSE.split(folded)
            if _PAY_LINE.search(c)
            and (not _NOT_PAY.search(c) or re.search(r"(?<![a-z])(?:mzd|plat)", c))
        ]
        # The wage clauses together ("plat 35 000 Kč, při plném výkonu až 45 000 Kč"), else
        # each alone, those naming the wage first ("hodinová mzda 180 Kč, měsíčně 30 000 Kč").
        tries = [" ".join(clauses)] if len(clauses) > 1 else []
        tries += sorted(clauses, key=lambda c: not _WAGE_WORD.search(c))
        for clause in tries:
            salary = parse_salary(_THOUSANDS.sub(_thousands, _THOUSANDS_RANGE.sub(_range, clause)))
            if salary is None or salary.currency != "CZK" or salary.unit == "piece":
                continue
            low, high = _HOUR_RANGE if salary.unit == "hour" else _MONTH_RANGE
            if all(v is None or low <= v <= high for v in (salary.min, salary.max)):
                return salary
    return None


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


# Lowest level first: "SŠ s maturitou nebo VŠ" asks for at least maturita (the lowest
# alternative is taken when several levels are listed, see `education_code`).
_EDUCATION_WORDS = (
    ("basic", r"zakladn|\bzs\b"),
    ("vocational", r"vyucen|vyucni|vyuc\b|bez maturity|stredni odborne|professional certificate"),
    # Not in "Středoškolské bez maturity".
    ("maturita", r"^(?!.*bez maturit).*(?:maturit|stredoskol|uplne stredni|\bss\b|high school)"),
    ("higher_vocational", r"vyssi odborn|\bvos\b|associate"),
    ("bachelor", r"bakal|bachelor|vysokoskol|\bvs\b|univerzit"),
    ("master", r"magist|master|inzenyr|postgraduate"),
    ("doctoral", r"doktor|ph\.?d|doctor"),
)
_EDUCATION_OR = re.compile(r"[,;]|/(?!-?a\b)|\b(?:nebo|ci|or)\b")


def education_code(text: str | None) -> str | None:
    """The education a portal label asks for: the lowest of the alternatives it lists, each
    alternative at the highest level it names. "Základní, Vyučení, SŠ s maturitou" -> basic,
    "Střední odborné vzdělání s maturitou" -> maturita, "Vyučení bez maturity" -> vocational."""
    found = []
    for part in _EDUCATION_OR.split(fold(text or "")):
        named = [i for i, (_, pattern) in enumerate(_EDUCATION_WORDS) if re.search(pattern, part)]
        if named:
            found.append(max(named))
    return _EDUCATION_WORDS[min(found)][0] if found else None


_SUITABLE_WORDS = (
    ("absol", ("absolvent",)),
    ("OZP2", ("ozp", "ztp", "zdravotnim postizenim", "invalid")),
    ("mladi", ("mladistv", "od 14 let", "od 15 let")),
)


def suitable_for(text: str | None) -> list[str]:
    """Groups a portal's "Vhodné pro" label names: "Absolventy, OZP" -> ["absol", "OZP2"]."""
    folded = fold(text or "")
    return [code for code, words in _SUITABLE_WORDS if any(w in folded for w in words)]


def split_places(text: str | None) -> list[str]:
    """ "Praha-Libeň, Středočeský" -> ["Praha-Libeň", "Středočeský"]."""
    if not text:
        return []
    return [p.strip() for p in re.split(r"[,;]| \+ ", text) if p.strip()]


def slugify(text: str) -> str:
    """ "Kutná Hora" -> "kutna-hora", as portals write places in URLs."""
    return re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-")


def valid_ico(text: str | None) -> bool:
    """Eight digits with the right check digit (mod 11): a typo is not a company."""
    if not text or not re.fullmatch(r"\d{8}", text):
        return False
    total = sum(int(d) * w for d, w in zip(text[:7], range(8, 1, -1), strict=True))
    return (11 - total % 11) % 10 == int(text[7])
