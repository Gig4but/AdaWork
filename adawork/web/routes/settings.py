"""Settings: one page per wizard step, and the form handlers the wizard shares."""

import re
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from adawork import paths
from adawork.config import TIME_OF_DAY, IntervalHours, Language, Settings, Theme, TravelMode
from adawork.i18n import translate
from adawork.scraper.sources import LABELS, NEEDS_KEY, SWITCHABLE
from adawork.search.service import TRAVEL_MODES
from adawork.services import Services
from adawork.web.pages import THEMES, Svc, render, status_context
from adawork.web.routes.sources import apply_keys, key_rows

router = APIRouter()

# Settings pages on their own: title key. AI and sources have their own pages (/ai, /sources).
SECTIONS = {
    "appearance": "settings.appearance",
    "profile": "settings.profile",
    "keys": "keys.title",
    "automation": "auto.title",
    "map": "routing.title",
}
LANGUAGE_NAMES = {"cs": "Čeština", "en": "English"}
INTERVALS: dict[str, IntervalHours] = {"6": 6, "12": 12, "24": 24, "48": 48}


def save_profile(
    svc: Services,
    home_address: str,
    travel_mode: TravelMode,
    max_minutes: int,
    kinds_of_work: str | None,
    depart_time: str,
) -> None:
    """`kinds_of_work`: None keeps them (only the wizard asks; then the search text)."""
    profile = svc.settings.profile
    profile.home_address = home_address.strip()
    profile.travel_mode = travel_mode
    profile.max_minutes = max_minutes
    if kinds_of_work is not None:
        profile.kinds_of_work = [k.strip() for k in kinds_of_work.split(",") if k.strip()]
    if re.fullmatch(TIME_OF_DAY, depart_time):
        profile.depart_time = depart_time
    svc.settings.save()
    svc.routing.wake()  # a home far away needs a new map area on smaller PCs


def apply_automation(settings: Settings, form) -> None:
    auto = settings.automation
    auto.enabled = form.get("enabled") == "on"
    if hours := INTERVALS.get(form.get("interval_hours")):
        auto.interval_hours = hours
    auto.start_with_windows = form.get("start_with_windows") == "on"
    auto.notifications = form.get("notifications") == "on"
    if not auto.enabled:
        auto.paused_until = None
    settings.save()


def save_map(svc: Services, form) -> None:
    """The area, and with "download" the first download of the map and timetables."""
    settings = svc.settings
    if form.get("routing_area") in ("auto", "whole", "home"):
        settings.routing_area = form["routing_area"]
    if form.get("download"):
        settings.routing_download = True
    settings.save()
    svc.routing.wake()


def map_context(svc: Services) -> dict:
    return {
        "routing_auto_whole": svc.ai.manager.resources.limits.routing_radius_km is None,
        "routing_needs_home": svc.routing.wanted_area() is None,  # home area without a home
    }


def routing_summary(svc: Services) -> str:
    lang, st = svc.settings.language, svc.routing.status()
    if st["step"]:
        return translate(lang, "routing.step." + st["step"]) + f" {st['percent']} %"
    if not svc.settings.routing_download:
        return translate(lang, "routing.not_downloaded")
    if not st["ready"]:
        return translate(lang, "routing.not_ready")
    if st["whole"]:
        return translate(lang, "routing.area.whole")
    return translate(lang, "routing.area.home_km", km=round(st["km"]))


def catalog(svc: Services) -> list[dict]:
    """One row per wizard step, in its order, with what is set now."""
    settings = svc.settings
    lang = settings.language

    def tr(key: str, **kw) -> str:
        return translate(lang, key, **kw)

    p, auto = settings.profile, settings.automation
    active = svc.ai.manager.active_model()
    if settings.ai.choice == "off":
        ai_summary = tr("settings.off")
    else:
        ai_summary = active.name if active else tr("settings.ai.none")
    sources_on = sum(1 for sid, on in SWITCHABLE if settings.sources.get(sid, on))
    keys = [LABELS[k] for k in NEEDS_KEY if svc.secrets.get(k)]
    rows = [
        (
            "/settings/appearance",
            "palette",
            "settings.appearance",
            f"{LANGUAGE_NAMES[lang]} · {tr('theme.' + settings.theme)}",
        ),
        (
            "/settings/profile",
            "pin",
            "settings.profile",
            tr(
                "settings.profile.summary",
                home=p.home_address or tr("settings.no_home"),
                mode=tr("mode." + p.travel_mode),
                minutes=p.max_minutes,
            ),
        ),
        ("/ai", "ai", "ai.title", ai_summary),
        (
            "/sources",
            "sources",
            "sources.title",
            tr("settings.sources.summary", on=sources_on, total=len(SWITCHABLE)),
        ),
        (
            "/settings/keys",
            "key",
            "keys.title",
            ", ".join(keys) if keys else tr("settings.keys.none"),
        ),
        (
            "/settings/automation",
            "clock",
            "auto.title",
            tr("auto.every", h=auto.interval_hours) if auto.enabled else tr("settings.off"),
        ),
        ("/settings/map", "map", "routing.title", routing_summary(svc)),
    ]
    return [{"href": h, "icon": i, "title": t, "summary": x} for h, i, t, x in rows]


