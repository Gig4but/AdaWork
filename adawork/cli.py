"""Entry point: `adawork` opens the app window; `adawork --browser` serves the UI only.
Developer tools: `adawork eval --model <id>` measures an AI model on the golden set,
`adawork probe [source ...]` checks that the portals can still be read."""

import argparse
import logging
import os
import sys
import webbrowser

from adawork import __version__
from adawork.desktop.instance import SingleInstance
from adawork.desktop.server import BackgroundServer, ServerStartError
from adawork.i18n import translate
from adawork.logs import report_error, setup_logging
from adawork.services import Services
from adawork.web.app import create_app


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["eval"]:  # developer tool: model accuracy and speed (PLAN §6.7)
        from adawork.ai.evaluate import main as eval_main

        return eval_main(argv[1:])
    if argv[:1] == ["probe"]:
        from adawork.scraper.probe import main as probe_main

        return probe_main(argv[1:])
    parser = argparse.ArgumentParser(prog="adawork")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--browser", action="store_true", help="open the UI in the default browser, no window"
    )
    parser.add_argument("--port", type=int, help="fixed port for the local web server")
    parser.add_argument(
        "--minimized", action="store_true", help="start in the tray (start with Windows)"
    )
    args = parser.parse_args(argv)

    log_file = setup_logging()
    gui = not args.browser
    instance = SingleInstance()
    if not instance.acquire():  # already running: bring that one up instead of a second copy
        if instance.wake_running(open_browser=args.browser):
            return 0
        report_error("AdaWork už běží, ale neodpovídá. Zkuste to za chvíli znovu.", gui)
        return 1
    try:
        app = create_app()  # settings, database (migrated), services
    except Exception:
        logging.getLogger(__name__).exception("start-up failed")
        instance.release()
        report_error(f"AdaWork se nepodařilo spustit. Podrobnosti: {log_file}", gui)
        return 1
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

            svc = app.state.services
            automation = svc.settings.automation

            def on_tray(icon) -> None:
                svc.scheduler.notify = lambda title, text: icon.notify(text, title)
                svc.scheduler.on_change = icon.update_menu

            # The tray is only needed while automatic searches are on.
            run_desktop(
                server,
                controls=tray_controls(svc),
                instance=instance,
                keep_in_tray=lambda: automation.enabled,
                on_tray=on_tray,
                hidden=args.minimized and automation.enabled,
            )
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        instance.release()
    leave_search(app)
    return 0


def tray_controls(svc: Services):
    from adawork.desktop.tray import TrayControls

    settings, scheduler = svc.settings, svc.scheduler
    return TrayControls(
        text=lambda key: translate(settings.language, key),
        search=svc.runner.start,
        auto_on=lambda: settings.automation.enabled,
        set_auto=svc.set_automation,
        paused=scheduler.paused,
        pause=scheduler.pause,
        resume=scheduler.resume,
    )


def leave_search(app) -> None:
    """A search the shutdown could not stop (a long download): Python would wait at exit for
    its threads, keeping the process and the terminal busy for minutes. What it found so far
    is saved."""
    if app.state.services.runner.progress.running:
        logging.shutdown()
        os._exit(0)
