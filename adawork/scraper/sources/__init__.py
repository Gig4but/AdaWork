"""Every job source, and what the app derives from their list."""

from adawork.core import Source
from adawork.scraper.sources import (
    bazos,
    dobraprace,
    easy_prace,
    fajn_brigady,
    hitprace,
    inwork,
    jenprace,
    jobs_cz,
    linkedin,
    mpsv,
    personalka,
    prace_cz,
    pracezarohem,
    profesia,
    startupjobs,
)

CRAWL_SOURCES = (
    prace_cz.SOURCE,
    jobs_cz.SOURCE,
    jenprace.SOURCE,
    pracezarohem.SOURCE,
    profesia.SOURCE,
    dobraprace.SOURCE,
    easy_prace.SOURCE,
    personalka.SOURCE,
    inwork.SOURCE,
    fajn_brigady.SOURCE,
    startupjobs.SOURCE,
    hitprace.SOURCE,
    bazos.SOURCE,
    linkedin.SOURCE,
)
# All of them, in the order the Sources page lists them.
SOURCES: tuple[Source, ...] = (mpsv.SOURCE, *CRAWL_SOURCES)

LABELS = {s.id: s.label for s in SOURCES}
# Sources the user can switch on and off, in display order: (id, on by default).
SWITCHABLE = tuple((s.id, s.default_enabled) for s in SOURCES)
NEEDS_CONSENT = frozenset(s.id for s in SOURCES if s.needs_consent)
