from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, select

from app.api.deps import get_current_user, get_tmdb_client, run_participant_guard
from app.db import get_session
from app.engines.registry import get_engine
from app.models.cache import CachedMovie
from app.models.run import DEFAULT_RULES_CONFIG, Run, RunParticipant, RunStep
from app.models.user import User
from app.schemas.discovery import DiscoveryCandidate
from app.schemas.engine import RunStats, Suggestion, SuggestionFilters
from app.schemas.runs import (
    MarkWatchedRequest,
    ParticipantAdd,
    ParticipantPublic,
    RunCreate,
    RunDetail,
    RunStepCreate,
    RunStepPublic,
    RunStepUpdate,
    RunSummary,
    RunUpdate,
)
from app.services import cache_repo
from app.services.tmdb import TMDBClient
from app.utils.dates import parse_release_year
from app.utils.ids import utcnow

router = APIRouter(prefix="/runs", tags=["runs"])

VALID_STATUSES = {"active", "completed", "abandoned"}


def _step_fields_from_movie(movie: CachedMovie) -> dict:
    return {
        "movie_id": movie.tmdb_id,
        "movie_title": movie.title,
        "movie_poster_path": movie.poster_path,
        "movie_release_year": parse_release_year(movie.release_date),
        "movie_origin_country": movie.origin_country,
    }


def _last_step(session: Session, run_id: str) -> RunStep | None:
    return session.exec(
        select(RunStep).where(RunStep.run_id == run_id).order_by(
            RunStep.logged_at.desc())
    ).first()


def _run_rules(run: Run) -> dict:
    return run.rules_config or dict(DEFAULT_RULES_CONFIG)


async def _enforce_run_rules(
    session: Session, tmdb: TMDBClient, run: Run, movie: CachedMovie, payload: RunStepCreate
) -> dict:
    """Validates a candidate step against the run's rules_config.

    Returns extra transition_metadata fields to merge in (repeat_penalty,
    runtime_flagged, wildcard_used). Raises 409 on any violation not covered
    by `payload.force`.
    """
    rules = _run_rules(run)
    force = payload.force
    extra_metadata: dict = {}
    broke_a_rule = False

    already_watched = session.exec(
        select(RunStep).where(RunStep.run_id == run.id,
                              RunStep.movie_id == movie.tmdb_id)
    ).first()
    if already_watched is not None:
        allow_repeats = rules.get("allow_repeats", "strict")
        if allow_repeats == "strict":
            if not force:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "valid": False,
                        "reason": "Movie already watched in this run",
                        "connections": [],
                    },
                )
            broke_a_rule = True
        elif allow_repeats == "penalty":
            extra_metadata["repeat_penalty"] = True

    min_runtime = rules.get("min_runtime", 0)
    if min_runtime and movie.runtime is not None and movie.runtime < min_runtime:
        if not force:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "valid": False,
                    "reason": f"Runtime is under this run's {min_runtime}-minute minimum",
                    "connections": [],
                },
            )
        broke_a_rule = True
        extra_metadata["runtime_flagged"] = True

    previous = _last_step(session, run.id)
    if previous is not None:
        engine = get_engine(run.game_type, session, tmdb)
        result = await engine.validate_next_step(
            previous.movie_id, movie.tmdb_id, cast_limit=rules.get(
                "max_cast_order")
        )
        if not result.valid:
            if not force:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail=result.model_dump())
            broke_a_rule = True
        elif rules.get("no_consecutive_actor", True):
            chosen_actor_id = (
                payload.transition_metadata or {}).get("actor_id")
            previous_actor_id = (
                previous.transition_metadata or {}).get("actor_id")
            if (
                chosen_actor_id is not None
                and previous_actor_id is not None
                and chosen_actor_id == previous_actor_id
            ):
                if not force:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "valid": False,
                            "reason": "Consecutive jumps using the same actor are disabled",
                            "connections": [],
                        },
                    )
                broke_a_rule = True

    if force and broke_a_rule:
        budget = rules.get("wildcards_budget", 2)
        if budget != -1:
            if budget <= 0:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "valid": False, "reason": "No wildcards remaining", "connections": []},
                )
            run.rules_config = {**rules, "wildcards_budget": budget - 1}
            session.add(run)
        extra_metadata["wildcard_used"] = True

    return extra_metadata


def _to_run_detail(session: Session, run: Run) -> RunDetail:
    steps = session.exec(
        select(RunStep).where(RunStep.run_id ==
                              run.id).order_by(RunStep.logged_at)
    ).all()
    participants = session.exec(
        select(RunParticipant)
        .where(RunParticipant.run_id == run.id)
        .order_by(RunParticipant.joined_at)
    ).all()
    return RunDetail(
        **RunSummary.model_validate(run).model_dump(),
        steps=[RunStepPublic.model_validate(s) for s in steps],
        participants=[ParticipantPublic.model_validate(
            p) for p in participants],
    )


