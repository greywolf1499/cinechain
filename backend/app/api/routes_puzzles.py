"""The Daily Bridge ("Cine-Wordle"): one deterministic puzzle per UTC day, computed on request."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.api.deps import get_current_user, get_tmdb_client
from app.db import get_session
from app.models.daily import ATTEMPT_IN_PROGRESS, FINISHED_ATTEMPT_STATUSES, DailyPuzzle
from app.models.user import User
from app.schemas.puzzles import (
    ConvertToRunResult,
    DailyPuzzleOut,
    ForfeitResult,
    ValidateHopRequest,
    ValidateHopResult,
)
from app.services import daily_puzzle
from app.services.tmdb import TMDBClient

router = APIRouter(prefix="/puzzles", tags=["puzzles"])


async def _today(session: Session, tmdb: TMDBClient) -> DailyPuzzle:
    try:
        return await daily_puzzle.get_or_create_puzzle(session, tmdb)
    except daily_puzzle.PuzzleUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "puzzle_unavailable", "message": str(exc)}) from exc


def _puzzle_out(session: Session, puzzle: DailyPuzzle, user: User) -> DailyPuzzleOut:
    attempt = daily_puzzle.get_attempt(session, puzzle.puzzle_date, user.id)
    finished = attempt is not None and attempt.status in FINISHED_ATTEMPT_STATUSES
    return DailyPuzzleOut(
        puzzle_number=puzzle.puzzle_number,
        date=puzzle.puzzle_date,
        start_movie=daily_puzzle.movie_summary(session, puzzle.start_movie_id),
        target_movie=daily_puzzle.movie_summary(session, puzzle.target_movie_id),
        par_hops=puzzle.par_hops,
        attempt=daily_puzzle.attempt_state(session, puzzle, attempt),
        optimal_path=daily_puzzle.optimal_hops(session, puzzle) if finished else None,
    )


@router.get("/daily", response_model=DailyPuzzleOut)
async def get_daily_puzzle(
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    user: User = Depends(get_current_user),
) -> DailyPuzzleOut:
    puzzle = await _today(session, tmdb)
    return _puzzle_out(session, puzzle, user)


@router.post("/daily/validate-hop", response_model=ValidateHopResult)
async def validate_daily_hop(
    payload: ValidateHopRequest,
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    user: User = Depends(get_current_user),
) -> ValidateHopResult:
    """Is there a shared cast/director link between the two films? A valid hop that continues
    the user's chain from its tip is also recorded (and solves the puzzle at the target)."""
    puzzle = await _today(session, tmdb)
    try:
        links, reason, recorded, attempt = await daily_puzzle.attempt_hop(
            session, tmdb, puzzle, user.id, payload.current_movie_id, payload.next_movie_id)
    except daily_puzzle.HopError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return ValidateHopResult(
        valid=reason is None,
        reason=reason,
        connections=links,
        recorded=recorded,
        solved=recorded and payload.next_movie_id == puzzle.target_movie_id,
        next_movie=daily_puzzle.movie_summary(session, payload.next_movie_id),
        attempt=daily_puzzle.attempt_state(session, puzzle, attempt),
    )


@router.post("/daily/undo", response_model=DailyPuzzleOut)
async def undo_daily_hop(
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    user: User = Depends(get_current_user),
) -> DailyPuzzleOut:
    """Takes back the last hop of an open attempt."""
    puzzle = await _today(session, tmdb)
    daily_puzzle.undo_hop(session, puzzle, user.id)
    return _puzzle_out(session, puzzle, user)


@router.post("/daily/forfeit", response_model=ForfeitResult)
async def forfeit_daily_puzzle(
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    user: User = Depends(get_current_user),
) -> ForfeitResult:
    """Gives up: stamps the attempt forfeited (unlocking the Bridge Solver) and reveals the
    optimal route. A solved puzzle can't be forfeited."""
    puzzle = await _today(session, tmdb)
    existing = daily_puzzle.get_attempt(session, puzzle.puzzle_date, user.id)
    if existing is not None and existing.status == "solved":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Today's puzzle is already solved")
    attempt = daily_puzzle.forfeit(session, puzzle, user.id)
    return ForfeitResult(
        puzzle_number=puzzle.puzzle_number,
        par_hops=puzzle.par_hops,
        optimal_path=daily_puzzle.optimal_hops(session, puzzle),
        attempt=daily_puzzle.attempt_state(session, puzzle, attempt),
    )


@router.post("/daily/convert-to-run", response_model=ConvertToRunResult, status_code=status.HTTP_201_CREATED)
async def convert_daily_to_run(
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    user: User = Depends(get_current_user),
) -> ConvertToRunResult:
    """Seeds a planned Challenge Run with the solved chain (or, after a forfeit, the optimal
    route). Only available once the puzzle is solved or forfeited, so it can't leak the answer."""
    puzzle = await _today(session, tmdb)
    attempt = daily_puzzle.get_attempt(session, puzzle.puzzle_date, user.id)
    if attempt is None or attempt.status == ATTEMPT_IN_PROGRESS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Solve or forfeit today's puzzle before queueing it as a run")
    run, count = daily_puzzle.convert_to_run(session, puzzle, attempt, user.id)
    return ConvertToRunResult(run_id=run.id, movies=count)
