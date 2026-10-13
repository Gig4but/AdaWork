"""Saved searches and the export of the matching jobs."""

from datetime import datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from starlette.datastructures import QueryParams

from adawork.core import utcnow
from adawork.db.models import SavedSearch
from adawork.i18n import translate
from adawork.search.saved import MAX_SAVED, newest_job_id, with_new_counts
from adawork.search.service import all_jobs, filters_from, sources_of_all
from adawork.services import Services
from adawork.web import export
from adawork.web.pages import Svc, filters_from_query, format_salary, render

router = APIRouter()


def render_saved(request: Request, svc: Services, **context) -> HTMLResponse:
    with svc.sessions() as s:
        rows = with_new_counts(s)
    return render(request, "_saved.html", saved=rows, max_saved=MAX_SAVED, **context)


@router.get("/saved", response_class=HTMLResponse)
def saved_list(request: Request, svc: Svc) -> HTMLResponse:
    return render_saved(request, svc)


@router.post("/saved", response_class=HTMLResponse)
async def save_search(request: Request, svc: Svc) -> HTMLResponse:
    """The search form's filters under the name the user typed."""
    form = await request.form()
    f = filters_from(form)
    skip = ("view", "offset", "since", "cleared", "shown", "more", "saved_name")
    # An empty text is kept: without it, opening would use the profile's kinds of work.
    query = urlencode([(k, v) for k, v in form.multi_items() if (v or k == "q") and k not in skip])
    name = str(form.get("saved_name") or "").strip() or f.q.strip()
    name = name[:100] or translate(svc.settings.language, "saved.default_name")
    with svc.sessions() as s:
        if (s.scalar(select(func.count(SavedSearch.id))) or 0) >= MAX_SAVED:
            return render_saved(request, svc, full=True)
        seen = newest_job_id(s)
        s.add(SavedSearch(name=name, query=query, seen_up_to=seen, created_at=utcnow()))
        s.commit()
    return render_saved(request, svc)


@router.get("/saved/{saved_id}")
def open_saved(svc: Svc, saved_id: int) -> RedirectResponse:
    """The saved filters on the search page; their matches stop counting as new."""
    with svc.sessions() as s:
        saved = s.get(SavedSearch, saved_id)
        if saved is None:
            return RedirectResponse("/", status_code=303)
        saved.seen_up_to = newest_job_id(s)
        s.commit()
        query = saved.query
    if "q" not in QueryParams(query):  # saved before: counted without the profile's kinds
        query = f"{query}&q=" if query else "q="
    return RedirectResponse(f"/?{query}", status_code=303)


@router.post("/saved/{saved_id}/notify", response_class=HTMLResponse)
def toggle_saved_notify(request: Request, svc: Svc, saved_id: int) -> HTMLResponse:
    with svc.sessions() as s:
        saved = s.get(SavedSearch, saved_id)
        if saved is not None:
            saved.notify = not saved.notify
            s.commit()
    return render_saved(request, svc, open_list=True)


@router.post("/saved/{saved_id}/delete", response_class=HTMLResponse)
def delete_saved(request: Request, svc: Svc, saved_id: int) -> HTMLResponse:
    with svc.sessions() as s:
        saved = s.get(SavedSearch, saved_id)
        if saved is not None:
            s.delete(saved)
            s.commit()
    return render_saved(request, svc, open_list=True)


@router.get("/export.{kind}")
def export_jobs(request: Request, svc: Svc, kind: str) -> Response:
    """The jobs matching the filters, every page of them, as CSV or Excel."""
    if kind not in ("csv", "xlsx"):
        return Response(status_code=404)
    f = filters_from_query(request)
    lang = svc.settings.language
    with svc.sessions() as s:
        jobs, sources = all_jobs(s, f, export.JOB_COLUMNS), sources_of_all(s, f)
        rows = list(export.table(jobs, sources, lang, lambda job: format_salary(job, lang)))
    data = export.to_csv(rows) if kind == "csv" else export.to_xlsx(rows)
    media = (
        "text/csv; charset=utf-8"
        if kind == "csv"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    name = f"adawork-{datetime.now():%Y-%m-%d}.{kind}"
    disposition = f'attachment; filename="{name}"'
    return Response(data, media_type=media, headers={"Content-Disposition": disposition})
