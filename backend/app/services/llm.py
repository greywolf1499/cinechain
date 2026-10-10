"""Opt-in generative model: connection pitches and cryptic Blind Draft teasers.

One interface, four providers (`llm_provider` setting; `off` by default):
- `local_gguf`: Qwen3.5-0.8B-Instruct (Q4_K_M) through `llama-cpp-python`, if it is installed.
  The model is loaded just in time and *never kept resident while idle*: it is unloaded
  `llm_keep_alive_seconds` (default 5 minutes) after the last generation, or straight away
  when that is 0. It peaks at roughly 600 MB of RAM while generating.
- `ollama`: a local Ollama server (`POST {base}/api/chat`), e.g. model `qwen3.5:0.8b`.
- `openai`: any OpenAI-compatible `POST {base}/v1/chat/completions` endpoint.

Generation is a nicety, never a dependency: every failure is an `LlmUnavailable` that callers
turn into a friendly message, and nothing else in the app waits on it.
"""

from __future__ import annotations

import asyncio
import ctypes
import gc
import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import anyio.to_thread
import httpx
from pydantic import BaseModel, ValidationError
from sqlmodel import Session

from app.config import get_settings
from app.models.cache import CachedMovie
from app.services import settings_repo
from app.utils.dates import parse_release_year

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StructuredResult[Model: BaseModel]:
    value: Model
    source: Literal["ai", "template"]


PROVIDER_OFF = "off"
PROVIDER_LOCAL = "local_gguf"
PROVIDER_OLLAMA = "ollama"
PROVIDER_OPENAI = "openai"
PROVIDERS = (PROVIDER_OFF, PROVIDER_LOCAL, PROVIDER_OLLAMA, PROVIDER_OPENAI)

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen3.5:0.8b"
DEFAULT_OPENAI_URL = "http://localhost:1234"
DEFAULT_OPENAI_MODEL = "qwen3.5-0.8b"
# The vetted local model: fixed URL and destination so nobody has to find or place a GGUF.
LOCAL_MODEL_URL = (
    "https://huggingface.co/unsloth/Qwen3.5-0.8B-GGUF/resolve/main/Qwen3.5-0.8B-Q4_K_M.gguf"
)
LOCAL_MODEL_FILENAME = "qwen3.5-0.8b-instruct-q4_k_m.gguf"
LOCAL_MODEL_APPROX_BYTES = 532_517_120
_LEGACY_MODEL_RELATIVE = Path("models") / "qwen3.5-0.8b" / "Qwen3.5-0.8B-Q4_K_M.gguf"
REMOTE_TIMEOUT_SECONDS = 30.0
MAX_KEEP_ALIVE_SECONDS = 3600
CONTEXT_TOKENS = 2048
CACHE_SIZE = 512


class LlmUnavailable(Exception):
    """The generative model is off, missing, or failed."""


@dataclass(frozen=True)
class LlmConfig:
    provider: str = PROVIDER_OFF
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    keep_alive_seconds: int = 300

    @property
    def enabled(self) -> bool:
        return self.provider != PROVIDER_OFF

    @property
    def effective_model(self) -> str:
        if self.provider == PROVIDER_OLLAMA:
            return self.model or DEFAULT_OLLAMA_MODEL
        if self.provider == PROVIDER_OPENAI:
            return self.model or DEFAULT_OPENAI_MODEL
        if self.provider == PROVIDER_LOCAL:
            return LOCAL_MODEL_FILENAME
        return ""

    @property
    def fingerprint(self) -> str:
        return f"{self.provider}:{self.effective_model}"


def load_config(session: Session) -> LlmConfig:
    """The admin's saved generative-model settings, falling back to `.env`, then to off."""
    base = get_settings()
    overrides = settings_repo.get_overrides(session)
    provider = overrides.get("llm_provider") or base.llm_provider
    if provider not in PROVIDERS:
        provider = PROVIDER_OFF
    raw_keep_alive = overrides.get("llm_keep_alive_seconds")
    try:
        keep_alive = int(raw_keep_alive) if raw_keep_alive else base.llm_keep_alive_seconds
    except ValueError:
        keep_alive = base.llm_keep_alive_seconds
    return LlmConfig(
        provider=provider,
        base_url=overrides.get("llm_base_url") or base.llm_base_url,
        api_key=overrides.get("llm_api_key") or base.llm_api_key,
        model=overrides.get("llm_model") or base.llm_model,
        keep_alive_seconds=max(0, min(keep_alive, MAX_KEEP_ALIVE_SECONDS)),
    )


