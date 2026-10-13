"""Careerjet job search API v4 with the user's own key (docs/spikes/sources.md § Careerjet).

`GET https://search.api.careerjet.net/v4/query`, the key as the HTTP Basic user name. The
API wants the end user's address and browser: this computer's address and our User-Agent.
The answer is `{type: "JOBS", hits, pages, jobs: [...]}`, or `type: "LOCATIONS"` when the
place is ambiguous. Read from the docs: no key was available to try it live.
"""

import hashlib
from collections.abc import Iterator
from email.utils import parsedate_to_datetime

import httpx

from adawork.core import ParsedJob, SalaryUnit, ScrapeQuery
from adawork.geo.places import PlaceResolver
from adawork.net.http import USER_AGENT
from adawork.normalize.text import clean_title, html_to_text
from adawork.scraper.api import ApiSource, local_ip, locate, searches
from adawork.scraper.crawl import set_salary
from adawork.scraper.structured import parse_date

SOURCE_ID = "careerjet"
URL = "https://search.api.careerjet.net/v4/query"
PAGE_SIZE = 50
MAX_PAGES = 3  # per search
# salary_type -> (unit, divisor to that unit)
SALARY_TYPES: dict[str, tuple[SalaryUnit, int]] = {
    "H": ("hour", 1),
    "M": ("month", 1),
    "Y": ("month", 12),
}


def _date(value: str | None):
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).replace(tzinfo=None)  # "Tue, 07 Oct 2026 …"
    except (TypeError, ValueError):
        return parse_date(value)


class Careerjet(ApiSource):
    id = SOURCE_ID
    label = "Careerjet"
    key_url = "https://www.careerjet.com/partners/api/"

    def search(
        self, client: httpx.Client, key: str, query: ScrapeQuery, places: PlaceResolver
    ) -> Iterator[ParsedJob]:
        requests = 0
        user = {"user_ip": local_ip(), "user_agent": USER_AGENT}
        for words, place, km in searches(query):
            for page in range(1, MAX_PAGES + 1):
                if requests >= self.max_requests:
                    return
                params: dict[str, str | int] = {
                    "locale_code": "cs_CZ",
                    "keywords": words,
                    "location": place,
                }
                params |= {"sort": "date", "page": page, "page_size": PAGE_SIZE} | user
                if km:
                    params["radius"] = round(km)
                resp = client.get(URL, params=params, auth=(key, ""))
                requests += 1
                resp.raise_for_status()
                data = resp.json()
                if data.get("type") != "JOBS":  # an ambiguous place: nothing to read
                    break
                for item in data.get("jobs") or []:
                    if job := self.parse(item, places):
                        yield job
                if page >= (data.get("pages") or 0):
                    break

    @staticmethod
    def parse(item: dict, places: PlaceResolver) -> ParsedJob | None:
        title = clean_title(item.get("title"))
        if not title or not item.get("url"):
            return None
        company, place = item.get("company") or None, item.get("locations") or None
        # The links carry tracking, so the ad is known by what it says.
        identity = f"{title}|{company or ''}|{place or ''}".lower()
        job = ParsedJob(
            external_id=hashlib.sha1(identity.encode()).hexdigest()[:20],
            title=title,
            url=item["url"],
            description=html_to_text(item.get("description")),
            employer_name=company,
            place_text=place,
            location=locate(place, places),
            posted_at=_date(item.get("date")),
        )
        unit = SALARY_TYPES.get(item.get("salary_type") or "")
        if unit and (item.get("salary_min") or item.get("salary_max")):
            name, divisor = unit
            job.salary_min, job.salary_max = (
                round(v / divisor) if v else None
                for v in (item.get("salary_min"), item.get("salary_max"))
            )
            job.salary_unit = name
            job.salary_currency = item.get("salary_currency_code") or "CZK"
        else:
            set_salary(job, item.get("salary"))
        return job


SOURCE = Careerjet()
