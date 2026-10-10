"""Data for the Tools hub: the Watchlist Bingo pool, squares and stamps."""

from __future__ import annotations

import logging
import time
from hashlib import sha256

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlmodel import Session, col, select

from app.api.deps import get_current_user, get_omdb_client, get_tmdb_client
from app.db import get_session
from app.engines import march_madness
from app.engines.base import RunSetupError
from app.engines.grid_crawler import tool_board
from app.facets import store as facet_store
from app.facets.genres import GENRE_IDS
from app.facets.query import FacetQuery, compile, universe_ids
from app.facets.registry import named_variants
from app.integrations.omdb import OMDbClient
from app.models.cache import CachedMovie, CachedMovieDirector, CachedMovieRating
from app.models.curated import CanonMovieBadge, LetterboxdWatchlist
from app.models.user import User
from app.schemas.movies import MovieSummary
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


def _needs_work(
    session: Session, movie: CachedMovie | None, omdb_enabled: bool, movie_id: int
) -> bool:
    if movie is None or movie.runtime is None or movie.directors_fetched_at is None:
        return True
    directors = session.exec(
        select(CachedMovieDirector).where(CachedMovieDirector.movie_id == movie_id)
    ).all()
    if any(d.gender is None for d in directors):
        return True
    return omdb_enabled and session.get(CachedMovieRating, movie_id) is None


def _build_films(session: Session, rows: list[LetterboxdWatchlist]) -> list[BingoFilm]:
    movie_ids = [row.movie_id for row in rows]
    movies = (
        {
            m.tmdb_id: m
            for m in session.exec(
                select(CachedMovie).where(col(CachedMovie.tmdb_id).in_(movie_ids))
            ).all()
        }
        if movie_ids
        else {}
    )
    ratings = (
        {
            r.movie_id: r
            for r in session.exec(
                select(CachedMovieRating).where(col(CachedMovieRating.movie_id).in_(movie_ids))
            ).all()
        }
        if movie_ids
        else {}
    )
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
        films.append(
            BingoFilm(
                movie_id=row.movie_id,
                title=movie.title if movie and movie.title else row.title,
                year=parse_release_year(movie.release_date)
                if movie and movie.release_date
                else row.year,
                poster_path=movie.poster_path if movie else None,
                runtime=movie.runtime if movie else None,
                original_language=movie.original_language if movie else None,
                origin_countries=_countries(movie.origin_country) if movie else [],
                genre_ids=(movie.genre_ids or []) if movie else [],
                imdb_rating=_imdb_value(rating.imdb_rating) if rating else None,
                popularity=movie.popularity if movie else None,
                canon_badges=badges.get(row.movie_id, []),
                directed_by_woman=(
                    any(g == FEMALE_GENDER for g in genders) if fetched and genders else None
                ),
            )
        )
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
    rows = list(
        session.exec(
            select(LetterboxdWatchlist)
            .where(LetterboxdWatchlist.user_id == current_user.id)
            .order_by(LetterboxdWatchlist.title)
        ).all()
    )

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
                            session, tmdb, movie_id, refresh=True
                        ),
                        deadline,
                    )
                existing = session.get(CachedMovie, row.movie_id)
                stale_gender = existing is not None and any(
                    d.gender is None
                    for d in session.exec(
                        select(CachedMovieDirector).where(
                            CachedMovieDirector.movie_id == row.movie_id
                        )
                    ).all()
                )
                if existing is None or existing.directors_fetched_at is None or stale_gender:
                    if stale_gender:  # re-read TMDB crew so genders are stored
                        existing.directors_fetched_at = None
                        session.add(existing)
                        session.commit()
                    await fetch_with_backoff(
                        lambda movie_id=row.movie_id: cache_repo.get_movie_directors(
                            session, tmdb, movie_id
                        ),
                        deadline,
                    )
                await fetch_with_backoff(
                    lambda movie_id=row.movie_id: cache_repo.get_movie_ratings(
                        session, tmdb, omdb, movie_id
                    ),
                    deadline,
                )
            except DeadlineReached:
                break
            except Exception:
                logger.warning("Bingo hydrate failed for %s", row.movie_id, exc_info=True)
                session.rollback()

    films = _build_films(session, rows)
    pending = sum(
        1
        for row in rows
        if _needs_work(session, session.get(CachedMovie, row.movie_id), omdb.enabled, row.movie_id)
    )
    return BingoWatchlist(films=films, total=len(films), pending=pending)


