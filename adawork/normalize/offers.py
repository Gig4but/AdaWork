"""Is a free-text post a job offer, and does it look suspicious? (PLAN §6.1–6.5)

Classified boards and social posts mix job offers with people looking for work, services,
loans and things for sale, and they carry many scams (fees for the applicant, MLM, "assembly
at home"). Rules on the title and the text sort them out: a non-offer is kept (so it is not
read again) but not listed, and a suspicious offer is hidden until the user ticks "show
suspicious offers" (the filter counts them), then listed with its warning.
"""

import re

from adawork.normalize.text import fold

# Title starts that mean "not an offer": the reason code and its pattern on folded text.
_NOT_OFFER = (
    ("seeker", r"(?:hledam|shanim|sezenu)\s+(?:si\s+)?(?:\w+\s+)?(?:praci|brigad|zamestnani"
     r"|privydel|misto|uplatneni|vypomoc)"),
    ("seeker", r"(?:student\w*|maminka|duchodc\w*|zena|muz)\s+hleda\s"),
    ("service", r"nabizim\s+(?:sve\s+|svou\s+)?(?:sluzb|hlidan|douc|ucetn|uklid|vypomoc|pomoc"
     r"|preklad|stehovan|doprav|oprav|masaz|kurz|lekce)"),
    ("service", r"(?:naucim|doucim|douceni|kurz)\b"),
    ("loan", r"(?:soukrom\w*\s+|rychl\w*\s+|nebankovn\w*\s+)?"
     r"(?:pujck|pujcime|pujcujeme|uver\s+bez|oddluzen)"),
    ("sale", r"(?:prodam|koupim|daruji|pronajmu|vymenim)\b"),
)  # fmt: skip
_NOT_OFFER_RE = [(code, re.compile(rf"^\W*{pattern}")) for code, pattern in _NOT_OFFER]

# Warning signs anywhere in the title or text (folded). Codes match vocab.RED_FLAGS.
_FLAGS = (
    ("fee", r"(?<!zadne )(?<!nulove )(?<!bez )(?<!zadny )"
     r"(?:vstupni|registracni|aktivacni|zapisn\w*)\s+(?:poplat|vklad)"
     r"|startovaci\s+balic|(?<!nejakych )(?<!bez )(?<!zadne )"
     r"poplat\w*\s+za\s+(?:registr|material|zarazen|zprostredk)"
     r"|zaplat\w*\s+(?:predem|zalohu)|(?:vratn\w*\s+)?kauc\w*\s+za\s+material"),
    ("mlm", r"(?<!zadny )(?<!bez )(?<!nevyhovuje )(?<!neni )"
     r"(?:\bmlm\b|multi-?level|sitov\w*\s+marketing)"
     r"|budovani\s+(?:sveho|vlastniho)\s+tymu|pasivni\s+prijem"
     r"|financni\s+(?:nezavislost|svobod)|herbalife|forever\s+living|oriflame"),
    ("home_assembly", r"(?:kompletac|skladani|baleni|lepeni|navlekani)\w*\s.{0,40}"
     r"\b(?:doma|z\s+domova|na\s+doma)\b|plneni\s+obalek|kompletace\s+propisovac"),
    ("easy_money", r"(?:snadn|lehk)\w*\s+(?:vydel|penez|prijem)"
     r"|bez\s+(?:namahy|prace)\s+(?:vydel|prijem)"
     r"|za\s+(?:par|\d+)\s+minut\s+(?:vaseho\s+)?(?:casu|denne)"),
    ("unpaid_trial", r"(?:zkusebni|zaucovaci)\w*\s+(?:den|dny|dnu|tyden|smen\w*|obdobi)\s+"
     r"(?:je\s+)?(?:zdarma|neplacen|bez\s+(?:naroku|mzdy|odmeny|zaplaceni))"
     r"|neplacen\w*\s+(?:zkusebni|zauceni)"),
    ("adult", r"\beroti|\bescort|\bspolecnic\w*\s+(?:pro|k)\s|sugar\s*dadd|\bwebcam|\bvideochat"
     r"|pro\s+pohledn\w*\s+(?:zen|slecn|divk|devc)"),
    ("referral", r"\btipar|tipare\b|provize\s+za\s+(?:tip|doporuc)"),
    ("investment", r"\bforex\b|(?:investuj|obchoduj|vydelavej)\w*\s+(?:na\s+|do\s+|s\s+)?"
     r"(?:kryptomen|bitcoin|akci)"),
)  # fmt: skip
_FLAGS_RE = [(code, re.compile(pattern)) for code, pattern in _FLAGS]


def not_offer_reason(title: str | None, text: str | None = None) -> str | None:
    """Why a post is not a job offer ("seeker", "service", "loan", "sale"), or None."""
    head = fold(title or "")
    if not head.strip() and text:
        head = fold(text.strip().split("\n", 1)[0])
    return next((code for code, pattern in _NOT_OFFER_RE if pattern.search(head)), None)


def red_flags(title: str | None, text: str | None) -> list[str]:
    """Warning signs in an offer, as vocab.RED_FLAGS codes."""
    folded = fold(f"{title or ''}\n{text or ''}")
    return [code for code, pattern in _FLAGS_RE if pattern.search(folded)]
