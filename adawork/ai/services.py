"""Wires the AI parts together for the app (one instance per app)."""

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from adawork import paths
from adawork.ai.enrich import Enricher
from adawork.ai.manager import EMBEDDER_DIR, ModelManager
from adawork.ai.runtime import LlamaServer
from adawork.config import Settings


@dataclass
class AiServices:
    manager: ModelManager
    enricher: Enricher

    def shutdown(self) -> None:
        self.enricher.stop()
        self.manager.shutdown()


def make_ai_services(
    settings: Settings, sessions: sessionmaker[Session], cache_dir: Path
) -> AiServices:
    models_dir = paths.models_dir()
    server = LlamaServer(paths.vendor_dir(), paths.log_dir() / "llama-server.log")
    manager = ModelManager(
        lambda: settings.ai, paths.data_dir() / "ai-state.json", models_dir, server
    )
    codebook = cache_dir / "mpsv" / "codebooks" / "cz-isco.json"
    enricher = Enricher(
        sessions,
        manager,
        codebook_path=codebook,
        profile=lambda: settings.profile,
        index_cache=models_dir / EMBEDDER_DIR / "isco-titles.npz",
        images_dir=cache_dir / "images",
    )
    return AiServices(manager, enricher)
