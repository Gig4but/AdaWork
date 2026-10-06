"""The search page: the job list, new finds while a search runs, and starting a search."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from adawork.db.models import Job
from adawork.search.service import fts_query, jobs_found_since, search_jobs
from adawork.services import Services
from adawork.web.pages import PAGE_SIZE, Svc, render, status_context

router = APIRouter()


def newest_id(svc: Services, s: Session) -> int | None:
    """While a search runs: the last stored job, from which the list asks for new ones."""
    return (s.scalar(select(func.max(Job.id))) or 0) if svc.runner.progress.running else None


@router.get("/", response_class=HTMLResponse)
def index(request: Request, svc: Svc, q: str | None = None) -> Response:
    """Without `q`: the kinds of work from the wizard."""
    if not svc.settings.wizard_completed:
        return RedirectResponse("/wizard/welcome", status_code=303)
    if q is None:
        q = ", ".join(svc.settings.profile.kinds_of_work)
    with svc.sessions() as s:
        since = newest_id(svc, s)
        result = search_jobs(s, q, limit=PAGE_SIZE, up_to_id=since)
    return render(
        request,
        "search.html",
        q=q,
        result=result,
        since=since,
        offset=0,
        progress=svc.runner.progress,
        **status_context(svc),
    )


@router.get("/results", response_class=HTMLResponse)
def results(request: Request, svc: Svc, q: str = "", offset: int = 0) -> HTMLResponse:
    with svc.sessions() as s:
        since = newest_id(svc, s) if offset == 0 else None
        result = search_jobs(s, q, limit=PAGE_SIZE, offset=offset, up_to_id=since)
    return render(
        request,
        "_results.html",
        q=q,
        result=result,
        since=since,
        progress=svc.runner.progress,
        offset=offset,
    )


@router.get("/results/new", response_class=HTMLResponse)
def new_results(
    request: Request, svc: Svc, q: str = "", since: int = 0, cleared: bool = False
) -> Response:
    """Jobs found since the list was shown, put on top of it while a search runs. When
    they belong elsewhere in the order, are too many, or the old jobs were deleted
    meanwhile, the whole list reloads."""
    p = svc.runner.progress
    if not p.running:
        return HTMLResponse("")  # the finished run reloads the list
    if p.cleared != cleared:
        return HTMLResponse("", headers={"HX-Trigger": "jobs-updated"})
    on_top = fts_query(q) is None
    with svc.sessions() as s:
        newest = newest_id(svc, s) or 0
        limit = PAGE_SIZE + 1 if on_top else 1
        jobs, total = jobs_found_since(s, since, newest, limit)
    if jobs and (not on_top or len(jobs) > PAGE_SIZE):
        return HTMLResponse("", headers={"HX-Trigger": "jobs-updated"})
    return render(
        request, "_new_results.html", jobs=jobs, total=total, q=q, since=newest, progress=p
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
