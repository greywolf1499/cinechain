"""The Perfect Marathon Router: order films by tonal smoothness and queue them as a run."""

from __future__ import annotations

import logging
import time
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlmodel import Session, col, select

from app.api.deps import get_current_user, get_tmdb_client
from app.api.routes_runs import _step_fields_from_movie, _to_run_detail
from app.db import get_session
from app.models.cache import CachedGenre, CachedMovie, CachedMovieRating
from app.models.run import DEFAULT_RULES_CONFIG, Run, RunParticipant, RunStep
from app.models.user import User
from app.schemas.runs import RunDetail
from app.services import cache_repo
from app.services import marathon_router as mr
from app.services.tmdb import TMDBClient, TMDBError, TMDBNotFoundError
from app.utils.dates import parse_release_year
from app.utils.ids import utcnow

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tools/router", tags=["marathon-router"])

# Any film may be logged in a Roulette run, so a router marathon never trips a link rule.
ROUTER_GAME_TYPE = "roulette"
ROUTER_METADATA_KEY = "marathon_router"


def _distinct(ids: list[int]) -> list[int]:
    if len(set(ids)) != len(ids):
        raise ValueError("Each film can only appear once")
    return ids


class OptimizeRequest(BaseModel):
    movie_ids: list[int] = Field(min_length=mr.MIN_FILMS, max_length=mr.MAX_FILMS)
    # Relative importance; rescaled so the four sum to 1. Omitted = the router's defaults.
    weight_genre: float | None = Field(default=None, ge=0, le=1)
    weight_year: float | None = Field(default=None, ge=0, le=1)
    weight_runtime: float | None = Field(default=None, ge=0, le=1)
    weight_rating: float | None = Field(default=None, ge=0, le=1)

    _unique = field_validator("movie_ids")(_distinct)


