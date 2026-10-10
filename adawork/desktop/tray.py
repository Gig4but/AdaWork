"""Tray icon: lets the app keep running when the window is closed."""

from collections.abc import Callable

import pystray
from pystray._base import Icon as TrayIcon  # pystray.Icon is the platform's subclass of it

from adawork import APP_NAME
from adawork.desktop.icon import make_icon_image


def create_tray(on_open: Callable[[], None], on_quit: Callable[[], None]) -> TrayIcon:
    menu = pystray.Menu(
        pystray.MenuItem("Otevřít", lambda: on_open(), default=True),
        pystray.MenuItem("Hledat nové nabídky", None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Ukončit", lambda: on_quit()),
    )
    return pystray.Icon("adawork", make_icon_image(), APP_NAME, menu)
