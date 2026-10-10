"""Pool shaping for Pick Next / search: The Chaser (palate cleansers) and the Underdog B-Side flip."""

from __future__ import annotations

import time
from collections.abc import Sequence

from sqlmodel import Session

from app.models.cache import CachedMovie
from app.services import cache_repo
from app.services.tmdb import TMDBClient, TMDBError
from app.services.tmdb_backoff import DeadlineReached, fetch_with_backoff
from app.services.vibe_controller import length_load

UNDERDOG = "underdog"
UNDERDOG_MIN_POPULARITY = 1.0  # below this a film is usually dead, unreleased or a stub

HYDRATE_BUDGET = 30
HYDRATE_SECONDS = 15.0


def needs_chaser(recent_loads: Sequence[float], setpoint: float = 0.55) -> bool:
    """Offer a Chaser when recent known film load is above the comfort setpoint."""
    known = list(recent_loads)
    return bool(known) and sum(known) / len(known) > setpoint


def is_chaser(
    load: float | None,
    runtime: int | None,
    language: str | None,
    medians: dict[str, float],
    setpoint: float = 0.55,
) -> bool:
    """A Chaser is below the vibe setpoint and shorter than its language's median."""
    relative_length = length_load(runtime, language, medians)
    return (
        load is not None
        and load <= setpoint - 0.15
        and relative_length is not None
        and relative_length < 0.5
    )


def is_underdog(popularity: float | None) -> bool:
    return popularity is not None and popularity >= UNDERDOG_MIN_POPULARITY


async def shape_pool(
    session: Session,
    tmdb: TMDBClient,
    movie_ids: Sequence[int],
    *,
    chaser: bool = False,
    sort_by: str | None = None,
    load_by_id: dict[int, float] | None = None,
    runtime_medians: dict[str, float] | None = None,
    setpoint: float = 0.55,
    hydrate_budget: int = HYDRATE_BUDGET,
    deadline: float | None = None,
) -> list[int]:
    """`movie_ids` narrowed and ordered for the options: `chaser` keeps only palate cleansers
    (fetching the runtime of lighthearted films whose detail isn't cached), `sort_by="underdog"`
    keeps films with popularity >= 1.0, least popular first. Without options the order is kept."""
    ids = list(dict.fromkeys(movie_ids))
    if chaser:
        ids = await _chasers(
            session,
            tmdb,
            ids,
            hydrate_budget,
            deadline,
            load_by_id or {},
            runtime_medians or {},
            setpoint,
        )
    if sort_by == UNDERDOG:
        rows = {i: session.get(CachedMovie, i) for i in ids}
        ranked = [
            (rows[i].popularity, rows[i].title, i)
            for i in ids
            if rows[i] is not None and is_underdog(rows[i].popularity)
        ]
        ids = [i for _, _, i in sorted(ranked)]
    return ids


async def _chasers(
    session: Session,
    tmdb: TMDBClient,
    ids: list[int],
    budget: int,
    deadline: float | None,
    load_by_id: dict[int, float],
    medians: dict[str, float],
    setpoint: float,
) -> list[int]:
    await hydrate_candidate_runtimes(session, tmdb, ids, budget, deadline)
    kept: list[int] = []
    for movie_id in ids:
        row = session.get(CachedMovie, movie_id)
        if row is None:
            continue
        if is_chaser(
            load_by_id.get(movie_id), row.runtime, row.original_language, medians, setpoint
        ):
            kept.append(movie_id)
    return kept


async def hydrate_candidate_runtimes(
    session: Session,
    tmdb: TMDBClient,
    movie_ids: Sequence[int],
    budget: int,
    deadline: float | None = None,
) -> None:
    """Fill only missing runtime details within the caller's request budget."""
    deadline = deadline if deadline is not None else time.monotonic() + HYDRATE_SECONDS
    for movie_id in movie_ids:
        row = session.get(CachedMovie, movie_id)
        if row is None or row.runtime is not None:
            continue
        if budget <= 0:
            break
        budget -= 1
        try:
            await fetch_with_backoff(
                lambda movie_id=movie_id: cache_repo.get_movie(
                    session, tmdb, movie_id, refresh=True
                ),
                deadline,
            )
        except DeadlineReached:
            break
        except TMDBError:
            session.rollback()
