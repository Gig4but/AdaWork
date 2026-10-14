"""The job map: pins of the matching jobs, and the offline map served from the routing data
(MapLibre style, vector tiles, fonts)."""

import json
import re

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from adawork import paths
from adawork.routing.motis import MotisUnavailable
from adawork.search.service import job_points, jobs_at_location
from adawork.web import mapstyle
from adawork.web.pages import Svc, render

router = APIRouter()

POPUP_SIZE = 50
OPENFREEMAP = "https://tiles.openfreemap.org/styles/{}"  # online map until routing data is ready


@router.get("/map/points")
def map_points(request: Request, svc: Svc) -> Response:
    """GeoJSON of job locations matching the filters (MapLibre clusters them).

    Serialized directly: FastAPI's generic encoder took >1 s for ~15k features.
    """
    f = svc.travel.filters(request.query_params)
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
    f = svc.travel.filters(request.query_params)
    with svc.sessions() as s:
        jobs, total = jobs_at_location(s, f, location_id, limit=POPUP_SIZE)
        times = svc.travel.times(s, f, jobs[:1])
    return render(request, "_map_popup.html", jobs=jobs, total=total, times=times)


@router.get("/map/style.json")
def map_style(request: Request, svc: Svc, dark: bool = False) -> Response:
    """The offline map from the imported data; the online one until it is ready."""
    if not svc.routing.ready:
        url = OPENFREEMAP.format("dark" if dark else "positron")
        return RedirectResponse(url, status_code=307)
    base = str(request.base_url).rstrip("/")  # MapLibre wants absolute tile addresses
    body = mapstyle.style(
        base + "/map/tiles/{z}/{x}/{y}.mvt",
        base + "/map/glyphs/{fontstack}/{range}.pbf",
        dark,
    )
    return Response(json.dumps(body), media_type="application/json")


@router.get("/map/tiles/{z}/{x}/{y}.mvt")
def map_tile(svc: Svc, z: int, x: int, y: int) -> Response:
    if not svc.routing.ready:
        return Response(status_code=404)
    try:
        with svc.motis.use(svc.routing.data) as m:
            r = m.tile(f"/tiles/{z}/{x}/{y}.mvt")
    except (httpx.HTTPError, MotisUnavailable):
        return Response(status_code=503)
    if r.status_code != 200:
        return Response(status_code=204)
    return Response(
        r.content,
        media_type="application/vnd.mapbox-vector-tile",
        headers={"Cache-Control": "max-age=86400"},
    )


@router.get("/map/glyphs/{fontstack}/{glyph_range}.pbf")
def map_glyphs(fontstack: str, glyph_range: str) -> Response:
    font = fontstack.split(",")[0].strip()
    if not re.fullmatch(r"\w[\w ]*", font) or not re.fullmatch(r"\d+-\d+", glyph_range):
        return Response(status_code=404)
    path = paths.vendor_dir() / "motis" / "ui" / "glyphs" / font / f"{glyph_range}.pbf"
    if not path.exists():
        return Response(status_code=404)
    return FileResponse(path, headers={"Cache-Control": "max-age=604800"})
