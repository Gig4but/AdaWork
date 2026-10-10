"""The search page: the job list with its filters, new finds while a search runs, and
starting a search."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from adawork.db.models import Job
from adawork.geo.scope import radius_km
from adawork.scraper.sources import SWITCHABLE, indeed
from adawork.search.service import jobs_found_since, search_jobs
from adawork.services import Services
from adawork.web.pages import (
    PAGE_SIZE,
    POSTED_DAYS,
    SALARY_STEPS,
    Svc,
    filters_from_query,
    query_string,
    render,
    status_context,
)
from adawork.web.routes.ai import ai_context

router = APIRouter()


def newest_id(svc: Services, s: Session) -> int | None:
    """While a search runs: the last stored job, from which the list asks for new ones."""
    return (s.scalar(select(func.max(Job.id))) or 0) if svc.runner.progress.running else None


@router.get("/", response_class=HTMLResponse)
def index(request: Request, svc: Svc) -> Response:
    if not svc.settings.wizard_completed:
        return RedirectResponse("/wizard/welcome", status_code=303)
    f = filters_from_query(request)
    with svc.sessions() as s:
        since = newest_id(svc, s)
        result = search_jobs(s, f, limit=PAGE_SIZE, up_to_id=since)
    return render(
        request,
        "search.html",
        f=f,
        qs=query_string(request),
        result=result,
        since=since,
        offset=0,
        progress=svc.runner.progress,
        salary_steps=SALARY_STEPS,
        posted_days=POSTED_DAYS,
        view="map" if request.query_params.get("view") == "map" else "list",
        ai=ai_context(svc),
        **status_context(svc),
    )


@router.get("/results", response_class=HTMLResponse)
def results(request: Request, svc: Svc, offset: int = 0) -> HTMLResponse:
    f = filters_from_query(request)
    with svc.sessions() as s:
        since = newest_id(svc, s) if offset == 0 else None
        result = search_jobs(s, f, limit=PAGE_SIZE, offset=offset, up_to_id=since)
    qs = query_string(request)
    response = render(
        request,
        "_results.html",
        f=f,
        qs=qs,
        result=result,
        since=since,
        progress=svc.runner.progress,
        offset=offset,
    )
    if offset == 0:  # keep the filters in the address, so a reload keeps them
        response.headers["HX-Push-Url"] = f"/?{qs}" if qs else "/"
    return response


@router.get("/results/new", response_class=HTMLResponse)
def new_results(request: Request, svc: Svc, since: int = 0, cleared: bool = False) -> Response:
    """Jobs found since the list was shown, put on top of it while a search runs. When
    they belong elsewhere in the order, are too many, or the old jobs were deleted
    meanwhile, the whole list reloads."""
    p = svc.runner.progress
    if not p.running:
        return HTMLResponse("")  # the finished run reloads the list
    if p.cleared != cleared:
        return HTMLResponse("", headers={"HX-Trigger": "jobs-updated"})
    f = filters_from_query(request)
    on_top = f.newest_found_first()
    with svc.sessions() as s:
        newest = newest_id(svc, s) or 0
        limit = PAGE_SIZE + 1 if on_top else 1
        jobs, total = jobs_found_since(s, f, since, newest, limit)
    if jobs and (not on_top or len(jobs) > PAGE_SIZE):
        return HTMLResponse("", headers={"HX-Trigger": "jobs-updated"})
    return render(
        request,
        "_new_results.html",
        jobs=jobs,
        total=total,
        qs=query_string(request),
        since=newest,
        progress=p,
    )


@router.get("/facets", response_class=HTMLResponse)
def facets(request: Request, svc: Svc) -> HTMLResponse:
    """Filter panel with counts; loaded after the results so they appear first."""
    f = filters_from_query(request)
    with svc.sessions() as s:
        result = search_jobs(s, f, limit=0, facets=True)
    return render(
        request,
        "_facets.html",
        f=f,
        sources_on=[sid for sid, on in SWITCHABLE if svc.settings.sources.get(sid, on)],
        facets=result.facets,
        salary_steps=SALARY_STEPS,
        posted_days=POSTED_DAYS,
    )


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, svc: Svc, job_id: int) -> Response:
    """A job with its whole text: in the search page's dialog, or as a page of its own."""
    with svc.sessions() as s:
        job = s.get(Job, job_id)
        if job is None:
            return Response(status_code=404)
        _ = job.sources  # loaded for the template
    dialog = request.headers.get("HX-Request") and not request.headers.get("HX-Boosted")
    return render(request, "_job_detail.html" if dialog else "job.html", job=job, dialog=dialog)


@router.post("/scrape", response_class=HTMLResponse)
def scrape(request: Request, svc: Svc, fresh: bool = False) -> HTMLResponse:
    """`fresh`: the jobs found so far are deleted and found again."""
    svc.runner.start(fresh=fresh)
    response = render(request, "_progress.html", progress=svc.runner.progress)
    response.headers["HX-Trigger"] = "jobs-updated"  # the list starts showing new finds
    return response


@router.get("/scrape/progress", response_class=HTMLResponse)
def scrape_progress(request: Request, svc: Svc) -> HTMLResponse:
    progress = svc.runner.progress
    response = render(request, "_progress.html", progress=progress)
    if not progress.running and progress.phase == "done":
        response.headers["HX-Trigger"] = "jobs-updated"
    return response


@router.get("/status", response_class=HTMLResponse)
def status(request: Request, svc: Svc) -> HTMLResponse:
    return render(request, "_status.html", **status_context(svc))


@router.get("/out/indeed")
def out_indeed(svc: Svc, q: str = "") -> RedirectResponse:
    """The same search on Indeed, opened in the user's browser (link-out only)."""
    profile = svc.settings.profile
    home = None
    if profile.home_address:
        found = svc.places().find_home(profile.home_address)
        home = found.name if found else None
    return RedirectResponse(indeed.search_url(q, home, radius_km(profile)), status_code=303)
