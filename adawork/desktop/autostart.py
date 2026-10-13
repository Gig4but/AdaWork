"""Start with Windows (PLAN §7.1 step 6): while automatic checks are on, a value under the
user's Run key starts the app at sign-in, hidden in the tray."""

import logging
import sys
from contextlib import suppress
from pathlib import Path

from adawork import APP_NAME

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
FLAG = "--minimized"

log = logging.getLogger(__name__)


def command() -> str:
    """The installed app, or on a developer machine the window Python running this package."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" {FLAG}'
    windowed = Path(sys.executable).with_name("pythonw.exe")
    python = windowed if windowed.exists() else Path(sys.executable)
    return f'"{python}" -m adawork {FLAG}'


def sync(wanted: bool) -> None:
    """Adds or removes the Run value; a failure only means no start with Windows."""
    if sys.platform != "win32":
        return
    import winreg

    try:
        access = winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, access) as key:
            if wanted:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command())
            else:
                with suppress(FileNotFoundError):
                    winreg.DeleteValue(key, APP_NAME)
    except OSError:
        log.warning("Could not change the start with Windows", exc_info=True)


def registered() -> str | None:
    """The command Windows runs at sign-in, if any."""
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, APP_NAME)[0]
    except OSError:
        return None
