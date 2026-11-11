"""Owns the AI model on this PC (PLAN §6.6, §3.2): which one, downloading it, testing its
speed, and handing out a running server for AI work.

State that is measured rather than chosen (hardware fingerprint, measured speeds, the model in
use) lives in `ai-state.json` next to the settings.

Auto mode keeps the choice right over time:
- the hardware is probed at every start; a different fingerprint re-runs the advice;
- a new target model is downloaded in the background and benchmarked; it replaces the old one
  only if it is fast enough. Models are never deleted automatically (the AI page has a
  delete button for each one not in use);
- real work keeps updating the measured speed, so a model that turns out too slow is replaced
  by a smaller tier.
"""

import json
import logging
import shutil
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from adawork.ai import advisor, benchmark, download, embeddings
from adawork.ai.advisor import Advice, Speed
from adawork.ai.catalog import MODELS, TIERS, ModelSpec, tier_model
from adawork.ai.client import LlmClient, Timing
from adawork.ai.runtime import LlamaServer, NotEnoughMemory
from adawork.config import AiSettings
from adawork.system import hardware
from adawork.system.resources import Priority, ResourceManager

log = logging.getLogger(__name__)

EMBEDDER_DIR = "e5-small"
SPEED_WEIGHT = 0.1  # moving average of real calls
MIN_SAMPLES = 20  # before real work may change the tier
METERED_CACHE_SECONDS = 60


class Busy(Exception):
    """AI work must wait: no model yet, not enough free RAM, or the PC is busy."""


class NoModel(Busy):
    """No model is downloaded: nothing to wait for."""


@dataclass
class DownloadStatus:
    state: str = "idle"  # idle | downloading | benchmark | waiting_metered | failed | done
    model_id: str | None = None
    done: int = 0
    total: int = 0
    error: str = ""

    @property
    def percent(self) -> int:
        return int(self.done * 100 / self.total) if self.total else 0


@dataclass
class AiState:
    fingerprint: str = ""
    speeds: dict[str, dict] = field(default_factory=dict)  # model id -> speed + fingerprint
    active: str | None = None
    rejected: dict[str, str] = field(default_factory=dict)  # model id -> fingerprint
    notice: str = ""  # i18n key shown once, e.g. "ai.notice.upgraded"
    notice_args: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "AiState":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (OSError, ValueError, TypeError, AttributeError):
            return cls()

    def save(self, path: Path) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.__dict__, indent=1), encoding="utf-8")
        tmp.replace(path)


