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
import numpy as np
from sqlmodel import Session

from app.models.cache import CachedMovie
from app.services import embeddings, llm
from app.services.aesthetic import extract_dominant_color
from app.services.tmdb import TMDBClient

logger = logging.getLogger(__name__)

POSTER_CONCURRENCY = 8
EMBED_BATCH_SIZE = 32
TROPE_CONCURRENCY = 2
TROPE_VERIFY_BATCH_SIZE = EMBED_BATCH_SIZE // 6
TROPE_CONFIDENCE_THRESHOLD = 0.85  # Provider-normalized, not Arctic's high raw cosine baseline.
# Only concepts with an inherent genre requirement belong here; themes such as revenge
# and found-family are deliberately unrestricted.
TROPE_GENRE_REQUIREMENTS: dict[str, frozenset[int]] = {
    "cyberpunk": frozenset({878}),
    "space-opera": frozenset({878}),
    "alien-invasion": frozenset({878}),
    "time-travel": frozenset({878, 14}),
    "time-loop": frozenset({878, 14}),
    "supernatural-horror": frozenset({27}),
    "zombie-apocalypse": frozenset({27, 878}),
}
TROPE_DESCRIPTIONS = {
    "cyberpunk": "High-tech dystopia with cybernetics, hackers and oppressive corporations.",
    "space-opera": "Interstellar adventures, spaceships and conflict across alien worlds.",
    "alien-invasion": "Extraterrestrial invaders threaten humanity.",
    "time-travel": "Characters travel into the past or future.",
    "time-loop": "Characters repeatedly relive the same period of time.",
    "supernatural-horror": "Supernatural forces terrorize the characters.",
    "zombie-apocalypse": "The undead overrun civilization and survivors fight to escape.",
    "heist": "A team plans and executes a robbery.",
    "romance": "People fall in love and overcome obstacles to their relationship.",
    "enemies-to-lovers": "Adversaries develop a romantic relationship.",
    "found-family": "Unrelated people form close bonds and become a chosen family.",
    "revenge": "A wronged character seeks revenge.",
}


async def guard_tropes(
    session: Session,
    proposals: list[tuple[CachedMovie, list[str]]],
) -> dict[int, list[str]]:
    """Verify overview/concept pairs in one fresh model space, never against stored vectors.

    Missing genres/overviews cannot verify a tag. Provider failure raises, so callers can
    keep historical cache rows without ever admitting them into game matching.
    """
    verified: dict[int, list[str]] = {}
    for start in range(0, len(proposals), TROPE_VERIFY_BATCH_SIZE):
        verified.update(
            await _guard_trope_batch(session, proposals[start : start + TROPE_VERIFY_BATCH_SIZE])
        )
    return verified


async def _guard_trope_batch(
    session: Session,
    proposals: list[tuple[CachedMovie, list[str]]],
) -> dict[int, list[str]]:
    accepted: dict[int, list[str]] = {movie.tmdb_id: [] for movie, _ in proposals}
    texts: list[str] = []
    checks: list[tuple[int, str, int, int]] = []
    for movie, tropes in proposals:
        if not movie.genre_ids or not (movie.overview or "").strip():
            continue
        candidates = list(
            dict.fromkeys(
                tag.strip().lower().replace("_", "-").replace(" ", "-")
                for tag in tropes
                if tag.strip()
            )
        )[:5]
        candidates = [
            tag
            for tag in candidates
            if tag not in TROPE_GENRE_REQUIREMENTS
            or set(movie.genre_ids) & TROPE_GENRE_REQUIREMENTS[tag]
        ]
        if not candidates:
            continue
        overview_index = len(texts)
        texts.append((movie.overview or "").strip())
        for tag in candidates:
            checks.append((movie.tmdb_id, tag, overview_index, len(texts)))
            texts.append(TROPE_DESCRIPTIONS.get(tag, f"A story involving {tag.replace('-', ' ')}."))
    if not texts:
        return accepted
    config = embeddings.load_config(session)
    result = await embeddings.embed_batch(config, texts)
    if result.fingerprint not in {config.fingerprint, config.local_fingerprint} or len(
        result.vectors
    ) != len(texts):
        raise embeddings.EmbeddingUnavailable(
            "Trope guard received an unexpected model fingerprint or batch size"
        )
    if any(
        vector.ndim != 1
        or vector.size == 0
        or not np.all(np.isfinite(vector))
        or np.linalg.norm(vector) == 0
        for vector in result.vectors
    ):
        raise embeddings.EmbeddingUnavailable("Trope guard received a malformed embedding")
    for movie_id, tag, overview_index, concept_index in checks:
        overview, concept = result.vectors[overview_index], result.vectors[concept_index]
        if overview.shape != concept.shape:
            raise embeddings.EmbeddingUnavailable(
                "Trope guard received incompatible vector dimensions"
            )
        confidence = embeddings.normalize_similarity(
            embeddings.cosine_similarity(overview, concept), result.fingerprint
        )
        if confidence >= TROPE_CONFIDENCE_THRESHOLD:
            accepted[movie_id].append(tag)
    return accepted


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
    proposals = await llm.extract_tropes(movie.overview or "", config)
    tropes = (await guard_tropes(session, [(movie, proposals)]))[movie.tmdb_id]
    if config.enabled:
        movie.extracted_tropes = tropes
        session.add(movie)
        session.commit()
    return tropes


async def ensure_tropes(session: Session, movies: Iterable[CachedMovie]) -> dict[int, list[str]]:
    """Fill `extracted_tropes` for films that lack them, when the generative model is on.
    A failing model is logged once and skips the rest: films stay NULL (never "no tropes")."""
    config = llm.load_config(session)
    pending = list({m.tmdb_id: m for m in movies}.values())
    if not pending:
        return {}
    gate = asyncio.Semaphore(TROPE_CONCURRENCY)
    failed = False

    async def extract(movie: CachedMovie) -> tuple[CachedMovie, list[str] | None]:
        nonlocal failed
        if movie.extracted_tropes is not None:
            return movie, movie.extracted_tropes
        if not config.enabled or not (movie.overview or "").strip():
            return movie, None
        async with gate:
            if failed:
                return movie, None
            try:
                return movie, await llm.extract_tropes(movie.overview or "", config)
            except llm.LlmUnavailable as exc:
                failed = True
                logger.warning("Trope extraction unavailable: %s", exc)
                return movie, None

    proposals = [
        (movie, tropes)
        for movie, tropes in await asyncio.gather(*(extract(m) for m in pending))
        if tropes is not None
    ]
    verified = {movie.tmdb_id: [] for movie in pending}
    try:
        verified.update(await guard_tropes(session, proposals))
    except embeddings.EmbeddingUnavailable as exc:
        logger.warning("Trope verification unavailable: %s", exc)
        return verified
    for movie, _ in proposals:
        movie.extracted_tropes = verified[movie.tmdb_id]
        session.add(movie)
    session.commit()
    return verified
