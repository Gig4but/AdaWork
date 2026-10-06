"""The Sources page: which sources are read, and consent for the risky ones."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from adawork.config import Settings
from adawork.scraper.sources import LABELS, NEEDS_CONSENT, SWITCHABLE
from adawork.services import Services
from adawork.web.pages import Svc, render

router = APIRouter()


def chosen_sources(form) -> dict[str, bool]:
    return {source_id: form.get(f"source_{source_id}") == "on" for source_id, _ in SWITCHABLE}


def apply_source_choices(settings: Settings, form) -> str | None:
    """Saves the source switches; returns an error message key instead when a source that
    needs consent (LinkedIn) is switched on without ticking its confirmation box."""
    chosen = chosen_sources(form)
    for source_id in NEEDS_CONSENT:
        newly_on = chosen[source_id] and not settings.sources.get(source_id, False)
        if newly_on and form.get(f"consent_{source_id}") != "on":
            return "sources.consent_missing"
    settings.sources = chosen
    settings.save()
    return None


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
        }
        for source_id, default in SWITCHABLE
    ]


@router.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request, svc: Svc, saved: bool = False) -> HTMLResponse:
    return render(request, "sources.html", sources=source_rows(svc), saved=saved)


@router.post("/sources", response_class=HTMLResponse)
async def sources_save(request: Request, svc: Svc) -> Response:
    form = await request.form()
    error = apply_source_choices(svc.settings, form)
    if error:
        rows = source_rows(svc, form)
        return render(request, "sources.html", sources=rows, error=error)
    return RedirectResponse("/sources?saved=1", status_code=303)
