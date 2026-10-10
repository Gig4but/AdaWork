"""Keeps the PC responsive: how many heavy jobs may run at once, and in what order
(PLAN §3.2.1, §3.2.2).

Heavy jobs are the AI model, the routing server and its imports, and the browser. On 8 GB only
one runs at a time; with more RAM the same manager allows more. Waiting jobs are served by
priority: what the user is waiting for first, background imports last.

Before a heavy job starts, it can also ask whether the PC is busy right now: low free RAM while
the user is working means "wait", so the app never makes the computer feel slow.
"""

import ctypes
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import IntEnum

from adawork.system import hardware
from adawork.system.hardware import GIB, HardwareProfile


class Priority(IntEnum):
    USER = 0  # commute recompute, opening an employer card
    AI = 1  # enrichment queue
    BROWSER = 2  # scraping with Edge
    IMPORT = 3  # routing data imports


@dataclass(frozen=True)
class Limits:
    parallel_heavy: int
    routing_radius_km: int | None  # None = whole Czech Republic
    min_free_ram: int  # below this, heavy work waits while the user is active


def limits_for(hw: HardwareProfile) -> Limits:
    ram = hw.ram_gb
    if ram < 12:
        return Limits(1, 150, int(1.5 * GIB))
    radius = None if hw.free_disk_gb > 20 else 150
    return Limits(2 if ram < 24 else 3, radius, 2 * GIB)


class _LastInput(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def user_idle_seconds() -> float:
    """Seconds since the last keyboard or mouse input (Windows); 0 elsewhere."""
    if sys.platform != "win32":
        return 0.0
    info = _LastInput(ctypes.sizeof(_LastInput), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    ticks = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
    return ((ticks - info.dwTime) & 0xFFFFFFFF) / 1000.0


class ResourceManager:
    def __init__(
        self,
        hw: HardwareProfile,
        available_ram: Callable[[], int] = hardware.available_ram,
        idle_seconds: Callable[[], float] = user_idle_seconds,
    ) -> None:
        self.update(hw)
        self.available_ram = available_ram
        self.idle_seconds = idle_seconds
        self._cond = threading.Condition()
        self._running: dict[int, Priority] = {}
        self._waiting: list[tuple[Priority, int]] = []
        self._next_ticket = 0

    def update(self, hw: HardwareProfile) -> None:
        self.hw = hw
        self.limits = limits_for(hw)

    @property
    def running(self) -> list[Priority]:
        with self._cond:
            return list(self._running.values())

    def busy(self, need: int = 0) -> bool:
        """True when heavy work should wait: free RAM is low and the user is at the PC."""
        low = self.available_ram() < need + self.limits.min_free_ram
        return low and self.idle_seconds() < 120

    @contextmanager
    def heavy(self, priority: Priority, timeout: float | None = None) -> Iterator[None]:
        """Hold one heavy-job slot. Raises TimeoutError if none frees up in time."""
        with self._cond:
            ticket = self._next_ticket
            self._next_ticket += 1
            entry = (priority, ticket)
            self._waiting.append(entry)
            deadline = None if timeout is None else time.monotonic() + timeout
            try:
                while not (
                    len(self._running) < self.limits.parallel_heavy and min(self._waiting) == entry
                ):
                    remaining = None if deadline is None else deadline - time.monotonic()
                    if remaining is not None and remaining <= 0:
                        raise TimeoutError(f"no free slot for {priority.name}")
                    self._cond.wait(remaining)
            finally:
                self._waiting.remove(entry)
                self._cond.notify_all()
            self._running[ticket] = priority
        try:
            yield
        finally:
            with self._cond:
                del self._running[ticket]
                self._cond.notify_all()
