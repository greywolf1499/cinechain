"""JIT feature extraction for cached movies: poster colour and overview embedding.

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
from app.services import embeddings
from app.services.aesthetic import extract_dominant_color
from app.services.tmdb import TMDBClient

logger = logging.getLogger(__name__)

POSTER_CONCURRENCY = 8
EMBED_BATCH_SIZE = 32


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


async def ensure_embeddings(session: Session, movies: Iterable[CachedMovie]) -> bool:
    """Embed every film that has an overview but no embedding yet, in as few model
    loads as possible. False when the model couldn't be used (callers stay lenient)."""
    pending = [m for m in movies if m.overview_embedding is None and (m.overview or "").strip()]
    for start in range(0, len(pending), EMBED_BATCH_SIZE):
        batch = pending[start: start + EMBED_BATCH_SIZE]
        try:
            vectors = await anyio.to_thread.run_sync(
                embeddings.embed_texts, [(m.overview or "").strip() for m in batch])
        except embeddings.EmbeddingUnavailable as exc:
            logger.warning("Semantic embeddings unavailable: %s", exc)
            return False
        for movie, vector in zip(batch, vectors, strict=True):
            movie.overview_embedding = embeddings.encode_embedding(vector)
            session.add(movie)
        session.commit()
    return True
