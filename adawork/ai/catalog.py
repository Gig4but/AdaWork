"""Models the app can download (PLAN §6.6). Every entry is pinned to a Hugging Face commit and
checked by SHA-256, so a model never changes under the user.

`TIERS` maps the three friendly tiers to models; the other entries can be picked under
"Pokročilé" (advanced). 8–14B models were evaluated and removed: they were no better than
the tiers (docs/spikes.md).
"""

from dataclasses import dataclass

from adawork.ai.download import RemoteFile

HF = "https://huggingface.co/{repo}/resolve/{revision}/{name}"
GB = 1_000_000_000


@dataclass(frozen=True)
class PromptStyle:
    """How the occupation candidates are offered; models differ in what works best (M5)."""

    shares: bool = False  # show how often similar Úřad práce ads used each candidate
    by_name: bool = False  # answer with the occupation's name instead of its code
    english: bool = False  # instructions in English (the ad and the answer values stay Czech)
    # First the 2-digit occupation group, then the occupation within it (a second, short call).
    two_step: bool = False


DEFAULT_STYLE = PromptStyle()
SHARES = PromptStyle(shares=True)  # best for most models in the M5 prompt tests


@dataclass(frozen=True)
class ModelSpec:
    id: str
    name: str  # shown in the UI, e.g. "Qwen3.5 2B"
    repo: str
    revision: str
    weights: tuple[str, int, str]  # file name, size, sha256
    mmproj: tuple[str, int, str] | None  # vision projector (images), loaded with the model
    params_b: float  # active parameters, billions: drives the speed estimate
    # KV cache per 1k tokens of context, MB (from the model's layer count and head sizes).
    kv_mb_per_1k: float
    chat_kwargs: dict | None = None  # passed as chat_template_kwargs, e.g. no "thinking"
    prompt: PromptStyle = DEFAULT_STYLE

    def files(self) -> list[RemoteFile]:
        parts = [self.weights] + ([self.mmproj] if self.mmproj else [])
        return [
            RemoteFile(HF.format(repo=self.repo, revision=self.revision, name=n), n, size, sha)
            for n, size, sha in parts
        ]

    @property
    def download_bytes(self) -> int:
        return sum(f.size for f in self.files())

    @property
    def download_gb(self) -> float:
        return self.download_bytes / GB


NO_THINKING = {"enable_thinking": False}

MODELS: dict[str, ModelSpec] = {
    m.id: m
    for m in (
        ModelSpec(
            id="qwen3.5-0.8b",
            name="Qwen3.5 0.8B",
            repo="unsloth/Qwen3.5-0.8B-GGUF",
            revision="6ab461498e2023f6e3c1baea90a8f0fe38ab64d0",
            weights=(
                "Qwen3.5-0.8B-Q4_K_M.gguf",
                532517120,
                "bd258782e35f7f458f8aced1adc053e6e92e89bc735ba3be89d38a06121dc517",
            ),
            mmproj=(
                "mmproj-F16.gguf",
                204987232,
                "56e4c6cfe73b0c82e3e82bc518d7591997e61d81f723fc41a586f4fa69ea2453",
            ),
            params_b=0.8,
            kv_mb_per_1k=12,
            chat_kwargs=NO_THINKING,
        ),
        ModelSpec(
            id="qwen3.5-2b",
            name="Qwen3.5 2B",
            repo="unsloth/Qwen3.5-2B-GGUF",
            revision="f6d5376be1edb4d416d56da11e5397a961aca8ae",
            weights=(
                "Qwen3.5-2B-Q4_K_M.gguf",
                1280835840,
                "aaf42c8b7c3cab2bf3d69c355048d4a0ee9973d48f16c731c0520ee914699223",
            ),
            mmproj=(
                "mmproj-F16.gguf",
                668227264,
                "7035e9cb8d7c6a9681d07eef9a364783e86ea4cd73faab2eabb4f43a101830c7",
            ),
            params_b=1.9,
            kv_mb_per_1k=12,
            chat_kwargs=NO_THINKING,
        ),
        ModelSpec(
            id="qwen3.5-4b",
            name="Qwen3.5 4B",
            repo="unsloth/Qwen3.5-4B-GGUF",
            revision="e87f176479d0855a907a41277aca2f8ee7a09523",
            weights=(
                "Qwen3.5-4B-Q4_K_M.gguf",
                2740937888,
                "00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4",
            ),
            mmproj=(
                "mmproj-F16.gguf",
                672423616,
                "cd88edcf8d031894960bb0c9c5b9b7e1fea6ebee02b9f7ce925a00d12891f864",
            ),
            params_b=4.2,
            kv_mb_per_1k=32,
            chat_kwargs=NO_THINKING,
        ),
        ModelSpec(
            id="qwen3-vl-2b",
            name="Qwen3-VL 2B",
            repo="Qwen/Qwen3-VL-2B-Instruct-GGUF",
            revision="52d6c8ffea26cc873ac5ad116f8631268d7eb503",
            weights=(
                "Qwen3VL-2B-Instruct-Q4_K_M.gguf",
                1107409952,
                "089d75c52f4b7ffc56ba998ffc50aae89fcafc755f9e7208aacca281dca6c2ae",
            ),
            mmproj=(
                "mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf",
                445053216,
                "f9a68fabba69c3b81e153367b2c7521030b0fa8bb0de400c9599c8e6725f9c82",
            ),
            params_b=1.7,
            kv_mb_per_1k=112,
            prompt=PromptStyle(shares=True, by_name=True),
        ),
        ModelSpec(
            id="qwen3-vl-4b",
            name="Qwen3-VL 4B",
            repo="Qwen/Qwen3-VL-4B-Instruct-GGUF",
            revision="1cd86afb9a95c410a6038ab3b40d8b578c892266",
            weights=(
                "Qwen3VL-4B-Instruct-Q4_K_M.gguf",
                2497281664,
                "66358cb18bb6b3b1b6675aa412c7a88ef01d228f481184d13668e5201c730a0a",
            ),
            mmproj=(
                "mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf",
                453974304,
                "30ba2c7dd3127a4561b6cba9d13d0f711c91bdb38742e2f56d73c8cb596bd06d",
            ),
            params_b=4.0,
            kv_mb_per_1k=144,
            prompt=SHARES,
        ),
    )
}

TIER_ORDER = ("simple", "good", "excellent")
# Evals on 70 golden ads: docs/spikes.md.
TIERS: dict[str, str] = {
    "simple": "qwen3.5-0.8b",
    "good": "qwen3.5-2b",
    "excellent": "qwen3-vl-4b",
}


def tier_model(tier: str) -> ModelSpec:
    return MODELS[TIERS[tier]]
