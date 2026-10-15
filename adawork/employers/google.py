"""Customer ratings from Google Maps, only with the user's own Places API key (PLAN §2.4).

The cheapest flow (docs/spikes/employers.md): a Text Search asking only for place ids (free,
unlimited), then Place Details with rating, count and up to 5 reviews, which bills the
"Enterprise + Atmosphere" SKU: 1 000 free a month. The app stops at FREE_DETAILS, so the
user's card is never charged; the count is shown in Settings.

These are reviews by customers of a shop or branch, not by employees; the card says so.
"""

from dataclasses import dataclass, field
from datetime import datetime

import httpx

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
DETAILS_URL = "https://places.googleapis.com/v1/places/{id}"
DETAILS_FIELDS = "displayName,rating,userRatingCount,reviews,googleMapsUri"
FREE_DETAILS = 950  # of the 1 000 free details a month, some kept in reserve


class QuotaUsed(Exception):
    """This month's free details are used up."""


@dataclass
class Quota:
    month: str = ""
    details: int = 0

    @classmethod
    def load(cls, data: dict | None, now: datetime) -> "Quota":
        month = f"{now:%Y-%m}"
        data = data or {}
        return cls(month, data.get("details", 0) if data.get("month") == month else 0)

    def dump(self) -> dict:
        return {"month": self.month, "details": self.details}

    @property
    def left(self) -> int:
        return max(FREE_DETAILS - self.details, 0)


@dataclass
class Place:
    name: str
    rating: float | None
    count: int
    url: str
    reviews: list[dict] = field(default_factory=list)  # {"rating", "text", "when"}


def find_place(client: httpx.Client, key: str, query: str) -> str | None:
    """The place id of the best match (the IDs-only Text Search, free)."""
    resp = client.post(
        SEARCH_URL,
        json={"textQuery": query, "languageCode": "cs", "regionCode": "CZ"},
        headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": "places.id"},
        timeout=30,
    )
    resp.raise_for_status()
    places = resp.json().get("places") or []
    return places[0]["id"] if places else None


def details(client: httpx.Client, key: str, place_id: str, quota: Quota) -> Place:
    if quota.left <= 0:
        raise QuotaUsed(quota.month)
    quota.details += 1  # counted even when it fails: Google may have billed it
    resp = client.get(
        DETAILS_URL.format(id=place_id),
        params={"languageCode": "cs"},
        headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": DETAILS_FIELDS},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    reviews = [
        {
            "rating": r.get("rating"),
            "text": ((r.get("text") or {}).get("text") or "").strip()[:1500],
            "when": r.get("relativePublishTimeDescription") or "",
        }
        for r in data.get("reviews") or []
    ]
    return Place(
        name=(data.get("displayName") or {}).get("text") or "",
        rating=data.get("rating"),
        count=data.get("userRatingCount") or 0,
        url=data.get("googleMapsUri") or "",
        reviews=[r for r in reviews if r["text"]],
    )
