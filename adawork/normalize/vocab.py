"""The app's own filter values. Every source maps onto these, so filters work across sources.

Categories are the 16 official "obory činnosti" of Úřad práce; MPSV maps every CZ-ISCO
occupation to one or more of them, so any job with an ISCO code gets categories for free.
"""

Labels = dict[str, dict[str, str]]  # code -> {"cs": ..., "en": ...}

CATEGORIES: Labels = {
    "1": {"cs": "Administrativa", "en": "Administration"},
    "2": {"cs": "Doprava", "en": "Transport"},
    "3": {"cs": "Finance", "en": "Finance"},
    "4": {"cs": "Informační technologie", "en": "IT"},
    "5": {"cs": "Právo", "en": "Law"},
    "6": {"cs": "Kultura a sport", "en": "Culture and sport"},
    "7": {"cs": "Management", "en": "Management"},
    "8": {"cs": "Obchod a cestovní ruch", "en": "Retail and tourism"},
    "9": {"cs": "Obrana a ochrana", "en": "Defence and security"},
    "10": {"cs": "Stavebnictví", "en": "Construction"},
    "11": {"cs": "Věda a výzkum", "en": "Science and research"},
    "12": {"cs": "Výchova a vzdělávání", "en": "Education"},
    "13": {"cs": "Výroba a provoz", "en": "Manufacturing and operations"},
    "14": {"cs": "Služby", "en": "Services"},
    "15": {"cs": "Zdravotnictví", "en": "Healthcare"},
    "16": {"cs": "Zemědělství a lesnictví", "en": "Agriculture and forestry"},
}

CONTRACT_TYPES: Labels = {
    "full_time": {"cs": "Plný úvazek", "en": "Full time"},
    "part_time": {"cs": "Zkrácený úvazek", "en": "Part time"},
    "dpp": {"cs": "DPP (dohoda o provedení práce)", "en": "DPP agreement"},
    "dpc": {"cs": "DPČ (dohoda o pracovní činnosti)", "en": "DPČ agreement"},
    "contractor": {"cs": "Na živnostenský list (IČO)", "en": "Self-employed (IČO)"},
    "service": {"cs": "Služební poměr", "en": "Civil service"},
    "internship": {"cs": "Stáž / praxe", "en": "Internship"},
}

# Ordered: a job needing level N is open to anyone with level >= N.
EDUCATION: Labels = {
    "basic": {"cs": "Základní", "en": "Basic"},
    "vocational": {"cs": "Vyučení / střední bez maturity", "en": "Vocational"},
    "maturita": {"cs": "Střední s maturitou", "en": "Secondary (maturita)"},
    "higher_vocational": {"cs": "Vyšší odborné", "en": "Higher vocational"},
    "bachelor": {"cs": "Vysokoškolské bakalářské", "en": "Bachelor's degree"},
    "master": {"cs": "Vysokoškolské magisterské", "en": "Master's degree"},
    "doctoral": {"cs": "Doktorské", "en": "Doctorate"},
}
EDUCATION_LEVEL = {code: level for level, code in enumerate(EDUCATION, start=1)}

SHIFTS: Labels = {
    "jednoSm": {"cs": "Jednosměnný provoz", "en": "Single shift"},
    "dvouSm": {"cs": "Dvousměnný provoz", "en": "Two shifts"},
    "triSm": {"cs": "Třísměnný provoz", "en": "Three shifts"},
    "ctyrSm": {"cs": "Čtyřsměnný provoz", "en": "Four shifts"},
    "deleneSm": {"cs": "Dělené směny", "en": "Split shifts"},
    "nepretrzity": {"cs": "Nepřetržitý provoz", "en": "Continuous operation"},
    "nocni": {"cs": "Noční provoz", "en": "Night work"},
    "pruznaPd": {"cs": "Pružná pracovní doba", "en": "Flexible hours"},
    "turnus": {"cs": "Turnusové služby", "en": "Rotating shifts"},
}

SUITABLE_FOR: Labels = {
    "absol": {"cs": "Absolventy", "en": "Graduates"},
    "mladi": {"cs": "Mladistvé do 18 let", "en": "Under 18"},
    "OZP2": {"cs": "OZP (invalidita 1.–2. stupně)", "en": "Disability (degree 1–2)"},
    "OZP3": {"cs": "OZP (invalidita 3. stupně)", "en": "Disability (degree 3)"},
    "vozik": {"cs": "Bezbariérový přístup", "en": "Wheelchair accessible"},
    "znevyhod": {"cs": "Zdravotně znevýhodněné", "en": "Health-disadvantaged"},
}

BENEFITS: Labels = {
    "ubyt": {"cs": "Ubytování", "en": "Accommodation"},
    "strav": {"cs": "Podnikové stravování", "en": "Meals"},
    "jizdne": {"cs": "Jízdní výhody", "en": "Travel allowance"},
    "dovol": {"cs": "Dovolená navíc", "en": "Extra holiday"},
    "premie": {"cs": "Prémie", "en": "Bonuses"},
    "predsk": {"cs": "Školka", "en": "Childcare"},
    "natur": {"cs": "Naturální výhody", "en": "Benefits in kind"},
    "jine": {"cs": "Jiné výhody", "en": "Other benefits"},
}

VOCABULARIES: dict[str, Labels] = {
    "categories": CATEGORIES,
    "contract_types": CONTRACT_TYPES,
    "education": EDUCATION,
    "shifts": SHIFTS,
    "suitable_for": SUITABLE_FOR,
    "benefits": BENEFITS,
}


def label(vocab: str, code: str, lang: str = "cs") -> str:
    entry = VOCABULARIES[vocab].get(code)
    return entry.get(lang) or entry["cs"] if entry else code


# Hourly rates are compared with monthly salaries via a full-time month (~37.5 h × 4.33 wk).
HOURS_PER_MONTH = 160
# Some "hourly" MPSV values are clearly monthly amounts (e.g. 139 380 Kč/h).
MAX_PLAUSIBLE_HOURLY = 2000


def monthly_salary(amount: int | None, unit: str | None) -> int | None:
    if not amount:
        return None
    if unit == "hour" and amount <= MAX_PLAUSIBLE_HOURLY:
        return amount * HOURS_PER_MONTH
    return amount


def derived_values(values: dict) -> dict:
    return {
        "salary_month_min": monthly_salary(values.get("salary_min"), values.get("salary_unit")),
        "salary_month_max": monthly_salary(values.get("salary_max"), values.get("salary_unit")),
        "education_level": EDUCATION_LEVEL.get(values.get("education") or ""),
    }