def render_section(request: Request, svc: Services, section: str, **context) -> HTMLResponse:
    if section == "keys":
        context.setdefault("keys", key_rows(svc))
    if section == "map":
        context |= map_context(svc)
    return render(
        request,
        "settings_section.html",
        section=section,
        title=SECTIONS[section],
        modes=TRAVEL_MODES,
        themes=THEMES,
        **status_context(svc),
        **context,
    )


@router.post("/settings/appearance")
def save_appearance(
    svc: Svc, language: Annotated[Language, Form()], theme: Annotated[Theme, Form()] = "system"
) -> Response:
    """Language and colour scheme, saved as soon as they are picked."""
    settings = svc.settings
    changed = language != settings.language
    settings.language, settings.theme = language, theme
    settings.save()
    return Response(headers={"HX-Refresh": "true"} if changed else None)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, svc: Svc) -> HTMLResponse:
    return render(
        request,
        "settings.html",
        catalog=catalog(svc),
        data_dir=svc.data_dir,
        log_dir=paths.log_dir(),
    )


@router.get("/settings/{section}", response_class=HTMLResponse)
def settings_section(request: Request, svc: Svc, section: str, saved: bool = False) -> Response:
    if section not in SECTIONS:
        return RedirectResponse("/settings", status_code=303)
    return render_section(request, svc, section, saved=saved)


@router.post("/settings/profile")
def profile_save(
    svc: Svc,
    home_address: Annotated[str, Form()] = "",
    travel_mode: Annotated[TravelMode, Form()] = "any",
    max_minutes: Annotated[int, Form(ge=5, le=180)] = 45,
    depart_time: Annotated[str, Form()] = "07:00",
) -> RedirectResponse:
    save_profile(svc, home_address, travel_mode, max_minutes, None, depart_time)
    return RedirectResponse("/settings/profile?saved=1", status_code=303)


@router.post("/settings/automation")
async def automation_save(request: Request, svc: Svc) -> RedirectResponse:
    apply_automation(svc.settings, await request.form())
    svc.set_automation()
    return RedirectResponse("/settings/automation?saved=1", status_code=303)


@router.post("/settings/pause")
def automation_pause(svc: Svc) -> RedirectResponse:
    svc.scheduler.pause()
    return RedirectResponse("/settings/automation", status_code=303)


@router.post("/settings/resume")
def automation_resume(svc: Svc) -> RedirectResponse:
    svc.scheduler.resume()
    return RedirectResponse("/settings/automation", status_code=303)


@router.post("/settings/map")
async def map_save(request: Request, svc: Svc) -> RedirectResponse:
    save_map(svc, await request.form())
    return RedirectResponse("/settings/map?saved=1", status_code=303)


@router.post("/settings/map/update")
def map_update(svc: Svc) -> RedirectResponse:
    """Downloads and imports the newest map and timetables now."""
    if svc.settings.routing_download:
        svc.routing.update_in_background()
    return RedirectResponse("/settings/map", status_code=303)


@router.get("/settings/map/status", response_class=HTMLResponse)
def map_status(request: Request, svc: Svc) -> HTMLResponse:
    return render(request, "_routing_status.html", **status_context(svc))


@router.post("/settings/keys", response_class=HTMLResponse)
async def keys_save(request: Request, svc: Svc) -> Response:
    invalid = apply_keys(svc.settings, svc.secrets, await request.form())
    if invalid:
        return render_section(request, svc, "keys", key_invalid=invalid)
    return RedirectResponse("/settings/keys?saved=1", status_code=303)
