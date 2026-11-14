"""The AI page (PLAN §6.6): model choice, download, and questions in a sentence."""

import re
from html import escape
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from adawork.ai.ask import ask
from adawork.ai.catalog import MODELS, TIERS
from adawork.ai.client import LlmError, list_models
from adawork.ai.manager import Busy
from adawork.ai.runtime import ServerFailed
from adawork.config import Settings
from adawork.i18n import translate
from adawork.search.service import filters_to_query
from adawork.services import Services
from adawork.system.resources import Priority
from adawork.web.pages import Svc, render

router = APIRouter()


def apply_ai_choice(settings: Settings, form) -> None:
    choice = form.get("choice") or "auto"
    model = form.get("model") or ""
    if model in MODELS:  # "Pokročilé": a specific model wins over the tier list
        choice = model
    if choice in ("auto", "off", *TIERS, *MODELS):
        settings.ai.choice = choice
    if "server_url" in form:
        url = (form.get("server_url") or "").strip()
        settings.ai.server_url = url if re.match(r"^https?://", url) else ""
        settings.ai.server_model = (form.get("server_model") or "").strip()[:100]
    settings.save()


def ai_context(svc: Services) -> dict:
    lang = svc.settings.language
    m = svc.ai.manager
    advice = m.advice()
    hw, gpu = m.hw, m.hw.best_gpu
    reason_args = dict(advice.reason_args)
    if reason_args.get("tier"):
        reason_args["tier"] = translate(lang, f"ai.tier.{reason_args['tier']}")
    options = [
        {
            "tier": f.tier,
            "model": f.model.name,
            "size": round(f.model.download_gb, 1),
            "icon": f.icon,
            "seconds": max(1, round(f.seconds_per_ad)),
            "measured": f.speed.measured,
            "reasons": f.reasons,
            "downloaded": f.downloaded,
            "recommended": advice.pick is f,
        }
        for f in advice.fits
    ]
    download, enrich = m.download, svc.ai.enricher.progress
    active, downloaded = m.active_model(), m.downloaded()
    return {
        "hardware_line": translate(
            lang,
            "ai.hardware",
            ram=round(hw.ram_gb),
            gpu=gpu.name if gpu else translate(lang, "ai.no_gpu"),
            disk=round(hw.free_disk_gb),
        ),
        "options": options,
        "pick": advice.pick,
        "choice": svc.settings.ai.choice,
        "reason": translate(lang, advice.reason, **reason_args),
        "active": active,
        "download": download,
        "download_model": MODELS[download.model_id].name if download.model_id else "",
        "enrich": enrich,
        "busy": download.state in ("downloading", "benchmark") or enrich.running,
        "notice": m.take_notice(),
        "models": list(MODELS.values()),
        "downloaded": [
            {
                "id": spec.id,
                "name": spec.name,
                "size": round(spec.download_gb, 1),
                "active": active is not None and active.id == spec.id,
            }
            for spec in MODELS.values()
            if spec.id in downloaded
        ]
        + [
            {"id": name, "name": MODELS[name].name if name in MODELS else name}
            | {"size": None, "active": False}
            for name in m.stale()
        ],
        "has_files": m.models_dir.exists() and any(m.models_dir.iterdir()),
        "ask": ask_context(svc),
    }


def ask_context(svc: Services) -> dict:
    """The question row; `wait` (key, args): why it is disabled."""
    m = svc.ai.manager
    d = m.download
    if m.ready:
        wait = None
    elif svc.settings.ai.choice == "off":
        wait = ("ask.wait.off", {})
    elif d.state == "downloading":
        wait = ("ask.wait.downloading", {"percent": d.percent})
    elif d.state in ("benchmark", "failed"):
        wait = (f"ask.wait.{d.state}", {})
    else:
        wait = ("ask.wait.none", {})
    return {"wait": wait}


@router.get("/ai", response_class=HTMLResponse)
def ai_page(request: Request, svc: Svc, saved: bool = False) -> HTMLResponse:
    return render(request, "ai.html", ai=ai_context(svc), saved=saved)


@router.post("/ai")
async def ai_save(request: Request, svc: Svc) -> RedirectResponse:
    apply_ai_choice(svc.settings, await request.form())
    svc.ai.manager.start_download()
    return RedirectResponse("/ai?saved=1", status_code=303)


@router.post("/ai/delete")
def ai_delete(
    svc: Svc, model_id: Annotated[str, Form()] = "", all: Annotated[bool, Form()] = False
) -> RedirectResponse:
    if all:
        svc.ai.delete_models()
    elif model_id:
        svc.ai.delete_models(model_id)
    return RedirectResponse("/ai", status_code=303)


@router.post("/ai/test", response_class=HTMLResponse)
def ai_test(request: Request, server_url: Annotated[str, Form()] = "") -> HTMLResponse:
    """Settings → AI → own server: does it answer, and with which models."""
    models, error = [], None
    url = server_url.strip()
    if not re.match(r"^https?://", url):
        error = ("ai.test.bad_url", {})
    else:
        try:
            models = list_models(url)
        except LlmError as exc:
            error = ("ai.test.failed", {"msg": str(exc)[:200]})
    return render(request, "_server_test.html", models=models, error=error)


@router.get("/ai/status", response_class=HTMLResponse)
def ai_status(request: Request, svc: Svc) -> HTMLResponse:
    return render(request, "_ai_status.html", ai=ai_context(svc))


@router.get("/ask", response_class=HTMLResponse)
def ask_row(request: Request, svc: Svc) -> HTMLResponse:
    return render(request, "_ask.html", ask=ask_context(svc))


@router.post("/ask", response_class=HTMLResponse)
def ask_question(svc: Svc, question: Annotated[str, Form()] = "") -> Response:
    """A question in a sentence -> the search page with the matching filters."""
    if not question.strip():
        return HTMLResponse("")
    lang = svc.settings.language
    try:
        with svc.ai.manager.session(priority=Priority.USER) as client:
            filters = ask(client, question)
    except Busy as exc:
        reason = translate(lang, exc.args[0] if exc.args else "")
    except (LlmError, ServerFailed) as exc:
        reason = str(exc)
    else:
        qs = filters_to_query(filters)
        return Response(headers={"HX-Redirect": f"/?{qs}" if qs else "/"})
    return HTMLResponse(escape(translate(lang, "ask.failed", msg=reason)))
