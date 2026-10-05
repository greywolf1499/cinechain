"""Text embeddings for the Semantic Trope Web, behind one pluggable provider interface.

Providers (`embedding_provider` setting):
- `local_onnx` (default): a small INT8 model on ONNX Runtime, no PyTorch, chosen from
  `LOCAL_PRESETS` (Snowflake Arctic-Embed-XS by default, multilingual-e5-small for world cinema,
  all-MiniLM-L6-v2 as the legacy preset). All presets emit 384-d vectors. The model is
  downloaded once into `config_dir/models/<preset>`, loaded only for the duration of one batch,
  then dropped and garbage-collected so the process stays under its RAM ceiling between requests.
- `ollama`: a local Ollama server (`POST {base}/api/embeddings`, one prompt per call).
- `openai`: any OpenAI-compatible `POST {base}/v1/embeddings` endpoint with an API key.

`embed_batch` is the entry point: an external provider that errors or exceeds
`EXTERNAL_TIMEOUT_SECONDS` falls back to the local model (and is skipped for a minute,
so a dead server costs one timeout, not one per request). Vectors from different
models aren't comparable, so each stored vector carries the `fingerprint` that made it.
"""

from __future__ import annotations

import asyncio
import ctypes
import gc
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import anyio.to_thread
import httpx
import numpy as np
from sqlmodel import Session

from app.config import get_settings
from app.services import settings_repo

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 384  # the local ONNX model's width
PROVIDER_LOCAL = "local_onnx"
PROVIDER_OLLAMA = "ollama"
PROVIDER_OPENAI = "openai"
PROVIDERS = (PROVIDER_LOCAL, PROVIDER_OLLAMA, PROVIDER_OPENAI)
LOCAL_MODEL_NAME = "all-MiniLM-L6-v2"
# What vectors stored before presets existed (a NULL model column) were made with.
LOCAL_FINGERPRINT = f"{PROVIDER_LOCAL}:{LOCAL_MODEL_NAME}"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "nomic-embed-text"
DEFAULT_OPENAI_URL = "https://api.openai.com"
DEFAULT_OPENAI_MODEL = "text-embedding-3-small"
# Cap on any external call: past it we fall back to the local model.
EXTERNAL_TIMEOUT_SECONDS = 5.0
EXTERNAL_CONCURRENCY = 4
SUSPEND_SECONDS = 60.0
MAX_TOKENS = 256
MODEL_FILE = "model_quantized.onnx"
TOKENIZER_FILE = "tokenizer.json"


@dataclass(frozen=True)
class LocalPreset:
    """One selectable on-device embedding model. All of them are 384-d, matching the float32
    blob schema, so switching preset only means re-embedding (the fingerprint differs)."""

    key: str
    model_name: str  # the fingerprint's model part
    hf_repo: str
    label: str
    badge: str
    description: str
    size_mb: int  # model + tokenizer, as downloaded
    model_url: str
    tokenizer_url: str
    pooling: str  # "mean" | "cls"
    text_prefix: str = ""  # prepended to every text (e5 wants "query: " for symmetric tasks)
    recommended: bool = False
    # Retrieval-tuned models are anisotropic: unrelated plots already score ~0.75-0.8 cosine.
    # `similarity_floor` is that "unrelated" level; `normalize_similarity` rescales above it so
    # one threshold means the same thing for every preset.
    similarity_floor: float = 0.0

    @property
    def directory(self) -> Path:
        return get_settings().config_dir / "models" / self.model_name

    @property
    def fingerprint(self) -> str:
        return f"{PROVIDER_LOCAL}:{self.model_name}"

    @property
    def downloaded(self) -> bool:
        return (self.directory / MODEL_FILE).exists() and (self.directory / TOKENIZER_FILE).exists()


def _hf(repo: str, path: str) -> str:
    return f"https://huggingface.co/{repo}/resolve/main/{path}"


