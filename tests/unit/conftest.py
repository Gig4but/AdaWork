"""Shared test data."""

import pytest

from adawork.db.models import Municipality
from adawork.geo.geocode import normalize_name

# code, name, okres, kraj, lat, lon, addresses, postal codes
MUNICIPALITIES = [
    ("554782", "Praha", "Hlavní město Praha", "Hlavní město Praha", 50.08, 14.43, 300000,
     "10000 11000 14000 14200 15000"),
    ("554791", "Plzeň", "Plzeň-město", "Plzeňský kraj", 49.74, 13.37, 40000, "30100 32600"),
    ("586846", "Jihlava", "Jihlava", "Kraj Vysočina", 49.40, 15.59, 15000, "58601"),
    ("582786", "Brno", "Brno-město", "Jihomoravský kraj", 49.19, 16.61, 80000, "60200 62800"),
    ("533165", "Kolín", "Kolín", "Středočeský kraj", 50.03, 15.20, 9000, "28002"),
    ("538094", "Brandýs nad Labem-Stará Boleslav", "Praha-východ", "Středočeský kraj",
     50.19, 14.66, 7000, "25001"),
    ("549240", "Lhota", "Kladno", "Středočeský kraj", 50.12, 14.02, 300, "27301"),
    ("555657", "Lhota", "Zlín", "Zlínský kraj", 49.09, 17.69, 250, "76323"),
    ("560251", "Chodov", "Sokolov", "Karlovarský kraj", 50.24, 12.75, 5000, "35735"),
    ("574121", "Pardubice", "Pardubice", "Pardubický kraj", 50.04, 15.78, 20000, "53002"),
]  # fmt: skip


def add_municipalities(session) -> None:
    for code, name, okres, kraj, lat, lon, count, pscs in MUNICIPALITIES:
        session.add(
            Municipality(
                code=code, name=name, name_normalized=normalize_name(name),
                okres_code=f"9{code[:3]}",
                okres=okres, kraj=kraj, lat=lat, lon=lon, address_count=count,
                postal_codes=pscs,
            )
        )  # fmt: skip
    session.commit()


@pytest.fixture
def municipalities():
    """Adds a few real municipalities (with made-up centres) to a session."""
    return add_municipalities


# --- AI test helpers -------------------------------------------------------------------------

GIB = 1024**3


def make_hw(ram_gb=8, gpu=None, vram_gb=0.0, cores=6, disk_gb=100, available_gb=None):
    """A HardwareProfile; `gpu` names a graphics adapter (integrated unless vram_gb >= 4)."""
    from adawork.system.hardware import Gpu, HardwareProfile

    gpus = (Gpu(gpu, "AMD", int(vram_gb * GIB), int(ram_gb / 2 * GIB)),) if gpu else ()
    return HardwareProfile(
        total_ram=int(ram_gb * GIB),
        available_ram=int((available_gb if available_gb is not None else ram_gb / 2) * GIB),
        cpu_name="Test CPU",
        physical_cores=cores,
        logical_cores=cores * 2,
        avx2=True,
        avx512=False,
        gpus=gpus,
        free_disk=int(disk_gb * GIB),
    )


class FakeEmbedder:
    """Bag-of-words vectors: titles sharing words are similar. No model files needed."""

    available = True

    def embed(self, texts, prefix="query: ", batch=64):
        import zlib

        import numpy as np

        from adawork.normalize.text import fold

        out = np.zeros((len(texts), 384), dtype=np.float32)
        for i, text in enumerate(texts):
            for word in fold(text).replace("/", " ").split():
                out[i, zlib.crc32(word.encode()) % 384] += 1.0
            norm = np.linalg.norm(out[i])
            if norm:
                out[i] /= norm
        return out


def make_ai(tmp_path, settings, sessions, hw=None, codebook=None):
    """AiServices on temporary folders: nothing is downloaded or started."""
    from pathlib import Path

    from adawork.ai.enrich import Enricher
    from adawork.ai.manager import ModelManager
    from adawork.ai.runtime import LlamaServer
    from adawork.ai.services import AiServices

    hw = hw or make_hw(16, "AMD Radeon(TM) Graphics", 0.5)
    manager = ModelManager(
        lambda: settings.ai,
        tmp_path / "ai-state.json",
        tmp_path / "models",
        LlamaServer(tmp_path / "vendor", tmp_path / "llama.log"),
        probe=lambda: hw,
    )
    manager.embedder = FakeEmbedder()
    manager.downloads_started = 0

    def start_download():
        manager.downloads_started += 1
        return True

    manager.start_download = start_download
    codebook = codebook or (
        Path(__file__).parents[1] / "fixtures" / "sources" / "mpsv" / "codebooks" / "cz-isco.json"
    )
    enricher = Enricher(
        sessions,
        manager,
        codebook_path=codebook,
        profile=lambda: settings.profile,
        wait_seconds=0,
    )
    return AiServices(manager, enricher)
