"""Local web interface (module 2). Only listens on 127.0.0.1."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from adawork import __version__
from adawork.config import Settings

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="AdaWork", version=__version__, docs_url=None, redoc_url=None)
    app.state.settings = settings or Settings.load()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {"settings": request.app.state.settings, "version": __version__},
        )

    return app
