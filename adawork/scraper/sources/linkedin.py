"""LinkedIn job ads through the public guest pages (no login, the account is never used).

**Off by default** (PLAN §4.3): LinkedIn's robots.txt disallows these pages, so the user has
to switch the source on after a warning. Kept small and slow so it looks like one person
browsing: ≤ 100 cards and ≤ 30 detail pages per run, 5 s between requests, newest first,
and a 429 pauses the source (PoliteFetcher → SourceBlocked).

The guest search shows only the newest ads near a place, so a job counts as gone when it has
not been seen for three weeks.
"""

from functools import partial
from urllib.parse import urlencode

from selectolax.lexbor import LexborHTMLParser

from adawork.core import ParsedJob, ScrapeQuery
from adawork.geo.places import PlaceResolver
from adawork.normalize.text import contract_types, node_text, split_places
from adawork.scraper.crawl import CrawlSource, Lead, Listing, ListingPage, node_value, set_salary
from adawork.scraper.crawl import clean_url as _clean
from adawork.scraper.structured import parse_date

SOURCE_ID = "linkedin"
BASE = "https://www.linkedin.com"
SEARCH = BASE + "/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL = BASE + "/jobs-guest/jobs/api/jobPosting/{id}"
PAGE_SIZE = 10
DISTANCES_MILES = (5, 10, 25, 50, 100)


def _distance(radius_km: float) -> int:
    miles = radius_km / 1.609
    return next((d for d in DISTANCES_MILES if d >= miles), DISTANCES_MILES[-1])


def _location(text: str | None, places: PlaceResolver):
    """ "Čáslav", "Praha-východ", "Brno, Jihomoravský kraj, Česko"."""
    return next((loc for n in split_places(text) if (loc := places.resolve(n))), None)


class LinkedIn(CrawlSource):
    id = SOURCE_ID
    label = "LinkedIn"
    default_enabled = False
    needs_consent = True  # the site's terms forbid it
    obey_robots = False  # the user's explicit opt-in, see the module docstring
    delay = 5.0
    max_pages = 10
    max_leads = 100
    max_details = 30
    newest_first = True
    expire_after_days = 21

    def listings(self, query: ScrapeQuery) -> list[Listing]:
        location = f"{query.home}, Czechia" if query.home else "Czechia"
        distance = _distance(query.radius_km)
        return [
            (None, partial(self.search_url, keywords, location, distance))
            for keywords in (query.keywords or [""])
        ]

    @staticmethod
    def search_url(keywords: str, location: str, distance: int, page: int) -> str:
        params = {
            "keywords": keywords,
            "location": location,
            "distance": distance,
            "sortBy": "DD",  # newest first
            "start": (page - 1) * PAGE_SIZE,
        }
        return f"{SEARCH}?{urlencode(params)}"

    def parse_listing(self, html: str, page: int, places: PlaceResolver) -> ListingPage:
        tree = LexborHTMLParser(html)
        leads = []
        for card in tree.css("div.base-search-card"):
            urn = card.attributes.get("data-entity-urn") or ""
            job_id = urn.rsplit(":", 1)[-1]
            link = card.css_first("a.base-card__full-link")
            if not job_id.isdigit() or link is None:
                continue
            place = node_value(card.css_first(".job-search-card__location"))
            posted = card.css_first("time")
            url = _clean(link.attributes.get("href") or "")
            job = ParsedJob(
                external_id=job_id,
                title=node_value(card.css_first(".base-search-card__title")) or "",
                url=url,
                employer_name=node_value(card.css_first(".base-search-card__subtitle")),
                place_text=place,
                location=_location(place, places),
                posted_at=parse_date(posted.attributes.get("datetime") if posted else None),
            )
            set_salary(job, node_value(card.css_first(".job-search-card__salary-info")))
            leads.append(Lead(job_id, url, DETAIL.format(id=job_id), job))
        return ListingPage(leads, has_next=len(leads) >= PAGE_SIZE)

    def parse_detail(self, html: str, lead: Lead, places: PlaceResolver) -> ParsedJob | None:
        tree = LexborHTMLParser(html)
        description = tree.css_first(".show-more-less-html__markup")
        title = node_value(tree.css_first(".top-card-layout__title"))
        if description is None or not title:
            return None
        criteria = {}
        for item in tree.css(".description__job-criteria-item"):
            name = node_value(item.css_first(".description__job-criteria-subheader"))
            value = node_value(item.css_first(".description__job-criteria-text"))
            if name and value:
                criteria[name] = value
        place = node_value(tree.css_first(".topcard__flavor--bullet"))
        job = ParsedJob(
            external_id=lead.external_id,
            title=title,
            url=lead.url,
            description=node_text(description),
            employer_name=node_value(tree.css_first(".topcard__org-name-link"))
            or node_value(tree.css_first(".topcard__flavor")),
            place_text=place,
            location=_location(place, places),
            contract_types=contract_types(*criteria.values()),
        )
        if criteria:
            job.extra["criteria"] = criteria
        set_salary(job, node_value(tree.css_first(".compensation__salary")))
        return job


SOURCE = LinkedIn()
