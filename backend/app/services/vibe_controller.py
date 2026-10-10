"""Pure, language-relative film-load calculations and controller state transitions."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from statistics import median
from typing import Any, Literal

from sqlmodel import Session, select

from app.facets import semantic
from app.models.cache import CachedMovie
from app.services import embeddings

VibeState = Literal["steady", "fatigued", "recovering"]
SETPOINTS = {"gentle": 0.45, "balanced": 0.55, "brave": 0.7}
INTEGRAL_LIMIT = 2.0
INTEGRAL_DECAY = 0.7
RECOVERY_MARGIN = 0.08


def language_runtime_medians(
    rows: Sequence[tuple[str | None, int | None]],
) -> dict[str, float]:
    """Return medians from positive, known runtimes, grouped by original language."""
    buckets: dict[str, list[int]] = {}
    for language, runtime in rows:
        if language and runtime and runtime > 0:
            buckets.setdefault(language.casefold(), []).append(runtime)
    return {language: float(median(values)) for language, values in buckets.items()}


def cached_runtime_medians(session: Session) -> dict[str, float]:
    rows = session.exec(
        select(CachedMovie.original_language, CachedMovie.runtime).where(
            CachedMovie.original_language.is_not(None),
            CachedMovie.runtime.is_not(None),
            CachedMovie.runtime > 0,
        )
    ).all()
    return language_runtime_medians(list(rows))


def length_load(
    runtime: int | None, language: str | None, medians: Mapping[str, float]
) -> float | None:
    """Map language-relative runtime to [0, 1], with a median-length film at 0.5."""
    if runtime is None or runtime <= 0 or language is None:
        return None
    typical = medians.get(language.casefold())
    if typical is None or typical <= 0:
        return None
    return clamp(0.5 + 0.5 * (runtime / typical - 1.0))


def calculate_load(heaviness: float | None, relative_length: float | None) -> float | None:
    """Blend household heaviness and language-relative length; unknown inputs stay unknown."""
    if heaviness is None or relative_length is None:
        return None
    return 0.8 * clamp(heaviness) + 0.2 * clamp(relative_length)


def update_controller(
    previous: Mapping[str, Any] | None,
    load: float | None,
    setpoint: float,
) -> dict[str, Any]:
    """Fold one known load through the decaying PID and hysteresis state machine."""
    state = dict(previous or {})
    current_state: VibeState = state.get("state", "steady")
    if load is None:
        return {**state, "state": current_state, "reason": "Film load is unknown."}

    load = clamp(load)
    error = load - setpoint
    integral = clamp(
        float(state.get("integral", 0.0)) * INTEGRAL_DECAY + error,
        -INTEGRAL_LIMIT,
        INTEGRAL_LIMIT,
    )
    prior_error = state.get("previous_error")
    derivative = error - float(prior_error) if prior_error is not None else 0.0
    output = 0.6 * error + 0.3 * integral + 0.1 * derivative

    if load > setpoint + RECOVERY_MARGIN:
        next_state: VibeState = "fatigued"
        reason = "Recent films are above the comfort setpoint."
    elif current_state == "fatigued" and load >= setpoint - RECOVERY_MARGIN:
        next_state = "fatigued"
        reason = "A lighter stretch is needed before recovery."
    elif current_state == "fatigued":
        next_state = "recovering"
        reason = "A lighter film has started recovery."
    elif current_state == "recovering" and load < setpoint - RECOVERY_MARGIN:
        next_state = "recovering"
        reason = "Keep the lighter stretch going."
    else:
        next_state = "steady"
        reason = "The recent films are near the selected comfort level."

    return {
        **state,
        "state": next_state,
        "load": load,
        "setpoint": setpoint,
        "integral": integral,
        "previous_error": error,
        "pid_output": output,
        "reason": reason,
    }


def rolling_load(
    films: Sequence[Mapping[str, Any]], medians: Mapping[str, float], window: int = 5
) -> float | None:
    """Average the latest known film loads; unknown rows are excluded."""
    values = []
    for film in films[-window:]:
        length = length_load(film.get("runtime"), film.get("original_language"), medians)
        value = calculate_load(film.get("heaviness"), length)
        if value is not None:
            values.append(value)
    return sum(values) / len(values) if values else None


async def cached_heaviness(session: Session, movie_ids: Sequence[int]) -> dict[int, float]:
    """Project cached embeddings against household anchors without fetching movie details."""
    wanted = set(movie_ids)
    target_rows = list(
        session.exec(
            select(CachedMovie).where(
                CachedMovie.tmdb_id.in_(wanted or {-1}),
                CachedMovie.overview_embedding.is_not(None),
            )
        ).all()
    )
    if not target_rows:
        return {}
    config = embeddings.load_config(session)
    fingerprint, centroids = await semantic.anchor_centroids(session)
    if fingerprint != config.fingerprint:
        return {}
    rows = list(
        session.exec(select(CachedMovie).where(CachedMovie.overview_embedding.is_not(None))).all()
    )
    scores: list[float] = []
    raw_by_id: dict[int, float] = {}
    for row in rows:
        if embeddings.row_fingerprint(row.overview_embedding_model) != fingerprint:
            continue
        vector = embeddings.decode_embedding(row.overview_embedding)
        if vector is None:
            continue
        projected = semantic.project_vector(vector, centroids, fingerprint)
        raw = projected.get("heaviness_raw")
        if raw is not None:
            scores.append(float(raw))
            if row.tmdb_id in wanted:
                raw_by_id[row.tmdb_id] = float(raw)
    if not scores:
        return {}
    scores.sort()
    return {
        movie_id: bisect_right(scores, raw) * 100.0 / len(scores)
        for movie_id, raw in raw_by_id.items()
    }


async def candidate_loads(session: Session, movie_ids: Sequence[int]) -> dict[int, float]:
    """Compute load only for films with both cached semantics and language-relative runtime."""
    ids = list(dict.fromkeys(movie_ids))
    if not ids:
        return {}
    movies = {
        movie.tmdb_id: movie
        for movie in session.exec(select(CachedMovie).where(CachedMovie.tmdb_id.in_(ids))).all()
    }
    medians = cached_runtime_medians(session)
    heaviness = await cached_heaviness(session, ids)
    result = {}
    for movie_id, movie in movies.items():
        length = length_load(movie.runtime, movie.original_language, medians)
        load = calculate_load(
            heaviness.get(movie_id, None) / 100 if movie_id in heaviness else None, length
        )
        if load is not None:
            result[movie_id] = load
    return result


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))