class ModelManager:
    def __init__(
        self,
        settings: Callable[[], AiSettings],
        state_path: Path,
        models_dir: Path,
        server: LlamaServer,
        resources: ResourceManager | None = None,
        probe: Callable[[], hardware.HardwareProfile] = hardware.probe,
        http_factory: Callable[[], httpx.Client] = lambda: httpx.Client(timeout=60),
        is_metered: Callable[[], bool] = download.is_metered,
    ) -> None:
        self.settings = settings
        self.state_path = state_path
        self.models_dir = models_dir
        self.server = server
        self.probe = probe
        self.http_factory = http_factory
        self.is_metered = is_metered
        self._metered: tuple[float, bool] | None = None  # (when asked, answer)
        self.state = AiState.load(state_path)
        self.hw = probe()
        self.resources = resources or ResourceManager(self.hw)
        self.download = DownloadStatus()
        self.embedder = embeddings.Embedder(
            models_dir / EMBEDDER_DIR, threads=advisor.threads(self.hw)
        )
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self.refresh_hardware()

    # --- what to use ---------------------------------------------------------------------

    def refresh_hardware(self) -> bool:
        """Probe again; True when the hardware changed since the last start."""
        self.hw = self.probe()
        self.resources.update(self.hw)
        changed = self.hw.fingerprint() != self.state.fingerprint
        if changed:
            log.info("Hardware: %s", self.hw.fingerprint())
            with self._lock:
                self.state.fingerprint = self.hw.fingerprint()
                self.state.rejected = {}
                self._save()
        return changed

    def downloaded(self) -> set[str]:
        return {
            m.id
            for m in MODELS.values()
            if all((self.models_dir / m.id / f.name).exists() for f in m.files())
        }

    def measured(self) -> dict[str, Speed]:
        return {
            model_id: Speed(s["prompt_tps"], s["output_tps"], measured=True)
            for model_id, s in self.state.speeds.items()
            if s.get("fingerprint") == self.state.fingerprint and s.get("samples", 0) >= MIN_SAMPLES
        }

    def advice(self) -> Advice:
        return advisor.advise(self.hw, self.measured(), self.downloaded())

    def target(self) -> ModelSpec | None:
        """The model the user's choice points to on this PC (may not be downloaded yet)."""
        choice = self.settings().choice
        if choice == "off":
            return None
        if choice == "auto" or (choice not in TIERS and choice not in MODELS):
            # Also a model removed from the catalog since the user picked it.
            pick = self.advice().pick
            if pick is None or self.state.rejected.get(pick.model.id) == self.state.fingerprint:
                return None
            return pick.model
        if choice in TIERS:
            return tier_model(choice)
        return MODELS.get(choice)

    def active_model(self) -> ModelSpec | None:
        """The model AI work uses right now: the target once it is ready, until then the
        previous model (fallback), else nothing (rules only)."""
        if self.settings().choice == "off":
            return None
        ready = self.downloaded()
        target = self.target()
        if target and target.id in ready and self.state.active == target.id:
            return target
        if self.state.active in ready:
            return MODELS[self.state.active]
        if target and target.id in ready:
            return target
        return None

    @property
    def custom_server(self) -> bool:
        return bool(self.settings().server_url)

    @property
    def ready(self) -> bool:
        return self.custom_server or (self.active_model() is not None and self.embedder.available)

    # --- downloading and switching -------------------------------------------------------

    def needs_download(self) -> bool:
        target = self.target()
        if self.custom_server or target is None:
            return not self.embedder.available
        return target.id not in self.downloaded() or target.id != self.state.active

    def start_download(self, allow_metered: bool | None = None) -> bool:
        """Fetch what the current choice needs, in the background. False if nothing to do
        or a download is already running. Returns at once (called from async code): the
        metered check runs in the download thread."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            if not self.needs_download():
                return False
            metered_ok = self.settings().allow_metered if allow_metered is None else allow_metered
            self._cancel.clear()
            self._thread = threading.Thread(
                target=self._fetch, args=(metered_ok,), name="model-download", daemon=True
            )
            self._thread.start()
            return True

    def metered(self) -> bool:
        """`is_metered()`, asked at most once a minute (it starts PowerShell)."""
        now = time.monotonic()
        if self._metered is None or now - self._metered[0] > METERED_CACHE_SECONDS:
            self._metered = (now, self.is_metered())
        return self._metered[1]

    def wait(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _fetch(self, metered_ok: bool = True) -> None:
        if not metered_ok and self.metered():
            self.download = DownloadStatus("waiting_metered")
            return
        target = None if self.custom_server else self.target()
        files: list[tuple[Path, download.RemoteFile]] = [
            (self.models_dir / EMBEDDER_DIR, f) for f in embeddings.FILES
        ]
        if target:
            files += [(self.models_dir / target.id, f) for f in target.files()]
        status = DownloadStatus("downloading", target.id if target else None)
        status.total = sum(f.size for d, f in files if not (d / f.name).exists())
        self.download = status
        try:
            # Checked before any byte is written; a full disk is not filled again at every start.
            download.ensure_space(self.models_dir, sum(download.remaining(f, d) for d, f in files))
            with self.http_factory() as client:
                for directory, remote in files:
                    base = status.done

                    def progress(done: int, _total: int, base: int = base) -> None:
                        status.done = base + done

                    if not (directory / remote.name).exists():
                        download.download(remote, directory, client, progress, self._cancel)
                        status.done = base + remote.size
            if target:
                if target.id not in self.measured():
                    status.state = "benchmark"
                self._try_switch(target)
            status.state = "done"
        except download.Cancelled:
            status.state = "idle"
        except Exception as exc:  # shown in the UI; the old model keeps working
            log.exception("Model download failed")
            status.state, status.error = "failed", str(exc)[:300]

    def _try_switch(self, target: ModelSpec) -> None:
        speed = self.measured().get(target.id) or self.run_benchmark(target)
        fit = advisor.fit(target, self.hw, measured=speed, downloaded=True)
        auto = self.settings().choice == "auto"
        if auto and fit.seconds_per_ad > advisor.YELLOW_SECONDS:
            log.info("%s too slow here (%.0f s per ad), keeping the old model", target.id,
                     fit.seconds_per_ad)  # fmt: skip
            with self._lock:
                self.state.rejected[target.id] = self.state.fingerprint
                self._save()
            return
        with self._lock:
            old = self.state.active
            upgraded = (
                auto and old is not None and old in MODELS
                and MODELS[old].params_b < target.params_b
            )  # fmt: skip
            # the old model stays on disk; only the user deletes models
            self.state.active = target.id
            if upgraded:
                self.state.notice = "ai.notice.upgraded"
                self.state.notice_args = {"model": target.name}
            self._save()

    def delete_model(self, model_id: str) -> bool:
        """Removes a downloaded model at the user's request; never the one in use."""
        active = self.active_model()
        if model_id not in MODELS or (active and active.id == model_id):
            return False
        if self.download.state in ("downloading", "benchmark") and (
            self.download.model_id == model_id
        ):
            return False
        shutil.rmtree(self.models_dir / model_id, ignore_errors=True)
        return True

    def run_benchmark(self, spec: ModelSpec) -> Speed:
        launch = advisor.launch_for(spec, self.hw, self.models_dir / spec.id)
        with (
            self.resources.heavy(Priority.AI),
            self.server.use(launch) as url,
            closing(self._client(url, spec)) as client,
        ):
            speed = benchmark.run(client)
        self._store_speed(spec.id, speed.prompt_tps, speed.output_tps, samples=MIN_SAMPLES)
        return speed

    # --- using the model -----------------------------------------------------------------

    @contextmanager
    def session(
        self, vision: bool = False, priority: Priority = Priority.AI
    ) -> Iterator[LlmClient]:
        """A client for a running model; holds a heavy-job slot of the ResourceManager.
        Raises Busy when AI work has to wait.

        A user's question (Priority.USER) shares an already loaded model without waiting for
        the slot; the server answers it right after the ad it is reading."""
        settings = self.settings()
        if settings.server_url:
            with closing(
                LlmClient(settings.server_url, model=settings.server_model, native=False)
            ) as client:
                yield client
            return
        spec = self.active_model()
        if spec is None:
            raise NoModel("ai.busy.no_model")
        launch = advisor.launch_for(spec, self.hw, self.models_dir / spec.id, vision)
        loaded = self.server.launch == launch
        if priority == Priority.USER and loaded:
            with self.server.use(launch) as url, closing(self._client(url, spec)) as c:
                yield c
            return
        if priority != Priority.USER and self.resources.busy(0 if loaded else launch.ram_need):
            raise Busy("ai.busy.pc_busy")
        timeout = 30 if priority == Priority.USER else None
        with ExitStack() as stack:
            try:
                stack.enter_context(self.resources.heavy(priority, timeout))
            except TimeoutError as exc:
                raise Busy("ai.busy.pc_busy") from exc
            try:
                url = stack.enter_context(self.server.use(launch))
            except NotEnoughMemory as exc:
                raise Busy("ai.busy.memory") from exc
            yield stack.enter_context(closing(self._client(url, spec)))

    @staticmethod
    def _client(url: str, spec: ModelSpec) -> LlmClient:
        return LlmClient(url, model=spec.id, chat_kwargs=spec.chat_kwargs)

    def record(self, timing: Timing) -> None:
        """Feeds the real speed of a call into the statistics (Auto re-checks the tier)."""
        spec = self.active_model()
        if self.custom_server or spec is None or timing.output_seconds <= 0:
            return
        current = self.state.speeds.get(spec.id)
        if current and current.get("fingerprint") == self.state.fingerprint:
            pp, tg = current["prompt_tps"], current["output_tps"]
            if timing.prompt_seconds > 0 and timing.prompt_tokens >= 50:
                pp += SPEED_WEIGHT * (timing.prompt_tps - pp)
            tg += SPEED_WEIGHT * (timing.output_tps - tg)
            samples = current.get("samples", 0) + 1
        else:
            pp = timing.prompt_tps or 1.0
            tg = timing.output_tps
            samples = 1
        self._store_speed(spec.id, pp, tg, samples)

    def tier_still_right(self) -> bool:
        """False when Auto should move to another tier (call `start_download()` then)."""
        if self.settings().choice != "auto":
            return True
        target = self.target()
        return target is None or target.id == self.state.active

    def take_notice(self) -> tuple[str, dict] | None:
        with self._lock:
            if not self.state.notice:
                return None
            notice = (self.state.notice, self.state.notice_args)
            self.state.notice, self.state.notice_args = "", {}
            self._save()
            return notice

    def shutdown(self) -> None:
        self._cancel.set()
        self.server.shutdown()

    def _store_speed(self, model_id: str, pp: float, tg: float, samples: int) -> None:
        with self._lock:
            self.state.speeds[model_id] = {
                "prompt_tps": round(pp, 2),
                "output_tps": round(tg, 2),
                "fingerprint": self.state.fingerprint,
                "samples": samples,
            }
            self._save()

    def _save(self) -> None:
        try:
            self.state.save(self.state_path)
        except OSError as exc:
            log.warning("Could not save AI state: %s", exc)