LOCAL_PRESETS: dict[str, LocalPreset] = {
    preset.key: preset
    for preset in (
        LocalPreset(
            key="arctic-embed-xs",
            model_name="arctic-embed-xs",
            hf_repo="Snowflake/snowflake-arctic-embed-xs",
            label="Arctic-Embed XS",
            badge="[~24MB] High-Precision Retrieval",
            description=(
                "Snowflake's retrieval-tuned model: the sharpest plot matching for its size. "
                "English-first."
            ),
            size_mb=24,
            model_url=_hf("Snowflake/snowflake-arctic-embed-xs", "onnx/model_int8.onnx"),
            tokenizer_url=_hf("Snowflake/snowflake-arctic-embed-xs", "tokenizer.json"),
            pooling="cls",
            recommended=True,
            similarity_floor=0.78,
        ),
        LocalPreset(
            key="multilingual-e5-small",
            model_name="multilingual-e5-small",
            hf_repo="intfloat/multilingual-e5-small",
            label="Multilingual E5 Small",
            badge="[~135MB] 100+ Languages",
            description=(
                "The World Cinema preset: understands overviews in 100+ languages, so "
                "non-English plots match properly. Bigger download."
            ),
            size_mb=135,
            model_url=_hf("Xenova/multilingual-e5-small", "onnx/model_quantized.onnx"),
            tokenizer_url=_hf("Xenova/multilingual-e5-small", "tokenizer.json"),
            pooling="mean",
            text_prefix="query: ",
            similarity_floor=0.74,
        ),
        LocalPreset(
            key="all-minilm-l6-v2",
            model_name=LOCAL_MODEL_NAME,
            hf_repo="sentence-transformers/all-MiniLM-L6-v2",
            label="MiniLM L6 v2",
            badge="[~23MB] Legacy",
            description="The original general-purpose model; keeps vectors from older installs valid.",
            size_mb=23,
            model_url=_hf("Xenova/all-MiniLM-L6-v2", "onnx/model_quantized.onnx"),
            tokenizer_url=_hf("Xenova/all-MiniLM-L6-v2", "tokenizer.json"),
            pooling="mean",
        ),
    )
}
DEFAULT_LOCAL_PRESET = "arctic-embed-xs"


def get_preset(key: str | None) -> LocalPreset:
    return LOCAL_PRESETS.get(key or "", LOCAL_PRESETS[DEFAULT_LOCAL_PRESET])


# One model in memory at a time, however many requests ask for embeddings.
_inference_lock = threading.Lock()
_download_lock = threading.Lock()


class EmbeddingUnavailable(Exception):
    """The model can't be fetched or run (offline, disk full, corrupt file)."""


def model_paths(preset: LocalPreset | None = None) -> tuple[Path, Path]:
    directory = (preset or get_preset(None)).directory
    return directory / MODEL_FILE, directory / TOKENIZER_FILE


def _download(url: str, destination: Path) -> None:
    tmp = destination.with_suffix(destination.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=120.0) as response:
        response.raise_for_status()
        with tmp.open("wb") as handle:
            for chunk in response.iter_bytes():
                handle.write(chunk)
    tmp.replace(destination)


def ensure_model_files(preset: LocalPreset | None = None) -> tuple[Path, Path]:
    """Blocking: fetch the preset's model + tokenizer on first use. Run it in a worker thread."""
    preset = preset or get_preset(None)
    model_path, tokenizer_path = model_paths(preset)
    with _download_lock:
        try:
            model_path.parent.mkdir(parents=True, exist_ok=True)
            if not tokenizer_path.exists():
                _download(preset.tokenizer_url, tokenizer_path)
            if not model_path.exists():
                _download(preset.model_url, model_path)
        except (httpx.HTTPError, OSError) as exc:
            raise EmbeddingUnavailable(f"Could not fetch the embedding model: {exc}") from exc
    return model_path, tokenizer_path


def _release_memory() -> None:
    gc.collect()
    try:  # hand freed arenas back to the OS (glibc only)
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


def embed_texts(texts: list[str], preset: LocalPreset | None = None) -> list[np.ndarray]:
    """Unit-length 384-d float32 embeddings, one per text. Blocking and CPU-bound:
    call from a worker thread. The model lives only for the duration of the call."""
    if not texts:
        return []
    preset = preset or get_preset(None)
    model_path, tokenizer_path = ensure_model_files(preset)
    with _inference_lock:
        session = None
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            tokenizer = Tokenizer.from_file(str(tokenizer_path))
            tokenizer.enable_truncation(max_length=MAX_TOKENS)
            # pad to the longest text in the batch, with the tokenizer's own pad token
            pad_token = next(
                (t for t in ("[PAD]", "<pad>") if tokenizer.token_to_id(t) is not None), "[PAD]"
            )
            tokenizer.enable_padding(
                pad_id=tokenizer.token_to_id(pad_token) or 0, pad_token=pad_token
            )

            options = ort.SessionOptions()
            options.intra_op_num_threads = 1
            options.enable_cpu_mem_arena = False
            options.enable_mem_pattern = False
            session = ort.InferenceSession(
                str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
            )

            encodings = tokenizer.encode_batch([preset.text_prefix + text for text in texts])
            feeds = {
                "input_ids": np.array([e.ids for e in encodings], dtype=np.int64),
                "attention_mask": np.array([e.attention_mask for e in encodings], dtype=np.int64),
                "token_type_ids": np.array([e.type_ids for e in encodings], dtype=np.int64),
            }
            wanted = {i.name for i in session.get_inputs()}
            hidden = session.run(None, {k: v for k, v in feeds.items() if k in wanted})[0]

            if preset.pooling == "cls":
                pooled = hidden[:, 0]
            else:
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
    if not blob or len(blob) % 4 != 0:
        return None
    return np.frombuffer(blob, dtype="<f4")


