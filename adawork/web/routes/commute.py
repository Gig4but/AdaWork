"""The travel-time bar and the commute window (PLAN §4.6)."""

import json
import logging
import re
from html import escape
from typing import Annotated

import httpx
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from adawork.config import TIME_OF_DAY
from adawork.db.models import Job
from adawork.geo.places import distance_km
from adawork.normalize.text import fold
from adawork.routing import deeplinks
from adawork.routing.commute import MODES, MOTIS_MODES, departure, estimate, routes
from adawork.routing.motis import MotisUnavailable
from adawork.search.service import MAX_TRAVEL, filters_from
from adawork.search.travel import origin_of
from adawork.web.pages import Svc, query_string, render

router = APIRouter()
log = logging.getLogger(__name__)


@router.get("/commute/places", response_class=HTMLResponse)
def commute_places(request: Request, svc: Svc) -> HTMLResponse:
    """Suggestions for an address box (a datalist): addresses from the map's geocoder
    where it exists, else municipality names."""
    text = next((v for k, v in request.query_params.items() if k in ("from", "home_address")), "")
    text = text.strip()
    names: list[str] = []
    if len(text) >= 2:
        if svc.routing.geocoding:
            try:
                with svc.motis.use(svc.routing.data) as m:
                    for place in m.geocode(text)[:8]:
                        areas = [a["name"] for a in place.get("areas", []) if a.get("matched")]
                        town = areas[-1] if areas else ""
                        names.append(f"{place['name']}, {town}" if town else place["name"])
            except (httpx.HTTPError, MotisUnavailable):
                log.warning("Geocoding %r failed", text, exc_info=True)
        if not names:
            folded = fold(text)
            found = [m for m in svc.places().municipalities if fold(m.name).startswith(folded)]
            found.sort(key=lambda m: -m.address_count)
            names = [f"{m.name}, {m.okres}" if m.okres else m.name for m in found[:8]]
    options = "".join(f'<option value="{escape(n)}"></option>' for n in dict.fromkeys(names))
    return HTMLResponse(options)


@router.get("/commute/locate")
def commute_locate(svc: Svc, text: str = "") -> Response:
    """The start point of a typed address (or "lat,lon"), for the travel-time bar."""
    origin = svc.travel.locator.locate(text)
    if origin is None:
        return Response(status_code=404)
    return Response(
        json.dumps({"at": origin.key, "label": origin.label, "precision": origin.precision}),
        media_type="application/json",
    )


@router.get("/commute/defaults")
def commute_defaults(svc: Svc) -> JSONResponse:
    """The profile's travel-time bar: the search page takes it after the profile is saved."""
    return JSONResponse(svc.travel.bar())


@router.get("/commute/area")
def commute_area(request: Request, svc: Svc) -> Response:
    """Grid cells reachable from the start point (MOTIS has no isochrone endpoint)."""
    f = filters_from(request.query_params)
    origin = origin_of(f)
    if origin is None:
        return Response(status_code=404)
    try:
        area = svc.commute.area(origin, f.travel_modes, f.depart, f.travel_max or MAX_TRAVEL)
    except (httpx.HTTPError, MotisUnavailable):
        log.warning("Reachable area failed", exc_info=True)
        return Response(status_code=503)
    return Response(json.dumps(area, separators=(",", ":")), media_type="application/geo+json")


@router.get("/commute/state", response_class=HTMLResponse)
def commute_status(request: Request, svc: Svc, was: bool = False) -> HTMLResponse:
    """The travel-time bar's note; asks the list to reload once exact times are in."""
    f = svc.travel.filters(request.query_params)
    state = svc.travel.state(f)
    response = render(request, "_commute_state.html", f=f, commute=state)
    data_state = state["routing"]
    busy = state["computing"] or (data_state["step"] and not data_state["ready"])
    if was and not busy:
        response.headers["HX-Trigger"] = "jobs-updated"
    return response


@router.get("/commute/job/{job_id}", response_class=HTMLResponse)
def commute_window(
    request: Request,
    svc: Svc,
    job_id: int,
    mode: str = "",
    time_: Annotated[str, Query(alias="time")] = "",
    arrive_: Annotated[str, Query(alias="arrive")] = "",  # "1": arrive by `time`
) -> HTMLResponse:
    """ "Jak se tam dostanu": routes by one mode with the time, legs and line numbers,
    the reachable area, and the same route in Mapy.com, IDOS or Google Maps."""
    f = svc.travel.filters(request.query_params)
    origin = origin_of(f)
    arrive = arrive_ == "1"
    with svc.sessions() as s:
        job = s.get(Job, job_id)
        if job is None:
            return HTMLResponse("", status_code=404)
        location = job.location
        times = svc.travel.times(s, f, [job]).get(job.location_id, {})
    place = (location and (location.address_text or location.municipality)) or job.place_text
    context = {"job": job, "location": location, "place": place or "", "origin": origin}
    if origin is None or location is None or location.lat is None or location.lon is None:
        return render(request, "_commute.html", f=f, missing=True, **context)
    if mode not in MODES:
        mode = f.travel_mode if f.travel_mode in MODES else "transit"
    depart = time_ if re.fullmatch(TIME_OF_DAY, time_) else f.depart
    when = departure(depart)
    destination = (location.lat, location.lon)
    found, error = [], None
    area = svc.routing.area
    routable = svc.routing.ready and (
        area is None or (area.contains(*origin.point) and area.contains(*destination))
    )
    if routable:
        try:
            with svc.motis.use(svc.routing.data) as m:
                plan = m.plan(
                    origin.point,
                    destination,
                    when,
                    "transit" if mode == "transit" else MOTIS_MODES[mode],
                    arrive_by=arrive,
                    count=5,
                )
            found = routes(plan, mode)
        except (httpx.HTTPError, MotisUnavailable) as exc:
            log.warning("Route to job %d failed: %s", job_id, exc)
            error = True
    km = distance_km(*origin.point, *destination)
    geo = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"route": i, "mode": leg["mode"]},
                "geometry": {"type": "LineString", "coordinates": leg["coords"]},
            }
            for i, r in enumerate(found)
            for leg in r["legs"]
            if len(leg["coords"]) > 1
        ],
    }
    links = {
        "mapy": deeplinks.mapy(origin.point, destination, mode),
        "idos": deeplinks.idos(origin.label, place or "", when, arrive),
        "google": deeplinks.google(origin.point, destination, mode),
    }
    return render(
        request,
        "_commute.html",
        f=f,
        mode=mode,
        times=times,
        routes=found,
        error=error,
        routable=routable,
        estimate=estimate(km, mode),
        geo=json.dumps(geo, separators=(",", ":")),
        ends=json.dumps([[origin.lon, origin.lat], [destination[1], destination[0]]]),
        depart=depart,
        when=when,
        arrive=arrive,
        links=links,
        qs=query_string(request),
        **context,
    )
