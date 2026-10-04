"""Data for the Tools hub: the Watchlist Bingo pool."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.api.deps import get_current_user, get_omdb_client, get_tmdb_client
from app.db import get_session
from app.integrations.omdb import OMDbClient
from app.models.cache import CachedMovie, CachedMovieDirector, CachedMovieRating
from app.models.curated import CanonMovieBadge, LetterboxdWatchlist
from app.models.user import User
from app.services import cache_repo
from app.services.tmdb import TMDBClient
from app.services.tmdb_backoff import DeadlineReached, fetch_with_backoff
from app.utils.dates import parse_release_year

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tools", tags=["tools"])

HYDRATE_DEADLINE_SECONDS = 20.0
FEMALE_GENDER = 1


class BingoFilm(BaseModel):
    """A watchlist film with whatever the local cache knows about it (None = unknown)."""

    movie_id: int
    title: str
    year: int | None = None
    poster_path: str | None = None
    runtime: int | None = None
    original_language: str | None = None
    origin_countries: list[str] = []
    genre_ids: list[int] = []
    imdb_rating: float | None = None
    popularity: float | None = None
    canon_badges: list[str] = []
    directed_by_woman: bool | None = None


class BingoWatchlist(BaseModel):
    films: list[BingoFilm]
    total: int
    # Films that still lack detail / directors / ratings - call again with `hydrate` to fill more.
    pending: int


def _imdb_value(raw: str | None) -> float | None:
    try:
        return float(raw) if raw and raw != "N/A" else None
    except ValueError:
        return None


def _countries(raw: str | None) -> list[str]:
    import json

    try:
        parsed = json.loads(raw) if raw else []
    except ValueError:
        return []
    return [c for c in parsed if isinstance(c, str)] if isinstance(parsed, list) else []


def _needs_work(session: Session, movie: CachedMovie | None, omdb_enabled: bool, movie_id: int) -> bool:
    if movie is None or movie.runtime is None or movie.directors_fetched_at is None:
        return True
    directors = session.exec(
        select(CachedMovieDirector).where(CachedMovieDirector.movie_id == movie_id)).all()
    if any(d.gender is None for d in directors):
        return True
    return omdb_enabled and session.get(CachedMovieRating, movie_id) is None


def _build_films(session: Session, rows: list[LetterboxdWatchlist]) -> list[BingoFilm]:
    movie_ids = [row.movie_id for row in rows]
    movies = {
        m.tmdb_id: m for m in session.exec(
            select(CachedMovie).where(col(CachedMovie.tmdb_id).in_(movie_ids))).all()
    } if movie_ids else {}
    ratings = {
        r.movie_id: r for r in session.exec(
            select(CachedMovieRating).where(col(CachedMovieRating.movie_id).in_(movie_ids))).all()
    } if movie_ids else {}
    badges: dict[int, list[str]] = {}
    directors: dict[int, list[CachedMovieDirector]] = {}
    if movie_ids:
        for badge in session.exec(
            select(CanonMovieBadge).where(col(CanonMovieBadge.movie_id).in_(movie_ids))
        ).all():
            badges.setdefault(badge.movie_id, []).append(badge.badge_label)
        for director in session.exec(
            select(CachedMovieDirector).where(col(CachedMovieDirector.movie_id).in_(movie_ids))
        ).all():
            directors.setdefault(director.movie_id, []).append(director)

    films = []
    for row in rows:
        movie = movies.get(row.movie_id)
        rating = ratings.get(row.movie_id)
        # 0 = TMDB doesn't know the gender, which says nothing either way.
        genders = [d.gender for d in directors.get(row.movie_id, []) if d.gender]
        fetched = movie is not None and movie.directors_fetched_at is not None
        films.append(BingoFilm(
            movie_id=row.movie_id,
            title=movie.title if movie and movie.title else row.title,
            year=parse_release_year(movie.release_date) if movie and movie.release_date else row.year,
            poster_path=movie.poster_path if movie else None,
            runtime=movie.runtime if movie else None,
            original_language=movie.original_language if movie else None,
            origin_countries=_countries(movie.origin_country) if movie else [],
            genre_ids=(movie.genre_ids or []) if movie else [],
            imdb_rating=_imdb_value(rating.imdb_rating) if rating else None,
            popularity=movie.popularity if movie else None,
            canon_badges=badges.get(row.movie_id, []),
            directed_by_woman=(
                any(g == FEMALE_GENDER for g in genders) if fetched and genders else None),
        ))
    return films


@router.get("/bingo/watchlist", response_model=BingoWatchlist)
async def bingo_watchlist(
    hydrate: int = Query(default=0, ge=0, le=25, description="Fetch details for up to N films"),
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    omdb: OMDbClient = Depends(get_omdb_client),
    current_user: User = Depends(get_current_user),
) -> BingoWatchlist:
    """The caller's synced Letterboxd watchlist joined with the local movie cache, for
    matching Bingo squares. `hydrate=N` first fills in runtime / language / genres,
    directors (for "directed by a woman") and ratings for up to N films still missing
    them; `pending` says how many films are left after that."""
    rows = list(session.exec(
        select(LetterboxdWatchlist).where(LetterboxdWatchlist.user_id == current_user.id)
        .order_by(LetterboxdWatchlist.title)
    ).all())

    if hydrate:
        deadline = time.monotonic() + HYDRATE_DEADLINE_SECONDS
        budget = hydrate
        for row in rows:
            if budget <= 0:
                break
            movie = session.get(CachedMovie, row.movie_id)
            if not _needs_work(session, movie, omdb.enabled, row.movie_id):
                continue
            budget -= 1
            try:
                if movie is None or movie.runtime is None:
                    await fetch_with_backoff(
                        lambda movie_id=row.movie_id: cache_repo.get_movie(
                            session, tmdb, movie_id, refresh=True), deadline)
                existing = session.get(CachedMovie, row.movie_id)
                stale_gender = existing is not None and any(
                    d.gender is None for d in session.exec(select(CachedMovieDirector).where(
                        CachedMovieDirector.movie_id == row.movie_id)).all())
                if existing is None or existing.directors_fetched_at is None or stale_gender:
                    if stale_gender:  # re-read TMDB crew so genders are stored
                        existing.directors_fetched_at = None
                        session.add(existing)
                        session.commit()
                    await fetch_with_backoff(
                        lambda movie_id=row.movie_id: cache_repo.get_movie_directors(
                            session, tmdb, movie_id), deadline)
                await fetch_with_backoff(
                    lambda movie_id=row.movie_id: cache_repo.get_movie_ratings(
                        session, tmdb, omdb, movie_id), deadline)
            except DeadlineReached:
                break
            except Exception:
                logger.warning("Bingo hydrate failed for %s", row.movie_id, exc_info=True)
                session.rollback()

    films = _build_films(session, rows)
    pending = sum(
        1 for row in rows
        if _needs_work(session, session.get(CachedMovie, row.movie_id), omdb.enabled, row.movie_id)
    )
    return BingoWatchlist(films=films, total=len(films), pending=pending)
