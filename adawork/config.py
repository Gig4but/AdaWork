"""User settings stored as JSON. Secrets (keys, logins) never go here — see secrets.py."""

import json
import logging
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr, ValidationError

from adawork import paths

Language = Literal["cs", "en"]
IntervalHours = Literal[6, 12, 24, 48]
TravelMode = Literal["any", "car", "transit", "walk", "bike"]
# "system" follows Windows light/dark; the rest are fixed colour schemes (web/static/app.css).
Theme = Literal["system", "light", "dark", "ocean", "forest", "sunset", "plum", "midnight"]

log = logging.getLogger(__name__)


class AutomationSettings(BaseModel):
    enabled: bool = False  # automatic checks are off until the user switches them on
    interval_hours: IntervalHours = 12
    start_with_windows: bool = True
    notifications: bool = True


class Profile(BaseModel):
    """Wizard step 2: drives generated searches, the travel-time filter and prefetching."""

    home_address: str = ""
    travel_mode: TravelMode = "any"
    max_minutes: int = Field(default=45, ge=5, le=180)
    kinds_of_work: list[str] = Field(default_factory=list)  # empty = everything


class AiSettings(BaseModel):
    """Wizard step "Umělá inteligence" and Settings → AI (PLAN §6.6)."""

    # "auto" (best for this PC, follows hardware changes), a tier ("simple", "good",
    # "excellent"), a catalog model id (advanced), or "off" (rules only).
    choice: str = "auto"
    allow_metered: bool = False  # download models over a metered connection
    # Advanced: an OpenAI-compatible server instead of the bundled one, e.g. Ollama
    # ("http://127.0.0.1:11434") with its model name.
    server_url: str = ""
    server_model: str = ""


class SocialSettings(BaseModel):
    """Facebook and Instagram reading (PLAN §4.3): off unless switched on in `sources`.
    No safe limit exists; these defaults are the plan's, Settings allow up to 200 / 100."""

    facebook_per_day: int = Field(default=100, ge=10, le=200)
    instagram_per_day: int = Field(default=50, ge=10, le=100)
    facebook_groups: list[str] = Field(default_factory=list)  # group links the user adds
    instagram_tags: list[str] = Field(default_factory=list)  # extra hashtags, without "#"


class Settings(BaseModel):
    language: Language = "cs"
    theme: Theme = "system"
    wizard_completed: bool = False
    profile: Profile = Field(default_factory=Profile)
    automation: AutomationSettings = Field(default_factory=AutomationSettings)
    ai: AiSettings = Field(default_factory=AiSettings)
    social: SocialSettings = Field(default_factory=SocialSettings)
    # Source id -> on/off as the user chose; sources not listed use their default.
    sources: dict[str, bool] = Field(default_factory=dict)

    _path: Path | None = PrivateAttr(default=None)

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or paths.config_file()
        settings = cls()
        if not path.exists():
            settings.language = system_language()
        else:
            try:
                settings = cls.model_validate_json(path.read_text(encoding="utf-8"))
            except (ValidationError, UnicodeDecodeError) as exc:
                backup = path.with_name(path.name + ".broken")
                path.replace(backup)
                settings = _valid_part(backup.read_bytes())  # one bad field keeps the rest
                log.warning("Invalid settings moved to %s, invalid ones reset: %s", backup, exc)
        settings._path = path
        return settings

    def save(self, path: Path | None = None) -> None:
        path = path or self._path or paths.config_file()
        with _save_lock:  # web handlers save concurrently
            fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    file.write(self.model_dump_json(indent=2))
                _replace(Path(tmp), path)
            finally:
                Path(tmp).unlink(missing_ok=True)  # only left if writing failed
        self._path = path


_save_lock = threading.Lock()


def system_language() -> Language:
    """The first start's language: Czech for Czech and Slovak Windows, otherwise English."""
    if sys.platform != "win32":
        return "cs"
    import ctypes

    primary = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
    return "cs" if primary in (0x05, 0x1B) else "en"  # LANG_CZECH, LANG_SLOVAK


def _replace(source: Path, target: Path, attempts: int = 20) -> None:
    """os.replace, retried briefly: on Windows it fails while another program (an antivirus,
    a reader) has the target open."""
    for attempt in range(attempts):
        try:
            source.replace(target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.05)


def _valid_part(raw: bytes) -> Settings:
    """The settings in `raw` with each invalid value dropped, so it gets its default."""
    try:
        data = json.loads(raw)
    except ValueError:  # not JSON or not text
        return Settings()
    while isinstance(data, dict):
        try:
            return Settings.model_validate(data)
        except ValidationError as exc:
            dropped = [_drop(data, error["loc"]) for error in exc.errors()]
            if not any(dropped):
                break
    return Settings()


def _drop(data: dict, loc: tuple) -> bool:
    """Removes the invalid value at `loc` from `data`. Only dict keys are removed: an invalid
    list item drops the whole list. False if there was nothing to remove."""
    container, key = data, loc[0]
    for part in loc[1:]:
        value = container.get(key)
        if not isinstance(value, dict) or part not in value:
            break
        container, key = value, part
    if key not in container:
        return False
    del container[key]
    return True