class ConvertRequest(BaseModel):
    run_name: str = Field(min_length=1, max_length=128)
    movie_ids: list[int] = Field(min_length=2, max_length=mr.MAX_FILMS)

    _unique = field_validator("movie_ids")(_distinct)

    @field_validator("run_name")
    @classmethod
    def _stripped(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("run_name can't be blank")
        return value


class RouterFilmOut(BaseModel):
    movie_id: int
    title: str
    year: int | None = None
    poster_path: str | None = None
    overview: str | None = None
    runtime: int | None = None
    rating: float | None = None
    genres: list[str] = []


class DeltasOut(BaseModel):
    genre: float
    year: float
    runtime: float
    rating: float


class TransitionOut(BaseModel):
    from_movie_id: int
    to_movie_id: int
    cost: float
    label: str
    summary: str
    deltas: DeltasOut


class WeightsOut(BaseModel):
    weight_genre: float
    weight_year: float
    weight_runtime: float
    weight_rating: float


class OptimizeResponse(BaseModel):
    ordered_movie_ids: list[int]
    initial_whiplash_score: float
    optimized_whiplash_score: float
    improvement_percentage: float
    transitions: list[TransitionOut]
    films: list[RouterFilmOut]  # in optimized order
    method: str
    weights: WeightsOut
    calculation_ms: float


def _rating_of(movie: CachedMovie, imdb: CachedMovieRating | None) -> float | None:
    try:
        if imdb is not None and imdb.imdb_rating and imdb.imdb_rating != "N/A":
            return float(imdb.imdb_rating)
    except ValueError:
        pass
    return movie.vote_average if movie.vote_average else None


async def _load_movies(session: Session, tmdb: TMDBClient, ids: list[int]) -> dict[int, CachedMovie]:
    """Every id from the cache, fetching what is missing (or cached without a runtime)."""
    movies: dict[int, CachedMovie] = {}
    for movie_id in ids:
        cached = session.get(CachedMovie, movie_id)
        try:
            if cached is None:
                movies[movie_id] = await cache_repo.get_movie(session, tmdb, movie_id)
            elif cached.runtime is None:
                try:
                    movies[movie_id] = await cache_repo.get_movie(
                        session, tmdb, movie_id, refresh=True)
                except TMDBError:
                    session.rollback()
                    movies[movie_id] = cached  # unknown runtime just scores as neutral
            else:
                movies[movie_id] = cached
        except TMDBNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Film {movie_id} not found") from exc
        except TMDBError as exc:
            logger.warning("Marathon router couldn't load film %s", movie_id, exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Couldn't load film {movie_id} from TMDB: {exc}") from exc
    return movies


def _router_films(session: Session, movies: dict[int, CachedMovie], ids: list[int]) -> list[mr.RouterFilm]:
    ratings = {
        r.movie_id: r for r in session.exec(
            select(CachedMovieRating).where(col(CachedMovieRating.movie_id).in_(ids))).all()}
    return [
        mr.RouterFilm(
            movie_id=movie_id, title=movies[movie_id].title,
            genres=tuple(movies[movie_id].genre_ids or ()),
            year=parse_release_year(movies[movie_id].release_date),
            runtime=movies[movie_id].runtime or None,
            rating=_rating_of(movies[movie_id], ratings.get(movie_id)))
        for movie_id in ids]


def _genre_names(session: Session) -> dict[int, str]:
    return {g.id: g.name for g in session.exec(select(CachedGenre)).all()}


@router.post("/optimize", response_model=OptimizeResponse)
async def optimize_marathon(
    payload: OptimizeRequest,
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    _user: User = Depends(get_current_user),
) -> OptimizeResponse:
    """Reorders the films for the least tonal whiplash and scores the before and after."""
    defaults = mr.Weights()
    try:
        weights = mr.Weights(
            genre=defaults.genre if payload.weight_genre is None else payload.weight_genre,
            year=defaults.year if payload.weight_year is None else payload.weight_year,
            runtime=defaults.runtime if payload.weight_runtime is None else payload.weight_runtime,
            rating=defaults.rating if payload.weight_rating is None else payload.weight_rating,
        ).normalised()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    movies = await _load_movies(session, tmdb, payload.movie_ids)
    films = _router_films(session, movies, payload.movie_ids)
    names = _genre_names(session)

    started = time.perf_counter()
    result = mr.optimize(films, weights, names)
    elapsed_ms = (time.perf_counter() - started) * 1000

    by_id = {film.movie_id: film for film in films}
    return OptimizeResponse(
        ordered_movie_ids=result.ordered_movie_ids,
        initial_whiplash_score=result.initial_whiplash_score,
        optimized_whiplash_score=result.optimized_whiplash_score,
        improvement_percentage=result.improvement_percentage,
        transitions=[
            TransitionOut(
                from_movie_id=t.from_movie_id, to_movie_id=t.to_movie_id, cost=t.cost,
                label=t.label, summary=t.summary,
                deltas=DeltasOut(
                    genre=round(t.deltas.genre, 3), year=round(t.deltas.year, 3),
                    runtime=round(t.deltas.runtime, 3), rating=round(t.deltas.rating, 3)))
            for t in result.transitions],
        films=[
            RouterFilmOut(
                movie_id=movie_id, title=by_id[movie_id].title, year=by_id[movie_id].year,
                poster_path=movies[movie_id].poster_path, overview=movies[movie_id].overview,
                runtime=by_id[movie_id].runtime, rating=by_id[movie_id].rating,
                genres=[names[g] for g in by_id[movie_id].genres if g in names])
            for movie_id in result.ordered_movie_ids],
        method=result.method,
        weights=WeightsOut(
            weight_genre=round(result.weights.genre, 4), weight_year=round(result.weights.year, 4),
            weight_runtime=round(result.weights.runtime, 4),
            weight_rating=round(result.weights.rating, 4)),
        calculation_ms=round(elapsed_ms, 1))


@router.post("/convert-to-run", response_model=RunDetail, status_code=status.HTTP_201_CREATED)
async def convert_to_run(
    payload: ConvertRequest,
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    current_user: User = Depends(get_current_user),
) -> RunDetail:
    """An active run whose planned steps are the marathon, in exactly the order given."""
    movies = await _load_movies(session, tmdb, payload.movie_ids)
    films = _router_films(session, movies, payload.movie_ids)
    transitions = mr.build_transitions(films, mr.Weights(), _genre_names(session))

    run = Run(
        name=payload.run_name, game_type=ROUTER_GAME_TYPE, rules_config=dict(DEFAULT_RULES_CONFIG))
    session.add(run)
    session.flush()
    session.add(RunParticipant(run_id=run.id, user_id=current_user.id, role="owner"))
    base = utcnow()
    for index, movie_id in enumerate(payload.movie_ids):
        metadata = None
        if index > 0:
            hop = transitions[index - 1]
            metadata = {ROUTER_METADATA_KEY: {"whiplash": hop.cost, "label": hop.label}}
        session.add(RunStep(
            run_id=run.id, logged_by_user_id=current_user.id, status="planned", watched_at=None,
            transition_metadata=metadata,
            # Distinct, ordered timestamps: runs read their steps back by `logged_at`.
            logged_at=base + timedelta(milliseconds=index),
            **_step_fields_from_movie(movies[movie_id])))
    session.commit()
    session.refresh(run)
    return _to_run_detail(session, run)
