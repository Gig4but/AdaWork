"""Reads a free-text job ad with the local model (PLAN §6.1–6.5).

One call per ad. The answer is a small JSON object whose shape is enforced by llama-server
(the schema becomes a grammar), with Czech enum values a small model handles well. The
occupation can only be one of the candidates found by `IscoIndex`.

Everything the model says is checked against the ad before it is used:
- salary numbers must appear in the text and be plausible;
- the town must appear in the text and be a real municipality;
- the occupation must be one of the offered codes.
"""

import re
from dataclasses import dataclass, field

from adawork.ai.catalog import DEFAULT_STYLE, PromptStyle
from adawork.ai.client import LlmClient, Timing
from adawork.ai.isco import Candidate, Lookup
from adawork.core import SalaryUnit
from adawork.normalize.text import clean_spaces, fold

PROMPT_VERSION = 6
MAX_AD_CHARS = 3000  # ~1k tokens; keeps the context within 4k on the smallest machines
OTHER = "jiná"

SHIFTS = {
    "jednosměnný": "jednoSm",
    "dvousměnný": "dvouSm",
    "třísměnný": "triSm",
    "čtyřsměnný": "ctyrSm",
    "nepřetržitý": "nepretrzity",
    "noční": "nocni",
    "pružná doba": "pruznaPd",
    "dělené směny": "deleneSm",
    "turnusy": "turnus",
}
EDUCATION = {
    "základní": "basic",
    "vyučení": "vocational",
    "maturita": "maturita",
    "VOŠ": "higher_vocational",
    "bakalář": "bachelor",
    "magistr": "master",
    "doktorát": "doctoral",
}
WARNINGS = {
    "poplatek uchazeče": "fee",
    "neplacené zkušební období": "unpaid_trial",
    "MLM / provize bez mzdy": "mlm",
    "práce bez smlouvy": "no_contract",
}
UNITS: dict[str, SalaryUnit] = {"měsíc": "month", "hodina": "hour"}  # "mzda_za"

SYSTEM = """Jsi pečlivý asistent, který čte inzeráty práce v češtině a vrací údaje jako JSON.
Pravidla:
- Uveď jen to, co v inzerátu opravdu stojí. Co tam není, dej null nebo [].
- "nabidka": true pro nabídku práce; false, když člověk práci hledá nebo jde o jiný text.
{profese}
- "obec": obec nebo město, kde se pracuje, přesně jak je v textu (bez ulice); jinak null.
- "mzda_od", "mzda_do": částky v Kč z inzerátu jako celá čísla (tisíce rozepiš, "X tis." = X000);
  "mzda_za": "měsíc" nebo "hodina".
- "smeny": typ směn nebo pracovní doby, pokud je uveden.
- "vzdelani": nejnižší požadované vzdělání, jen pokud je v textu uvedeno
  (středoškolské s maturitou = "maturita", výuční list = "vyučení").
- "varovani": jen zjevné varovné signály (poplatek po uchazeči, neplacené zkušební období, MLM)."""


# Field groups the caller can leave out when the job already has them (fewer output tokens).
# Contract types are not asked: ads that state them use fixed phrases ("HPP", "DPP"), which
# the rules read better, and small models guess them where the ad says nothing (M5 eval).
FIELDS = ("profese", "obec", "mzda", "smeny", "vzdelani")


def schema(codes: list[str], fields: tuple[str, ...] = FIELDS, untrusted: bool = False) -> dict:
    """`untrusted`: posts that may not be job offers at all, or may be scams (forums, social
    groups, classifieds): also asks "nabidka" and "varovani"."""
    nullable = lambda values: {"enum": [*values, None]}  # noqa: E731
    groups = {
        "profese": {"profese": {"enum": [*codes, OTHER]}},
        "obec": {"obec": {"type": ["string", "null"], "maxLength": 60}},
        "mzda": {
            "mzda_od": {"type": ["integer", "null"]},
            "mzda_do": {"type": ["integer", "null"]},
            "mzda_za": nullable(["měsíc", "hodina"]),
        },
        "smeny": {"smeny": {"type": "array", "items": {"enum": list(SHIFTS)}, "maxItems": 3}},
        "vzdelani": {"vzdelani": nullable(list(EDUCATION))},
    }
    properties: dict = {"nabidka": {"type": "boolean"}} if untrusted else {}
    for name in FIELDS:
        if name in fields and (name != "profese" or codes):
            properties.update(groups[name])
    if untrusted:
        properties["varovani"] = {
            "type": "array",
            "items": {"enum": list(WARNINGS)},
            "maxItems": 3,
        }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


