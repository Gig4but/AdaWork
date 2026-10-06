"""Every UI text exists in both languages, and every key the code names exists."""

import re
import string
from pathlib import Path

from adawork.i18n import TEXTS

PACKAGE = Path(__file__).parents[2] / "adawork"
# t("key") in templates, translate(lang, "key") in code; keys built at run time
# (t("sort." ~ name)) end with a dot and are left out.
TEMPLATE_KEY = re.compile(r"""\bt\(\s*["']([\w.]*\w)["']""")
CODE_KEY = re.compile(r"""\btranslate\([^,()]+,\s*["']([\w.]*\w)["']""")


def placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_every_text_in_both_languages_with_the_same_placeholders():
    for key, texts in TEXTS.items():
        assert set(texts) == {"cs", "en"}, key
        assert all(texts.values()), key
        assert placeholders(texts["cs"]) == placeholders(texts["en"]), key


def test_every_named_key_exists():
    used = set()
    for path in (PACKAGE / "web" / "templates").glob("*.html"):
        used |= set(TEMPLATE_KEY.findall(path.read_text(encoding="utf-8")))
    for path in PACKAGE.rglob("*.py"):
        used |= set(CODE_KEY.findall(path.read_text(encoding="utf-8")))
    assert used
    assert sorted(used - TEXTS.keys()) == []
