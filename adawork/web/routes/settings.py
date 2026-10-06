"""Settings: the language and colour scheme, saved as soon as they are picked."""

from typing import Annotated

from fastapi import APIRouter, Form
from fastapi.responses import Response

from adawork.config import Language, Theme
from adawork.web.pages import Svc

router = APIRouter()


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
