"""Every job source, and what the app derives from their list."""

from adawork.core import Source
from adawork.scraper.sources import jenprace, mpsv, prace_cz, profesia

CRAWL_SOURCES = (prace_cz.SOURCE, jenprace.SOURCE, profesia.SOURCE)
# All of them, in the order the Sources page lists them.
SOURCES: tuple[Source, ...] = (mpsv.SOURCE, *CRAWL_SOURCES)

LABELS = {s.id: s.label for s in SOURCES}