def local_runtime_available() -> bool:
    """Is `llama-cpp-python` importable (without loading anything)?"""
    import importlib.util

    return importlib.util.find_spec("llama_cpp") is not None


# --- local GGUF (JIT load, idle unload) ---

_local_lock = threading.RLock()
_download_lock = threading.Lock()
_local_model: Any = None
_local_last_used = 0.0
_unload_timer: threading.Timer | None = None


def _import_llama() -> Any:
    try:
        from llama_cpp import Llama
    except ImportError as exc:
        raise LlmUnavailable(
            "Local inference needs llama-cpp-python (`pip install llama-cpp-python`). "
            "Alternatively point CineChain at an Ollama or OpenAI-compatible server."
        ) from exc
    return Llama


def _release_memory() -> None:
    gc.collect()
    try:  # hand freed arenas back to the OS (glibc only)
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


def local_model_loaded() -> bool:
    return _local_model is not None


def unload_local() -> None:
    """Drops the resident model, if any, and returns its memory."""
    global _local_model, _unload_timer
    with _local_lock:
        if _unload_timer is not None:
            _unload_timer.cancel()
            _unload_timer = None
        model, _local_model = _local_model, None
        if model is None:
            return
        close = getattr(model, "close", None)
        if callable(close):
            close()
        del model
    _release_memory()
    logger.info("Local LLM unloaded")


def _unload_if_idle(idle_seconds: int) -> None:
    with _local_lock:
        if _local_model is not None and time.monotonic() - _local_last_used >= idle_seconds - 0.5:
            unload_local()


def _schedule_unload(keep_alive_seconds: int) -> None:
    global _unload_timer
    if keep_alive_seconds <= 0:
        unload_local()
        return
    if _unload_timer is not None:
        _unload_timer.cancel()
    _unload_timer = threading.Timer(keep_alive_seconds, _unload_if_idle, args=(keep_alive_seconds,))
    _unload_timer.daemon = True
    _unload_timer.start()


def local_model_path() -> Path:
    return get_settings().config_dir / "models" / LOCAL_MODEL_FILENAME


def local_model_status() -> dict[str, Any]:
    """`{"downloaded", "size_bytes", "path"}` for the fixed local GGUF."""
    path = local_model_path()
    legacy = get_settings().config_dir / _LEGACY_MODEL_RELATIVE
    if not path.exists() and legacy.exists():  # reuse a download from before the fixed path
        legacy.replace(path)
    exists = path.is_file()
    return {
        "downloaded": exists,
        "size_bytes": path.stat().st_size if exists else 0,
        "path": str(path),
    }


def _download(
    url: str, destination: Path, progress: Callable[[int, int], None] | None = None
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(destination.suffix + ".part")
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=600.0) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length") or LOCAL_MODEL_APPROX_BYTES)
            done = 0
            with tmp.open("wb") as handle:
                for chunk in response.iter_bytes(1024 * 256):
                    handle.write(chunk)
                    done += len(chunk)
                    if progress is not None:
                        progress(done, max(total, done))
        tmp.replace(destination)
    finally:
        tmp.unlink(missing_ok=True)


def ensure_model_file(
    config: LlmConfig | None = None, progress: Callable[[int, int], None] | None = None
) -> Path:
    """Blocking: the GGUF on disk, downloaded on first use. Run it in a worker thread."""
    with _download_lock:
        if local_model_status()["downloaded"]:
            return local_model_path()
        try:
            _download(LOCAL_MODEL_URL, local_model_path(), progress)
        except (httpx.HTTPError, OSError) as exc:
            raise LlmUnavailable(f"Could not fetch the local model: {exc}") from exc
    return local_model_path()


