"""What every page of the web interface shares: the templates and their helpers, and `Svc`,
the app's services in a route (`def page(request: Request, svc: Svc)`)."""

from datetime import datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import Row, func, select

from adawork import __version__
from adawork.db.models import Job, Run
from adawork.employers.names import client_in
from adawork.i18n import translate
from adawork.normalize.vocab import VOCABULARIES, label
from adawork.routing.commute import MODES
from adawork.scraper.sources import LABELS as SOURCE_LABELS
from adawork.search.service import SATISFACTION_STEPS, TRAVEL_MODES
from adawork.services import Services

WEB_DIR = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=WEB_DIR / "templates")
PAGE_SIZE = 30
# Colour scheme -> the Pico base it builds on (None: follows Windows light/dark).
THEMES = {
    "system": None,
    "light": "light",
    "dark": "dark",
    "ocean": "light",
    "forest": "light",
    "sunset": "light",
    "plum": "dark",
    "midnight": "dark",
}
SALARY_STEPS = (20000, 25000, 30000, 35000, 40000, 50000, 60000)
POSTED_DAYS = (1, 3, 7, 14, 30)


def services_of(request: Request) -> Services:
    return request.app.state.services


Svc = Annotated[Services, Depends(services_of)]


def format_salary(job: Job | Row, lang: str) -> str | None:
    if not job.salary_min and not job.salary_max:
        return None

    def num(n: int) -> str:
        return f"{n:,}".replace(",", "\xa0")

    unit = translate(lang, f"job.salary_{job.salary_unit}") if job.salary_unit else ""
    if job.salary_currency and job.salary_currency != "CZK":
        unit = unit.replace("Kč", job.salary_currency).replace("CZK", job.salary_currency)
    if job.salary_min and job.salary_max and job.salary_max != job.salary_min:
        amount = f"{num(job.salary_min)} – {num(job.salary_max)}"
    else:
        amount = num(job.salary_min or job.salary_max or 0)
    return f"{amount} {unit}".strip()


def _safe_url(url: str | None) -> str:
    return url if url and url.lower().startswith(("http://", "https://")) else "#"


def _format_date(value: datetime | None) -> str:
    return f"{value.day}. {value.month}. {value.year}" if value else ""


def _format_datetime(value: datetime | None) -> str:
    return f"{_format_date(value)} {value:%H:%M}" if value else ""


def local_time(utc: datetime) -> datetime:
    """A stored (naive UTC) time in this computer's time zone."""
    return datetime.fromtimestamp((utc - datetime(1970, 1, 1)).total_seconds())


TEMPLATES.env.filters["safe_url"] = _safe_url
TEMPLATES.env.filters["date"] = _format_date
TEMPLATES.env.filters["datetime"] = _format_datetime
TEMPLATES.env.filters["agency_client"] = client_in
TEMPLATES.env.filters["source_label"] = lambda source_id: SOURCE_LABELS.get(source_id, source_id)


def render(request: Request, name: str, **context) -> HTMLResponse:
    svc = services_of(request)
    settings = svc.settings
    lang = settings.language
    context |= {
        "settings": settings,
        "lang": lang,
        "theme_base": THEMES[settings.theme],
        "path": request.url.path,
        "version": __version__,
        "t": lambda key, **kw: translate(lang, key, **kw),
        "salary": lambda job: format_salary(job, lang),
        "label": lambda vocab, code: label(vocab, code, lang),
        "vocab": VOCABULARIES,
        "commute_modes": MODES,
        "travel_modes": TRAVEL_MODES,
        "satisfaction_steps": SATISFACTION_STEPS,
        "commute_on": svc.travel.on(),
    }
    context.setdefault("times", {})  # travel times of the listed jobs
    return TEMPLATES.TemplateResponse(request, name, context)


def query_string(request: Request) -> str:
    """The current filters as a query string, without paging."""
    paging = ("offset", "since", "cleared")
    return urlencode([(k, v) for k, v in request.query_params.multi_items() if k not in paging])


def status_context(svc: Services) -> dict:
    """The status line: jobs, the last and the next search, the map."""
    with svc.sessions() as s:
        active = s.scalar(select(func.count(Job.id)).where(Job.status == "active")) or 0
        last = s.scalar(
            select(Run.finished_at)
            .where(Run.status.in_(("ok", "skipped")))
            .order_by(Run.id.desc())
            .limit(1)
        )
    next_check = svc.scheduler.next_check()
    paused = svc.settings.automation.paused_until if svc.scheduler.paused() else None
    return {
        "routing": svc.routing.status(),
        "active_jobs": active,
        "last_run": local_time(last) if last else None,
        "next_check": local_time(next_check) if next_check else None,
        "paused_until": local_time(paused) if paused else None,
    }