def normalize_similarity(cosine: float, fingerprint: str) -> float:
    """Cosine rescaled so a local preset's "unrelated" floor reads as 0 and identical as 1;
    other models (legacy MiniLM, Ollama, OpenAI) are used as they are."""
    floor = next(
        (p.similarity_floor for p in LOCAL_PRESETS.values() if p.fingerprint == fingerprint), 0.0
    )
    if floor <= 0.0:
        return cosine
    return (cosine - floor) / (1.0 - floor)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        return 0.0
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denominator) if denominator else 0.0


# --- pluggable providers ---


@dataclass(frozen=True)
class EmbeddingConfig:
    provider: str = PROVIDER_LOCAL
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    # Which on-device model the `local_onnx` provider (and every fallback to it) uses.
    local_preset: str = DEFAULT_LOCAL_PRESET

    @property
    def local(self) -> LocalPreset:
        return get_preset(self.local_preset)

    @property
    def local_fingerprint(self) -> str:
        return self.local.fingerprint

    @property
    def external(self) -> bool:
        return self.provider in (PROVIDER_OLLAMA, PROVIDER_OPENAI)

    @property
    def effective_model(self) -> str:
        if self.provider == PROVIDER_OLLAMA:
            return self.model or DEFAULT_OLLAMA_MODEL
        if self.provider == PROVIDER_OPENAI:
            return self.model or DEFAULT_OPENAI_MODEL
        return self.local.model_name

    @property
    def fingerprint(self) -> str:
        return f"{self.provider}:{self.effective_model}"


@dataclass
class EmbeddingBatch:
    vectors: list[np.ndarray]
    fingerprint: str  # the model that actually produced them (the local one after a fallback)
    fell_back: bool = False


def load_config(session: Session) -> EmbeddingConfig:
    """The admin's saved provider settings, falling back to `.env` and then local ONNX."""
    base = get_settings()
    overrides = settings_repo.get_overrides(session)
    provider = overrides.get("embedding_provider") or base.embedding_provider
    if provider not in PROVIDERS:
        provider = PROVIDER_LOCAL
    return EmbeddingConfig(
        provider=provider,
        base_url=overrides.get("embedding_base_url") or base.embedding_base_url,
        api_key=overrides.get("embedding_api_key") or base.embedding_api_key,
        model=overrides.get("embedding_model") or base.embedding_model,
        local_preset=get_preset(
            overrides.get("embedding_local_preset") or base.embedding_local_preset
        ).key,
    )


def row_fingerprint(stored: str | None) -> str:
    return stored or LOCAL_FINGERPRINT


def ollama_url(config: EmbeddingConfig) -> str:
    base = (config.base_url or DEFAULT_OLLAMA_URL).rstrip("/")
    return base if base.endswith("/api/embeddings") else f"{base}/api/embeddings"


def openai_url(config: EmbeddingConfig) -> str:
    base = (config.base_url or DEFAULT_OPENAI_URL).rstrip("/")
    if base.endswith("/embeddings"):
        return base
    return f"{base}/embeddings" if base.endswith("/v1") else f"{base}/v1/embeddings"


def _unit(vector: list[float]) -> np.ndarray:
    array = np.asarray(vector, dtype=np.float32)
    if array.ndim != 1 or array.size == 0 or not np.all(np.isfinite(array)):
        raise EmbeddingUnavailable("The provider returned a malformed embedding")
    return array / max(float(np.linalg.norm(array)), 1e-12)


async def _embed_ollama(config: EmbeddingConfig, texts: list[str]) -> list[np.ndarray]:
    url = ollama_url(config)
    gate = asyncio.Semaphore(EXTERNAL_CONCURRENCY)

    async def one(client: httpx.AsyncClient, text: str) -> np.ndarray:
        async with gate:
            response = await client.post(
                url, json={"model": config.effective_model, "prompt": text}
            )
            response.raise_for_status()
            return _unit(response.json()["embedding"])

    async with httpx.AsyncClient(timeout=EXTERNAL_TIMEOUT_SECONDS) as client:
        return list(await asyncio.gather(*(one(client, text) for text in texts)))


