"""Pool shaping for Pick Next / search: The Chaser (palate cleansers) and the Underdog B-Side flip."""

from __future__ import annotations

import time
from collections.abc import Sequence

from sqlmodel import Session

from app.facets.query import FacetQuery
from app.facets.registry import named_variants
from app.models.cache import CachedMovie
from app.services import cache_repo
from app.services.tmdb import TMDBClient, TMDBError
from app.services.tmdb_backoff import DeadlineReached, fetch_with_backoff

UNDERDOG = "underdog"
UNDERDOG_MIN_POPULARITY = 1.0  # below this a film is usually dead, unreleased or a stub

# TMDB genre ids.
ANIMATION, COMEDY, DRAMA = 16, 35, 18
CHASER_GENRES = frozenset({ANIMATION, COMEDY})
CHASER_MAX_RUNTIME = named_variants()["chaser"]["query"]["all"][1]["value"]
CHASER_TRIGGER_RUNTIME = named_variants()["chaser_trigger"]["query"]["any"][0]["value"]
HYDRATE_BUDGET = 30
HYDRATE_SECONDS = 15.0


def needs_chaser(runtime: int | None, genre_ids: Sequence[int] | None) -> bool:
    """A heavy film (long, or a drama) earns a palate cleanser."""
    return (
        FacetQuery.model_validate(named_variants()["chaser_trigger"]["query"]).evaluate(
            {
                "runtime": runtime or None,
                "genre": list(genre_ids) if genre_ids is not None else None,
            }
        )
        is True
    )


def is_chaser(runtime: int | None, genre_ids: Sequence[int] | None) -> bool:
    """Quick and lighthearted: at most 95 minutes and Comedy or Animation. Unknown never qualifies."""
    return (
        FacetQuery.model_validate(named_variants()["chaser"]["query"]).evaluate(
            {
                "runtime": runtime or None,
                "genre": list(genre_ids) if genre_ids is not None else None,
            }
        )
        is True
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
    hydrate_budget: int = HYDRATE_BUDGET,
    deadline: float | None = None,
) -> list[int]:
    """`movie_ids` narrowed and ordered for the options: `chaser` keeps only palate cleansers
    (fetching the runtime of lighthearted films whose detail isn't cached), `sort_by="underdog"`
    keeps films with popularity >= 1.0, least popular first. Without options the order is kept."""
    ids = list(dict.fromkeys(movie_ids))
    if chaser:
        ids = await _chasers(session, tmdb, ids, hydrate_budget, deadline)
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
    session: Session, tmdb: TMDBClient, ids: list[int], budget: int, deadline: float | None
) -> list[int]:
    deadline = deadline if deadline is not None else time.monotonic() + HYDRATE_SECONDS
    kept: list[int] = []
    for movie_id in ids:
        row = session.get(CachedMovie, movie_id)
        if row is None or (row.genre_ids is not None and not CHASER_GENRES & set(row.genre_ids)):
            continue  # not lighthearted (or unknown film): no fetch needed
        if row.runtime is None and budget > 0:
            budget -= 1
            try:
                row = (
                    await fetch_with_backoff(
                        lambda movie_id=movie_id: cache_repo.get_movie(
                            session, tmdb, movie_id, refresh=True
                        ),
                        deadline,
                    )
                    or row
                )
            except DeadlineReached:
                budget = 0
            except TMDBError:
                session.rollback()
        if is_chaser(row.runtime, row.genre_ids):
            kept.append(movie_id)
    return kept
