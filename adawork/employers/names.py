"""Employer names as ads write them -> a comparable key and search variants (PLAN §4.5).

"ADECCO spol.s r.o." and "ADECCO spol. s r.o." are one employer: the key folds case and
diacritics, drops punctuation and the legal form. Search variants for ARES expand "ČR"/"CZ"
(ARES has no abbreviation handling) and drop generic venue words ("Hospoda U Lípy").
"""

import re

from adawork.normalize.text import fold

# Legal forms as folded word sequences, longest first; stripped from the end of a name.
_FORMS = (
    "spol s r o", "spolecnost s rucenim omezenym", "s r o", "sro", "a s", "as", "k s",
    "akciova spolecnost", "verejna obchodni spolecnost", "komanditni spolecnost",
    "v o s", "s p", "z s", "z u", "o p s", "p o", "se", "druzstvo",
    "statni podnik", "statni organizace", "prispevkova organizace", "organizacni slozka",
    "gmbh", "ltd", "inc", "bv", "b v", "kft", "sp z o o", "s a", "sa", "a g", "ag", "n v",
    "nv", "plc", "llc", "kg", "spa", "s p a", "srl", "s r l", "o z", "odstepny zavod",
    "zapsany spolek", "zapsany ustav", "obecne prospesna spolecnost",
)  # fmt: skip
FORM_WORDS = tuple(sorted((tuple(f.split()) for f in _FORMS), key=len, reverse=True))
GENERIC = frozenset({
    "hospoda", "restaurace", "hostinec", "kavarna", "bistro", "pivnice", "firma", "spolecnost",
    "provozovna", "prodejna", "pobocka",
})  # fmt: skip
_EXPAND = {"cr": "Česká republika", "cz": "Česká republika", "čr": "Česká republika"}
# Words for the country, which ARES names write in many ways ("CZ", "ČR", "Czech Republic").
COUNTRY = frozenset({"cz", "cr", "czech", "republic", "ceska", "republika", "czechia"})
_AGENCY_TEXT = re.compile(
    r"agenturni zamestnav|agentura prace|personalni agentur|pracovni agentur"
    r"|pro naseho (klienta|partnera|zakaznika)|pro vyznamneho klienta|nas klient"
    r"|docasne prideleni|pridelen\w* k uzivateli"
)
_NOT_AGENCY = re.compile(r"(nejedna se o|nejde o|neni|nejsme|zadna|bez|primo|ne pres)\W+\w*\W*$")


