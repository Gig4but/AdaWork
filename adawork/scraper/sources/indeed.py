"""Indeed CZ: link-out only, decided in M4 (2026-10-06).

Indeed's search page answers the app's honest User-Agent with a Cloudflare "Security Check"
(HTTP 403); it worked in the M0 spike only with a browser-like User-Agent. Rather than
disguise the app, the search page offers a link that opens the same search on Indeed in the
user's own browser. Indeed mostly re-lists ads that other sources bring in anyway.
"""

from urllib.parse import urlencode

SEARCH = "https://cz.indeed.com/jobs"
RADII_KM = (5, 10, 15, 25, 35, 50, 100)  # the values Indeed's radius filter offers


def search_url(keywords: str = "", home: str | None = None, radius_km: float = 25) -> str:
    params: dict[str, str | int] = {"q": keywords.strip(), "l": home or "", "sort": "date"}
    if home:
        params["radius"] = next((r for r in RADII_KM if r >= radius_km), RADII_KM[-1])
    return f"{SEARCH}?{urlencode(params)}"
