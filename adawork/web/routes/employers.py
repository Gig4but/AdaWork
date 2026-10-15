"""Employer cards (PLAN §4.5)."""

import re
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi import Path as InPath
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select

from adawork.db.models import Employer, EmployerMention, EmployerReview, EmployerSummary, Job
from adawork.employers import official
from adawork.employers.service import card_links
from adawork.services import GOOGLE_KEY, Services
from adawork.web.pages import Svc, render

router = APIRouter()

EmployerId = Annotated[int, InPath(ge=1, le=2**63 - 1)]  # larger: not an SQLite integer


def employer_card(
    request: Request, svc: Services, employer_id: int, fresh: bool = False, **extra
) -> HTMLResponse:
    """The card; what is missing or 30 days old is fetched in the background, and the
    card asks again until it is in."""
    employers = svc.employers
    with svc.sessions() as s:
        e = s.get(Employer, employers.current_id(employer_id))
        if e is None:
            return render(request, "_employer.html", e=None)
        reviews = {
            r.source: r
            for r in s.scalars(select(EmployerReview).where(EmployerReview.employer_id == e.id))
        }
        found = list(
            s.scalars(
                select(EmployerMention)
                .where(EmployerMention.employer_id == e.id)
                .order_by(EmployerMention.id)
            )
        )
        summary = s.get(EmployerSummary, e.id)
        names = list(
            s.scalars(
                select(Job.employer_name)
                .where(Job.employer_id == e.id, Job.status == "active")
                .distinct()
                .limit(5)
            )
        )
        where = employers.job_municipality(s, e.id)
    if svc.employer_updates and not fresh and employers.needs_refresh(e):
        employers.refresh(e.id)
    return render(
        request,
        "_employer.html",
        e=e,
        reviews=reviews,
        mentions=found,
        summary=summary,
        names=[n for n in names if n],
        links={
            k: v
            for k, v in card_links(e, where).items()
            if k != "facebook" or svc.settings.employers.facebook
        },
        band=official.BANDS.get(e.employee_band or ""),
        isir_state=official.insolvency_state,
        bands=official.BANDS,
        step=employers.refreshing.get(e.id),
        google_key=bool(svc.secrets.get(GOOGLE_KEY)),
        **extra,
    )


@router.get("/employer/{employer_id}", response_class=HTMLResponse)
def employer_page(
    request: Request, svc: Svc, employer_id: EmployerId, poll: bool = False
) -> Response:
    """`poll`: the card asks while its refresh runs: only the step line, until the whole
    card comes once at the end (what the user types in it meanwhile stays)."""
    step = svc.employers.refreshing.get(employer_id)
    if poll and step:
        return render(request, "_employer_step.html", eid=employer_id, step=step)
    response = employer_card(request, svc, employer_id, fresh=poll)
    if poll:
        response.headers["HX-Retarget"] = "#employer-body"
        response.headers["HX-Reswap"] = "innerHTML"
    return response


@router.post("/employer/{employer_id}/refresh", response_class=HTMLResponse)
def employer_refresh(request: Request, svc: Svc, employer_id: EmployerId) -> HTMLResponse:
    """ "Aktualizovat": everything fetched again now."""
    if svc.employer_updates:
        svc.employers.refresh(svc.employers.current_id(employer_id), force=True)
    return employer_card(request, svc, employer_id, fresh=True)


@router.post("/employer/{employer_id}/correct", response_class=HTMLResponse)
def employer_correct(
    request: Request, svc: Svc, employer_id: EmployerId, ico: Annotated[str, Form()] = ""
) -> HTMLResponse:
    """The user's fix of a wrong or uncertain match: the company with this IČO."""
    ico = re.sub(r"\s", "", ico)
    target = svc.employers.correct(employer_id, ico)
    if target is None:
        return employer_card(request, svc, employer_id, bad_ico=True)
    response = employer_card(request, svc, target)
    response.headers["HX-Trigger"] = "jobs-updated"  # the cards show the new employer
    return response
