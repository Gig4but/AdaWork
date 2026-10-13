"""A service's background thread: a round of work, then a rest until the next round is due
or `wake()` asks for it now (Scheduler, EmployerService, RoutingData)."""

import logging
import threading

log = logging.getLogger(__name__)

RETRY_AFTER_ERROR = 60.0  # seconds; `work` handles its own errors, this is the last net


class Worker:
    """Subclasses give `name` and `work()`: one round of work, returning the seconds until
    the next one. A long round looks at `stopping` to end early."""

    name = "worker"
    # Seconds `stop` waits for the round to end; 0: not at all (a routing import takes minutes
    # and ends with the app's process).
    stop_timeout: float = 10.0

    def __init__(self) -> None:
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def stopping(self) -> bool:
        return self._stopping.is_set()

    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stopping.clear()
            self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
            self._thread.start()

    def wake(self) -> None:
        """The next round starts now."""
        self._wake.set()

    def stop(self) -> None:
        self._stopping.set()
        self._wake.set()
        if self._thread is not None and self.stop_timeout > 0:
            self._thread.join(self.stop_timeout)

    def sleep(self, seconds: float) -> bool:
        """Waits, but not past `stop`; True when stopping."""
        return self._stopping.wait(seconds)

    def work(self) -> float:
        raise NotImplementedError

    def _run(self) -> None:
        while not self.stopping:
            try:
                delay = self.work()
            except Exception:
                log.exception("%s: round failed", self.name)
                delay = RETRY_AFTER_ERROR
            self._wake.wait(delay)
            self._wake.clear()
