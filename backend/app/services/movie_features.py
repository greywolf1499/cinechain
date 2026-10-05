"""JIT feature extraction for cached movies: poster colour, overview embedding and LLM tropes.

Both are computed lazily, persisted on `cached_movies`, and never recomputed
unless the source (poster / overview) changes. Failures are logged and leave the
column NULL, so engines treat the film as "unverified" instead of erroring.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

import anyio
from sqlmodel import Session

from app.models.cache import CachedMovie
from app.services import embeddings, llm
from app.services.aesthetic import extract_dominant_color
from app.services.tmdb import TMDBClient

logger = logging.getLogger(__name__)

POSTER_CONCURRENCY = 8
EMBED_BATCH_SIZE = 32
TROPE_CONCURRENCY = 2


async def ensure_dominant_color(
    session: Session, tmdb: TMDBClient, movie: CachedMovie
) -> str | None:
    """`movie.dominant_color`, computing and saving it first when missing."""
    if movie.dominant_color or not movie.poster_path:
        return movie.dominant_color
    data = await tmdb.get_poster_bytes(movie.poster_path)
    if data is None:
        return None
    color = await anyio.to_thread.run_sync(extract_dominant_color, data)
    if color is not None:
        movie.dominant_color = color
        session.add(movie)
        session.commit()
        session.refresh(movie)
    return color


async def ensure_dominant_colors(
    session: Session, tmdb: TMDBClient, movies: Iterable[CachedMovie]
) -> None:
    """Fill in missing colours for many films, downloading posters concurrently."""
    pending = [m for m in movies if not m.dominant_color and m.poster_path]
    if not pending:
        return
    gate = asyncio.Semaphore(POSTER_CONCURRENCY)

    async def fetch(movie: CachedMovie) -> tuple[CachedMovie, str | None]:
        async with gate:
            data = await tmdb.get_poster_bytes(movie.poster_path or "")
        if data is None:
            return movie, None
        return movie, await anyio.to_thread.run_sync(extract_dominant_color, data)

    for movie, color in await asyncio.gather(*(fetch(m) for m in pending)):
        if color is not None:
            movie.dominant_color = color
            session.add(movie)
    session.commit()


def _needs_embedding(
    movie: CachedMovie, config: embeddings.EmbeddingConfig, suspended: bool
) -> bool:
    """No vector yet, or one from a different model than the one in use. While the external
    provider is suspended the local fallback's vectors are good enough (re-embedding them
    would only burn CPU); once it is back they are upgraded."""
    if not (movie.overview or "").strip():
        return False
    if movie.overview_embedding is None:
        return True
    stored = embeddings.row_fingerprint(movie.overview_embedding_model)
    if stored == config.fingerprint:
        return False
    return not (suspended and stored == config.local_fingerprint)


async def ensure_embeddings(session: Session, movies: Iterable[CachedMovie]) -> bool:
    """Embed every film that lacks a vector from the configured provider, in as few
    batches as possible. An unreachable external provider falls back to the local model;
    False only when no model could be used at all (callers stay lenient)."""
    config = embeddings.load_config(session)
    suspended = embeddings.external_suspended(config)
    pending = [m for m in movies if _needs_embedding(m, config, suspended)]
    for start in range(0, len(pending), EMBED_BATCH_SIZE):
        batch = pending[start : start + EMBED_BATCH_SIZE]
        try:
            result = await embeddings.embed_batch(
                config, [(m.overview or "").strip() for m in batch]
            )
        except embeddings.EmbeddingUnavailable as exc:
            logger.warning("Semantic embeddings unavailable: %s", exc)
            return False
        for movie, vector in zip(batch, result.vectors, strict=True):
            movie.overview_embedding = embeddings.encode_embedding(vector)
            movie.overview_embedding_model = result.fingerprint
            session.add(movie)
        session.commit()
    return True


async def extract_and_store_tropes(
    session: Session, movie: CachedMovie, config: llm.LlmConfig
) -> list[str]:
    """Extract and persist `movie`'s tropes. [] when the model is off; raises `LlmUnavailable`
    when it fails, so a failure is never cached as "no tropes"."""
    tropes = await llm.extract_tropes(movie.overview or "", config)
    if tropes:
        movie.extracted_tropes = tropes
        session.add(movie)
        session.commit()
    return tropes


async def ensure_tropes(session: Session, movies: Iterable[CachedMovie]) -> None:
    """Fill `extracted_tropes` for films that lack them, when the generative model is on.
    A failing model is logged once and skips the rest: films stay NULL (never "no tropes")."""
    config = llm.load_config(session)
    if not config.enabled:
        return
    pending = list(
        {
            m.tmdb_id: m
            for m in movies
            if m.extracted_tropes is None and (m.overview or "").strip()
        }.values()
    )
    if not pending:
        return
    gate = asyncio.Semaphore(TROPE_CONCURRENCY)
    failed = False

    async def extract(movie: CachedMovie) -> tuple[CachedMovie, list[str]]:
        nonlocal failed
        async with gate:
            if failed:
                return movie, []
            try:
                return movie, await llm.extract_tropes(movie.overview or "", config)
            except llm.LlmUnavailable as exc:
                failed = True
                logger.warning("Trope extraction unavailable: %s", exc)
                return movie, []

    for movie, tropes in await asyncio.gather(*(extract(m) for m in pending)):
        if tropes:
            movie.extracted_tropes = tropes
            session.add(movie)
    session.commit()