# (square id, named variant, label template, hint template); {v} is the variant's threshold.
_VARIANT_SQUARES = (
    ("classic", "classic", "Pre-{v} Classic", "Released before {v}"),
    ("short", "short", "Runtime Under {v}m", "Shorter than {v} minutes"),
    ("epic", "epic", "Epic: Over {v} Minutes", "Longer than {v} minutes"),
    ("non-english", "non_english", "Non-English Language", "Original language isn't English"),
    ("woman-director", "female_director", "Directed by a Woman", "At least one woman directed it"),
    ("recent", "recent", "Released in the Last 5 Years", "Released {v} or later"),
    (
        "hidden-gem",
        "hidden_gem",
        "Hidden Gem",
        "TMDB popularity under {v} - hardly anyone's seen it",
    ),
    ("asian", "asian", "Asian Cinema", "Made in Asia"),
    ("european", "european", "European Cinema", "Made in Europe"),
    (
        "latam-africa",
        "latam_africa",
        "Latin American or African Cinema",
        "Made in Latin America or Africa",
    ),
)
_GENRE_SQUARES = (
    ("documentary", "documentary", "Documentary"),
    ("animation", "animation", "Animation"),
    ("horror", "horror", "Horror"),
    ("scifi", "sci-fi", "Sci-Fi"),
    ("comedy", "comedy", "Comedy"),
    ("romance", "romance", "Romance"),
    ("war", "war", "War"),
    ("western", "western", "Western"),
    ("musical", "musical", "Musical"),
    ("thriller", "thriller", "Thriller"),
    ("crime", "crime", "Crime"),
    ("fantasy", "fantasy", "Fantasy"),
    ("mystery", "mystery", "Mystery"),
)
_DECADES = (1960, 1970, 1980, 1990, 2000, 2010, 2020)


class BingoSquare(BaseModel):
    id: str
    label: str
    hint: str
    query: FacetQuery


def bingo_squares() -> list[BingoSquare]:
    """Every square as a server facet query; thresholds come from the named variants."""
    variants = named_variants()

    def leaf(facet: str, op: str, value) -> FacetQuery:
        return FacetQuery(facet=facet, op=op, value=value)

    squares = []
    for square_id, name, label, hint in _VARIANT_SQUARES:
        query = variants[name]["query"]
        value = query.get("value")
        squares.append(
            BingoSquare(
                id=square_id,
                label=label.format(v=value),
                hint=hint.format(v=value),
                query=FacetQuery.model_validate(query),
            )
        )
    squares += [
        BingoSquare(
            id="imdb-high",
            label="IMDb > 8.0",
            hint="Rated above 8.0 on IMDb",
            query=leaf("imdb_rating", "gt", 8.0),
        ),
        BingoSquare(
            id="imdb-low",
            label="IMDb Under 5.5",
            hint="So bad it's good: rated under 5.5",
            query=leaf("imdb_rating", "lt", 5.5),
        ),
        BingoSquare(
            id="canon",
            label="Sight & Sound / Canon Film",
            hint="On one of your curated canon lists",
            query=leaf("canon", "eq", True),
        ),
        BingoSquare(
            id="not-us-uk",
            label="Made Outside the US & UK",
            hint="Neither American nor British",
            # A known-empty country list says nothing about where the film was made.
            query=FacetQuery.model_validate(
                {
                    "all": [
                        {"facet": "origin_country_count", "op": "gt", "value": 0},
                        {
                            "not": {
                                "facet": "origin_country",
                                "op": "has_any",
                                "value": ["US", "GB"],
                            }
                        },
                    ]
                }
            ),
        ),
    ]
    squares += [
        BingoSquare(
            id=square_id,
            label=label,
            hint=f"A {label.lower()} film",
            query=leaf("genre", "contains", GENRE_IDS[genre]),
        )
        for square_id, genre, label in _GENRE_SQUARES
    ]
    squares += [
        BingoSquare(
            id=f"decade-{decade}",
            label=f"Decade: {decade}s",
            hint=f"Released between {decade} and {decade + 9}",
            query=leaf("release_decade", "eq", decade),
        )
        for decade in _DECADES
    ]
    return squares


class BingoSquareMatches(BingoSquare):
    # Caller's watchlist films that fill the square / whose cached facts can't decide yet.
    matches: list[int]
    unknown: int


class BingoSquares(BaseModel):
    squares: list[BingoSquareMatches]


class BingoStampRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    square_id: str
    movie_id: int


def _grid_bingo_seed(user_id: str) -> int:
    """Stable per-user generator seed so local board stamps remain verifiable."""
    return int.from_bytes(sha256(f"watchlist-bingo:{user_id}".encode()).digest()[:4], "big")


class BingoStampResult(BaseModel):
    square_id: str
    movie_id: int
    valid: bool
    # False = the film doesn't fit, None = the cache can't tell yet.
    verdict: bool | None
    reason: str


def _watchlist_ids(session: Session, user_id: str) -> list[int]:
    return list(
        dict.fromkeys(
            session.exec(
                select(LetterboxdWatchlist.movie_id).where(LetterboxdWatchlist.user_id == user_id)
            ).all()
        )
    )


