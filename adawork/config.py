"""User settings stored as JSON. Secrets (keys, logins) never go here — see secrets.py."""

import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from adawork import paths

Language = Literal["cs", "en"]
IntervalHours = Literal[6, 12, 24, 48]

log = logging.getLogger(__name__)


class AutomationSettings(BaseModel):
    enabled: bool = False  # automatic checks are off until the user switches them on
    interval_hours: IntervalHours = 12
    start_with_windows: bool = True
    notifications: bool = True


class Settings(BaseModel):
    language: Language = "cs"
    wizard_completed: bool = False
    automation: AutomationSettings = Field(default_factory=AutomationSettings)

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or paths.config_file()
        if not path.exists():
            return cls()
        try:
            return cls.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValidationError, UnicodeDecodeError) as exc:
            backup = path.with_name(path.name + ".broken")
            path.replace(backup)
            log.warning("Unreadable settings moved to %s, using defaults: %s", backup, exc)
            return cls()

    def save(self, path: Path | None = None) -> None:
        path = path or paths.config_file()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)
