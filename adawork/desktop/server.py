"""Runs the FastAPI app in a background thread on a free localhost port."""

import threading
import time

import uvicorn
from fastapi import FastAPI

from adawork.system.ports import HOST, free_port


class ServerStartError(RuntimeError):
    """The local web server did not come up (port taken, start-up hang)."""


class BackgroundServer:
    def __init__(self, app: FastAPI, port: int | None = None) -> None:
        self.port = port or free_port()
        # log_config=None: uvicorn logs through the app's handlers (adawork.logs), not its own.
        config = uvicorn.Config(
            app, host=HOST, port=self.port, log_level="warning", log_config=None
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, name="web-server", daemon=True)

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.port}/"

    def start(self, timeout: float = 10.0) -> None:
        self._thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self._thread.is_alive():
            if self._server.started:
                return
            time.sleep(0.05)
        self.stop()  # don't leave a half-started server running
        raise ServerStartError(f"Web server did not start on {self.url}")

    def wait(self) -> None:
        # Short joins so Ctrl+C is delivered on Windows.
        while self._thread.is_alive():
            self._thread.join(timeout=0.5)

    def stop(self) -> None:
        """Ends the server; its shutdown also stops the app's background work (a running
        search, the employer pass, MOTIS and the AI model), which can take up to a minute."""
        self._server.should_exit = True
        self._thread.join(timeout=60)