def _verdicts(session: Session, query: FacetQuery, ids: list[int]) -> dict[int, bool | None]:
    """Cache-only three-valued verdicts for `ids` (facets must already be refreshed)."""
    if not ids:
        return {}
    universe, params = universe_ids(ids)
    sql, bindings = compile(query, universe)
    return {
        row.movie_id: None if row.verdict is None else bool(row.verdict)
        for row in session.execute(text(sql), {**params, **bindings})
    }


def _refresh_facets(session: Session, ids: list[int]) -> None:
    if not ids:
        return
    facet_store.refresh(session, ids)
    session.commit()


def _tool_squares(session: Session, user_id: str, ids: list[int]) -> list[BingoSquare]:
    board = tool_board(session, user_id, seed=_grid_bingo_seed(user_id))
    return [
        BingoSquare(
            id=cell["id"],
            label=cell["label"],
            hint=f"Match {cell['label'].lower()}",
            query=FacetQuery.model_validate(cell["query"]),
        )
        for cell in board["cells"]
    ]


def _square_matches(session: Session, squares: list[BingoSquare], ids: list[int]) -> BingoSquares:
    matched = []
    for square in squares:
        verdicts = _verdicts(session, square.query, ids)
        matched.append(
            BingoSquareMatches(
                **square.model_dump(by_alias=True),
                matches=[movie_id for movie_id in ids if verdicts.get(movie_id) is True],
                unknown=sum(verdict is None for verdict in verdicts.values()),
            )
        )
    return BingoSquares(squares=matched)


@router.get("/bingo/squares", response_model=BingoSquares, response_model_exclude_none=True)
def bingo_squares_for_watchlist(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> BingoSquares:
    """Every Bingo square with the caller's watchlist films that fill it (cache only)."""
    ids = _watchlist_ids(session, current_user.id)
    _refresh_facets(session, ids)
    return _square_matches(session, bingo_squares(), ids)


@router.get("/bingo/grid", response_model=BingoSquares, response_model_exclude_none=True)
def bingo_grid_for_watchlist(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> BingoSquares:
    """A seeded Grid Crawler board for Watchlist Bingo, using only cached watchlist facets."""
    ids = _watchlist_ids(session, current_user.id)
    _refresh_facets(session, ids)
    try:
        return _square_matches(session, _tool_squares(session, current_user.id, ids), ids)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Your watchlist needs more varied cached films for a Grid Crawler Bingo board.",
        ) from exc


def _stamp_square(session: Session, user_id: str, square_id: str) -> BingoSquare | None:
    square = next((item for item in bingo_squares() if item.id == square_id), None)
    if square is not None:
        return square
    if ":" not in square_id:
        return None
    ids = _watchlist_ids(session, user_id)
    try:
        return next(
            (item for item in _tool_squares(session, user_id, ids) if item.id == square_id),
            None,
        )
    except ValueError:
        return None


@router.post("/bingo/stamp", response_model=BingoStampResult)
def bingo_stamp(
    body: BingoStampRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> BingoStampResult:
    """Check a watchlist film fills a square, using the server's own query for that square."""
    square = _stamp_square(session, current_user.id, body.square_id)
    if square is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown Bingo square")

    def result(valid: bool, verdict: bool | None, reason: str) -> BingoStampResult:
        return BingoStampResult(
            square_id=square.id,
            movie_id=body.movie_id,
            valid=valid,
            verdict=verdict,
            reason=reason,
        )

    if body.movie_id not in _watchlist_ids(session, current_user.id):
        return result(False, False, "That film isn't on your watchlist")
    _refresh_facets(session, [body.movie_id])
    verdict = _verdicts(session, square.query, [body.movie_id]).get(body.movie_id)
    if verdict is True:
        return result(True, True, "Stamped")
    if verdict is None:
        return result(False, None, "Not enough is known about that film yet")
    return result(False, False, f"That film doesn't fit: {square.hint.lower()}")


@router.get("/march-madness/seed", response_model=list[MovieSummary])
def march_madness_seed(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[MovieSummary]:
    """16 random films from the user's synced Letterboxd watchlist, to seed a bracket."""
    try:
        ids = march_madness.seed_from_watchlist(session, current_user.id)
    except RunSetupError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    titles = {
        row.movie_id: row
        for row in session.exec(
            select(LetterboxdWatchlist).where(
                LetterboxdWatchlist.user_id == current_user.id,
                col(LetterboxdWatchlist.movie_id).in_(ids),
            )
        ).all()
    }
    cached = {
        m.tmdb_id: m
        for m in session.exec(select(CachedMovie).where(col(CachedMovie.tmdb_id).in_(ids))).all()
    }
    return [
        MovieSummary(
            tmdb_id=movie_id,
            title=cached[movie_id].title if movie_id in cached else titles[movie_id].title,
            poster_path=cached[movie_id].poster_path if movie_id in cached else None,
            release_year=(
                parse_release_year(cached[movie_id].release_date)
                if movie_id in cached
                else titles[movie_id].year
            ),
            origin_country=cached[movie_id].origin_country if movie_id in cached else None,
        )
        for movie_id in ids
    ]
