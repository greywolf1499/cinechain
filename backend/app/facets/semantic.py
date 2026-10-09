"""Semantic vibe facets projected from already-cached overview embeddings."""

from __future__ import annotations

import asyncio
import hashlib
import os
import threading
from pathlib import Path
from typing import Any

import numpy as np
from sqlmodel import Session, select

from app.facets.anchors import ANCHOR_VERSION, ANCHORS
from app.models.cache import CachedMovie
from app.services import embeddings

VALENCE_THRESHOLD = 0.1
AROUSAL_THRESHOLD = 0.1
_cache_lock = threading.Lock()
_centroids: dict[str, dict[str, np.ndarray]] = {}
_async_locks: dict[tuple[int, str], asyncio.Lock] = {}


def centroid_cache_path(config_dir: Path, fingerprint: str) -> Path:
    digest = hashlib.sha256(fingerprint.encode()).hexdigest()
    return config_dir / "facet_anchors" / f"{ANCHOR_VERSION}-{digest}.npz"


def _validate_vectors(vectors: list[np.ndarray], count: int) -> None:
    if len(vectors) != count or not vectors:
        raise embeddings.EmbeddingUnavailable("Anchor embedding batch had an unexpected size")
    shape = vectors[0].shape
    if any(
        vector.ndim != 1
        or vector.size == 0
        or vector.shape != shape
        or not np.all(np.isfinite(vector))
        or np.linalg.norm(vector) == 0
        for vector in vectors
    ):
        raise embeddings.EmbeddingUnavailable("Anchor embedding batch was malformed")


async def anchor_centroids(session: Session) -> tuple[str, dict[str, np.ndarray]]:
    config = embeddings.load_config(session)
    fingerprint = config.fingerprint
    key = (id(asyncio.get_running_loop()), fingerprint)
    with _cache_lock:
        lock = _async_locks.setdefault(key, asyncio.Lock())
    async with lock:
        cached = _centroids.get(fingerprint)
        if cached is not None:
            return fingerprint, cached
        return await _load_or_embed_centroids(config, fingerprint)


async def _load_or_embed_centroids(
    config: embeddings.EmbeddingConfig, fingerprint: str
) -> tuple[str, dict[str, np.ndarray]]:
    from app.config import get_settings

    path = centroid_cache_path(get_settings().config_dir, fingerprint)
    if path.is_file():
        try:
            with np.load(path, allow_pickle=False) as archive:
                if str(archive["fingerprint"].item()) == fingerprint and int(
                    archive["version"].item()
                ) == ANCHOR_VERSION:
                    loaded = {name: archive[name].astype(np.float32) for name in ANCHORS}
                    if all(
                        value.ndim == 1
                        and value.size > 0
                        and np.all(np.isfinite(value))
                        and np.linalg.norm(value) > 0
                        for value in loaded.values()
                    ):
                        _centroids[fingerprint] = loaded
                        return fingerprint, loaded
        except (OSError, KeyError, ValueError):
            pass
    texts = [text for group in ANCHORS.values() for text in group]
    result = await embeddings.embed_batch(config, texts)
    if result.fingerprint != fingerprint:
        raise embeddings.EmbeddingUnavailable("Anchor vectors do not match the active fingerprint")
    _validate_vectors(result.vectors, len(texts))
    centroids: dict[str, np.ndarray] = {}
    offset = 0
    for name, group in ANCHORS.items():
        rows = result.vectors[offset : offset + len(group)]
        offset += len(group)
        centroid = np.mean(np.stack(rows), axis=0)
        norm = float(np.linalg.norm(centroid))
        if not np.isfinite(norm) or norm == 0:
            raise embeddings.EmbeddingUnavailable(f"Could not compute the {name} anchor centroid")
        centroids[name] = (centroid / norm).astype(np.float32)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".npz.tmp")
    with temporary.open("wb") as handle:
        np.savez(
            handle,
            fingerprint=np.array(fingerprint),
            version=np.array(ANCHOR_VERSION),
            **centroids,
        )
    os.replace(temporary, path)
    with _cache_lock:
        _centroids[fingerprint] = centroids
    return fingerprint, centroids


def _projection(
    vector: np.ndarray,
    centroids: dict[str, np.ndarray],
    positive: str,
    negative: str,
    fingerprint: str,
) -> float:
    positive_score = embeddings.normalize_similarity(
        embeddings.cosine_similarity(vector, centroids[positive]), fingerprint
    )
    negative_score = embeddings.normalize_similarity(
        embeddings.cosine_similarity(vector, centroids[negative]), fingerprint
    )
    return positive_score - negative_score


def quadrant(valence: float, arousal: float) -> str | None:
    """Return a quadrant only when both axes clear the audit's confidence floor."""
    if abs(valence) < VALENCE_THRESHOLD or abs(arousal) < AROUSAL_THRESHOLD:
        return None
    positive = valence >= VALENCE_THRESHOLD
    energized = arousal >= AROUSAL_THRESHOLD
    if not positive and energized:
        return "tense"
    if positive and not energized:
        return "serene"
    return "euphoric" if positive else "melancholic"


def project_vector(
    vector: np.ndarray, centroids: dict[str, np.ndarray], fingerprint: str
) -> dict[str, Any]:
    if any(vector.shape != centroid.shape for centroid in centroids.values()):
        return {}
    valence = _projection(
        vector, centroids, "valence_positive", "valence_negative", fingerprint
    )
    arousal = _projection(vector, centroids, "arousal_high", "arousal_low", fingerprint)
    heaviness = _projection(vector, centroids, "heaviness", "spectacle", fingerprint)
    return {
        "vibe_valence": valence,
        "vibe_arousal": arousal,
        "vibe_quadrant": quadrant(valence, arousal),
        "heaviness_raw": heaviness,
        "fingerprint": fingerprint,
    }


async def movie_semantics(session: Session, movie: CachedMovie) -> dict[str, Any] | None:
    config = embeddings.load_config(session)
    if embeddings.row_fingerprint(movie.overview_embedding_model) != config.fingerprint:
        return None
    vector = embeddings.decode_embedding(movie.overview_embedding)
    if vector is None:
        return None
    fingerprint, centroids = await anchor_centroids(session)
    projected = project_vector(vector, centroids, fingerprint)
    if projected:
        projected["heaviness_percentile"] = heaviness_percentile(
            session, projected["heaviness_raw"], centroids, fingerprint
        )
    return projected or None


def heaviness_percentile(
    session: Session, raw: float, centroids: dict[str, np.ndarray], fingerprint: str
) -> float | None:
    """Compute the household-relative percentile from compatible cached vectors only."""
    scores = []
    for movie in session.exec(
        select(CachedMovie).where(CachedMovie.overview_embedding.is_not(None))
    ):
        if embeddings.row_fingerprint(movie.overview_embedding_model) != fingerprint:
            continue
        vector = embeddings.decode_embedding(movie.overview_embedding)
        if vector is not None and vector.shape == next(iter(centroids.values())).shape:
            scores.append(_projection(vector, centroids, "heaviness", "spectacle", fingerprint))
    if not scores:
        return None
    return sum(score <= raw for score in scores) * 100.0 / len(scores)
