"""The job map: pins of the matching jobs."""

import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from adawork.search.service import job_points, jobs_at_location
from adawork.web.pages import Svc, filters_from_query, render

router = APIRouter()

POPUP_SIZE = 50


@router.get("/map/points")
def map_points(request: Request, svc: Svc) -> Response:
    """GeoJSON of job locations matching the filters (MapLibre clusters them).

    Serialized directly: FastAPI's generic encoder took >1 s for ~15k features.
    """
    f = filters_from_query(request)
    with svc.sessions() as s:
        points = job_points(s, f)
    features = [
        {
            "type": "Feature",
            # 5 decimals = ~1 m, plenty for a job pin
            "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
            "properties": {"id": loc_id, "count": count, "approx": precision != "address"},
        }
        for loc_id, lat, lon, precision, count in points
    ]
    body = json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":"))
    return Response(body, media_type="application/geo+json")


@router.get("/map/location/{location_id}", response_class=HTMLResponse)
def map_location(request: Request, svc: Svc, location_id: int) -> HTMLResponse:
    f = filters_from_query(request)
    with svc.sessions() as s:
        jobs, total = jobs_at_location(s, f, location_id, limit=POPUP_SIZE)
    return render(request, "_map_popup.html", jobs=jobs, total=total)
