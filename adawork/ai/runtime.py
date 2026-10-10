"""Runs the bundled llama.cpp `llama-server` (PLAN §3.1, §3.2).

The server is started on demand for one model and stopped after a few idle minutes, so the
model only occupies RAM while there is AI work. Before every start the free RAM is checked:
if other programs use too much, `NotEnoughMemory` is raised and the AI work waits.

Two builds are bundled: Vulkan (any GPU, incl. integrated graphics) and CPU. If the Vulkan
build fails to start, e.g. because of a broken graphics driver, the CPU build is used from
then on. A start that only timed out or lost its port to another program is tried once more.
"""

import logging
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

import httpx

from adawork.system import hardware, winjob
from adawork.system.ports import HOST, free_port

log = logging.getLogger(__name__)

IDLE_SECONDS = 300
START_TIMEOUT = 180  # loading a model from a slow disk
# Kept free for Windows and the user's programs when deciding whether the model may start.
RAM_HEADROOM = 1 * hardware.GIB


class NotEnoughMemory(Exception):
    def __init__(self, need: int, available: int) -> None:
        super().__init__(f"needs {need / hardware.GIB:.1f} GB, {available / hardware.GIB:.1f} free")
        self.need, self.available = need, available


class ServerFailed(Exception):
    def __init__(self, message: str, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient  # timed out or port taken: not the backend's fault


@dataclass(frozen=True)
class Launch:
    """How to run one model on this PC; derived by the advisor from the hardware profile."""

    weights: Path
    mmproj: Path | None  # loaded only for image work (saves ~0.5 GB otherwise)
    backend: str  # "vulkan" | "cpu"
    gpu_layers: int | None  # None = as many as fit in GPU memory, 0 = CPU only
    threads: int
    ctx: int
    ram_need: int  # bytes, for the free-RAM check
    cpu_ram_need: int = 0  # bytes with the whole model in RAM (CPU build); 0 = ram_need

    def args(self, port: int) -> list[str]:
        args = [
            "-m", str(self.weights),
            "--host", HOST,
            "--port", str(port),
            "-c", str(self.ctx),
            "-np", "1",
            "-t", str(self.threads),
            "--no-webui",
            "--reasoning", "off",  # short JSON answers, no "thinking" tokens
            "--cache-ram", "0",  # no extra prompt cache: it may take GBs on small PCs
            "--prio", "-1",  # below normal: the user's programs come first
        ]  # fmt: skip
        if self.mmproj:
            args += ["--mmproj", str(self.mmproj)]
        if self.backend == "cpu" or self.gpu_layers == 0:
            args += ["--device", "none", "-ngl", "0"]
        elif self.gpu_layers is not None:
            args += ["-ngl", str(self.gpu_layers)]
        return args


class LlamaServer:
    def __init__(
        self,
        vendor: Path,
        log_path: Path,
        idle_seconds: float = IDLE_SECONDS,
        available_ram: Callable[[], int] = hardware.available_ram,
    ) -> None:
        self.vendor = vendor
        self.log_path = log_path
        self.idle_seconds = idle_seconds
        self.available_ram = available_ram
        self.vulkan_broken = False
        self._proc: subprocess.Popen | None = None
        self._launch: Launch | None = None
        self._port = 0
        self._last_used = 0.0
        self._users = 0  # open `use()` blocks: never stopped for idleness while > 0
        self._lock = threading.RLock()
        self._released = threading.Condition(self._lock)
        self._watcher: threading.Thread | None = None
        self._closing = threading.Event()  # the app quits: no more starts

    def binary(self, backend: str) -> Path:
        return self.vendor / "llama.cpp" / backend / "llama-server.exe"

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self._port}"

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def launch(self) -> Launch | None:
        return self._launch if self.running else None

    def ensure(self, launch: Launch) -> str:
        """Base URL of a server running `launch`; starts or restarts it when needed."""
        with self._lock:
            if self._closing.is_set():
                raise ServerFailed("the app is closing")
            if self.running and self._launch == launch:
                self.touch()
                return self.url
            self.stop()
            actual = _on_cpu(launch) if self.vulkan_broken else launch
            self._check_ram(actual)
            try:
                self._start_twice(actual)
            except ServerFailed as exc:
                if actual.backend != "vulkan" or exc.transient or self._closing.is_set():
                    raise
                log.warning("Vulkan llama-server failed, falling back to the CPU build")
                self.vulkan_broken = True
                self._check_ram(_on_cpu(launch))
                self._start_twice(_on_cpu(launch))
            self._launch = launch  # what was asked for, so the next ensure() matches
            self.touch()
            return self.url

    def _check_ram(self, launch: Launch) -> None:
        available = self.available_ram()
        if launch.ram_need + RAM_HEADROOM > available:
            raise NotEnoughMemory(launch.ram_need + RAM_HEADROOM, available)

    def _start_twice(self, launch: Launch) -> None:
        try:
            self._start(launch)
        except ServerFailed as exc:
            if not exc.transient or self._closing.is_set():
                raise
            log.warning("llama-server did not start (%s), trying once more", exc)
            self._start(launch)

    def touch(self) -> None:
        self._last_used = time.monotonic()

    @contextmanager
    def use(self, launch: Launch) -> Iterator[str]:
        """The server's URL for the duration of the block; it is not stopped as idle
        while any block is open, and a different launch waits until all blocks are closed."""
        with self._released:
            while self._users and self.running and self._launch != launch:
                self._released.wait()
            url = self.ensure(launch)
            self._users += 1
        try:
            yield url
        finally:
            with self._released:
                self._users -= 1
                self.touch()
                self._released.notify_all()

    def shutdown(self) -> None:
        """Stops the server for good, also while a start is still waiting for the model to
        load (that start holds the lock, so the process is ended without it first)."""
        self._closing.set()
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.kill()
        self.stop()

    def stop(self) -> None:
        with self._lock:
            self._released.notify_all()  # a `use()` waiting for another launch looks again
            proc, self._proc, self._launch = self._proc, None, None
            if proc is None or proc.poll() is not None:
                return
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            log.info("llama-server stopped")

    def _start(self, launch: Launch) -> None:
        binary = self.binary(launch.backend)
        if not binary.exists():
            raise ServerFailed(f"{binary} is missing")
        self._port = free_port()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log_start = self.log_path.stat().st_size if self.log_path.exists() else 0
        log.info("Starting llama-server: %s", " ".join(launch.args(self._port)))
        with self.log_path.open("ab") as log_file:
            self._proc = subprocess.Popen(
                [str(binary), *launch.args(self._port)],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0),
            )
        winjob.bind_to_app(self._proc.pid)  # never outlives the app, even after a crash
        deadline = time.monotonic() + START_TIMEOUT
        with httpx.Client(timeout=2) as client:
            while time.monotonic() < deadline:
                if self._closing.is_set():
                    self.stop()
                    raise ServerFailed("the app is closing")
                if self._proc.poll() is not None:
                    self._proc = None
                    with self.log_path.open("rb") as f:
                        f.seek(log_start)
                        port_taken = b"couldn't bind" in f.read()  # another program was quicker
                    raise ServerFailed(f"llama-server exited, see {self.log_path}", port_taken)
                try:
                    if client.get(f"{self.url}/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.25)
            else:
                self.stop()
                raise ServerFailed("llama-server did not become ready", transient=True)
        if self._watcher is None or not self._watcher.is_alive():
            self._watcher = threading.Thread(
                target=self._stop_when_idle, name="llama-idle", daemon=True
            )
            self._watcher.start()

    def _stop_when_idle(self) -> None:
        while True:
            time.sleep(min(10.0, self.idle_seconds / 4))
            with self._lock:
                if not self.running:
                    return
                if self._users == 0 and time.monotonic() - self._last_used > self.idle_seconds:
                    log.info("llama-server idle for %.0f s", self.idle_seconds)
                    self.stop()
                    return


def _on_cpu(launch: Launch) -> Launch:
    """The same model on the CPU build: what was meant for the GPU's memory is in RAM too."""
    ram_need = max(launch.ram_need, launch.cpu_ram_need)
    return replace(launch, backend="cpu", gpu_layers=0, ram_need=ram_need)