@dataclass
class Extraction:
    is_job_offer: bool = True
    isco_group: str | None = None  # 4-digit unit group from the candidates
    town: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_unit: SalaryUnit | None = None
    shifts: list[str] = field(default_factory=list)
    education: str | None = None
    warnings: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)  # model claims that failed the checks
    timing: Timing = field(default_factory=lambda: Timing(0, 0.0, 0, 0.0, 0.0))


def trim(text: str, limit: int = MAX_AD_CHARS) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit)
    return text[: cut if cut > limit // 2 else limit] + "\n…"


SYSTEM_EN = """You are a careful assistant that reads Czech job ads and returns their details
as JSON. Rules:
- Give only what the ad really says. If something is not in the ad, use null or [].
- "nabidka": true for a job offer; false when a person is looking for work or it is other text.
{profese}
- "obec": the town or village where the work is, exactly as written in the ad (no street);
  otherwise null.
- "mzda_od", "mzda_do": amounts in CZK from the ad as whole numbers (write thousands out,
  "X tis." = X000); "mzda_za": "měsíc" (month) or "hodina" (hour).
- "smeny": the shift pattern or working hours, if the ad states them.
- "vzdelani": the lowest education required, only if the ad states it
  (secondary with maturita = "maturita", apprenticeship certificate = "vyučení").
- "varovani": only obvious warning signs (a fee from the applicant, unpaid trial period, MLM).
All answer values must be copied exactly from the allowed Czech values."""

PROFESE_CODE_EN = """- "profese": the code from the list of possible occupations that best fits the
  position; if none fits, "jiná"."""
PROFESE_NAME_EN = """- "profese": the name from the list of possible occupations that best fits the
  position; if none fits, "jiná"."""
PROFESE_SHARES_EN = """
  Each occupation shows how often the Czech labour office used it for similar ads.
  When unsure, choose the most frequent one."""

PROFESE_CODE = """- "profese": kód ze seznamu možných profesí, který nejlépe odpovídá pozici;
  když žádný nesedí, "jiná"."""
PROFESE_NAME = """- "profese": název profese ze seznamu možných profesí, který nejlépe odpovídá
  pozici; když žádný nesedí, "jiná"."""
PROFESE_SHARES = """
  U každé profese je uvedeno, jak často ji úřad práce použil u podobných inzerátů.
  Když si nejsi jistý, vyber tu nejčastější."""


SKUPINA = """- "skupina": skupina profesí ze seznamu, do které pozice patří;
  když žádná nesedí, "jiná".
  Hlavní třídy podle kvalifikace: 1 řídící pracovníci; 2 specialisté (obvykle VŠ);
  3 techničtí a odborní pracovníci (obvykle SŠ s maturitou nebo VOŠ); 4 úředníci;
  5 pracovníci ve službách a prodeji; 6 zemědělci a lesníci; 7 řemeslníci a opraváři;
  8 obsluha strojů, montéři a řidiči; 9 pomocní a nekvalifikovaní pracovníci."""


def system_prompt(style: PromptStyle = DEFAULT_STYLE) -> str:
    if style.two_step:
        profese = SKUPINA + "\n" + (PROFESE_NAME if style.by_name else PROFESE_CODE)
        return SYSTEM.format(profese=profese + (PROFESE_SHARES if style.shares else ""))
    if style.english:
        profese = PROFESE_NAME_EN if style.by_name else PROFESE_CODE_EN
        return SYSTEM_EN.format(profese=profese + (PROFESE_SHARES_EN if style.shares else ""))
    profese = PROFESE_NAME if style.by_name else PROFESE_CODE
    return SYSTEM.format(profese=profese + (PROFESE_SHARES if style.shares else ""))


def choices(candidates: list[Candidate], style: PromptStyle = DEFAULT_STYLE) -> dict[str, str]:
    """Answer value -> 4-digit code; names get the code appended if two are equal."""
    if not style.by_name:
        return {c.code: c.code for c in candidates}
    out: dict[str, str] = {}
    for c in candidates:
        out[c.name if c.name not in out else f"{c.name} ({c.code})"] = c.code
    return out


LABELS_CS = {"list": "Možné profese:", "share": "podobné inzeráty", "eg": "např.", "ad": "Inzerát:"}
LABELS_EN = {"list": "Possible occupations:", "share": "similar ads", "eg": "e.g.", "ad": "Job ad:"}


def _options(header: str, candidates: list[Candidate], style: PromptStyle) -> list[str]:
    words = LABELS_EN if style.english else LABELS_CS
    lines = [header]
    total = sum(c.score for c in candidates) or 1.0
    for answer, c in zip(choices(candidates, style), candidates, strict=True):
        notes = []
        if style.shares:
            notes.append(f"{words['share']}: {round(100 * c.score / total)} %")
        if c.examples:
            notes.append(f"{words['eg']} " + ", ".join(c.examples[:2]))
        head = f"- {answer}" if style.by_name else f"{c.code} {c.name}"
        lines.append(head + (f" ({'; '.join(notes)})" if notes else ""))
    return lines


def ad_first_message(text: str, header: str, options: list[Candidate], style) -> str:
    """The ad first, so a second call about the same ad reuses the processed prompt."""
    lines = ["Inzerát:", trim(text)]
    if options:
        lines += [""] + _options(header, options, style)
    return "\n".join(lines)


def user_message(text: str, candidates: list[Candidate], style: PromptStyle = DEFAULT_STYLE) -> str:
    words = LABELS_EN if style.english else LABELS_CS
    lines = [*_options(words["list"], candidates, style), ""] if candidates else []
    return "\n".join([*lines, words["ad"], trim(text)])


def extract(
    client: LlmClient,
    text: str,
    candidates: list[Candidate],
    fields: tuple[str, ...] = FIELDS,
    untrusted: bool = False,
    style: PromptStyle = DEFAULT_STYLE,
    found: Lookup | None = None,
) -> Extraction:
    if style.two_step and found is not None and "profese" in fields:
        return _extract_two_step(client, text, found, fields, untrusted, style)
    offered = candidates if "profese" in fields else []
    answers = choices(offered, style)
    reply = client.chat(
        [
            {"role": "system", "content": system_prompt(style)},
            {"role": "user", "content": user_message(text, offered, style)},
        ],
        schema=schema(list(answers), fields, untrusted),
        max_tokens=200,
    )
    result = validate(reply.data, text, answers)
    result.timing = reply.timing
    return result


GROUPS = "Možné skupiny profesí:"


def _extract_two_step(client, text, found, fields, untrusted, style) -> Extraction:
    """Group first, then the occupation within the group (only if there is a choice)."""
    groups = found.groups()
    group_answers = choices(groups, style)
    first = schema(list(group_answers), fields, untrusted)
    properties = {("skupina" if k == "profese" else k): v for k, v in first["properties"].items()}
    first = first | {"properties": properties, "required": list(properties)}
    system = {"role": "system", "content": system_prompt(style)}
    reply = client.chat(
        [system, {"role": "user", "content": ad_first_message(text, GROUPS, groups, style)}],
        schema=first,
        max_tokens=200,
    )
    data = dict(reply.data)
    group = group_answers.get(data.pop("skupina", None) or "")
    result = validate(data, text, {})
    timings = [reply.timing]
    if group:
        inside = found.within(group)
        if len(inside) == 1:
            result.isco_group = inside[0].code
        elif inside:
            answers = choices(inside, style)
            header = f"Možné profese ve skupině {found.codebook.name(group)}:"
            second = client.chat(
                [
                    system,
                    {"role": "user", "content": ad_first_message(text, header, inside, style)},
                ],
                schema=schema(list(answers), ("profese",)),
                max_tokens=40,
            )
            result.isco_group = answers.get(second.data.get("profese") or "")
            timings.append(second.timing)
    result.timing = Timing.total(timings)
    return result


# --- checks -------------------------------------------------------------------------------

# Words that must appear (folded) for a shift or education answer to be kept: small models
# fill these in from general knowledge ("a driver needs an apprenticeship") otherwise.
SHIFT_EVIDENCE = {
    "jednoSm": r"jednosmen|ranni smen|pouze rann|jen rann|\b[67][:.]?\d{0,2} ?- ?1[45]",
    "dvouSm": r"dvousmen|2 ?smen|ranni a odpoledni|odpoledni smen",
    "triSm": r"trismen|3 ?smen|nocni smen",
    "ctyrSm": r"ctyrsmen|4 ?smen",
    "nepretrzity": r"nepretrzit|12 ?h\w* smen|kratky a dlouhy tyden",
    "nocni": r"nocni|v noci",
    "pruznaPd": r"pruzn|flexibiln",
    "deleneSm": r"delen\w* smen",
    "turnus": r"turnus",
}
EDUCATION_EVIDENCE = {
    "basic": r"zakladni vzdel|zakladni skol|\bzs\b",
    "vocational": r"vyucen|vyucni|\bsou\b|stredni odborne",
    "maturita": r"maturit|stredoskol|\bss\b|\bsos\b",
    "higher_vocational": r"vyssi odborn|\bvos\b",
    "bachelor": r"bakal|vysokoskol|\bvs\b|univerzit",
    "master": r"magist|inzenyr|\bing\b|vysokoskol|\bvs\b|univerzit",
    "doctoral": r"doktor|ph\.?d",
}
MONTH_RANGE = (5_000, 500_000)
HOUR_RANGE = (60, 3_000)


def _digits(text: str) -> str:
    """The ad's numbers with thousands separators removed: "35 000" and "35.000" -> 35000."""
    return re.sub(r"(?<=\d)[\s.  ](?=\d{3}\b)", "", text)


def _number_in_text(value: int, text: str) -> bool:
    digits = _digits(text)
    if re.search(rf"(?<!\d){value}(?!\d)", digits):
        return True
    # "35 tis.", "35 tisíc", "35k"
    if value % 1000 == 0:
        short = value // 1000
        folded = fold(text)
        return bool(re.search(rf"(?<!\d){short}\s*(tis|k\b)", folded))
    return False


def _plausible(value: int | None, unit: str | None) -> bool:
    low, high = HOUR_RANGE if unit == "hour" else MONTH_RANGE
    return value is not None and low <= value <= high


def validate(data: dict, text: str, answers: dict[str, str] | list[str]) -> Extraction:
    """`answers`: the offered occupation answers -> codes (a list of codes also works)."""
    if not isinstance(answers, dict):
        answers = {code: code for code in answers}
    out = Extraction(is_job_offer=bool(data.get("nabidka", True)))
    answer = data.get("profese")
    if answer in answers:
        out.isco_group = answers[answer]
    elif answer not in (None, OTHER):
        out.dropped.append(f"profese={answer}")

    folded = fold(text)
    town = clean_spaces(data.get("obec") or "")
    if town:
        if re.search(rf"(?<![a-z0-9]){re.escape(fold(town))}", folded):
            out.town = town
        else:
            out.dropped.append(f"obec={town}")

    for name in data.get("smeny") or ():
        code = SHIFTS.get(name)
        if code and re.search(SHIFT_EVIDENCE[code], folded):
            out.shifts.append(code)
        elif code:
            out.dropped.append(f"smeny={name}")
    education = EDUCATION.get(data.get("vzdelani") or "")
    if education and re.search(EDUCATION_EVIDENCE[education], folded):
        out.education = education
    elif education:
        out.dropped.append(f"vzdelani={education}")
    out.warnings = [WARNINGS[w] for w in data.get("varovani") or () if w in WARNINGS]

    unit = UNITS.get(data.get("mzda_za") or "")
    amounts = {}
    for key in ("mzda_od", "mzda_do"):
        value = data.get(key)
        if value is None:
            continue
        if not isinstance(value, int) or not _number_in_text(value, text):
            out.dropped.append(f"{key}={value}")
            continue
        amounts[key] = value
    if amounts:
        unit = unit or ("hour" if max(amounts.values()) < 1000 else "month")
        low, high = amounts.get("mzda_od"), amounts.get("mzda_do")
        if low is not None and high is not None and low > high:
            low, high = high, low
        if all(v is None or _plausible(v, unit) for v in (low, high)):
            out.salary_min, out.salary_max, out.salary_unit = low, high, unit
        else:
            out.dropped.append(f"mzda={low}-{high}/{unit}")
    return out
