"""Post-processing for solved bridge paths (Bridge Solver 2.0): dynamic highlight
tags, JIT detail hydration, and same-actor node swaps.

Everything here works off the SQLite cache the solve just built; the only TMDB
traffic is `hydrate_movies` (a path's few film-detail rows) and, for swaps,
fetching an actor filmography that was never cached.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence

import anyio
from sqlmodel import Session, col, select

from app.models.cache import CachedActor, CachedMovie, CachedMovieCast
from app.models.curated import CanonMovieBadge
from app.schemas.engine import (
    BridgeNode,
    PathTag,
    SharedActorConnection,
    SwapCandidate,
)
from app.services import cache_repo
from app.services.movie_filters import is_reality_eligible
from app.services.tmdb import TMDBClient
from app.services.tmdb_backoff import DeadlineReached, fetch_with_backoff
from app.utils.dates import parse_release_year

CANON_HEAVY_MIN_FILMS = 2
MULTI_COUNTRY_MIN_COUNTRIES = 3
EPIC_RUNTIME_MINUTES = 150  # strictly longer than this counts as "epic"
EPIC_RUNTIME_MIN_FILMS = 2

HYDRATE_DEADLINE_SECONDS = 10.0
SWAP_RESPONSE_CAP = 12


def parse_countries(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [c for c in parsed if isinstance(c, str)] if isinstance(parsed, list) else []


def make_bridge_node(movie: CachedMovie | None, movie_id: int) -> BridgeNode:
    if movie is None:
        return BridgeNode(movie_id=movie_id, title=str(movie_id))
    return BridgeNode(
        movie_id=movie_id,
        title=movie.title,
        poster_path=movie.poster_path,
        release_year=parse_release_year(movie.release_date),
        popularity=movie.popularity,
        runtime=movie.runtime or None,
        origin_countries=parse_countries(movie.origin_country),
    )


# --- tag analyzers: each takes the path nodes, returns a tag or None ---


def _canon_heavy(session: Session, nodes: Sequence[BridgeNode]) -> PathTag | None:
    movie_ids = {n.movie_id for n in nodes}
    badged = set(session.exec(
        select(CanonMovieBadge.movie_id).where(
            col(CanonMovieBadge.movie_id).in_(movie_ids))
    ).all())
    if len(badged) < CANON_HEAVY_MIN_FILMS:
        return None
    return PathTag(
        key="canon_heavy", label="Canon Heavy", emoji="🏆",
        detail=f"{len(badged)} films from curated canons")


def _multi_country(_session: Session, nodes: Sequence[BridgeNode]) -> PathTag | None:
    countries = sorted({c for n in nodes for c in n.origin_countries})
    if len(countries) < MULTI_COUNTRY_MIN_COUNTRIES:
        return None
    return PathTag(
        key="multi_country", label="Multi-Country", emoji="🌍",
        detail=f"Spans {len(countries)} countries: {', '.join(countries)}")


def _epic_runtimes(_session: Session, nodes: Sequence[BridgeNode]) -> PathTag | None:
    epics = [n for n in nodes if n.runtime and n.runtime > EPIC_RUNTIME_MINUTES]
    if len(epics) < EPIC_RUNTIME_MIN_FILMS:
        return None
    return PathTag(
        key="epic_runtimes", label="Epic Runtimes", emoji="⏱️",
        detail=f"{len(epics)} films over {EPIC_RUNTIME_MINUTES} minutes")


_ANALYZERS = (_canon_heavy, _multi_country, _epic_runtimes)


def analyze_path_tags(session: Session, nodes: Sequence[BridgeNode]) -> list[PathTag]:
    """Sync DB reads only. Runtime/country tags need hydrated nodes (see `hydrate_movies`)."""
    tags = (analyzer(session, nodes) for analyzer in _ANALYZERS)
    return [tag for tag in tags if tag is not None]


async def hydrate_movies(
    session: Session,
    tmdb: TMDBClient,
    movie_ids: Sequence[int],
    deadline: float | None = None,
) -> None:
    """Fetches full detail for cached movies that are still search/credit stubs
    (no runtime/origin country), so runtime and country tags can be computed.
    Best effort: a failure just leaves that film unhydrated."""
    deadline = deadline if deadline is not None else time.monotonic() + HYDRATE_DEADLINE_SECONDS
    for movie_id in dict.fromkeys(movie_ids):
        movie = await anyio.to_thread.run_sync(session.get, CachedMovie, movie_id)
        if movie is not None and movie.runtime is not None and movie.origin_country is not None:
            continue
        try:
            await fetch_with_backoff(
                lambda movie_id=movie_id: cache_repo.get_movie(
                    session, tmdb, movie_id, refresh=True),
                deadline,
            )
        except DeadlineReached:
            return
        except Exception:  # noqa: BLE001 - tags are a nice-to-have, never fail the path
            session.rollback()


def build_nodes(session: Session, movie_ids: Sequence[int]) -> list[BridgeNode]:
    return [make_bridge_node(session.get(CachedMovie, mid), mid) for mid in movie_ids]


# --- same-actor swap ---


def _connection(
    session: Session, actor_id: int, from_movie_id: int, to_movie_id: int
) -> SharedActorConnection:
    actor = session.get(CachedActor, actor_id)
    cast_from = session.get(CachedMovieCast, (from_movie_id, actor_id))
    cast_to = session.get(CachedMovieCast, (to_movie_id, actor_id))
    return SharedActorConnection(
        actor_id=actor_id,
        actor_name=actor.name if actor else str(actor_id),
        profile_path=actor.profile_path if actor else None,
        character_in_from=cast_from.character_name if cast_from else None,
        character_in_to=cast_to.character_name if cast_to else None,
    )


def find_same_actor_swaps(
    session: Session,
    *,
    from_movie_id: int,
    to_movie_id: int,
    actor_in_id: int,
    actor_out_id: int,
    exclude_movie_ids: set[int],
    limit: int = SWAP_RESPONSE_CAP,
) -> tuple[list[SwapCandidate], int]:
    """`Intersection(Actor_X_Movies, Actor_Y_Movies)` straight from SQLite.

    Actor filmographies are stored as (movie, actor) rows in `cached_movie_cast`,
    so "movies starring both actors" is a self-join on that table. Callers must
    make sure both filmographies are cached first (`ensure_filmographies`).
    """
    cast_x = CachedMovieCast.__table__.alias("cast_x")
    cast_y = CachedMovieCast.__table__.alias("cast_y")
    statement = (
        select(CachedMovie)
        .join(cast_x, cast_x.c.movie_id == CachedMovie.tmdb_id)
        .join(cast_y, cast_y.c.movie_id == CachedMovie.tmdb_id)
        .where(cast_x.c.actor_id == actor_in_id, cast_y.c.actor_id == actor_out_id)
    )
    movies = [
        m for m in session.exec(statement).all()
        if m.tmdb_id not in exclude_movie_ids and is_reality_eligible(m)
    ]
    # Most mainstream first: the likeliest "oh, THAT film" swap.
    movies.sort(key=lambda m: (-(m.popularity or 0.0), m.title))

    candidates = [
        SwapCandidate(
            node=make_bridge_node(movie, movie.tmdb_id),
            connection_in=_connection(session, actor_in_id, from_movie_id, movie.tmdb_id),
            connection_out=_connection(session, actor_out_id, movie.tmdb_id, to_movie_id),
        )
        for movie in movies[:limit]
    ]
    return candidates, len(movies)


async def ensure_filmographies(
    session: Session, tmdb: TMDBClient, actor_ids: Sequence[int], deadline: float
) -> None:
    for actor_id in dict.fromkeys(actor_ids):
        await fetch_with_backoff(
            lambda actor_id=actor_id: cache_repo.get_actor_credits(session, tmdb, actor_id),
            deadline,
        )
