"""Ask in a sentence ("Zeptat se větou", PLAN §6.1): a question in plain Czech becomes the
search filters, which the user then sees and can change like any other filter.

"práce ve skladu na Kolínsku, jen ranní, aspoň 30 tisíc" ->
q="sklad", kraj=["Středočeský kraj"], shifts=["jednoSm"], salary_min=30000.
"""

from adawork.ai.client import BadAnswer, LlmClient
from adawork.geo.places import KRAJE
from adawork.normalize.vocab import CATEGORIES, CONTRACT_TYPES, EDUCATION, SHIFTS
from adawork.search.service import Filters

PROMPT_VERSION = 1
POSTED_DAYS = (1, 3, 7, 14, 30)

SYSTEM = """Převeď dotaz uživatele na filtry pro hledání práce. Vrať JSON.
- "slova": 1–3 klíčová slova pro fulltext (pozice nebo firma, v základním tvaru), jinak "".
  Místo, mzdu, směny ani typ úvazku do slov nedávej, na ty jsou filtry.
- "obor", "uvazek", "smeny", "kraj": vyber jen to, co dotaz opravdu žádá; jinak [].
- "kraj": kraj podle zmíněného místa (např. Kolín -> Středočeský kraj, Brno -> Jihomoravský).
- "mzda_od": nejnižší měsíční mzda v Kč ("30 tisíc" = 30000), jinak null.
- "vzdelani": vzdělání, které uživatel má, jinak null.
- "dni": jak čerstvé nabídky chce (počet dní), jinak null."""


def _labels(vocab: dict) -> dict[str, str]:
    """Czech label -> code."""
    return {entry["cs"]: code for code, entry in vocab.items()}


CATEGORY_BY_LABEL = _labels(CATEGORIES)
CONTRACT_BY_LABEL = _labels(CONTRACT_TYPES)
SHIFT_BY_LABEL = _labels(SHIFTS)
EDUCATION_BY_LABEL = _labels(EDUCATION)


def schema() -> dict:
    array = lambda values, n: {  # noqa: E731
        "type": "array",
        "items": {"enum": list(values)},
        "maxItems": n,
    }
    return {
        "type": "object",
        "properties": {
            "slova": {"type": "string", "maxLength": 40},
            "obor": array(CATEGORY_BY_LABEL, 3),
            "kraj": array(KRAJE, 3),
            "uvazek": array(CONTRACT_BY_LABEL, 3),
            "smeny": array(SHIFT_BY_LABEL, 3),
            "mzda_od": {"type": ["integer", "null"]},
            "vzdelani": {"enum": [*EDUCATION_BY_LABEL, None]},
            "dni": {"enum": [*POSTED_DAYS, None]},
        },
        "required": ["slova", "obor", "kraj", "uvazek", "smeny", "mzda_od", "vzdelani", "dni"],
        "additionalProperties": False,
    }


def to_filters(data: dict) -> Filters:
    salary = data.get("mzda_od")
    return Filters(
        q=(data.get("slova") or "").strip(),
        categories=[CATEGORY_BY_LABEL[x] for x in data.get("obor") or () if x in CATEGORY_BY_LABEL],
        kraj=[k for k in data.get("kraj") or () if k in KRAJE],
        contract_types=[
            CONTRACT_BY_LABEL[x] for x in data.get("uvazek") or () if x in CONTRACT_BY_LABEL
        ],
        shifts=[SHIFT_BY_LABEL[x] for x in data.get("smeny") or () if x in SHIFT_BY_LABEL],
        salary_min=salary if isinstance(salary, int) and 5_000 <= salary <= 500_000 else None,
        education=EDUCATION_BY_LABEL.get(data.get("vzdelani") or ""),
        posted_days=data.get("dni") if data.get("dni") in POSTED_DAYS else None,
    )


def ask(client: LlmClient, question: str) -> Filters:
    reply = client.chat(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question[:300]}],
        schema=schema(),
        max_tokens=150,
    )
    try:
        return to_filters(reply.data)
    except (AttributeError, TypeError) as exc:  # a value of the wrong type
        raise BadAnswer(f"unexpected answer: {str(reply.data)[:200]}") from exc
