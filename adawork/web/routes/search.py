"""The search page: the job list with its filters, new finds while a search runs, hidden
ads, and starting a search."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from adawork.core import utcnow
from adawork.db.models import Job, table
from adawork.geo.scope import radius_km
from adawork.scraper.check import unchecked
from adawork.scraper.sources import SWITCHABLE, indeed
from adawork.search.service import (
    batch_starts,
    count_between,
    facet_counts,
    filters_from,
    jobs_found_since,
    new_after,
    search_jobs,
    still_matching,
)
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


def hidden_count(s: Session) -> int:
    stmt = select(func.count(Job.id)).where(Job.hidden_at.is_not(None), Job.status == "active")
    return s.scalar(stmt) or 0


def newest_id(svc: Services, s: Session) -> int | None:
    """While a search runs: the last stored job, from which the list asks for new ones.
    While a search starts over (the jobs found before are deleted), none is listed (0)."""
    p = svc.runner.progress
    if not p.running:
        return None
    if p.restarting or p.phase == "clear":
        return 0
    return s.scalar(select(func.max(Job.id))) or 0


def unchecked_of(svc: Services, s: Session, job_ids) -> set[int]:
    """While a refresh checks the ads found before: those of the jobs not checked yet."""
    p = svc.runner.progress
    if not (p.running and p.checked_since and p.checking and job_ids):
        return set()
    return unchecked(s, p.checking, p.checked_since, job_ids)


@router.get("/", response_class=HTMLResponse)
def index(request: Request, svc: Svc) -> Response:
    if not svc.settings.wizard_completed:
        return RedirectResponse("/wizard/welcome", status_code=303)
    f = filters_from_query(request)
    svc.runner.focus = f  # a refresh checks the ads of this list first
    with svc.sessions() as s:
        since = newest_id(svc, s)
        result = search_jobs(s, f, limit=PAGE_SIZE, up_to_id=since)
        hidden = hidden_count(s)
        marks = unchecked_of(svc, s, [j.id for j in result.jobs])
        fresh_from = new_after(s)
    return render(
        request,
        "search.html",
        unchecked=marks,
        new_after=fresh_from,
        hidden=hidden,
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
    svc.runner.focus = f
    with svc.sessions() as s:
        since = newest_id(svc, s) if offset == 0 else None
        result = search_jobs(s, f, limit=PAGE_SIZE, offset=offset, up_to_id=since)
        hidden = hidden_count(s)
        marks = unchecked_of(svc, s, [j.id for j in result.jobs])
        fresh_from = new_after(s)
    qs = query_string(request)
    response = render(
        request,
        "_results.html",
        unchecked=marks,
        new_after=fresh_from,
        hidden=hidden,
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


@router.post("/results/new", response_class=HTMLResponse)
async def new_results(
    request: Request, svc: Svc, since: int = 0, cleared: bool = False
) -> Response:
    """While a search runs: drops the listed jobs that no longer match (expired, merged)
    and adds the new finds where they belong in the order found, after this search's
    earlier finds and above the jobs found before. With another order the list reloads.

    `shown`: the ids listed, in order; `more`: the list ends with a "more" button."""
    p = svc.runner.progress
    if not p.running:
        return HTMLResponse("")  # the finished run reloads the list
    if p.cleared != cleared:
        return HTMLResponse("", headers={"HX-Trigger": "jobs-updated"})
    form = await request.form()
    shown = [int(v) for v in str(form.get("shown", "")).split(",")[:5000] if v.isdigit()]
    has_more = form.get("more") == "1"

    def found(since: int) -> Response:  # off the event loop: a hide is not held up
        f = filters_from_query(request)
        with svc.sessions() as s:
            newest = newest_id(svc, s) or 0
            still = still_matching(s, f, shown) if shown else set()
            listed = [i for i in shown if i in still]
            marks = unchecked_of(svc, s, listed)
            jobs, total = jobs_found_since(s, f, since, newest, PAGE_SIZE + 1, skip=listed)
            if jobs and not f.new_first():
                return HTMLResponse("", headers={"HX-Trigger": "jobs-updated"})
            batch = (batch_starts(s) or [0])[-1]
            fresh_from = new_after(s)
            older = next((i for i in listed if i <= batch), None)
            if older is None and jobs:
                # This search's finds continue on pages not loaded: the new ones go there too.
                loaded = sum(1 for i in listed if batch < i <= since)
                if count_between(s, f, batch, since) > loaded:
                    jobs = []
        if older is not None:  # above the jobs found before, a page at a time
            where, add_more = f"beforebegin:#job-{older}", False
            since = jobs[PAGE_SIZE - 1].id if len(jobs) > PAGE_SIZE else newest
            jobs = jobs[:PAGE_SIZE]
        else:  # at the end, filling the loaded pages; the rest is behind "more"
            pages = max(1, -(-len(listed) // PAGE_SIZE))
            room = max(0, pages * PAGE_SIZE - len(listed))
            where = "beforebegin:#job-list > .more" if has_more else "beforeend:#job-list"
            add_more = len(jobs) > room and not has_more
            since, jobs = newest, jobs[:room]
        return render(
            request,
            "_new_results.html",
            jobs=jobs,
            gone=[i for i in shown if i not in still],
            where=where,
            add_more=add_more,
            total=total,
            qs=query_string(request),
            since=since,
            progress=p,
            unchecked=marks,
            new_after=fresh_from,
        )

    return await run_in_threadpool(found, since)


@router.post("/jobs/{job_id}/hide", response_class=HTMLResponse)
async def hide_job(request: Request, svc: Svc, job_id: int) -> Response:
    """Hides an ad from the list; the hidden bar brings it back."""
    form = await request.form()  # the search form, when hidden from the list

    def hide() -> Response:  # off the event loop: the write may wait for a scrape's
        with svc.sessions() as s:
            if s.get(Job, job_id) is None:
                return Response(status_code=404)
            # Not through the ORM: a mark does not change what the filter counts cache holds.
            s.execute(update(table(Job)).where(Job.id == job_id).values(hidden_at=utcnow()))
            s.commit()
            total = None
            if "sort" in form:
                f = filters_from(form)
                total = search_jobs(s, f, limit=0, up_to_id=newest_id(svc, s)).total
            hidden = hidden_count(s)
        return render(request, "_hidden.html", hidden=hidden, total=total, oob=True)

    return await run_in_threadpool(hide)


@router.post("/hidden/restore", response_class=HTMLResponse)
def restore_hidden(request: Request, svc: Svc, every: bool = False) -> HTMLResponse:
    """Brings back the ad hidden last (again and again: the one before), or all."""
    with svc.sessions() as s:
        ids = select(Job.id).where(Job.hidden_at.is_not(None), Job.status == "active")
        if not every:
            ids = ids.order_by(Job.hidden_at.desc(), Job.id.desc()).limit(1)
        chosen = list(s.scalars(ids))
        s.execute(update(table(Job)).where(Job.id.in_(chosen)).values(hidden_at=None))
        s.commit()
        hidden = hidden_count(s)
    response = render(request, "_hidden.html", hidden=hidden)
    response.headers["HX-Trigger"] = "jobs-updated"  # the ads return to their places
    return response


@router.get("/facets", response_class=HTMLResponse)
def facets(request: Request, svc: Svc) -> HTMLResponse:
    """Filter panel with counts; loaded after the results so they appear first."""
    f = filters_from_query(request)
    with svc.sessions() as s:
        counts, flagged = facet_counts(s, f)
    return render(
        request,
        "_facets.html",
        f=f,
        sources_on=[sid for sid, on in SWITCHABLE if svc.settings.sources.get(sid, on)],
        facets=counts,
        flagged=flagged,
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
    """A refresh: the jobs found so far are checked and new ones added (not again while one
    runs). `fresh`: they are deleted and found again; a running search stops for it."""
    if fresh:
        svc.runner.start_over()
    else:
        svc.runner.start()
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