def _generate_local(config: LlmConfig, system: str, prompt: str, max_tokens: int) -> str:
    global _local_model, _local_last_used
    with _local_lock:
        try:
            if _local_model is None:
                llama = _import_llama()
                path = ensure_model_file(config)
                _local_model = llama(
                    model_path=str(path),
                    n_ctx=CONTEXT_TOKENS,
                    n_threads=max(1, min(4, os.cpu_count() or 1)),
                    n_gpu_layers=0,
                    use_mlock=False,
                    verbose=False,
                )
            reply = _local_model.create_chat_completion(
                messages=_messages(config, system, prompt),
                max_tokens=max_tokens,
                temperature=0.8,
                top_p=0.9,
            )
            text = reply["choices"][0]["message"]["content"]
        except LlmUnavailable:
            raise
        except Exception as exc:  # llama.cpp raises assorted types
            unload_local()
            raise LlmUnavailable(f"Local model failed: {exc}") from exc
        _local_last_used = time.monotonic()
        _schedule_unload(config.keep_alive_seconds)
    return text


# --- remote chat endpoints ---


def ollama_url(config: LlmConfig) -> str:
    base = (config.base_url or DEFAULT_OLLAMA_URL).rstrip("/")
    return base if base.endswith("/api/chat") else f"{base}/api/chat"


def openai_url(config: LlmConfig) -> str:
    base = (config.base_url or DEFAULT_OPENAI_URL).rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"


def _messages(config: LlmConfig, system: str, prompt: str) -> list[dict[str, str]]:
    # Qwen3.x "thinks" by default; the soft switch keeps a 0.8B model from burning its budget on it.
    suffix = " /no_think" if "qwen" in config.effective_model.lower() else ""
    return [{"role": "system", "content": system}, {"role": "user", "content": prompt + suffix}]


def _error_text(response: httpx.Response) -> str:
    try:
        error = response.json().get("error")
        if isinstance(error, dict):
            error = error.get("message")
        if error:
            return str(error)[:300]
    except (ValueError, AttributeError):
        pass
    return response.reason_phrase or "request failed"


