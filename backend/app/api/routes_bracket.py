"""Watchlist March Madness: deciding the bracket's matchups (and, for partners, voting on them)."""

import copy

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.deps import get_current_user, get_tmdb_client, run_participant_guard
from app.api.routes_runs import _ensure_run_open, _step_fields_from_movie, _to_run_detail
from app.db import get_session
from app.engines import march_madness
from app.models.run import RUN_STATUS_COMPLETED, Run, RunParticipant, RunStep
from app.models.user import User
from app.schemas.runs import RunDetail
from app.services import cache_repo, llm
from app.utils.ids import utcnow

router = APIRouter(prefix="/runs", tags=["bracket"])


class AdvanceRequest(BaseModel):
    matchup_id: str
    winning_movie_id: int


class VoteRequest(BaseModel):
    matchup_id: str
    movie_id: int


class CommentaryRequest(BaseModel):
    matchup_id: str


class CommentaryOut(BaseModel):
    matchup_id: str
    commentary: str  # "" = the AI model is off
    enabled: bool
    cached: bool = False


COMMENTARY_KEY = "bracket_commentary"


def _bracket_of(run: Run) -> dict:
    if run.game_type != march_madness.MARCH_MADNESS or not (run.rules_config or {}).get("bracket"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="This run has no bracket"
        )
    return run.rules_config["bracket"]


def _bracket_error(exc: march_madness.BracketError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


async def _decide(
    session: Session,
    tmdb,
    run: Run,
    user: User,
    bracket: dict,
    matchup_id: str,
    winner_id: int,
) -> None:
    """Applies the result: moves the winner on, logs it as a watched step (once per film) and,
    if it was the final, crowns the champion and completes the run."""
    try:
        updated, round_name, crowned = march_madness.advance_matchup(bracket, matchup_id, winner_id)
    except march_madness.BracketError as exc:
        raise _bracket_error(exc) from exc
    movie = await cache_repo.get_movie(session, tmdb, winner_id)
    already_logged = session.exec(
        select(RunStep).where(RunStep.run_id == run.id, RunStep.movie_id == winner_id)
    ).first()
    if already_logged is None:
        session.add(
            RunStep(
                run_id=run.id,
                logged_by_user_id=user.id,
                status="watched",
                watched_at=utcnow(),
                transition_metadata={"bracket_round": round_name, "matchup_id": matchup_id},
                **_step_fields_from_movie(movie),
            )
        )
    rules = copy.deepcopy(run.rules_config or {})
    rules["bracket"] = updated
    run.rules_config = rules
    if crowned:
        run.status = RUN_STATUS_COMPLETED
        run.status_reason = f"Champion Crowned: {movie.title}!"
        run.completed_at = utcnow()
    session.add(run)
    session.commit()
    session.refresh(run)


@router.post("/{run_id}/bracket/advance", response_model=RunDetail)
async def advance_bracket(
    payload: AdvanceRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    run: Run = Depends(run_participant_guard),
    tmdb=Depends(get_tmdb_client),
) -> RunDetail:
    """Resolves one matchup: the winner moves into the next round and is logged as watched. Deciding
    the final crowns the champion and completes the run."""
    bracket = _bracket_of(run)
    _ensure_run_open(run)
    await _decide(
        session, tmdb, run, current_user, bracket, payload.matchup_id, payload.winning_movie_id
    )
    return _to_run_detail(session, run)


@router.post("/{run_id}/bracket/vote", response_model=RunDetail)
async def vote_in_bracket(
    payload: VoteRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    run: Run = Depends(run_participant_guard),
    tmdb=Depends(get_tmdb_client),
) -> RunDetail:
    """A partner's vote on an open matchup. When a strict majority of all participants agrees the
    matchup resolves by itself; a tie stays open for anyone to settle with Advance Winner."""
    bracket = _bracket_of(run)
    _ensure_run_open(run)
    try:
        updated = march_madness.record_vote(
            bracket, payload.matchup_id, current_user.id, payload.movie_id
        )
    except march_madness.BracketError as exc:
        raise _bracket_error(exc) from exc
    participant_ids = [
        p.user_id
        for p in session.exec(select(RunParticipant).where(RunParticipant.run_id == run.id)).all()
    ]
    _, _, matchup = march_madness.find_matchup(updated, payload.matchup_id)
    winner = march_madness.majority_winner(matchup, participant_ids)
    if winner is not None:
        await _decide(session, tmdb, run, current_user, updated, payload.matchup_id, winner)
    else:
        rules = copy.deepcopy(run.rules_config or {})
        rules["bracket"] = updated
        run.rules_config = rules
        session.add(run)
        session.commit()
        session.refresh(run)
    return _to_run_detail(session, run)


@router.post("/{run_id}/bracket/commentary", response_model=CommentaryOut)
async def matchup_commentary(
    payload: CommentaryRequest,
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
) -> CommentaryOut:
    """The AI announcer's one-sentence "Tale of the Tape" for a matchup whose two films are known.
    Generated once and kept in `rules_config["bracket_commentary"][matchup_id]`; with the model
    off the answer is empty (and nothing is stored)."""
    bracket = _bracket_of(run)
    _ensure_run_open(run)
    try:
        _, _, matchup = march_madness.find_matchup(bracket, payload.matchup_id)
    except march_madness.BracketError as exc:
        raise _bracket_error(exc) from exc
    if matchup["a"] is None or matchup["b"] is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Both films must be in the matchup first"
        )
    stored = (run.rules_config or {}).get(COMMENTARY_KEY) or {}
    if payload.matchup_id in stored:
        return CommentaryOut(
            matchup_id=payload.matchup_id,
            commentary=stored[payload.matchup_id],
            enabled=True,
            cached=True,
        )

    config = llm.load_config(session)
    if not config.enabled:
        return CommentaryOut(matchup_id=payload.matchup_id, commentary="", enabled=False)
    films = (run.rules_config or {}).get("bracket_films") or {}
    try:
        text = await llm.generate_matchup_commentary(
            films.get(str(matchup["a"]), {}), films.get(str(matchup["b"]), {}), config
        )
    except llm.LlmUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    session.refresh(run)  # a partner may have generated it meanwhile: the first one wins
    rules = copy.deepcopy(run.rules_config or {})
    kept = rules.setdefault(COMMENTARY_KEY, {})
    text = kept.setdefault(payload.matchup_id, text)
    run.rules_config = rules
    session.add(run)
    session.commit()
    return CommentaryOut(matchup_id=payload.matchup_id, commentary=text, enabled=True)
