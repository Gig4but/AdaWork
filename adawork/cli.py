"""Entry point: `adawork` opens the app window; `adawork --browser` serves the UI only."""

import argparse
import webbrowser

from adawork import __version__
from adawork.desktop.instance import SingleInstance
from adawork.desktop.server import BackgroundServer, ServerStartError
from adawork.logs import report_error, setup_logging
from adawork.web.app import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="adawork")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--browser", action="store_true", help="open the UI in the default browser, no window"
    )
    parser.add_argument("--port", type=int, help="fixed port for the local web server")
    args = parser.parse_args(argv)

    log_file = setup_logging()
    gui = not args.browser
    instance = SingleInstance()
    if not instance.acquire():  # already running: bring that one up instead of a second copy
        if instance.wake_running(open_browser=args.browser):
            return 0
        report_error("AdaWork už běží, ale neodpovídá. Zkuste to za chvíli znovu.", gui)
        return 1
    app = create_app()
    instance.add_show_route(app)
    server = BackgroundServer(app, port=args.port)
    try:
        server.start()
    except ServerStartError:
        instance.release()
        report_error(f"AdaWork se nepodařilo spustit. Podrobnosti: {log_file}", gui)
        return 1
    instance.publish(server.url)
    try:
        if args.browser:
            instance.on_show = lambda: webbrowser.open(server.url)
            webbrowser.open(server.url)
            print(f"AdaWork běží na {server.url} (ukončete Ctrl+C)")
            server.wait()
        else:
            from adawork.desktop.window import run_desktop

            # The tray is only needed while automatic searches are on.
            run_desktop(server, instance, lambda: app.state.settings.automation.enabled)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        instance.release()
    return 0
