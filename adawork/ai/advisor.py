"""Which model suits this computer, and how to run it (PLAN §6.6 `ModelAdvisor`).

For every tier: the RAM it needs, an estimate of seconds per ad, and an icon:
- green  "Poběží plynule": RAM need ≤ 40 % of total RAM and ≤ 15 s per ad;
- yellow "Poběží, ale pomaleji": RAM need ≤ 60 % and ≤ 40 s per ad;
- red    "Nedoporučeno": otherwise, or not enough free disk for the download.
Auto picks the largest green tier, else the largest yellow one, else Simple with a warning,
or no model (rules only) when even Simple cannot fit.

The speed estimate starts from a table of hardware classes and is replaced by the measured
speed once a model has run on this PC (benchmark or real work).
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from adawork.ai.catalog import TIER_ORDER, ModelSpec, tier_model
from adawork.ai.runtime import Launch
from adawork.system.hardware import GIB, HardwareProfile

CTX = 4096
# Compute buffers and the process itself; images need extra buffers (measured in M5:
# estimates within 0.2 GB of the real use).
RUNTIME_OVERHEAD = int(0.6 * GIB)
VISION_OVERHEAD = int(0.5 * GIB)
OS_RESERVE = 3 * GIB
APP_RESERVE = int(0.7 * GIB)
DISK_MARGIN = 2 * GIB

# A typical ad: instructions + candidates + ~2 500 characters of text, and a short answer
# (M5 eval: 700–870 prompt and 70–80 output tokens per ad).
TYPICAL_PROMPT_TOKENS = 900
TYPICAL_OUTPUT_TOKENS = 70
CACHED_PROMPT_TOKENS = 400  # the shared instructions are processed once per batch
# Wall time per ad vs. pure token time (grammar, sampling, HTTP); measured 1.3–1.7 in M5.
WALL_FACTOR = 1.6

GREEN_RAM, YELLOW_RAM = 0.40, 0.60
GREEN_SECONDS, YELLOW_SECONDS = 15.0, 40.0

# Throughput of the first estimate, before a model has run on this PC. Constants per hardware
# class come from published llama.cpp measurements of 7B Q4 models (Vulkan and CUDA
# scoreboards, github.com/ggml-org/llama.cpp/discussions/10879 and /15013; localscore.ai),
# and from our own M5 measurement; docs/spikes.md lists the sources:
#   prompt tokens/s ≈ pp_k × scale / params_b
#   output tokens/s ≈ bandwidth × scale / GB read per token
# GB read per token = active parameters at ~0.6 bytes each in Q4 (Gemma's per-layer
# embeddings are not read). Small models reach less of the hardware's peak (fixed costs per
# token), hence `scale`, ~0.6 at 2B and 1.0 from ~7B, fitted to the published 1B/7B pairs.
BYTES_PER_PARAM = 0.6


@dataclass(frozen=True)
class SpeedClass:
    name: str
    pattern: str  # regex on the lowercased graphics adapter name
    pp_k: float  # prompt tokens/s × billions of parameters, 7B-class model
    bandwidth: float  # GB/s effectively read during generation, 7B-class model


IGPU_CLASSES = (
    SpeedClass("AMD Radeon 760M/780M/880M/890M", r"radeon.*\b(7[68]0m|8[89]0m)\b", 1800, 76),
    # No Vulkan numbers published for the 2-CU 610M: the CPU of a Ryzen 5 7520U instead.
    SpeedClass("AMD Radeon 610M", r"radeon.*\b610m\b", 260, 45),
    # Ryzen 3000–5000 laptops name their Vega iGPU just "AMD Radeon(TM) Graphics".
    SpeedClass("AMD Radeon Vega", r"^amd radeon\(tm\) graphics$|vega", 610, 42),
    SpeedClass("Intel Arc 130V/140V", r"arc.*\b1[34]0v\b", 5800, 93),
    SpeedClass("Intel Arc", r"\barc\b", 1250, 31),
    SpeedClass("Intel Iris Xe", r"iris", 700, 30),
    # Core Ultra 200U ("Intel(R) Graphics", 4 Xe cores): measured in M5 on Qwen3-VL 2B.
    SpeedClass("Intel Graphics", r"^intel\(r\) graphics$", 1000, 36),
    SpeedClass("Intel UHD", r"\buhd\b", 300, 20),
)
OTHER_IGPU = SpeedClass("integrated graphics", "", 500, 30)
# Dedicated GPUs, model fully in VRAM: RTX 3050 / RX 6600 class; ≥ 12 GB: RTX 3060 / 4060 Ti.
DGPU = SpeedClass("graphics card", "", 8000, 170)
DGPU_LARGE = SpeedClass("graphics card ≥ 12 GB", "", 12000, 250)
# CPU only: desktop Ryzen 5 5600 / i5-12400 ≈ 260–380 for 6 cores, laptops less (M5: 8
# threads of a Core Ultra 7 read 89 tokens/s of a 1.7B model, ≈ 45 per core after scaling).
CPU_PP_PER_CORE = 45.0
CPU_BANDWIDTH = 35.0


def model_scale(params_b: float) -> float:
    return min(1.0, 0.45 + 0.08 * params_b)


def speed_class(hw: HardwareProfile) -> SpeedClass | None:
    """The class of the PC's graphics, or None for CPU only."""
    gpu = hw.dedicated_gpu
    if gpu:
        return DGPU_LARGE if gpu.vram_gb >= 11 else DGPU
    if not hw.gpus:
        return None
    name = (hw.best_gpu.name if hw.best_gpu else "").lower()
    return next((c for c in IGPU_CLASSES if re.search(c.pattern, name)), OTHER_IGPU)


