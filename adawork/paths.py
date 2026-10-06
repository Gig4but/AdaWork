"""Where the app keeps its files (per-user, outside the install folder)."""

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