async def _generate_remote(config: LlmConfig, system: str, prompt: str, max_tokens: int) -> str:
    headers = {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
    messages = _messages(config, system, prompt)
    async with httpx.AsyncClient(timeout=REMOTE_TIMEOUT_SECONDS) as client:
        if config.provider == PROVIDER_OLLAMA:
            response = await client.post(
                ollama_url(config),
                headers=headers,
                json={
                    "model": config.effective_model,
                    "messages": messages,
                    "stream": False,
                    "think": False,
                    "options": {"temperature": 0.8, "num_predict": max_tokens},
                },
            )
            response.raise_for_status()
            return response.json()["message"]["content"]
        response = await client.post(
            openai_url(config),
            headers=headers,
            json={
                "model": config.effective_model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.8,
            },
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]


_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def clean_output(text: str) -> str:
    """One tidy line: reasoning blocks, wrapping quotes and extra whitespace removed."""
    text = _THINK_BLOCK.sub("", text)
    text = text.split("</think>")[-1]  # an unbalanced block (the model ran out of tokens)
    text = " ".join(text.split()).strip().strip("\"'“”‘’ ")
    return text


async def generate(config: LlmConfig, system: str, prompt: str, max_tokens: int = 96) -> str:
    """One short completion. Raises `LlmUnavailable` for every failure (including `off`)."""
    if not config.enabled:
        raise LlmUnavailable(
            "The generative model is off - enable it under Settings > Integrations > AI & Embeddings."
        )
    try:
        if config.provider == PROVIDER_LOCAL:
            raw = await anyio.to_thread.run_sync(
                _generate_local, config, system, prompt, max_tokens
            )
        else:
            raw = await _generate_remote(config, system, prompt, max_tokens)
    except LlmUnavailable:
        raise
    except httpx.HTTPStatusError as exc:
        raise LlmUnavailable(
            f"{config.provider} chat failed (HTTP {exc.response.status_code}): "
            f"{_error_text(exc.response)}"
        ) from exc
    except (httpx.HTTPError, TimeoutError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise LlmUnavailable(f"{config.provider} chat failed: {exc or type(exc).__name__}") from exc
    text = clean_output(raw)
    if not text:
        raise LlmUnavailable("The model returned an empty answer")
    return text


_JSON_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


async def generate_structured[Model: BaseModel](
    config: LlmConfig,
    system: str,
    facts: Any,
    schema: type[Model],
    retries: int = 1,
    fallback: Model | dict[str, Any] | Callable[[], Model | dict[str, Any]] | None = None,
    validator: Callable[[Model], bool | None] | None = None,
) -> StructuredResult[Model]:
    """Generate and validate JSON from supplied facts, falling back deterministically.

    Prompts contain only the caller's facts and the JSON schema. Malformed or failed
    generations are retried once by default; failures and template output are never
    inserted into the text-generation cache.
    """
    prompt = (
        "Return one JSON object matching this schema. Use only the supplied facts; "
        "do not add facts or commentary.\n"
        f"Schema: {json.dumps(schema.model_json_schema(), ensure_ascii=False, sort_keys=True)}\n"
        f"Facts: {json.dumps(facts, ensure_ascii=False, sort_keys=True, default=str)}"
    )
    last_error: Exception | None = None
    for attempt in range(max(0, retries) + 1):
        try:
            raw = await generate(config, system, prompt, max_tokens=240)
            body = _THINK_BLOCK.sub("", raw).split("</think>")[-1].strip()
            body = _JSON_FENCE.sub("", body).strip()
            decoded = json.loads(body)
            value = schema.model_validate(decoded)
            if validator is not None and validator(value) is False:
                raise ValueError("Structured output failed its domain validator")
            return StructuredResult(value=value, source="ai")
        except (
            LlmUnavailable,
            json.JSONDecodeError,
            ValidationError,
            TypeError,
            ValueError,
        ) as exc:
            last_error = exc
            if attempt < max(0, retries):
                logger.info(
                    "Structured generation attempt %s failed validation: %s", attempt + 1, exc
                )
    if fallback is None:
        message = "No deterministic fallback was supplied for structured generation"
        if last_error is not None:
            message = f"{message}: {last_error}"
        raise LlmUnavailable(message) from last_error
    fallback_value = fallback() if callable(fallback) else fallback
    try:
        value = schema.model_validate(fallback_value)
        if validator is not None and validator(value) is False:
            raise ValueError("Structured fallback failed its domain validator")
    except (ValidationError, TypeError, ValueError) as exc:
        raise LlmUnavailable(f"Structured fallback did not match {schema.__name__}: {exc}") from exc
    return StructuredResult(value=value, source="template")


async def check_connection(config: LlmConfig) -> dict:
    """A tiny real generation for the Settings page: {ok, latency_ms, output, provider, model, detail}."""
    result: dict = {
        "ok": False,
        "latency_ms": None,
        "output": None,
        "provider": config.provider,
        "model": config.effective_model,
        "detail": None,
    }
    started = time.perf_counter()
    try:
        output = await generate(
            config,
            "You are a concise assistant.",
            "Reply with one short, upbeat sentence about movie night.",
            max_tokens=40,
        )
    except LlmUnavailable as exc:
        result["detail"] = str(exc)
        return result
    result.update(ok=True, output=output, latency_ms=round((time.perf_counter() - started) * 1000))
    return result


# --- prompts and cached high-level helpers ---

PITCH_SYSTEM = (
    "You are a witty cinephile friend. You explain, in exactly one punchy sentence of at most "
    "30 words, why one film is a great next watch after another. No preamble, no lists, "
    "no spoilers, no quotation marks."
)
CRITIC_SYSTEM = (
    "You are a witty, slightly merciless film critic advising a friend who may veto one film. "
    "In exactly one punchy sentence of at most 30 words, say why the next film might be an "
    "exhausting or difficult watch or hop after the previous one (length, pacing, mood whiplash, "
    "heaviness). No preamble, no lists, no spoilers, no quotation marks."
)
TEASER_SYSTEM = (
    "You write cryptic, spoiler-free teasers for a blind movie pick. Reply with exactly one "
    "evocative sentence of at most 25 words that captures the vibe only. Never name the film, "
    "its characters, actors, or director, and never reveal twists."
)

_cache: OrderedDict[str, str] = OrderedDict()


def _remember(key: str, value: str) -> str:
    _cache[key] = value
    _cache.move_to_end(key)
    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)
    return value


def clear_cache() -> None:
    _cache.clear()


def _blurb(movie: CachedMovie) -> str:
    year = parse_release_year(movie.release_date)
    head = f"{movie.title} ({year})" if year else movie.title
    plot = (movie.overview or "").strip()
    return f"{head}: {plot[:400]}" if plot else head


async def pitch(
    config: LlmConfig,
    previous: CachedMovie,
    candidate: CachedMovie,
    link: str | None = None,
    critic: bool = False,
) -> str:
    """Why `candidate` is a great next film after `previous`, in one cinephile sentence - or,
    with `critic`, why it might be an exhausting hop (Blind Fork veto advice)."""
    kind = "critic" if critic else "pitch"
    key = f"{config.fingerprint}|{kind}|{previous.tmdb_id}|{candidate.tmdb_id}|{link or ''}"
    if key in _cache:
        return _cache[key]
    system = CRITIC_SYSTEM if critic else PITCH_SYSTEM

    class Sentence(BaseModel):
        text: str

    facts = {
        "previous": _blurb(previous),
        "next": _blurb(candidate),
        "connection": link,
    }
    fallback = Sentence(
        text=(
            f"{candidate.title} follows {previous.title} through a "
            f"{'challenging' if critic else 'fresh'} change of tone."
        )
    )
    result = await generate_structured(
        config,
        system + " Return JSON with a single text field containing that sentence.",
        facts,
        Sentence,
        fallback=fallback,
        validator=lambda value: 0 < len(value.text.split()) <= 30,
    )
    return _remember(key, result.value.text) if result.source == "ai" else result.value.text


def mask_title(text: str, title: str) -> str:
    """Hides any accidental mention of the film's own title in a teaser."""
    if len(title) < 3:
        return text
    return re.sub(re.escape(title), "▒▒▒", text, flags=re.IGNORECASE)


async def teaser(config: LlmConfig, movie: CachedMovie) -> str:
    """A spoiler-free, title-free one-sentence vibe teaser for the Blind Draft."""
    key = f"{config.fingerprint}|teaser|{movie.tmdb_id}"
    if key in _cache:
        return _cache[key]

    class Teaser(BaseModel):
        text: str

    result = await generate_structured(
        config,
        TEASER_SYSTEM + " Return JSON with one text field. Do not include the film title.",
        {"movie": _blurb(movie)},
        Teaser,
        fallback=Teaser(
            text="A curious journey shifts between intimate choices and larger stakes."
        ),
        validator=lambda value: (
            0 < len(value.text.split()) <= 25
            and movie.title.casefold() not in value.text.casefold()
        ),
    )
    text = mask_title(result.value.text, movie.title)
    return _remember(key, text) if result.source == "ai" else text


TROPE_SYSTEM = (
    "You tag films for cinephiles. From a plot summary, extract 3 to 5 concise, normalized "
    "tropes or themes (for example heist, time-loop, cyberpunk, unreliable-narrator). "
    "Reply with ONLY a JSON object containing a tropes array of lowercase kebab-case strings."
)
MAX_TROPES = 5
MAX_TROPE_LENGTH = 40
_NON_SLUG = re.compile(r"[^a-z0-9]+")
_tropes_inflight: dict[str, asyncio.Future[list[str]]] = {}
_JSON_ARRAY = re.compile(r"\[.*?\]", re.DOTALL)


def normalize_trope(raw: object) -> str | None:
    """A trope as a kebab-case slug ("Time Loop!" -> "time-loop"); None if nothing usable."""
    if not isinstance(raw, str):
        return None
    slug = _NON_SLUG.sub("-", raw.lower()).strip("-")
    return slug if slug and len(slug) <= MAX_TROPE_LENGTH else None


def parse_tropes(text: str) -> list[str]:
    """Normalized, de-duplicated tropes from a model reply: a JSON array, else a loose list."""
    text = _THINK_BLOCK.sub("", text).split("</think>")[-1]
    items: Sequence[object] = []
    match = _JSON_ARRAY.search(text)
    if match:
        try:
            parsed = json.loads(match.group(0))
            items = parsed if isinstance(parsed, list) else []
        except ValueError:
            items = []
    if not items:  # small models sometimes answer with a bare or bulleted list
        items = re.split(r"[,\n;]", text.replace("[", "").replace("]", ""))
    tropes: list[str] = []
    for item in items:
        slug = normalize_trope(item)
        if slug and slug not in tropes:
            tropes.append(slug)
    return tropes[:MAX_TROPES]


def _env_config() -> LlmConfig:
    base = get_settings()
    provider = base.llm_provider if base.llm_provider in PROVIDERS else PROVIDER_OFF
    return LlmConfig(
        provider=provider,
        base_url=base.llm_base_url,
        api_key=base.llm_api_key,
        model=base.llm_model,
        keep_alive_seconds=base.llm_keep_alive_seconds,
    )


async def extract_tropes(overview: str, config: LlmConfig | None = None) -> list[str]:
    """3-5 kebab-case cinephile tropes/themes for a plot overview.

    `config` defaults to the `.env` settings; pass `load_config(session)` to honour the admin's
    saved ones. Returns `[]` when the model is off or there is no plot to read; raises
    `LlmUnavailable` when the model is on but fails (so callers don't cache a false "none").
    """
    config = config or _env_config()
    overview = (overview or "").strip()
    if not config.enabled or not overview:
        return []
    # Concurrent requests for the same plot (Pick Next + the modal) share one generation.
    key = f"{config.fingerprint}|{overview}"
    task = _tropes_inflight.get(key)
    if task is None:
        task = asyncio.ensure_future(_generate_tropes(config, overview))
        _tropes_inflight[key] = task
        task.add_done_callback(lambda _: _tropes_inflight.pop(key, None))
    return list(await asyncio.shield(task))


async def _generate_tropes(config: LlmConfig, overview: str) -> list[str]:
    class Tropes(BaseModel):
        tropes: list[str]

    result = await generate_structured(
        config,
        TROPE_SYSTEM,
        {"plot": overview[:800]},
        Tropes,
        fallback=Tropes(tropes=[]),
        validator=lambda value: (
            len(value.tropes) <= MAX_TROPES
            and all(normalize_trope(item) is not None for item in value.tropes)
        ),
    )
    tropes = list(
        dict.fromkeys(filter(None, (normalize_trope(item) for item in result.value.tropes)))
    )[:MAX_TROPES]
    if not tropes:
        raise LlmUnavailable("The model returned no usable tropes")
    return tropes


async def judge_trope(overview: str, definition: str, config: LlmConfig) -> bool:
    """Ask the configured model for a constrained yes/no trope check."""
    output = await generate(
        config,
        "Answer only yes or no. Judge whether the plot contains the described story concept.",
        f"Plot: {overview[:800]}\nConcept: {definition[:240]}\nDoes the plot contain this concept?",
        max_tokens=4,
    )
    match = re.match(r"^\s*(yes|no)\b", output.strip(), flags=re.IGNORECASE)
    if match is None:
        raise LlmUnavailable("The trope judge returned neither yes nor no")
    return match.group(1).lower() == "yes"


COMMENTARY_SYSTEM = (
    "You are a witty cinephile ring announcer calling a boxing match between two films. In "
    "exactly one punchy 'Tale of the Tape' sentence of at most 35 words, preview the "
    "head-to-head clash and name both films. No preamble, no lists, no spoilers, no quotation "
    "marks."
)


def _card_blurb(movie: dict[str, Any]) -> str:
    """A bracket film card ({title, release_year, runtime, overview, tagline}) as prompt text."""
    title = str(movie.get("title") or "Unknown film")
    year = movie.get("release_year")
    head = f"{title} ({year})" if year else title
    extras = [f"{movie['runtime']} min"] if movie.get("runtime") else []
    if movie.get("tagline"):
        extras.append(f"tagline: {str(movie['tagline'])[:120]}")
    plot = str(movie.get("overview") or "").strip()[:300]
    return f"{head}{' [' + '; '.join(extras) + ']' if extras else ''}{': ' + plot if plot else ''}"


async def generate_matchup_commentary(
    movie_a: dict[str, Any], movie_b: dict[str, Any], config: LlmConfig | None = None
) -> str:
    """One sentence of boxing-announcer hype for a March Madness matchup ("Tale of the Tape").

    `config` defaults to the `.env` settings; pass `load_config(session)` to honour the admin's
    saved ones. Returns `""` when the model is off; raises `LlmUnavailable` when it is on but fails
    (so callers never cache a failure)."""
    config = config or _env_config()
    if not config.enabled:
        return ""
    key = f"{config.fingerprint}|commentary|{_card_blurb(movie_a)}|{_card_blurb(movie_b)}"
    if key in _cache:
        return _cache[key]
    prompt = (
        f"In the left corner - {_card_blurb(movie_a)}\n"
        f"In the right corner - {_card_blurb(movie_b)}\n"
        "Announce the Tale of the Tape in one sentence."
    )
    return _remember(key, await generate(config, COMMENTARY_SYSTEM, prompt, max_tokens=90))
