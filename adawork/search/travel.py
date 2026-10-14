"""The travel-time filter of the search (PLAN §4.6): its start point, its defaults from the
profile, and the travel times made ready before a search uses them."""

import logging
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import QueryParams

from adawork.config import Profile
from adawork.db.models import SavedSearch
from adawork.geo.home import Locator, Origin
from adawork.routing.commute import CommuteService
from adawork.routing.data import RoutingData
from adawork.search.saved import filters_of
from adawork.search.service import MAX_TRAVEL, Filters, filters_from

log = logging.getLogger(__name__)


def origin_of(f: Filters) -> Origin | None:
    if not f.origin:
        return None
    lat, lon = map(float, f.origin.split(","))
    return Origin(lat, lon, f.origin_label or f.origin, "point")


def kept_starts(s: Session, home: tuple[str, str] | None) -> set[tuple[str, str]]:
    """Start points whose times are never dropped: home, and those of saved searches
    (counted on every page and after each automatic check)."""
    starts = {home} if home else set()
    for saved in s.scalars(select(SavedSearch)):
        f = filters_of(saved)
        if f.origin:
            starts.add((f.origin, f.depart))
    return starts


class TravelFilter:
    def __init__(
        self,
        profile: Callable[[], Profile],
        locator: Locator,
        routing: RoutingData,
        commute: CommuteService,
    ) -> None:
        self.profile = profile
        self.locator = locator
        self.routing = routing
        self.commute = commute

    def on(self) -> bool:
        """Exact times and routes need the map imported at least once (Settings → Mapy);
        until then the filter uses straight-line estimates."""
        return self.routing.version > 0

    def home(self) -> Origin | None:
        return self.locator.locate(self.profile().home_address)

    def home_start(self) -> tuple[str, str] | None:
        home = self.home()
        return (home.key, self.profile().depart_time) if home else None

    def bar(self) -> dict[str, str]:
        """The travel-time bar as the profile sets it ("at" empty: the home is not found)."""
        p = self.profile()
        home = self.home()
        return {
            "from": p.home_address,
            "at": home.key if home else "",
            "mode": p.travel_mode,
            "max_min": str(min(p.max_minutes, MAX_TRAVEL)),
            "depart": p.depart_time,
        }

    def defaults(self) -> dict[str, str]:
        """The profile's kinds of work and travel-time filter, for a search page without
        them."""
        p = self.profile()
        found = {"q": ", ".join(p.kinds_of_work)} if p.kinds_of_work else {}
        bar = self.bar()
        return found | bar if bar["at"] else found

    def filters(self, params: QueryParams) -> Filters:
        """Filters from query parameters; unknown or malformed values are ignored. Travel
        times from the chosen start point are made ready (estimates at first)."""
        f = filters_from(params, self.defaults())
        self.prepare(f)
        return f

    def prepare(self, f: Filters) -> None:
        """Travel times from the filters' start point are made ready (estimates at first)."""
        origin = origin_of(f)
        if origin is not None:
            try:
                self.commute.ensure(origin, f.depart)
            except Exception:
                log.exception("Travel times from %s not ready", origin.key)

    def times(self, s: Session, f: Filters, jobs) -> dict:
        origin = origin_of(f)
        if origin is None:
            return {}
        return self.commute.times(s, origin, f.depart, [j.location_id for j in jobs])

    def state(self, f: Filters) -> dict:
        origin = origin_of(f)
        return {
            "routing": self.routing.status(),
            "computing": origin is not None and self.commute.computing(origin, f.depart),
            "progress": self.commute.progress,
        }

    def routing_ready(self) -> None:
        """New routing data: times are computed again, from home first."""
        self.commute.forget()
        home = self.home()
        if home is not None:
            self.commute.ensure(home, self.profile().depart_time)