async def _embed_openai(config: EmbeddingConfig, texts: list[str]) -> list[np.ndarray]:
    headers = {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
    async with httpx.AsyncClient(timeout=EXTERNAL_TIMEOUT_SECONDS) as client:
        response = await client.post(
            openai_url(config),
            headers=headers,
            json={"model": config.effective_model, "input": texts},
        )
        response.raise_for_status()
        items = sorted(response.json()["data"], key=lambda item: item["index"])
    if len(items) != len(texts):
        raise EmbeddingUnavailable("The provider returned the wrong number of embeddings")
    return [_unit(item["embedding"]) for item in items]


def _error_text(response: httpx.Response) -> str:
    """The provider's own explanation (Ollama: {"error": "..."}, OpenAI: {"error": {"message"}})."""
    try:
        error = response.json().get("error")
        if isinstance(error, dict):
            error = error.get("message")
        if error:
            return str(error)[:300]
    except (ValueError, AttributeError):
        pass
    return response.reason_phrase or "request failed"


async def embed_external(config: EmbeddingConfig, texts: list[str]) -> list[np.ndarray]:
    """One batch through the configured external provider, capped at `EXTERNAL_TIMEOUT_SECONDS`.
    Every failure mode (HTTP error, timeout, bad JSON, bad shape) becomes `EmbeddingUnavailable`."""
    call = _embed_ollama if config.provider == PROVIDER_OLLAMA else _embed_openai
    try:
        async with asyncio.timeout(EXTERNAL_TIMEOUT_SECONDS):
            return await call(config, texts)
    except EmbeddingUnavailable:
        raise
    except httpx.HTTPStatusError as exc:
        raise EmbeddingUnavailable(
            f"{config.provider} embeddings failed (HTTP {exc.response.status_code}): "
            f"{_error_text(exc.response)}"
        ) from exc
    except Exception as exc:  # a bad provider must never break a request
        raise EmbeddingUnavailable(
            f"{config.provider} embeddings failed: {exc or type(exc).__name__}"
        ) from exc


# External providers that just failed: fingerprint+URL -> monotonic time to retry after.
_suspended_until: dict[str, float] = {}


def _suspension_key(config: EmbeddingConfig) -> str:
    return f"{config.fingerprint}@{config.base_url}"


def external_suspended(config: EmbeddingConfig) -> bool:
    return config.external and _suspended_until.get(_suspension_key(config), 0.0) > time.monotonic()


def reset_suspension() -> None:
    _suspended_until.clear()


async def embed_batch(config: EmbeddingConfig, texts: list[str]) -> EmbeddingBatch:
    """Embeds `texts` with the configured provider, or with local ONNX if it is unavailable.
    Raises `EmbeddingUnavailable` only when the local model can't run either."""
    if config.external and not external_suspended(config):
        try:
            return EmbeddingBatch(await embed_external(config, texts), config.fingerprint)
        except EmbeddingUnavailable as exc:
            _suspended_until[_suspension_key(config)] = time.monotonic() + SUSPEND_SECONDS
            logger.warning("%s; using the local model for now", exc)
            vectors = await anyio.to_thread.run_sync(embed_texts, texts, config.local)
            return EmbeddingBatch(vectors, config.local_fingerprint, fell_back=True)
    vectors = await anyio.to_thread.run_sync(embed_texts, texts, config.local)
    return EmbeddingBatch(vectors, config.local_fingerprint, fell_back=config.external)


async def check_connection(config: EmbeddingConfig) -> dict:
    """Embeds a dummy sentence with `config` (no fallback, no suspension) for the Settings page:
    {ok, latency_ms, dimension, provider, model, detail}."""
    result: dict = {
        "ok": False,
        "latency_ms": None,
        "dimension": None,
        "provider": config.provider,
        "model": config.effective_model,
        "detail": None,
    }
    started = time.perf_counter()
    try:
        if config.external:
            vectors = await embed_external(config, ["CineChain connection test"])
        else:
            vectors = await anyio.to_thread.run_sync(
                embed_texts, ["CineChain connection test"], config.local
            )
    except EmbeddingUnavailable as exc:
        result["detail"] = str(exc)
        return result
    result.update(
        ok=True,
        latency_ms=round((time.perf_counter() - started) * 1000),
        dimension=int(vectors[0].shape[0]),
    )
    return result
