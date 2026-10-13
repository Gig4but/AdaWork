"""Tray icon: lets the app keep running when the window is closed, and runs a search or
switches automatic checks without opening it (PLAN §7.1 step 6)."""

from collections.abc import Callable
from dataclasses import dataclass

import pystray
from pystray._base import Icon as TrayIcon  # pystray.Icon is the platform's subclass of it

from adawork import APP_NAME
from adawork.desktop.icon import make_icon_image


@dataclass
class TrayControls:
    """What the menu shows and does. `text` looks a key up in the UI language. Windows keeps
    the menu as built: call the icon's update_menu() when the shown state changes."""

    text: Callable[[str], str]
    search: Callable[[], object]  # does nothing while a search runs
    auto_on: Callable[[], bool]
    set_auto: Callable[[bool], None]
    paused: Callable[[], bool]
    pause: Callable[[], None]
    resume: Callable[[], None]


def create_tray(
    on_open: Callable[[], None], on_quit: Callable[[], None], controls: TrayControls
) -> TrayIcon:
    c = controls

    def text(key: str) -> Callable:  # read when the menu opens: follows the language
        return lambda item: c.text(key)

    items = [
        pystray.MenuItem(text("tray.open"), lambda: on_open(), default=True),
        pystray.MenuItem(text("scrape.button"), lambda: c.search()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            text("tray.auto"), lambda: c.set_auto(not c.auto_on()), checked=lambda item: c.auto_on()
        ),
        pystray.MenuItem(
            text("tray.pause"),
            lambda: c.pause(),
            visible=lambda item: c.auto_on() and not c.paused(),  # pyright: ignore[reportArgumentType]
        ),
        pystray.MenuItem(
            text("tray.resume"),
            lambda: c.resume(),
            visible=lambda item: c.auto_on() and c.paused(),  # pyright: ignore[reportArgumentType]
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(text("tray.quit"), lambda: on_quit()),
    ]
    return pystray.Icon("adawork", make_icon_image(), APP_NAME, pystray.Menu(*items))
