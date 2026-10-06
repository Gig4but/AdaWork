"""Every job source, and what the app derives from their list."""

from adawork.core import Source
from adawork.scraper.sources import jenprace, linkedin, mpsv, prace_cz, profesia

CRAWL_SOURCES = (prace_cz.SOURCE, jenprace.SOURCE, profesia.SOURCE, linkedin.SOURCE)
# All of them, in the order the Sources page lists them.
SOURCES: tuple[Source, ...] = (mpsv.SOURCE, *CRAWL_SOURCES)

LABELS = {s.id: s.label for s in SOURCES}
# Sources the user can switch on and off, in display order: (id, on by default).
SWITCHABLE = tuple((s.id, s.default_enabled) for s in SOURCES)
NEEDS_CONSENT = frozenset(s.id for s in SOURCES if s.needs_consent)