@router.get("", response_model=list[RunSummary])
def list_runs(
    status_filter: str | None = Query(default=None, alias="status"),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[Run]:
    statement = (
        select(Run)
        .join(RunParticipant, RunParticipant.run_id == Run.id)
        .where(RunParticipant.user_id == current_user.id)
    )
    if status_filter is not None:
        statement = statement.where(Run.status == status_filter)
    statement = statement.order_by(Run.created_at.desc())
    return list(session.exec(statement).all())


@router.post("", response_model=RunDetail, status_code=status.HTTP_201_CREATED)
async def create_run(
    payload: RunCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> RunDetail:
    participant_ids = {current_user.id, *payload.participant_user_ids}
    for user_id in participant_ids:
        if session.get(User, user_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"User {user_id} not found"
            )

    run = Run(
        name=payload.name,
        game_type=payload.game_type,
        rules_config=payload.rules_config if payload.rules_config is not None else dict(
            DEFAULT_RULES_CONFIG
        ),
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    for user_id in participant_ids:
        role = "owner" if user_id == current_user.id else "member"
        session.add(RunParticipant(run_id=run.id, user_id=user_id, role=role))
    session.commit()

    if payload.seed_movie_id is not None:
        movie = await cache_repo.get_movie(session, tmdb, payload.seed_movie_id)
        step = RunStep(
            run_id=run.id,
            logged_by_user_id=current_user.id,
            status="watched",
            watched_at=utcnow(),
            **_step_fields_from_movie(movie),
        )
        session.add(step)
        session.commit()

    return _to_run_detail(session, run)


@router.get("/{run_id}", response_model=RunDetail)
def get_run(session: Session = Depends(get_session), run: Run = Depends(run_participant_guard)):
    return _to_run_detail(session, run)


@router.patch("/{run_id}", response_model=RunDetail)
def update_run(
    payload: RunUpdate,
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
):
    if payload.name is not None:
        run.name = payload.name
    if payload.status is not None:
        if payload.status not in VALID_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid status"
            )
        run.status = payload.status
        run.completed_at = utcnow() if payload.status == "completed" else None
    session.add(run)
    session.commit()
    session.refresh(run)
    return _to_run_detail(session, run)


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_run(
    session: Session = Depends(get_session), run: Run = Depends(run_participant_guard)
) -> None:
    # No DB-level ON DELETE CASCADE is configured, so children go first.
    for step in session.exec(select(RunStep).where(RunStep.run_id == run.id)).all():
        session.delete(step)
    for participant in session.exec(
        select(RunParticipant).where(RunParticipant.run_id == run.id)
    ).all():
        session.delete(participant)
    # Flush children in their own transaction: no ORM relationship() links
    # Run to its children, so a single flush can't be trusted to order
    # deletes correctly across unrelated mapped tables.
    session.commit()

    session.delete(run)
    session.commit()


@router.post(
    "/{run_id}/participants", response_model=ParticipantPublic, status_code=status.HTTP_201_CREATED
)
def add_participant(
    payload: ParticipantAdd,
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
):
    if session.get(User, payload.user_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if session.get(RunParticipant, (run.id, payload.user_id)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Already a participant"
        )
    participant = RunParticipant(
        run_id=run.id, user_id=payload.user_id, role=payload.role)
    session.add(participant)
    session.commit()
    session.refresh(participant)
    return participant


@router.delete("/{run_id}/participants/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_participant(
    user_id: str,
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
) -> None:
    participant = session.get(RunParticipant, (run.id, user_id))
    if participant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Participant not found")
    session.delete(participant)
    session.commit()


@router.post("/{run_id}/steps", response_model=RunStepPublic, status_code=status.HTTP_201_CREATED)
async def create_step(
    payload: RunStepCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
):
    movie = await cache_repo.get_movie(session, tmdb, payload.movie_id)
    extra_metadata = await _enforce_run_rules(session, tmdb, run, movie, payload)

    transition_metadata = payload.transition_metadata
    if extra_metadata:
        transition_metadata = {**(transition_metadata or {}), **extra_metadata}

    watched_at = payload.watched_at if payload.status == "watched" else None
    if payload.status == "watched" and watched_at is None:
        watched_at = utcnow()

    step = RunStep(
        run_id=run.id,
        transition_metadata=transition_metadata,
        user_notes=payload.user_notes,
        status=payload.status,
        watched_at=watched_at,
        logged_by_user_id=current_user.id,
        **_step_fields_from_movie(movie),
    )
    session.add(step)
    session.commit()
    session.refresh(step)
    return step


@router.patch("/{run_id}/steps/{step_id}/mark-watched", response_model=RunStepPublic)
def mark_step_watched(
    step_id: str,
    payload: MarkWatchedRequest,
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
):
    step = session.get(RunStep, step_id)
    if step is None or step.run_id != run.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Step not found")
    if step.status == "watched":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Step is already marked as watched"
        )
    step.status = "watched"
    step.watched_at = payload.watched_at or utcnow()
    if payload.user_notes is not None:
        step.user_notes = payload.user_notes
    session.add(step)
    session.commit()
    session.refresh(step)
    return step


@router.patch("/{run_id}/steps/{step_id}", response_model=RunStepPublic)
def update_step(
    step_id: str,
    payload: RunStepUpdate,
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
):
    step = session.get(RunStep, step_id)
    if step is None or step.run_id != run.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Step not found")
    if payload.user_notes is not None:
        step.user_notes = payload.user_notes
    if payload.transition_metadata is not None:
        step.transition_metadata = payload.transition_metadata
    if payload.watched_at is not None:
        step.watched_at = payload.watched_at
        # Setting a watched date IS the act of marking it watched - keep the
        # 1-click "Mark as Watched" quick action (which PATCHes only
        # watched_at, not status) from leaving stale "planned" state behind.
        if step.status == "planned":
            step.status = "watched"
    session.add(step)
    session.commit()
    session.refresh(step)
    return step


@router.delete("/{run_id}/steps/{step_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_step(
    step_id: str,
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
) -> None:
    step = session.get(RunStep, step_id)
    if step is None or step.run_id != run.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Step not found")

    last_step = _last_step(session, run.id)
    if last_step is None or last_step.id != step.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only the most recently logged step can be deleted",
        )

    session.delete(step)
    session.commit()


@router.get("/{run_id}/suggestions", response_model=list[Suggestion])
async def get_run_suggestions(
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    country: str | None = Query(default=None),
    decade: int | None = Query(default=None),
    genre_id: int | None = Query(default=None),
) -> list[Suggestion]:
    current = _last_step(session, run.id)
    if current is None:
        return []
    logged_movie_ids = [
        step.movie_id for step in session.exec(select(RunStep).where(RunStep.run_id == run.id)).all()
    ]
    engine = get_engine(run.game_type, session, tmdb)
    filters = SuggestionFilters(
        country=country, decade=decade, genre_id=genre_id)
    return await engine.get_suggestions(current.movie_id, logged_movie_ids, filters)


@router.get("/{run_id}/stats", response_model=RunStats)
async def get_run_stats(
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> RunStats:
    steps = list(
        session.exec(
            select(RunStep).where(RunStep.run_id ==
                                  run.id).order_by(RunStep.logged_at)
        ).all()
    )
    engine = get_engine(run.game_type, session, tmdb)
    return await engine.compute_stats(steps)


@router.get("/{run_id}/discover", response_model=list[DiscoveryCandidate])
async def discover_next_movies(
    frontier_movie_id: int = Query(...),
    mode: str = Query(default="or", pattern="^(or|and)$"),
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> list[DiscoveryCandidate]:
    """Unified "Pick Next" pool: every top-billed cast member's filmography,
    pooled into one set of candidates (Phase 13). Movies already logged in
    this run are NOT excluded from the pool - they're flagged
    `already_in_run` instead, so the frontend can show/disable them rather
    than silently hiding them.
    """
    engine = get_engine(run.game_type, session, tmdb)
    rules = _run_rules(run)
    try:
        candidates = await engine.discover_candidates(
            frontier_movie_id=frontier_movie_id,
            mode=mode,
            cast_limit=rules.get("max_cast_order"),
        )
    except NotImplementedError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Discovery is not supported for game_type '{run.game_type}'",
        ) from None

    logged_movie_ids = {
        step.movie_id for step in session.exec(select(RunStep).where(RunStep.run_id == run.id)).all()
    }
    ordered_steps = session.exec(
        select(RunStep).where(RunStep.run_id ==
                              run.id).order_by(RunStep.logged_at)
    ).all()
    step_number_by_movie_id: dict[int, int] = {}
    for index, step in enumerate(ordered_steps):
        step_number_by_movie_id.setdefault(step.movie_id, index + 1)
    for candidate in candidates:
        candidate.already_in_run = candidate.movie_id in logged_movie_ids
        candidate.existing_step_number = step_number_by_movie_id.get(
            candidate.movie_id)
    return candidates
