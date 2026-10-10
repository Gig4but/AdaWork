"""Which regions to crawl: those reachable from home within the user's travel-time limit.

Portals are read region by region, so a user in Kolín does not wait for every listing page of
Ostrava. Straight-line distance at the best speed of the travel mode keeps this an upper
bound; the exact travel-time filter is applied later on the jobs themselves.
"""

from adawork.config import Profile
from adawork.geo.places import KRAJE, REACH_KM_PER_MINUTE, PlaceResolver

MIN_RADIUS_KM = 15


def _speed(mode: str) -> float:
    return max(REACH_KM_PER_MINUTE.values()) if mode == "any" else REACH_KM_PER_MINUTE[mode]


def crawl_kraje(places: PlaceResolver, profile: Profile | None) -> list[str]:
    """Regions to crawl, nearest first. Every region when home or its region is unknown."""
    home = places.find_home(profile.home_address) if profile and profile.home_address else None
    if home is None or profile is None:
        return list(KRAJE)
    km = max(MIN_RADIUS_KM, profile.max_minutes * _speed(profile.travel_mode))
    kraje = places.kraje_within(home.lat, home.lon, km)
    if home.kraj and home.kraj not in kraje:
        kraje.insert(0, home.kraj)
    return kraje or list(KRAJE)
