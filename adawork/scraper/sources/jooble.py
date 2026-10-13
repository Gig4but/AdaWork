"""Jooble job search API with the user's own key (docs/spikes/sources.md § Jooble).

`POST https://jooble.org/api/<key>` with `{keywords, location, radius, page, ResultOnPage}`
answers `{totalCount, jobs: [{id, title, location, snippet, salary, type, link, company,
updated, source}]}`. The key is issued for one country (Czechia here). Only the request shape
is known from the docs: no key was available to try it live.
"""

from collections.abc import Iterator

import httpx

from adawork.core import ParsedJob, ScrapeQuery
from adawork.geo.places import PlaceResolver
from adawork.normalize.text import clean_title, contract_types, html_to_text
from adawork.scraper.api import ApiSource, locate, searches
from adawork.scraper.crawl import set_salary
from adawork.scraper.structured import parse_date

SOURCE_ID = "jooble"
URL = "https://jooble.org/api/{key}"
RADII_KM = (0, 4, 8, 16, 26, 40, 80)  # the values the API accepts
PAGE_SIZE = 50
MAX_PAGES = 3  # per search


def radius(km: float) -> str:
    return str(next((r for r in RADII_KM if r >= km), RADII_KM[-1]))


class Jooble(ApiSource):
    id = SOURCE_ID
    label = "Jooble"
    key_url = "https://jooble.org/api/about"

    def search(
        self, client: httpx.Client, key: str, query: ScrapeQuery, places: PlaceResolver
    ) -> Iterator[ParsedJob]:
        requests = 0
        for words, place, km in searches(query):
            for page in range(1, MAX_PAGES + 1):
                if requests >= self.max_requests:
                    return
                body = {"keywords": words, "location": place, "page": page}
                body |= {"ResultOnPage": PAGE_SIZE} | ({"radius": radius(km)} if km else {})
                resp = client.post(URL.format(key=key), json=body)
                requests += 1
                if resp.is_error:  # not raise_for_status: its message shows the key in the URL
                    raise RuntimeError(f"Jooble answered HTTP {resp.status_code}")
                data = resp.json()
                jobs = data.get("jobs") or []
                for item in jobs:
                    if job := self.parse(item, places):
                        yield job
                if len(jobs) < PAGE_SIZE or page * PAGE_SIZE >= (data.get("totalCount") or 0):
                    break

    @staticmethod
    def parse(item: dict, places: PlaceResolver) -> ParsedJob | None:
        title = clean_title(item.get("title"))
        if not item.get("id") or not title or not item.get("link"):
            return None
        place = item.get("location") or None
        job = ParsedJob(
            external_id=str(item["id"]),
            title=title,
            url=item["link"],
            description=html_to_text(item.get("snippet")),
            employer_name=item.get("company") or None,
            place_text=place,
            location=locate(place, places),
            contract_types=contract_types(item.get("type")),
            posted_at=parse_date(item.get("updated")),
        )
        set_salary(job, item.get("salary"))
        if item.get("source"):
            job.extra["via"] = item["source"]
        return job


SOURCE = Jooble()
