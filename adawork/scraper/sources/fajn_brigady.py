"""fajn-brigady.cz: ~2k short-term jobs (brigády) in one sitemap with lastmod; detail pages in
the shared "Fajn skupina" template (see _inzerce.py).

Ad URLs name a category and the town (/brigady/<category>/<town>/<id>-<slug>/); the town is
looked up among municipalities to skip ads outside the user's regions. Ads abroad
(/brigady-v-zahranici/) are left out. inwork.cz lists the same ads under /brigady/.
"""

import re
from collections import defaultdict
from datetime import datetime

from adawork.core import ParsedJob, ScrapeQuery
from adawork.geo.places import PlaceResolver
from adawork.normalize.text import slugify
from adawork.scraper.crawl import Lead, SitemapSource
from adawork.scraper.sources import _inzerce

SOURCE_ID = "fajn_brigady"
BASE = "https://www.fajn-brigady.cz"
AD_URL = re.compile(r"^https://www\.fajn-brigady\.cz/brigady/[a-z0-9-]+/([a-z0-9-]+)/(\d+)-[^/]*/$")
CITY_DISTRICT = re.compile(r"-\d{1,2}$")  # "praha-4", "plzen-3"


class FajnBrigady(SitemapSource):
    id = SOURCE_ID
    label = "Fajn brigády"
    _kraje_of_town: dict[str, frozenset[str]] = {}

    def sitemap_url(self, page: int) -> str:
        return f"{BASE}/static/sitemap/sitemap_fb/inzeraty_detail.xml"

    def lead(self, url: str, modified: datetime | None) -> Lead | None:
        m = AD_URL.match(url)
        return Lead(m[2], url, url, modified=modified) if m else None

    def prepare(self, query: ScrapeQuery, places: PlaceResolver) -> None:
        super().prepare(query, places)
        towns: dict[str, set[str]] = defaultdict(set)
        for m in places.municipalities:
            if m.kraj:
                towns[slugify(m.name)].add(m.kraj)
        self._kraje_of_town = {slug: frozenset(kraje) for slug, kraje in towns.items()}

    def wanted(self, lead: Lead, query: ScrapeQuery, places: PlaceResolver) -> bool:
        m = AD_URL.match(lead.url)
        kraje = self._kraje_of_town.get(CITY_DISTRICT.sub("", m[1])) if m else None
        # Unknown slugs ("cela-cr", a town part) are kept; same-named towns in any region match.
        return kraje is None or not kraje.isdisjoint(query.kraje)

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedJob | None:
        return _inzerce.parse_detail(html, lead, places)


SOURCE = FajnBrigady()
