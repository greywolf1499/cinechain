"""The Rotten Tomatoes Split: the pool of films critics and audiences disagree about."""

import time

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.api.deps import get_omdb_client, get_tmdb_client, run_participant_guard
from app.db import get_session
from app.engines.rt_split import MIN_DIVERGENCE, RT_SPLIT, RottenTomatoesSplitEngine, SplitScores
from app.integrations.omdb import OMDbClient
from app.models.cache import CachedMovie, CachedMovieRating
from app.models.run import Run, RunStep
from app.services import cache_repo
from app.services.tmdb import TMDBClient
from app.utils.dates import parse_release_year

router = APIRouter(prefix="/runs", tags=["split"])

MAX_SCAN = 20
SCAN_DEADLINE_SECONDS = 20.0
POOL_SIZE = 30


class SplitCandidate(BaseModel):
    movie_id: int
    title: str
    year: int | None = None
    poster_path: str | None = None
    critic_score: int
    audience_score: int
    divergence: int
    favours: str  # "critics" | "audience": who rated it higher


class SplitPool(BaseModel):
    omdb_enabled: bool
    min_divergence: int
    scanned: int
    candidates: list[SplitCandidate]


class SplitUnqualified(BaseModel):
    qualifies: bool = False
    reason: str


def _candidate(movie: CachedMovie, scores: SplitScores) -> SplitCandidate:
    return SplitCandidate(
        movie_id=movie.tmdb_id,
        title=movie.title,
        year=parse_release_year(movie.release_date),
        poster_path=movie.poster_path,
        critic_score=scores.critic,
        audience_score=scores.audience,
        divergence=scores.divergence,
        favours="critics" if scores.critic > scores.audience else "audience",
    )


@router.post(
    "/{run_id}/split/ratings/{movie_id}/retry",
    response_model=SplitCandidate | SplitUnqualified,
)
async def retry_split_ratings(
    movie_id: int,
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    omdb: OMDbClient = Depends(get_omdb_client),
) -> SplitCandidate | SplitUnqualified:
    if run.game_type != RT_SPLIT:
        raise HTTPException(400, detail="This isn't a Rotten Tomatoes Split run")
    await cache_repo.get_movie_ratings(session, tmdb, omdb, movie_id, force=True)
    engine = RottenTomatoesSplitEngine(session, tmdb)
    validation = await engine.validate_candidate(movie_id, run.rules_config or {})
    if not validation.valid:
        return SplitUnqualified(reason=validation.reason or "This film does not qualify")
    movie = session.get(CachedMovie, movie_id)
    scores = engine.scores_of(movie_id)
    assert movie is not None and scores is not None
    return _candidate(movie, scores)


@router.get("/{run_id}/split-pool", response_model=SplitPool)
async def get_split_pool(
    scan: int = Query(
        default=0, ge=0, le=MAX_SCAN, description="Rate up to N more cached films first"
    ),
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    omdb: OMDbClient = Depends(get_omdb_client),
) -> SplitPool:
    """Cached films whose Tomatometer and audience score diverge by 25+ points, biggest split
    first. `scan=N` first fetches OMDb ratings for the N most popular cached films that have none
    yet (OMDb has no bulk lookup, so the pool grows a little at a time)."""
    if run.game_type != RT_SPLIT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="This isn't a Rotten Tomatoes Split run"
        )
    scanned = 0
    if scan and omdb.enabled:
        rated = select(CachedMovieRating.movie_id)
        unrated = session.exec(
            select(CachedMovie.tmdb_id)
            .where(col(CachedMovie.tmdb_id).not_in(rated))
            .order_by(col(CachedMovie.popularity).desc())
            .limit(scan)
        ).all()
        deadline = time.monotonic() + SCAN_DEADLINE_SECONDS
        for movie_id in unrated:
            if time.monotonic() >= deadline:
                break
            await cache_repo.get_movie_ratings(session, tmdb, omdb, movie_id)
            scanned += 1

    watched = set(session.exec(select(RunStep.movie_id).where(RunStep.run_id == run.id)).all())
    engine = RottenTomatoesSplitEngine(session, tmdb)
    return SplitPool(
        omdb_enabled=omdb.enabled,
        min_divergence=MIN_DIVERGENCE,
        scanned=scanned,
        candidates=[
            _candidate(movie, scores)
            for movie, scores in engine.split_pool(exclude_ids=list(watched), limit=POOL_SIZE)
        ],
    )
