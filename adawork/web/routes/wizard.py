"""The first-run setup wizard (PLAN §7.1); each step saves like its Settings page."""

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from adawork.config import Language, Theme, TravelMode
from adawork.services import Services
from adawork.web.pages import THEMES, Svc, render

router = APIRouter()

STEPS = ("welcome", "profile", "done")  # plan §7.1 steps 1, 2 and 7; more come later
TRAVEL_MODES = ("any", "car", "transit", "walk", "bike")


def render_step(request: Request, svc: Services, step: str, **context) -> HTMLResponse:
    return render(
        request,
        "wizard.html",
        step=step,
        step_no=STEPS.index(step) + 1,
        step_total=len(STEPS),
        modes=TRAVEL_MODES,
        themes=THEMES,
        **context,
    )


@router.get("/wizard/{step}", response_class=HTMLResponse)
def wizard(request: Request, svc: Svc, step: str) -> Response:
    if step not in STEPS:
        return RedirectResponse("/wizard/welcome", status_code=303)
    return render_step(request, svc, step)


@router.post("/wizard/welcome")
def wizard_welcome(
    svc: Svc, language: Annotated[Language, Form()], theme: Annotated[Theme, Form()] = "system"
) -> RedirectResponse:
    svc.settings.language = language
    svc.settings.theme = theme
    svc.settings.save()
    return RedirectResponse("/wizard/profile", status_code=303)


@router.post("/wizard/profile")
def wizard_profile(
    svc: Svc,
    home_address: Annotated[str, Form()] = "",
    travel_mode: Annotated[TravelMode, Form()] = "any",
    max_minutes: Annotated[int, Form(ge=5, le=180)] = 45,
    kinds_of_work: Annotated[str, Form()] = "",
) -> RedirectResponse:
    profile = svc.settings.profile
    profile.home_address = home_address.strip()
    profile.travel_mode = travel_mode
    profile.max_minutes = max_minutes
    profile.kinds_of_work = [k.strip() for k in kinds_of_work.split(",") if k.strip()]
    svc.settings.save()
    return RedirectResponse("/wizard/done", status_code=303)


@router.post("/wizard/done")
def wizard_done(svc: Svc) -> RedirectResponse:
    svc.settings.wizard_completed = True
    svc.settings.save()
    svc.runner.start()  # "Start" runs MPSV first: fast, all industries
    return RedirectResponse("/", status_code=303)
