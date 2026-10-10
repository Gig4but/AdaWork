"""Local web interface (module 2). Only listens on 127.0.0.1.

`create_app` builds the app's services (services.py) and serves the pages of web/routes/."""

import io

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from adawork import __version__, services
from adawork.config import Settings
from adawork.desktop.icon import make_icon_image
from adawork.web.pages import WEB_DIR
from adawork.web.routes import ROUTERS

LOCAL_HOSTS = ["127.0.0.1", "localhost"]


def create_app(settings: Settings | None = None, **options) -> FastAPI:
    """`options`: those of services.build (other data folders, test doubles)."""
    svc = services.build(settings, **options)
    app = FastAPI(title="AdaWork", version=__version__, docs_url=None, redoc_url=None)
    app.state.services = svc
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS)

    @app.middleware("http")
    async def same_origin_writes(request: Request, call_next):
        origin = request.headers.get("origin")
        own_origin = f"{request.url.scheme}://{request.headers.get('host')}"
        if request.method not in ("GET", "HEAD") and origin and origin != own_origin:
            return Response("Cross-origin request blocked", status_code=403)
        return await call_next(request)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        buf = io.BytesIO()
        make_icon_image(32).save(buf, format="PNG")
        return Response(buf.getvalue(), media_type="image/png")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    for router in ROUTERS:
        app.include_router(router)
    return app
