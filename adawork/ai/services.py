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

    def delete_models(self, model_id: str | None = None) -> bool:
        """One model, or all (None); AI work stops first when it uses them."""
        active = self.manager.active_model()
        if model_id is None or (active is not None and active.id == model_id):
            self.enricher.stop()
            self.manager.server.stop()  # ends a call in progress, so the enricher sees "stop"
            self.enricher.wait(15)  # it must not load the model again while it is deleted
        if model_id is None:
            self.manager.delete_all()
            return True
        return self.manager.delete_model(model_id)

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
