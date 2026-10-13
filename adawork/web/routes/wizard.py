"""The first-run setup wizard (PLAN §7.1); each step saves like its Settings page."""

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from adawork.config import TravelMode
from adawork.services import Services
from adawork.web.pages import THEMES, Svc, render
from adawork.web.routes.ai import ai_context, apply_ai_choice
from adawork.web.routes.settings import TRAVEL_MODES, apply_automation, save_profile
from adawork.web.routes.sources import apply_keys, apply_source_choices, key_rows, source_rows

router = APIRouter()

STEPS = ("welcome", "profile", "ai", "sources", "keys", "automation", "done")


def render_step(request: Request, svc: Services, step: str, **context) -> HTMLResponse:
    if step == "sources":
        context.setdefault("sources", source_rows(svc))
    if step == "ai":
        context.setdefault("ai", ai_context(svc))
    if step == "keys":
        context.setdefault("keys", key_rows(svc))
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


@router.post("/wizard/profile")
def wizard_profile(
    svc: Svc,
    home_address: Annotated[str, Form()] = "",
    travel_mode: Annotated[TravelMode, Form()] = "any",
    max_minutes: Annotated[int, Form(ge=5, le=180)] = 45,
    kinds_of_work: Annotated[str, Form()] = "",
) -> RedirectResponse:
    save_profile(svc, home_address, travel_mode, max_minutes, kinds_of_work)
    return RedirectResponse("/wizard/ai", status_code=303)


@router.post("/wizard/ai")
async def wizard_ai(request: Request, svc: Svc) -> RedirectResponse:
    apply_ai_choice(svc.settings, await request.form())
    svc.ai.manager.start_download()  # in the background while the wizard continues
    return RedirectResponse("/wizard/sources", status_code=303)


@router.post("/wizard/sources", response_class=HTMLResponse)
async def wizard_sources(request: Request, svc: Svc) -> Response:
    form = await request.form()
    missing = apply_source_choices(svc.settings, form)
    if missing:
        rows = source_rows(svc, form)
        return render_step(request, svc, "sources", sources=rows, consent_missing=missing)
    return RedirectResponse("/wizard/keys", status_code=303)


@router.post("/wizard/keys", response_class=HTMLResponse)
async def wizard_keys(request: Request, svc: Svc) -> Response:
    invalid = apply_keys(svc.settings, svc.secrets, await request.form())
    if invalid:
        return render_step(request, svc, "keys", key_invalid=invalid)
    return RedirectResponse("/wizard/automation", status_code=303)


@router.post("/wizard/automation")
async def wizard_automation(request: Request, svc: Svc) -> RedirectResponse:
    apply_automation(svc.settings, await request.form())
    if svc.settings.wizard_completed:  # the wizard again: as if saved in Settings
        svc.set_automation()
    return RedirectResponse("/wizard/done", status_code=303)


@router.post("/wizard/done")
def wizard_done(svc: Svc) -> RedirectResponse:
    svc.settings.wizard_completed = True
    svc.settings.save()
    svc.runner.start()  # "Start" runs MPSV first: fast, all industries
    svc.set_automation()  # the next automatic check counts from this search
    return RedirectResponse("/", status_code=303)
