"""Where the app keeps its files (per-user, outside the install folder)."""

import sys
from pathlib import Path

from platformdirs import user_data_dir, user_log_dir

from adawork import APP_NAME


def data_dir() -> Path:
    path = Path(user_data_dir(APP_NAME, appauthor=False))
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_dir() -> Path:
    path = Path(user_log_dir(APP_NAME, appauthor=False))
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_file() -> Path:
    return data_dir() / "config.json"


def models_dir() -> Path:
    path = data_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def vendor_dir() -> Path:
    """Bundled third-party programs (llama.cpp, MOTIS): next to the installed app, or the
    repository's vendor/ folder on a developer machine (tools/fetch_llama.py)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "vendor"
    return Path(__file__).parents[1] / "vendor"
