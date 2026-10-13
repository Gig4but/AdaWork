"""The Sources page: which sources are read, consent for the risky ones, Facebook and
Instagram logins, and the API keys some sources need."""

import re
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from adawork.config import Settings
from adawork.db.models import SourceState
from adawork.scraper.sources import KINDS, LABELS, NEEDS_CONSENT, NEEDS_KEY, SOCIAL, SWITCHABLE
from adawork.secrets import Secrets
from adawork.services import Services
from adawork.social import browser
from adawork.social.reader import SocialState
from adawork.web.pages import Svc, render

router = APIRouter()

API_KEY = re.compile(r"[\w.:-]{8,200}")
FACEBOOK_GROUP = r"https://(?:www\.|m\.)?facebook\.com/groups/[\w.-]+"


def chosen_sources(form) -> dict[str, bool]:
    return {source_id: form.get(f"source_{source_id}") == "on" for source_id, _ in SWITCHABLE}


def apply_source_choices(settings: Settings, form) -> str | None:
    """Saves the source switches; returns the label of a source that needs consent
    (LinkedIn, Facebook, Instagram) and was switched on without ticking its confirmation box."""
    chosen = chosen_sources(form)
    for source_id, _ in SWITCHABLE:
        newly_on = chosen[source_id] and not settings.sources.get(source_id, False)
        if source_id in NEEDS_CONSENT and newly_on and form.get(f"consent_{source_id}") != "on":
            return LABELS[source_id]
    settings.sources = chosen
    social = settings.social
    for name, low, high in (("facebook_per_day", 10, 200), ("instagram_per_day", 10, 100)):
        value = form.get(name, "")
        if re.fullmatch(r"[0-9]{1,3}", value):
            setattr(social, name, min(max(int(value), low), high))
    if "facebook_groups" in form:
        links = re.findall(FACEBOOK_GROUP, form["facebook_groups"])
        social.facebook_groups = [u.replace("://m.", "://www.") for u in dict.fromkeys(links)][:30]
    if "instagram_tags" in form:
        tags = re.findall(r"#?([\w]{2,40})", form["instagram_tags"])
        social.instagram_tags = list(dict.fromkeys(tags))[:20]
    settings.save()
    return None


def apply_keys(settings: Settings, keys: Secrets, form) -> str | None:
    """Saves the API keys typed in: a new key switches its source on, a deleted one off.
    Returns the label of a source whose key looks wrong (nothing is saved then)."""
    typed = {sid: str(form.get(f"key_{sid}") or "").strip() for sid in NEEDS_KEY}
    for source_id, key in typed.items():
        if key and not API_KEY.fullmatch(key):
            return LABELS[source_id]
    for source_id, key in typed.items():
        if form.get(f"remove_{source_id}") == "on":
            keys.delete(source_id)
            settings.sources[source_id] = False
        elif key:
            keys.set(source_id, key)
            settings.sources[source_id] = True
    settings.save()
    return None


def key_rows(svc: Services) -> list[dict]:
    """The key-based sources; `end`: the last characters of a saved key."""
    return [
        {
            "id": source_id,
            "label": LABELS[source_id],
            "url": url,
            "end": (svc.secrets.get(source_id) or "")[-4:],
        }
        for source_id, url in NEEDS_KEY.items()
    ]


def social_status(svc: Services, platform: str) -> dict:
    with svc.sessions() as s:
        row = s.get(SourceState, platform)
        state = SocialState.load(row.state if row else None).for_today(datetime.now())
    until = datetime.fromisoformat(state.paused_until) if state.paused_until else None
    paused = until is not None and until > datetime.now()
    return {
        "id": platform,
        "label": LABELS[platform],
        "logged_in": browser.has_session(svc.data_dir, platform),
        "login_running": svc.logins.running(platform),
        "login_failed": svc.logins.failed(platform),
        "paused_until": until if paused else None,
        "pause_reason": state.pause_reason if paused else "",
        "read_today": state.read,
        "suggested": state.suggested,
    }


def source_rows(svc: Services, form=None) -> list[dict]:
    settings = svc.settings
    choices = settings.sources if form is None else chosen_sources(form)
    return [
        {
            "id": source_id,
            "label": LABELS[source_id],
            "on": choices.get(source_id, default),
            "consented": settings.sources.get(source_id, False)
            or (form is not None and form.get(f"consent_{source_id}") == "on"),
            "consent": source_id in NEEDS_CONSENT,
            "kind": KINDS[source_id],
            "social": social_status(svc, source_id) if source_id in SOCIAL else None,
            "no_key": source_id in NEEDS_KEY and not svc.secrets.get(source_id),
        }
        for source_id, default in SWITCHABLE
    ]


@router.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request, svc: Svc, saved: bool = False) -> HTMLResponse:
    return render(request, "sources.html", sources=source_rows(svc), saved=saved)


@router.post("/sources", response_class=HTMLResponse)
async def sources_save(request: Request, svc: Svc) -> Response:
    form = await request.form()
    missing = apply_source_choices(svc.settings, form)
    if missing:
        rows = source_rows(svc, form)
        return render(request, "sources.html", sources=rows, consent_missing=missing)
    return RedirectResponse("/sources?saved=1", status_code=303)


@router.post("/social/{platform}/login", response_class=HTMLResponse)
def social_login(request: Request, svc: Svc, platform: str) -> Response:
    if platform not in SOCIAL or not svc.settings.sources.get(platform, False):
        return Response(status_code=404)
    svc.logins.start(platform)
    return render(request, "_social_status.html", s=social_status(svc, platform))


@router.get("/social/{platform}/status", response_class=HTMLResponse)
def social_state(request: Request, svc: Svc, platform: str) -> Response:
    if platform not in SOCIAL:
        return Response(status_code=404)
    return render(request, "_social_status.html", s=social_status(svc, platform))
