"""Sentence embeddings with multilingual-e5-small (ONNX, int8, ~120 MB, MIT licence).

Used to find ISCO candidates for an ad (PLAN §6.1) and, later, for semantic search. Runs on
the CPU in onnxruntime without llama-server, so it works before any AI model is downloaded.
"""

import logging
import threading
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from adawork.ai.download import RemoteFile

log = logging.getLogger(__name__)

REPO = "https://huggingface.co/intfloat/multilingual-e5-small/resolve/614241f622f53c4eeff9890bdc4f31cfecc418b3/onnx/"
FILES = (
    RemoteFile(
        REPO + "model_qint8_avx512_vnni.onnx",
        "e5-small-int8.onnx",
        118346824,
        "dd476dd0c2514e9b9be83aeb3853fac0763e0bdf4a71645407587d77c48a2d88",
    ),
    RemoteFile(
        REPO + "tokenizer.json",
        "e5-tokenizer.json",
        17082730,
        "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39",
    ),
)
DIM = 384
MAX_TOKENS = 128


class Embedder:
    def __init__(self, model_dir: Path, threads: int = 2) -> None:
        self.model_dir = model_dir
        self.threads = threads
        self._session = None
        self._tokenizer = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return all((self.model_dir / f.name).exists() for f in FILES)

    def _load(self) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        options = ort.SessionOptions()
        options.intra_op_num_threads = self.threads
        options.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(self.model_dir / FILES[0].name), options, providers=["CPUExecutionProvider"]
        )
        tokenizer = Tokenizer.from_file(str(self.model_dir / FILES[1].name))
        tokenizer.enable_truncation(MAX_TOKENS)
        tokenizer.enable_padding()
        self._tokenizer = tokenizer
        self._inputs = {i.name for i in self._session.get_inputs()}

    def unload(self) -> None:
        with self._lock:
            self._session = self._tokenizer = None

    def embed(self, texts: Sequence[str], prefix: str = "query: ", batch: int = 64) -> np.ndarray:
        """Unit-length vectors, shape (len(texts), 384). e5 wants "query: " for short search
        texts and "passage: " for documents."""
        with self._lock:
            if self._session is None:
                self._load()
            session, tokenizer = self._session, self._tokenizer
            assert session is not None and tokenizer is not None
            out = np.zeros((len(texts), DIM), dtype=np.float32)
            for start in range(0, len(texts), batch):
                chunk = [prefix + t for t in texts[start : start + batch]]
                encoded = tokenizer.encode_batch(chunk)
                ids = np.array([e.ids for e in encoded], dtype=np.int64)
                mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
                feed = {"input_ids": ids, "attention_mask": mask}
                if "token_type_ids" in self._inputs:
                    feed["token_type_ids"] = np.zeros_like(ids)
                hidden = np.asarray(session.run(None, feed)[0])  # (batch, tokens, 384)
                summed = (hidden * mask[..., None]).sum(axis=1)
                vectors = summed / np.maximum(mask.sum(axis=1, keepdims=True), 1)
                norms = np.linalg.norm(vectors, axis=1, keepdims=True)
                out[start : start + len(chunk)] = vectors / np.maximum(norms, 1e-9)
            return out
