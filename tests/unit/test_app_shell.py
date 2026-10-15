import logging
import sys
import time
import webbrowser
from logging.handlers import RotatingFileHandler

import pystray
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from adawork import cli, logs, paths
from adawork.desktop import autostart
from adawork.desktop.instance import TOKEN_HEADER, SingleInstance
from adawork.desktop.server import BackgroundServer, ServerStartError
from adawork.desktop.tray import TrayControls, create_tray
from adawork.secrets import WindowsSecrets


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """cli.main() on a temporary data folder, without a log file or message boxes."""
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(cli, "setup_logging", lambda: tmp_path / "adawork.log")
    monkeypatch.setattr(cli, "report_error", lambda message, gui: logs.report_error(message, False))
    return tmp_path


def test_only_one_instance_holds_the_lock(tmp_path):
    first, second = SingleInstance(tmp_path), SingleInstance(tmp_path)
    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()


def test_second_launch_wakes_the_running_app(data_dir, monkeypatch):
    running = SingleInstance()
    assert running.acquire()
    shown = []
    running.on_show = lambda: shown.append(True)
    app = FastAPI()
    running.add_show_route(app)
    server = BackgroundServer(app)
    server.start()
    running.publish(server.url)
    opened = []
    monkeypatch.setattr(webbrowser, "open", opened.append)
    monkeypatch.setattr(cli, "create_app", lambda: pytest.fail("a second app was started"))
    try:
        assert cli.main([]) == 0
        assert shown == [True]
        assert cli.main(["--browser"]) == 0
        assert opened == [server.url]
    finally:
        server.stop()
        running.release()


def test_show_request_needs_the_token(tmp_path):
    instance = SingleInstance(tmp_path)
    shown = []
    instance.on_show = lambda: shown.append(True)
    app = FastAPI()
    instance.add_show_route(app)
    client = TestClient(app)
    assert client.post("/instance/show", headers={TOKEN_HEADER: "guess"}).status_code == 403
    assert client.post("/instance/show", headers={TOKEN_HEADER: instance.token}).is_success
    assert shown == [True]


def test_running_app_that_does_not_answer(tmp_path):
    assert SingleInstance(tmp_path).wake_running(open_browser=False, wait=0.2) is False


def test_server_that_never_starts_is_stopped(monkeypatch):
    class NeverStarts:
        started = False
        should_exit = False

        def __init__(self, config):
            pass

        def run(self):
            while not self.should_exit:
                time.sleep(0.01)

    monkeypatch.setattr(uvicorn, "Server", NeverStarts)
    server = BackgroundServer(FastAPI())
    with pytest.raises(ServerStartError):
        server.start(timeout=0.2)
    assert not server._thread.is_alive()


def test_cli_reports_a_server_that_does_not_start(data_dir, monkeypatch, capsys):
    def fail(self, timeout=10.0):
        raise ServerStartError("Web server did not start")

    monkeypatch.setattr(cli, "create_app", FastAPI)
    monkeypatch.setattr(BackgroundServer, "start", fail)
    assert cli.main(["--browser"]) == 1
    assert "nepodařilo spustit" in capsys.readouterr().err
    assert SingleInstance(data_dir).acquire()  # the lock was given back


def test_logging_goes_to_a_rotating_file(tmp_path):
    root = logging.getLogger()
    before, level = list(root.handlers), root.level
    try:
        path = logs.setup_logging(tmp_path)
        logging.getLogger("adawork.test").info("hello log")
        added = [h for h in root.handlers if h not in before]
        file_handler = next(h for h in added if isinstance(h, RotatingFileHandler))
        assert (file_handler.maxBytes, file_handler.backupCount) == (1_000_000, 3)
        file_handler.flush()
        assert path == tmp_path / "adawork.log"
        assert "INFO adawork.test: hello log" in path.read_text("utf-8")
    finally:
        for handler in root.handlers[:]:
            if handler not in before:
                root.removeHandler(handler)
                handler.close()
        root.setLevel(level)


# --- M7: keys, start with Windows, tray -------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Credential Manager")
def test_keys_round_trip_through_the_credential_manager():
    store = WindowsSecrets(prefix="AdaWork-test")
    try:
        assert store.get("jooble") is None
        store.set("jooble", "klíč-1234")
        assert store.get("jooble") == "klíč-1234"
        store.set("jooble", "other")  # replaced, not added
        assert store.get("jooble") == "other"
    finally:
        store.delete("jooble")
    assert store.get("jooble") is None
    store.delete("jooble")  # deleting a missing key is fine


@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")
def test_start_with_windows_adds_and_removes_the_run_value(monkeypatch):
    import winreg

    key = r"Software\AdaWork-test\Run"
    winreg.CreateKey(winreg.HKEY_CURRENT_USER, key).Close()
    monkeypatch.setattr(autostart, "RUN_KEY", key)
    monkeypatch.delenv(paths.DATA_ENV, raising=False)
    try:
        autostart.sync(True)
        command = autostart.registered()
        assert command.endswith("-m adawork --minimized") and "pythonw.exe" in command
        autostart.sync(False)
        assert autostart.registered() is None
        autostart.sync(False)  # nothing to remove
        monkeypatch.setenv(paths.DATA_ENV, "C:\\test-data")  # a try-out copy leaves it alone
        autostart.sync(True)
        assert autostart.registered() is None
    finally:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\AdaWork-test")


def test_tray_menu_follows_the_automatic_checks():
    state = {"auto": False, "paused": False}
    calls = []
    controls = TrayControls(
        text=lambda key: {"tray.auto": "Automatické hledání"}.get(key, key),
        search=lambda: calls.append("search"),
        auto_on=lambda: state["auto"],
        set_auto=lambda on: state.update(auto=on),
        paused=lambda: state["paused"],
        pause=lambda: state.update(paused=True),
        resume=lambda: state.update(paused=False),
    )
    icon = create_tray(lambda: None, lambda: None, controls)

    real = [i for i in icon.menu.items if i is not pystray.Menu.SEPARATOR]

    def shown():
        return [(i.text, i.checked) for i in real if i.visible]

    assert shown() == [
        ("tray.open", None),
        ("scrape.button", None),
        ("Automatické hledání", False),
        ("tray.quit", None),
    ]
    items = {i.text: i for i in real}
    items["Automatické hledání"](icon)
    assert state["auto"] and ("tray.pause", None) in shown()
    items["tray.pause"](icon)
    assert ("tray.resume", None) in shown() and ("tray.pause", None) not in shown()
    items["scrape.button"](icon)
    assert calls == ["search"]


def test_minimized_start_stays_in_the_tray_only_while_checks_are_on(data_dir, monkeypatch):
    from adawork.desktop import window

    started = {}
    monkeypatch.setattr(window, "run_desktop", lambda *a, **kw: started.update(kw))
    monkeypatch.setattr(BackgroundServer, "start", lambda self, timeout=10.0: None)
    monkeypatch.setattr(BackgroundServer, "stop", lambda self: None)
    monkeypatch.setattr(autostart, "sync", lambda wanted: None)
    assert cli.main(["--minimized"]) == 0
    assert started["hidden"] is False  # checks off: the window shows
    assert isinstance(started["controls"], TrayControls)
