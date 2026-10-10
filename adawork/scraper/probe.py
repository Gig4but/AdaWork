"""Developer tool: is each portal still readable? `adawork probe [source id ...]`

Portals change their pages without notice, and the tests only see the pages saved in
tests/fixtures. A probe reads, politely, the first listing page of each portal and one ad's
detail page, and tells what came back empty. Run it before a release, or when a source's
counts drop.
"""

import argparse
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from adawork.core import ParsedJob, ScrapeQuery
from adawork.db.session import db_file, make_engine, make_sessionmaker, migrate
from adawork.geo.places import PlaceResolver
from adawork.net.http import make_client
from adawork.net.polite import PoliteFetcher, SourceBlocked
from adawork.scraper.crawl import CrawlSource, merge_card
from adawork.scraper.sources import CRAWL_SOURCES

QUERY = ScrapeQuery(kraje=["Hlavní město Praha"], home="Praha", radius_km=25)
# What a readable ad has; an empty one is reported.
CHECKED = ("title", "employer_name", "location", "salary_min", "description", "posted_at")


@dataclass
class Probe:
    source_id: str
    leads: int = 0
    detail: bool | None = None  # an ad's detail page was read; None: the source has none
    empty: list[str] = field(default_factory=list)  # CHECKED fields the ad lacks
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and self.leads > 0 and self.detail is not False

    def line(self) -> str:
        if self.error:
            return f"{self.source_id:22} FAILED  {self.error}"
        state = "ok" if self.ok else "CHECK"
        detail = {True: "detail", False: "detail UNREADABLE", None: "cards only"}[self.detail]
        empty = f"  empty: {', '.join(self.empty)}" if self.empty else ""
        return f"{self.source_id:22} {state:6}  {self.leads:3} leads, {detail}{empty}"


def missing(job: ParsedJob, places: PlaceResolver) -> list[str]:
    def empty(name: str) -> bool:
        value = getattr(job, name)
        return not value or (name == "description" and len(value) < 100)

    # Without municipalities (no search run yet) no ad gets a location.
    return [name for name in CHECKED if empty(name) and (places or name != "location")]


def probe(
    source: CrawlSource, fetcher: PoliteFetcher, places: PlaceResolver, query: ScrapeQuery = QUERY
) -> Probe:
    found = Probe(source.id)
    get = {"obey_robots": source.obey_robots, "min_delay": source.delay}
    try:
        source.prepare(query, places)
        _, listing_url = source.listings(query)[0]
        resp = fetcher.get(listing_url(1), **get)
        if resp is None:
            found.error = "listing page gone or disallowed by robots.txt"
            return found
        leads = source.parse_listing(resp.text, 1, places).leads
        found.leads = len(leads)
        lead = next((x for x in leads if x.fetch_url), None)
        job = None
        if lead is not None and lead.fetch_url:
            page = fetcher.get(lead.fetch_url, **get)
            job = source.parse_detail(page.text, lead, places) if page else None
            found.detail = job is not None
            job = merge_card(job, lead.card) if job else lead.card
        elif leads:
            job = leads[0].card
        if job is not None:
            found.empty = missing(job, places)
        elif leads:
            found.error = "no ad could be read"
        else:
            found.error = "no ads on the listing page (a new layout?)"
    except (httpx.HTTPError, SourceBlocked) as exc:
        found.error = f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # a parser broken by a new layout
        found.error = f"parser failed: {type(exc).__name__}: {exc}"
    return found


def run(sources: Sequence[CrawlSource], places: PlaceResolver) -> list[Probe]:
    with make_client() as client:
        fetcher = PoliteFetcher(client)
        results = []
        for source in sources:
            result = probe(source, fetcher, places)
            print(result.line(), flush=True)
            results.append(result)
    return results


def main(argv: list[str] | None = None) -> int:
    by_id = {s.id: s for s in CRAWL_SOURCES}
    parser = argparse.ArgumentParser(prog="adawork probe")
    parser.add_argument("sources", nargs="*", help=f"default: all of {', '.join(by_id)}")
    parser.add_argument("--db", type=Path, help="database with the municipalities (places)")
    args = parser.parse_args(argv)
    if unknown := [s for s in args.sources if s not in by_id]:
        parser.error(f"unknown sources: {', '.join(unknown)}")
    sources = [by_id[s] for s in args.sources] or list(CRAWL_SOURCES)
    path = args.db or db_file()
    with tempfile.TemporaryDirectory() as tmp:
        if not path.exists():  # no app data yet: ads without places
            path = Path(tmp) / "probe.db"
        engine = make_engine(path)
        migrate(engine)
        with make_sessionmaker(engine)() as s:
            places = PlaceResolver(s)
        if not places:
            print("No municipalities in the database: locations are not checked.")
        results = run(sources, places)
        engine.dispose()
    return 0 if all(r.ok for r in results) else 1
