import time

import httpx
import pytest
from fastapi.testclient import TestClient

from adawork.config import Settings
from adawork.desktop.server import BackgroundServer
from adawork.desktop.tray import make_icon_image
from adawork.desktop.window import ICON_SIZES, parse_css_color, write_icon_file
from adawork.web.app import create_app


def test_defaults_keep_automatic_checks_off():
    settings = Settings()
    assert settings.language == "cs"
    assert settings.automation.enabled is False
    assert settings.automation.interval_hours == 12


def test_settings_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    Settings(language="en").save(path)
    assert Settings.load(path).language == "en"
    assert Settings.load(tmp_path / "missing.json") == Settings()


def test_broken_settings_fall_back_to_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"language": "xx"', encoding="utf-8")
    assert Settings.load(path) == Settings()
    assert not path.exists() and (tmp_path / "config.json.broken").exists()


def test_index_page_in_czech():
    client = TestClient(create_app(Settings()))
    assert client.get("/health").json()["status"] == "ok"
    page = client.get("/")
    assert page.status_code == 200
    assert "Automatické hledání je vypnuté" in page.text


def test_background_server_starts_and_stops(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    server = BackgroundServer(create_app(Settings()))
    server.start()
    assert httpx.get(server.url + "health", trust_env=False).json()["status"] == "ok"
    server.stop()
    with pytest.raises(httpx.TransportError):  # Windows retries a refused connect for ~2 s
        httpx.get(server.url + "health", trust_env=False, timeout=0.5)


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_background_server_fails_fast_when_port_is_taken():
    first = BackgroundServer(create_app(Settings()))
    first.start()
    try:
        second = BackgroundServer(create_app(Settings()), port=first.port)
        started = time.monotonic()
        with pytest.raises(RuntimeError):
            second.start()
        assert time.monotonic() - started < 5
    finally:
        first.stop()


def test_tray_icon_renders():
    assert make_icon_image().size == (64, 64)


def test_window_frame_reads_the_page_colours():
    assert parse_css_color("rgb(19, 22.5, 30.5)") == (19, 22, 30)
    assert parse_css_color("rgba(255, 255, 255, 0.5)") == (255, 255, 255)
    assert parse_css_color("transparent") is None


def test_window_icon_file_has_every_size(tmp_path):
    from PIL import Image

    with Image.open(write_icon_file(tmp_path / "a.ico")) as ico:
        assert ico.info["sizes"] == {(s, s) for s in ICON_SIZES}