@dataclass(frozen=True)
class Speed:
    prompt_tps: float
    output_tps: float
    measured: bool = False

    def seconds_per_ad(self) -> float:
        prompt = TYPICAL_PROMPT_TOKENS - CACHED_PROMPT_TOKENS
        tokens = prompt / self.prompt_tps + TYPICAL_OUTPUT_TOKENS / self.output_tps
        return WALL_FACTOR * tokens


@dataclass(frozen=True)
class Fit:
    model: ModelSpec
    tier: str | None
    icon: str  # green | yellow | red
    ram_need: int
    seconds_per_ad: float
    speed: Speed
    reasons: tuple[str, ...] = ()  # i18n keys explaining a red/yellow icon
    downloaded: bool = False


@dataclass
class Advice:
    fits: list[Fit]
    pick: Fit | None  # None = rules only
    reason: str  # i18n key, e.g. "ai.reason.igpu"
    reason_args: dict = field(default_factory=dict)


def ram_need(spec: ModelSpec, ctx: int = CTX, vision: bool = False) -> int:
    weights = spec.weights[1]
    if vision and spec.mmproj:
        weights += spec.mmproj[1] + VISION_OVERHEAD
    return weights + int(spec.kv_mb_per_1k * ctx / 1024 * 1024 * 1024) + RUNTIME_OVERHEAD


