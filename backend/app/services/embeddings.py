"""JIT text embeddings for the Semantic Trope Web (all-MiniLM-L6-v2, ONNX, no PyTorch).

The quantized model (~23 MB) is downloaded once into `config_dir/models`, loaded
only for the duration of one batch, then dropped and garbage-collected so the
process stays under its RAM ceiling between requests.
"""

from __future__ import annotations

import ctypes
import gc
import logging
import threading
from pathlib import Path

import httpx
import numpy as np

from app.config import get_settings

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 384
MAX_TOKENS = 256
MODEL_FILE = "model_quantized.onnx"
TOKENIZER_FILE = "tokenizer.json"

# One model in memory at a time, however many requests ask for embeddings.
_inference_lock = threading.Lock()
_download_lock = threading.Lock()


class EmbeddingUnavailable(Exception):
    """The model can't be fetched or run (offline, disk full, corrupt file)."""


def model_paths() -> tuple[Path, Path]:
    directory = get_settings().onnx_model_dir
    return directory / MODEL_FILE, directory / TOKENIZER_FILE


def _download(url: str, destination: Path) -> None:
    tmp = destination.with_suffix(destination.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=120.0) as response:
        response.raise_for_status()
        with tmp.open("wb") as handle:
            for chunk in response.iter_bytes():
                handle.write(chunk)
    tmp.replace(destination)


def ensure_model_files() -> tuple[Path, Path]:
    """Blocking: fetch the model + tokenizer on first use. Run it in a worker thread."""
    model_path, tokenizer_path = model_paths()
    settings = get_settings()
    with _download_lock:
        try:
            model_path.parent.mkdir(parents=True, exist_ok=True)
            if not tokenizer_path.exists():
                _download(settings.onnx_tokenizer_url, tokenizer_path)
            if not model_path.exists():
                _download(settings.onnx_model_url, model_path)
        except (httpx.HTTPError, OSError) as exc:
            raise EmbeddingUnavailable(f"Could not fetch the embedding model: {exc}") from exc
    return model_path, tokenizer_path


def _release_memory() -> None:
    gc.collect()
    try:  # hand freed arenas back to the OS (glibc only)
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


def embed_texts(texts: list[str]) -> list[np.ndarray]:
    """Unit-length 384-d float32 embeddings, one per text. Blocking and CPU-bound:
    call from a worker thread. The model lives only for the duration of the call."""
    if not texts:
        return []
    model_path, tokenizer_path = ensure_model_files()
    with _inference_lock:
        session = None
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            tokenizer = Tokenizer.from_file(str(tokenizer_path))
            tokenizer.enable_truncation(max_length=MAX_TOKENS)
            tokenizer.enable_padding()  # pad to the longest text in the batch

            options = ort.SessionOptions()
            options.intra_op_num_threads = 1
            options.enable_cpu_mem_arena = False
            options.enable_mem_pattern = False
            session = ort.InferenceSession(
                str(model_path), sess_options=options, providers=["CPUExecutionProvider"])

            encodings = tokenizer.encode_batch(texts)
            feeds = {
                "input_ids": np.array([e.ids for e in encodings], dtype=np.int64),
                "attention_mask": np.array([e.attention_mask for e in encodings], dtype=np.int64),
                "token_type_ids": np.array([e.type_ids for e in encodings], dtype=np.int64),
            }
            wanted = {i.name for i in session.get_inputs()}
            hidden = session.run(None, {k: v for k, v in feeds.items() if k in wanted})[0]

            mask = feeds["attention_mask"][..., None].astype(np.float32)
            pooled = (hidden * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1e-9)
            norms = np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)
            return [row.astype(np.float32) for row in pooled / norms]
        except EmbeddingUnavailable:
            raise
        except Exception as exc:  # onnxruntime raises assorted types
            raise EmbeddingUnavailable(f"Embedding model failed: {exc}") from exc
        finally:
            del session
            _release_memory()


def encode_embedding(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype="<f4").tobytes()


def decode_embedding(blob: bytes | None) -> np.ndarray | None:
    if not blob or len(blob) != EMBEDDING_DIM * 4:
        return None
    return np.frombuffer(blob, dtype="<f4")


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denominator) if denominator else 0.0