def words(name: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", fold(name).replace("&", " a "))


def strip_form(tokens: list[str]) -> list[str]:
    """The name's words without trailing legal forms ("Manuvia, a. s., organizační složka")."""
    changed = True
    while changed and tokens:
        changed = False
        for form in FORM_WORDS:
            n = len(form)
            if len(tokens) > n and tuple(tokens[-n:]) == form:
                tokens, changed = tokens[:-n], True
                break
    return tokens


def name_key(name: str | None) -> str:
    """ "Penny Market, s. r. o." -> "penny market"; "" for no name."""
    return " ".join(strip_form(words(name or "")))[:200]


def short_name(name: str) -> str:
    """ "Penny Market s.r.o." -> "Penny Market": how people write it in reviews."""
    key, parts = name_key(name), name.split()
    for n in range(1, len(parts)):
        if name_key(" ".join(parts[:n])) == key:
            return " ".join(parts[:n]).strip(" ,&-–")
    return name


def legal_form_in(name: str) -> bool:
    return len(strip_form(words(name))) < len(words(name))


def variants(name: str, places: frozenset[str] = frozenset()) -> list[str]:
    """ARES name queries, best first: as written, "ČR" expanded, without generic words, without
    a town after the name ("SNOP Zápy"), the part in or before brackets ("AAA Auto (AURES
    Holdings a.s.)"), without the country ("Foxconn Czech Republic")."""
    name = re.sub(r"[\"„“”»«]", " ", name).strip(" ,.-")  # "McDonald's" keeps its apostrophe
    name = re.sub(r"\s+", " ", name)
    out = [name]
    expanded = " ".join(_EXPAND.get(w.lower().strip(",."), w) for w in name.split())
    out.append(expanded)

    def without(drop, keep_first: bool = True) -> str:
        kept = [
            w
            for i, w in enumerate(name.split())
            if (keep_first and i == 0) or not drop(fold(w).strip(",.-"))
        ]
        return " ".join(kept)

    out.append(without(lambda w: w in GENERIC, keep_first=False))
    out.append(without(lambda w: w in places))
    inner = re.search(r"\(([^)]{3,})\)", name)
    if inner:
        out += [inner.group(1), name[: inner.start()]]
    out.append(without(lambda w: w in COUNTRY))
    return [v.strip(" ,.-") for v in dict.fromkeys(out) if len(name_key(v)) >= 2]


def core_words(name: str) -> list[str]:
    """The name's words without legal forms and country words: what is compared. Inside a
    name only unmistakable forms go ("s r o", "GmbH"; not "p o" in "J A P O")."""
    tokens = strip_form(words(name))
    out: list[str] = []
    i = 0
    while i < len(tokens):
        form = next((f for f in FORM_WORDS if tuple(tokens[i : i + len(f)]) == f), None)
        if form and i > 0 and (len(form[0]) >= 3 or len(form) >= 3):
            i += len(form)
            continue
        if tokens[i] not in COUNTRY or i == 0:
            out.append(tokens[i])
        i += 1
    return out


# Words that may surround a known employer's name in an ad without making it another company.
BRANCH_WORDS = GENERIC | {
    "ambulance", "zavod", "sklad", "divize", "region", "kancelar",
    "oddeleni", "stredisko", "filialka", "nabor",
}  # fmt: skip


def known_inside(
    name: str, known: dict[tuple[str, ...], int], places: frozenset[str] = frozenset()
) -> int | None:
    """A known employer's name inside a longer one where the other words only say which
    branch or town: "Ambulance Penta Hospitals CZ" -> Penta Hospitals CZ, but not "JS Trans
    logistic" -> JS Trans. `known`: core words -> employer id (unambiguous ones only)."""
    w = core_words(name)
    for size in range(len(w) - 1, 1, -1):
        hits = set()
        for i in range(len(w) - size + 1):
            rest = w[:i] + w[i + size :]
            if (t := tuple(w[i : i + size])) in known and all(
                x in BRANCH_WORDS or x in places for x in rest
            ):
                hits.add(known[t])
        if hits:
            return hits.pop() if len(hits) == 1 else None
    return None


_CLIENT = re.compile(
    r"\b(?i:pro)\s+(?i:na[sš]eho\s+)?(?i:klienta|partnera|společnost|spolecnost|firmu|zaměstnavatele)"
    r"[\s,:]+([A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ0-9][\w&.,\- ]{1,60}?"
    r"(?:s\.\s?r\.\s?o\.|a\.\s?s\.|spol\.\s?s\s?r\.\s?o\.|k\.\s?s\.|v\.\s?o\.\s?s\.|SE\b))"
)


def client_in(text: str | None) -> str | None:
    """The client company an agency ad names: "pro společnost Foxconn CZ s.r.o." Only a name
    with its legal form counts ("pro našeho klienta, výrobce aut" names nobody)."""
    m = _CLIENT.search(text or "")
    return m.group(1).strip() if m else None


def looks_like_agency(text: str | None) -> bool:
    """Ad text that speaks of agency work or of the agency's client (not "nejedná se o
    agenturní zaměstnávání")."""
    if not text:
        return False
    folded = fold(text)
    return any(
        not _NOT_AGENCY.search(folded[max(0, m.start() - 30) : m.start()])
        for m in _AGENCY_TEXT.finditer(folded)
    )


def slug(name: str) -> str:
    """As Atmoskop (and most Czech sites) write a company name in a URL:
    "Penny Market s.r.o." -> "penny-market-s-r-o"."""
    return re.sub(r"[^a-z0-9]+", "-", fold(name)).strip("-")
