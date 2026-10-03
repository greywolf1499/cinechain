"""Stale-entry eviction for the JIT TMDB/OMDb cache tables.

Everything dropped here is re-fetched lazily the next time something needs it
(that is what makes the cache "JIT"), so a flush is always safe - it only costs
future TMDB calls. Rules, with `cutoff = now - max_age_days`:

* `cached_movie_ratings` older than the cutoff are deleted.
* A movie whose cast was fetched before the cutoff loses its cast edges and its
  `cast_fetched_at` flag. Edges double as actor credits, so every actor that
  lost an edge also gets `credits_fetched_at` cleared (their credit list is no
  longer complete).
* An actor whose credits were fetched before the cutoff gets the flag cleared
  so the credits are re-fetched, but their edges stay (they may still be part of
  a fresh movie's cast).
* Actors left with no edges and no completeness flag are deleted - this is
  where the disk space comes back from.

Movie detail rows are kept: they carry no timestamp, are tiny, and runs/badges
reference them.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.models.cache import CachedActor, CachedMovie, CachedMovieCast, CachedMovieRating
from app.utils.ids import utcnow

logger = logging.getLogger(__name__)


def flush_stale_cache(session: Session, max_age_days: int = 7) -> dict[str, int]:
    cutoff = utcnow() - timedelta(days=max_age_days)

    stale_movies = select(CachedMovie.tmdb_id).where(
        CachedMovie.cast_fetched_at.is_not(None), CachedMovie.cast_fetched_at < cutoff
    )
    ratings_removed = session.execute(
        delete(CachedMovieRating).where(CachedMovieRating.fetched_at < cutoff)
    ).rowcount

    actors_marked = session.execute(
        update(CachedActor)
        .where(CachedActor.credits_fetched_at.is_not(None), CachedActor.credits_fetched_at < cutoff)
        .values(credits_fetched_at=None)
    ).rowcount
    # Their edges are about to disappear, so these actors' credit lists are incomplete.
    session.execute(
        update(CachedActor)
        .where(CachedActor.tmdb_id.in_(
            select(CachedMovieCast.actor_id).where(CachedMovieCast.movie_id.in_(stale_movies))))
        .values(credits_fetched_at=None)
    )
    edges_removed = session.execute(
        delete(CachedMovieCast).where(CachedMovieCast.movie_id.in_(stale_movies))
    ).rowcount
    movies_reset = session.execute(
        update(CachedMovie)
        .where(CachedMovie.cast_fetched_at.is_not(None), CachedMovie.cast_fetched_at < cutoff)
        .values(cast_fetched_at=None)
    ).rowcount
    actors_removed = session.execute(
        delete(CachedActor).where(
            CachedActor.credits_fetched_at.is_(None),
            CachedActor.tmdb_id.not_in(select(CachedMovieCast.actor_id)),
        )
    ).rowcount
    session.commit()

    return {
        "ratings_removed": ratings_removed,
        "movies_cast_reset": movies_reset,
        "cast_edges_removed": edges_removed,
        "actors_marked_stale": actors_marked,
        "actors_removed": actors_removed,
    }


def vacuum(engine: Engine) -> bool:
    """Returns freed pages to the OS. Best effort: another open reader can make it fail."""
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql("VACUUM")
            conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
        return True
    except Exception:
        logger.warning("VACUUM after cache flush failed", exc_info=True)
        return False
