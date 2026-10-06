"""Every job source, and what the app derives from their list."""

from adawork.core import Source
from adawork.scraper.sources import mpsv

SOURCES: tuple[Source, ...] = (mpsv.SOURCE,)
