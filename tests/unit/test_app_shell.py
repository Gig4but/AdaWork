import logging
import time
import webbrowser
from logging.handlers import RotatingFileHandler

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from adawork import cli, logs, paths
from adawork.desktop.instance import TOKEN_HEADER, SingleInstance
from adawork.desktop.server import BackgroundServer, ServerStartError


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
