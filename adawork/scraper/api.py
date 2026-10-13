"""Job search APIs that need the user's own free key: Jooble and Careerjet (PLAN §2.1).

Both answer a search (words, place, radius) with the newest ads, a short excerpt each and a
link to the full ad. They are asked once per kind of work near home (or per region when the
home is unknown), a few pages at most, so a run costs a handful of requests: keys may have
small request limits. An ad no search returned for `expire_after_days` counts as gone.
"""

import socket
from collections.abc import Iterator

import httpx

from adawork.core import ParsedJob, ParsedLocation, ScrapeQuery, SourceKind
from adawork.geo.places import PlaceResolver
from adawork.normalize.text import split_places


class ApiSource:
    id: str
    label: str
    kind: SourceKind = "api"
    default_enabled = False  # switched on when the user saves a key
    needs_consent = False
    key_url: str  # where the user gets a key
    max_requests = 10  # per run
    expire_after_days = 14

    def search(
        self, client: httpx.Client, key: str, query: ScrapeQuery, places: PlaceResolver
    ) -> Iterator[ParsedJob]:
        raise NotImplementedError


def searches(query: ScrapeQuery) -> list[tuple[str, str, float | None]]:
    """(words, place, radius km) to ask for: each kind of work (or everything) near home, or
    in each region when the home is unknown."""
    words = query.keywords or [""]
    if query.home:
        return [(w, query.home, query.radius_km) for w in words]
    return [(w, kraj, None) for w in words for kraj in query.kraje]


def locate(text: str | None, places: PlaceResolver) -> ParsedLocation | None:
    """ "Kolín, Středočeský kraj" -> the first part that is a known place."""
    return next((loc for part in split_places(text) if (loc := places.resolve(part))), None)


def local_ip() -> str:
    """This computer's address on its network (no packet is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))  # TEST-NET address: only picks the outgoing interface
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"
