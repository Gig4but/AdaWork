"""The same route opened in Mapy.com, IDOS or Google Maps (links only, no API or key)."""

from datetime import datetime
from urllib.parse import urlencode

Point = tuple[float, float]  # (lat, lon)

GOOGLE_MODES = {"car": "driving", "transit": "transit", "walk": "walking", "bike": "bicycling"}
# Mapy.com has no public transport in its route links (developer.mapy.com, "Mapy.com URL").
MAPY_MODES = {"car": "car_fast", "walk": "foot_fast", "bike": "bike_road"}


def google(origin: Point, destination: Point, mode: str) -> str:
    return "https://www.google.com/maps/dir/?" + urlencode(
        {
            "api": 1,
            "origin": f"{origin[0]},{origin[1]}",
            "destination": f"{destination[0]},{destination[1]}",
            "travelmode": GOOGLE_MODES[mode],
        }
    )


def mapy(origin: Point, destination: Point, mode: str) -> str | None:
    if mode not in MAPY_MODES:
        return None
    return "https://mapy.com/fnc/v1/route?" + urlencode(
        {
            "start": f"{origin[1]},{origin[0]}",  # lon,lat
            "end": f"{destination[1]},{destination[0]}",
            "routeType": MAPY_MODES[mode],
        }
    )


def idos(origin_name: str, destination_name: str, when: datetime, arrive: bool = False) -> str:
    """Public transport connections by place names (IDOS does not take coordinates)."""
    params = {
        "f": origin_name,
        "t": destination_name,
        "date": f"{when.day}.{when.month}.{when.year}",
        "time": f"{when.hour}:{when.minute:02d}",
    }
    if arrive:
        params["arr"] = "true"
    return "https://idos.cz/vlakyautobusymhdvse/spojeni/vysledky/?" + urlencode(params)
