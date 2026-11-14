"""Native app window (WebView2) with the tray icon. Closing the window quits the app, or
hides it to the tray while automatic searches are on."""

import logging
import re
import sys
from collections.abc import Callable
from pathlib import Path

import webview

from adawork import APP_NAME, paths
from adawork.desktop.icon import make_icon_image
from adawork.desktop.instance import SingleInstance
from adawork.desktop.server import BackgroundServer
from adawork.desktop.tray import TrayControls, TrayIcon, create_tray

# DwmSetWindowAttribute attributes (Windows 11; older Windows ignores them).
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36
DWMWCP_ROUND = 2
APP_ID = "AdaWork.App"  # groups the taskbar button under our icon, not Python's
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

log = logging.getLogger(__name__)


def parse_css_color(css: str) -> tuple[int, int, int] | None:
    """(r, g, b) of a computed CSS colour such as "rgb(19, 22.5, 30.5)"."""
    parts = re.findall(r"[\d.]+", css)
    if not css.startswith("rgb") or len(parts) < 3:
        return None
    r, g, b = (min(255, round(float(p))) for p in parts[:3])
    return r, g, b


def _colorref(rgb: tuple[int, int, int]) -> int:
    r, g, b = rgb
    return r | g << 8 | b << 16


def write_icon_file(path: Path) -> Path:
    """The app icon as a .ico with every size Windows asks for (window, taskbar, Alt+Tab)."""
    # BMP entries: Windows Forms cannot read the PNG ones Pillow writes by default.
    make_icon_image(256).save(path, sizes=[(s, s) for s in ICON_SIZES], bitmap_format="bmp")
    return path


class FrameApi:
    """Called by the page (static/frame.js): the title bar and border take the page's colours,
    so the window looks like one surface, as other Windows 11 apps do."""

    def __init__(self, window: Callable[[], webview.Window | None]) -> None:
        # Private: pywebview exposes public attributes to the page and walks into them.
        self._window = window

    def frame(self, background: str, text: str, dark: bool) -> None:
        bg, fg = parse_css_color(background), parse_css_color(text)
        window = self._window()
        native = window.native if window else None
        if sys.platform != "win32" or native is None or not bg:
            return
        fg = fg or ((255, 255, 255) if dark else (0, 0, 0))
        mix = [round(b * 0.85 + f * 0.15) for b, f in zip(bg, fg, strict=True)]
        edge = (mix[0], mix[1], mix[2])
        try:
            import ctypes

            hwnd = native.Handle.ToInt64()
            for attribute, value in (
                (DWMWA_USE_IMMERSIVE_DARK_MODE, int(dark)),
                (DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND),
                (DWMWA_CAPTION_COLOR, _colorref(bg)),
                (DWMWA_TEXT_COLOR, _colorref(fg)),
                (DWMWA_BORDER_COLOR, _colorref(edge)),
            ):
                data = ctypes.c_int(value)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data)
                )
        except Exception:  # a plain title bar is fine
            log.debug("Could not colour the window frame", exc_info=True)


def run_desktop(
    server: BackgroundServer,
    controls: TrayControls,
    instance: SingleInstance | None = None,
    keep_in_tray: Callable[[], bool] = lambda: True,
    on_tray: Callable[[TrayIcon], None] = lambda icon: None,
    hidden: bool = False,
    on_quit: Callable[[Callable[[], None]], None] = lambda quit_app: None,
) -> None:
    """`hidden`: starts in the tray only (start with Windows); `on_tray` gets the tray icon,
    e.g. to show notifications through it; `on_quit` gets what quits the app."""
    quitting = False
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    webview.settings["ALLOW_DOWNLOADS"] = True  # exported job lists
    window = webview.create_window(
        APP_NAME,
        server.url,
        width=1280,
        height=820,
        min_size=(900, 600),
        hidden=hidden,
        js_api=FrameApi(lambda: window),
    )
    assert window is not None

    def on_closing() -> bool:
        if quitting or not keep_in_tray():
            return True  # the app ends, and the terminal that started it is free again
        window.hide()
        return False  # keep running in the tray for the automatic searches

    def show() -> None:
        window.show()
        window.restore()

    def quit_app() -> None:
        nonlocal quitting
        quitting = True
        tray.stop()
        window.destroy()

    window.events.closing += on_closing
    if instance:
        instance.on_show = show  # a second launch brings this window up
    tray = create_tray(on_open=show, on_quit=quit_app, controls=controls)
    tray.run_detached()
    on_tray(tray)
    on_quit(quit_app)
    try:
        # Blocks on the main thread until the window is destroyed.
        webview.start(
            icon=str(write_icon_file(paths.data_dir() / "adawork.ico")),
            # WebView2's own files; without a folder every start leaves a new one in %TEMP%.
            storage_path=str(paths.data_dir() / "webview"),
        )
    finally:
        tray.stop()
