"""Models the app can download (PLAN §6.6). Every entry is pinned to a Hugging Face commit and
checked by SHA-256, so a model never changes under the user.

`TIERS` maps the three friendly tiers to models; the other entries are evaluation candidates
(`adawork eval`) and can be picked under "Pokročilé" (advanced).
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
        ModelSpec(
            id="gemma-4-e2b",
            name="Gemma 4 E2B",
            repo="ggml-org/gemma-4-E2B-it-GGUF",
            revision="b4243c156154b6dca9324415f8c7ccc098b4aed1",
            weights=(
                "gemma-4-E2B-it-Q4_0.gguf",
                2841481184,
                "8e30dff3ac4c8434c49a7036fa15564bdbb6044e42bf04550bf1a096ad7e6a52",
            ),
            mmproj=(
                "mmproj-gemma-4-E2B-it-Q8_0.gguf",
                557368064,
                "9406f99c16d68cda4f1f0552192dcc99021ea1fc6d2fd50b1dc3ccf30d04b292",
            ),
            params_b=2.0,
            kv_mb_per_1k=40,
        ),
        ModelSpec(
            id="gemma-4-e4b",
            name="Gemma 4 E4B",
            repo="ggml-org/gemma-4-E4B-it-GGUF",
            revision="b8093469224f83f5c38f691eb906c380e9e63114",
            weights=(
                "gemma-4-E4B-it-Q4_0.gguf",
                4590807392,
                "a555b900214b477d8880e7832e0b8925e139b0159640036b09fe472b6f2097f2",
            ),
            mmproj=(
                "mmproj-gemma-4-E4B-it-Q8_0.gguf",
                559874816,
                "197f49a93027f9843772bd24a6a9e0be2a32a788de5a3def330e9c585d86edd1",
            ),
            params_b=4.0,
            kv_mb_per_1k=56,
            prompt=SHARES,
        ),
        ModelSpec(
            id="qwen3-vl-8b",
            name="Qwen3-VL 8B",
            repo="Qwen/Qwen3-VL-8B-Instruct-GGUF",
            revision="f982a07559d4a2f6c8744d840bf6fccab30eea96",
            weights=(
                "Qwen3VL-8B-Instruct-Q4_K_M.gguf",
                5027784800,
                "67d1659bfe71b89d50b45a4ad1a9e5b997e5bb16ce5da66a6a6167abd569e9e2",
            ),
            mmproj=(
                "mmproj-Qwen3VL-8B-Instruct-Q8_0.gguf",
                752289728,
                "c6ba85508d82f42590e6eb77d5340369ab6fecf107a7561d809523d8aa5f3bfd",
            ),
            params_b=8.2,
            kv_mb_per_1k=144,
            prompt=SHARES,
        ),
        ModelSpec(
            id="qwen3.5-9b",
            name="Qwen3.5 9B",
            repo="unsloth/Qwen3.5-9B-GGUF",
            revision="3885219b6810b007914f3a7950a8d1b469d598a5",
            weights=(
                "Qwen3.5-9B-Q4_K_M.gguf",
                5680522464,
                "03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8",
            ),
            mmproj=(
                "mmproj-F16.gguf",
                918166080,
                "f70dc3509053962b0d0d3ee8a7eacebf5d60aa560cad78254ae8698516ae029f",
            ),
            params_b=9.0,
            kv_mb_per_1k=32,
            chat_kwargs=NO_THINKING,
            prompt=SHARES,
        ),
        ModelSpec(
            id="ministral-3-8b",
            name="Ministral 3 8B",
            repo="mistralai/Ministral-3-8B-Instruct-2512-GGUF",
            revision="0102285ad796bd99af90f58de616092e5630e970",
            weights=(
                "Ministral-3-8B-Instruct-2512-Q4_K_M.gguf",
                5198911904,
                "33e7a72cf5e6e2cfc2f2847075acc013d68bba023e35310cef86b5cf8fdca761",
            ),
            mmproj=(
                "Ministral-3-8B-Instruct-2512-BF16-mmproj.gguf",
                858283168,
                "e799380f596d152ff4026a72d409ecc89c96cd437676804bc3f2ab3bd6b486ec",
            ),
            params_b=8.4,
            kv_mb_per_1k=136,
            prompt=SHARES,
        ),
        ModelSpec(
            id="gemma-4-12b",
            name="Gemma 4 12B",
            repo="google/gemma-4-12B-it-qat-q4_0-gguf",
            revision="29d097773436b69ff9feafd636ab4cf873786537",
            weights=(
                "gemma-4-12b-it-qat-q4_0.gguf",
                6975879296,
                "93567e57a8fe10b23569b9d9ec38cd005deedf71e29477c421a4b83f418a538b",
            ),
            mmproj=(
                "mmproj-gemma-4-12b-it-qat-q4_0.gguf",
                175115616,
                "cb018338a7538a9814d994bfe54644c71eb7ed54e31eae2f721e45fd3c260da7",
            ),
            params_b=12.0,
            kv_mb_per_1k=96,
            prompt=SHARES,
        ),
        ModelSpec(
            id="ministral-3-14b",
            name="Ministral 3 14B",
            repo="mistralai/Ministral-3-14B-Instruct-2512-GGUF",
            revision="74fac473c43357d7fb2671713608183cc72496d0",
            weights=(
                "Ministral-3-14B-Instruct-2512-Q4_K_M.gguf",
                8239593024,
                "824e0f3373e69b84f2cae46fdcb9bd1ebc6ab3bfc7acc125d818b7b8178cc613",
            ),
            mmproj=(
                "Ministral-3-14B-Instruct-2512-BF16-mmproj.gguf",
                879258784,
                "ab7616148b7081030fd38e410c9213ce8346a8b0588291c8ca279058e78cfae7",
            ),
            params_b=13.5,
            kv_mb_per_1k=160,
            prompt=SHARES,
        ),
        ModelSpec(
            id="qwen3-14b",
            name="Qwen3 14B (text)",
            repo="Qwen/Qwen3-14B-GGUF",
            revision="530227a7d994db8eca5ab5ced2fb692b614357fd",
            weights=(
                "Qwen3-14B-Q4_K_M.gguf",
                9001752960,
                "500a8806e85ee9c83f3ae08420295592451379b4f8cf2d0f41c15dffeb6b81f0",
            ),
            mmproj=None,
            params_b=14.8,
            kv_mb_per_1k=160,
            chat_kwargs=NO_THINKING,
            prompt=SHARES,
        ),
    )
}

TIER_ORDER = ("simple", "good", "excellent")
# Chosen by the M5 eval on 70 golden ads (docs/spikes.md): Qwen3-VL 2B is the smallest and
# fastest with good salary and occupation results; Gemma 4 E2B is as fast, reads places and
# occupation groups best and fills more fields, but needs ~1.5 GB more RAM; Qwen3-VL 4B is the
# most accurate on details at twice the time. The Qwen3.5 models guessed fields the ads did
# not state; Gemma 4 E4B was not better than E2B.
TIERS: dict[str, str] = {
    "simple": "qwen3-vl-2b",
    "good": "gemma-4-e2b",
    "excellent": "qwen3-vl-4b",
}


def tier_model(tier: str) -> ModelSpec:
    return MODELS[TIERS[tier]]
