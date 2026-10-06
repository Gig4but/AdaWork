"""The app's log file, and how start-up failures reach the user."""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from adawork import APP_NAME, paths

LOG_FILE = "adawork.log"
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

log = logging.getLogger(__name__)


def setup_logging(directory: Path | None = None) -> Path:
    """Logs INFO and above to a rotating file in the log folder (about 1 MB x 3) and to the
    console when there is one. Returns the log file path."""
    path = (directory or paths.log_dir()) / LOG_FILE
    handlers: list[logging.Handler] = [
        # delay: a second launch that only wakes the running app never opens the file
        RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8", delay=True)
    ]
    if sys.stderr is not None:  # None in the window app (pythonw)
        handlers.append(logging.StreamHandler())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # httpx logs every request at INFO: one line per AI call and per page fetched.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    for handler in handlers:
        handler.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(handler)
    return path


def report_error(message: str, gui: bool) -> None:
    """Logs a start-up failure and tells the user: a message box for the window app,
    otherwise the console."""
    log.error(message, exc_info=sys.exc_info()[0] is not None)
    if gui and sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)  # MB_ICONERROR
    elif sys.stderr is not None:
        print(message, file=sys.stderr)
