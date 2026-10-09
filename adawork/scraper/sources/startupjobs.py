"""startupjobs.cz: ~400 ads, mostly IT and start-ups. The listing pages are rendered in the
browser and carry no ads; the offers sitemap lists them all, JSON-LD on detail pages.
"""

import re
from datetime import datetime

from adawork.core import ParsedJob
from adawork.geo.places import PlaceResolver
from adawork.scraper.crawl import Lead, SitemapSource
from adawork.scraper.structured import find_job_postings, posting_to_job

SOURCE_ID = "startupjobs"
BASE = "https://www.startupjobs.cz"
AD_URL = re.compile(r"^https://www\.startupjobs\.cz/nabidka/(\d+)/[^/]+$")


class StartupJobs(SitemapSource):
    id = SOURCE_ID
    label = "StartupJobs.cz"

    def sitemap_url(self, page: int) -> str:
        return f"{BASE}/sitemap/offers.xml"

    def lead(self, url: str, modified: datetime | None) -> Lead | None:
        m = AD_URL.match(url)
        return Lead(m.group(1), url, url, modified=modified) if m else None

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedJob | None:
        postings = find_job_postings(html)
        if not postings:
            return None
        return posting_to_job(postings[0], lead.external_id, lead.url, places)


SOURCE = StartupJobs()