def system_ram_need(spec: ModelSpec, hw: HardwareProfile, vision: bool = False) -> int:
    """What the model takes from system RAM: a dedicated GPU holds what fits in its VRAM."""
    need = ram_need(spec, vision=vision)
    gpu = hw.dedicated_gpu
    if gpu is None:
        return need
    on_gpu = min(int(gpu.dedicated_bytes * 0.9), need - RUNTIME_OVERHEAD // 2)
    return need - on_gpu


def estimate_speed(spec: ModelSpec, hw: HardwareProfile) -> Speed:
    scale = model_scale(spec.params_b)
    read_gb = spec.params_b * BYTES_PER_PARAM
    cpu_pp = CPU_PP_PER_CORE * min(hw.physical_cores, 8) * (1.0 if hw.avx2 else 0.4)
    cpu = Speed(cpu_pp * scale / spec.params_b, CPU_BANDWIDTH * scale / read_gb)
    cls = speed_class(hw)
    if cls is None:
        return cpu
    gpu = hw.dedicated_gpu
    if gpu and gpu.dedicated_bytes * 0.9 < ram_need(spec):
        cls = OTHER_IGPU  # does not fit in VRAM: partly on the CPU, like integrated graphics
    pp = max(cpu.prompt_tps, cls.pp_k * scale / spec.params_b)
    return Speed(pp, cls.bandwidth * scale / read_gb)


def fit(
    spec: ModelSpec,
    hw: HardwareProfile,
    tier: str | None = None,
    measured: Speed | None = None,
    downloaded: bool = False,
) -> Fit:
    need = system_ram_need(spec, hw)
    speed = measured or estimate_speed(spec, hw)
    seconds = speed.seconds_per_ad()
    share = need / hw.total_ram
    reasons = []
    if share > YELLOW_RAM or need > hw.total_ram - OS_RESERVE - APP_RESERVE:
        reasons.append("ai.why.ram")
    if seconds > YELLOW_SECONDS:
        reasons.append("ai.why.slow")
    if not downloaded and hw.free_disk < spec.download_bytes + DISK_MARGIN:
        reasons.append("ai.why.disk")
    if reasons:
        icon = "red"
    elif share <= GREEN_RAM and seconds <= GREEN_SECONDS:
        icon = "green"
    else:
        icon = "yellow"
        if share > GREEN_RAM:
            reasons.append("ai.why.ram_tight")
        if seconds > GREEN_SECONDS:
            reasons.append("ai.why.slower")
    return Fit(spec, tier, icon, need, seconds, speed, tuple(reasons), downloaded)


def advise(
    hw: HardwareProfile,
    measured: dict[str, Speed] | None = None,
    downloaded: set[str] | None = None,
) -> Advice:
    measured = measured or {}
    downloaded = downloaded or set()
    models = [(tier, tier_model(tier)) for tier in TIER_ORDER]
    fits = [fit(m, hw, tier, measured.get(m.id), m.id in downloaded) for tier, m in models]
    pick = next((f for f in reversed(fits) if f.icon == "green"), None)
    pick = pick or next((f for f in reversed(fits) if f.icon == "yellow"), None)
    simple = fits[0]
    if pick is None and simple.ram_need <= hw.total_ram - OS_RESERVE - APP_RESERVE:
        pick = simple
    return Advice(fits, pick, *_reason(hw, pick))


def _reason(hw: HardwareProfile, pick: Fit | None) -> tuple[str, dict]:
    args = {"ram": round(hw.ram_gb), "tier": pick.tier if pick else ""}
    if pick is None:
        return "ai.reason.none", args
    gpu = hw.dedicated_gpu
    if gpu:
        return "ai.reason.dgpu", args | {"vram": round(gpu.vram_gb)}
    if hw.gpus:
        return "ai.reason.igpu", args
    return "ai.reason.cpu", args


def threads(hw: HardwareProfile) -> int:
    """Leaves a core for the user's programs; more threads rarely help beyond 8."""
    return max(2, min(hw.physical_cores - 1, 8))


def launch_for(
    spec: ModelSpec, hw: HardwareProfile, model_dir: Path, vision: bool = False
) -> Launch:
    files = spec.files()
    return Launch(
        weights=model_dir / files[0].name,
        mmproj=model_dir / files[1].name if vision and len(files) > 1 else None,
        backend="vulkan" if hw.gpus else "cpu",
        gpu_layers=None if hw.gpus else 0,
        threads=threads(hw),
        ctx=CTX,
        ram_need=system_ram_need(spec, hw, vision),
        cpu_ram_need=ram_need(spec, vision=vision),
    )
