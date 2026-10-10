"""Ads the user adds by hand (PLAN §4.3), and the pictures of ads."""

import re
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from sqlalchemy import select
from starlette.datastructures import UploadFile

from adawork.core import ParsedJob, utcnow
from adawork.db.models import Job, JobImage, JobSource
from adawork.i18n import translate
from adawork.scraper.images import ImageStore
from adawork.scraper.sources import MANUAL
from adawork.scraper.store import JobStore
from adawork.search.service import start_batch
from adawork.web.pages import Svc, render

router = APIRouter()

MAX_UPLOAD = 15_000_000


@router.get("/add", response_class=HTMLResponse)
def add_page(request: Request) -> HTMLResponse:
    return render(request, "add.html")


@router.post("/add", response_class=HTMLResponse)
async def add_ad(request: Request, svc: Svc) -> HTMLResponse:
    """An ad the user pasted or dropped in as a photo or screenshot."""
    form = await request.form()
    text = str(form.get("text") or "").strip()[:20000]
    link = str(form.get("link") or "").strip()
    upload = form.get("picture")
    upload = upload if isinstance(upload, UploadFile) and upload.filename else None
    data = await upload.read(MAX_UPLOAD + 1) if upload else b""
    again = {"text": text, "link": link}
    if not text and not data:
        return render(request, "add.html", error="add.empty", **again)
    if len(data) > MAX_UPLOAD:
        return render(request, "add.html", error="add.too_big", **again)
    first = text.split("\n", 1)[0].strip()[:90] if text else ""
    parsed = ParsedJob(
        external_id=uuid.uuid4().hex,
        title=first or translate(svc.settings.language, "add.picture_title"),
        url=link if re.match(r"^https?://", link) else None,
        description=text,
        posted_at=utcnow(),
    )
    if not first:
        parsed.extra["title_from_picture"] = True
    image = None
    if upload and data:
        image = JobImage(url=f"upload:{upload.filename}"[:200], created_at=utcnow())
        try:
            ImageStore(svc.sessions, svc.cache_dir / "images").keep(image, data)
        except ValueError:
            return render(request, "add.html", error="add.not_picture", **again)
    with svc.sessions() as s:
        if not svc.runner.progress.running:
            start_batch(s)  # listed on top, like a search's finds
        store = JobStore(s, MANUAL)
        store.add(parsed)
        store.commit()
        job = s.scalars(
            select(Job)
            .join(JobSource)
            .where(JobSource.source_id == MANUAL, JobSource.external_id == parsed.external_id)
        ).one()
        if image is not None:
            job.images.append(image)
        s.commit()
        _ = job.sources, job.images, job.location  # loaded for the card
    svc.ai.enricher.start()  # reads the picture and fills the fields in the background
    return render(request, "add.html", job=job)


@router.get("/images/{image_id}")
def job_image(svc: Svc, image_id: int) -> Response:
    """An ad's picture from the local image cache (scraper/images.py)."""
    with svc.sessions() as s:
        image = s.get(JobImage, image_id)
    path = svc.cache_dir / "images" / image.file if image and image.file else None
    if path is None or not path.exists():
        return Response(status_code=404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})
