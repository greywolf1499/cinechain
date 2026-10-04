from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, select

from app.api.deps import get_current_user, get_omdb_client, get_tmdb_client, run_participant_guard
from app.db import get_session
from app.engines import chaos, rabbit_hole
from app.engines.base import RunSetupError
from app.engines.meet_in_middle import (
    MEET_IN_THE_MIDDLE,
    SIDE_HEAD,
    SIDE_TAIL,
    MeetInTheMiddleEngine,
    split_sides,
)
from app.engines.registry import ENGINE_REGISTRY, get_engine
from app.engines.rt_split import RT_SPLIT, RottenTomatoesSplitEngine
from app.engines.rt_split import VICTORY_PREFIX as SPLIT_VICTORY_PREFIX
from app.engines.rt_split import compute_scores as compute_split_scores
from app.engines.rt_split import winning_team as split_winner
from app.engines.tug_of_war import (
    TUG_OF_WAR,
    VICTORY_PREFIX,
    compute_scores,
    leading_team,
)
from app.integrations.omdb import OMDbClient
from app.models.cache import CachedMovie
from app.models.run import (
    DEFAULT_RULES_CONFIG,
    IMPORT_GAME_TYPE,
    LEGACY_ENGINE_VERSION,
    RUN_STATUS_ACTIVE,
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FORFEITED,
    TERMINAL_RUN_STATUSES,
    Run,
    RunParticipant,
    RunStep,
)
from app.models.user import User
from app.schemas.discovery import DiscoveryCandidate
from app.schemas.engine import (
    ConstraintInfo,
    RunStats,
    Suggestion,
    SuggestionFilters,
    TunnelState,
    ValidationResult,
)
from app.schemas.runs import (
    MODIFIER_UPDATE_KEYS,
    ForkAccept,
    ForkOffer,
    ForkVeto,
    GoldenVeto,
    GoldenVetoResult,
    MarkWatchedRequest,
    ParticipantAdd,
    ParticipantPublic,
    RunCreate,
    RunDetail,
    RunRulesUpdate,
    RunStepCreate,
    RunStepPublic,
    RunStepUpdate,
    RunSummary,
    RunUpdate,
    StepValidateRequest,
)
from app.services import blind_fork, bounties, cache_repo, pool_options
from app.services.tmdb import TMDBClient
from app.services.veto import consume_veto_token
from app.utils.dates import parse_release_year
from app.utils.ids import utcnow

router = APIRouter(prefix="/runs", tags=["runs"])

MANUAL_STATUS_REASONS = {
    RUN_STATUS_COMPLETED: "Marked as completed",
    RUN_STATUS_FORFEITED: "Forfeited by a participant",
}


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


def _run_history(session: Session, run_id: str) -> list[RunStep]:
    """Every step logged so far, oldest first (the history modifiers like country_cooldown read)."""
    return list(session.exec(
        select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.logged_at)).all())


# Metadata keys only the server may set: a client-supplied `collision` would be a free win.
SERVER_OWNED_METADATA = (
    "tunnel_side", "collision", "collision_with", "golden_reunion", "character_hop",
    # Bounty Board awards and Rotten Tomatoes Split settlements: a client could mint wildcards/points.
    "completed_bounty", "bounty_replacement",
    "household_score", "critic_score", "audience_score", "divergence", "point_to")
# Link bonuses the engine detected: stamped from its own validation, never from the client.
BONUS_LINK_KEYS = ("golden_reunion", "character_hop")


def _without_server_keys(metadata: dict | None) -> dict | None:
    if metadata is None:
        return None
    return {k: v for k, v in metadata.items() if k not in SERVER_OWNED_METADATA}


def _tunnel_sides(
    session: Session, run: Run, side: str | None
) -> tuple[list[RunStep], list[RunStep]]:
    """(the steps of the end being extended, the steps of the opposite end)."""
    if side not in (SIDE_HEAD, SIDE_TAIL):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Choose which end of the tunnel you are extending (head or tail)")
    head, tail = split_sides(_run_history(session, run.id))
    return (head, tail) if side == SIDE_HEAD else (tail, head)


def _run_rules(run: Run) -> dict:
    return run.rules_config or dict(DEFAULT_RULES_CONFIG)


def _count_wildcards_consumed(session: Session, run_id: str) -> int:
    steps = session.exec(select(RunStep).where(RunStep.run_id == run_id)).all()
    return sum(1 for step in steps if (step.transition_metadata or {}).get("wildcard_used"))


def _ensure_run_open(run: Run) -> None:
    """V2 runs are locked once finished. Legacy runs (engine_version 1) were
    never locked, so they keep accepting steps regardless of status."""
    if run.engine_version > LEGACY_ENGINE_VERSION and run.status in TERMINAL_RUN_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "valid": False,
                "reason": f"This run is {run.status} and can no longer be played",
                "connections": [],
            },
        )


def _apply_run_outcome(session: Session, tmdb: TMDBClient, run: Run) -> None:
    """Evaluates the run's win/fail conditions and, if one fired, moves the run
    to its terminal status. Caller commits."""
    engine_class = ENGINE_REGISTRY.get(run.game_type)
    if engine_class is None:
        return
    steps = session.exec(select(RunStep).where(RunStep.run_id == run.id)).all()
    engine = engine_class(session, tmdb)
    engine.sync_run_state(run, steps)
    outcome = engine.evaluate_run_outcome(run, list(steps))
    if outcome is not None:
        run.status = outcome.status
        run.status_reason = outcome.reason
        run.completed_at = utcnow()
        session.add(run)


async def _enforce_run_rules(
    session: Session, tmdb: TMDBClient, run: Run, movie: CachedMovie, payload: RunStepCreate
) -> dict:
    """Validates a candidate step against the run's rules_config.

    Returns (extra transition_metadata fields to merge in - repeat_penalty,
    runtime_flagged, wildcard_used - and the engine's own authoritative
    metadata for the step, or None to keep the client's). Raises 409 on any
    violation not covered by `payload.force`.
    """
    rules = _run_rules(run)
    force = payload.force
    extra_metadata: dict = {}
    linked_metadata: dict | None = None
    broke_a_rule = False

    tunnel = run.game_type == MEET_IN_THE_MIDDLE
    side_steps: list[RunStep] | None = None
    opposing_steps: list[RunStep] = []
    if tunnel:
        side_steps, opposing_steps = _tunnel_sides(session, run, payload.tunnel_side)
        extra_metadata["tunnel_side"] = payload.tunnel_side

    already_watched = session.exec(
        select(RunStep).where(RunStep.run_id == run.id,
                              RunStep.movie_id == movie.tmdb_id)
    ).first()
    # Closing the tunnel on the opposite end's frontier film is the one legal "repeat".
    closes_tunnel = bool(opposing_steps) and opposing_steps[-1].movie_id == movie.tmdb_id
    if already_watched is not None and not closes_tunnel:
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

    if tunnel:
        previous = side_steps[-1] if side_steps else None
    else:
        previous = _last_step(session, run.id)
    engine_class = ENGINE_REGISTRY.get(run.game_type)
    if previous is None:
        # The very first film has no inbound link; ignore any client-claimed one so it
        # can't dictate an alternating mode's next hop.
        claimed = payload.transition_metadata or {}
        if "connection_type" in claimed:
            linked_metadata = {
                k: v for k, v in claimed.items()
                if k not in ("connection_type", "director_id", "director_name")
            } or None
    if previous is None and engine_class is not None:
        # Nothing to link from, but run-scoped film rules (canon list, decade)
        # still apply to the very first film.
        first = await engine_class(session, tmdb).validate_candidate(movie.tmdb_id, rules)
        if not first.valid:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=first.model_dump())
    if previous is not None:
        engine = get_engine(run.game_type, session, tmdb)
        result = await engine.validate_next_step(
            previous.movie_id, movie.tmdb_id, cast_limit=rules.get(
                "max_cast_order"), rules=rules,
            previous_transition=previous.transition_metadata,
            history=side_steps if tunnel else _run_history(session, run.id),
        )
        if not result.valid and result.blocked:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=result.model_dump())
        if not result.valid:
            if not force:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail=result.model_dump())
            broke_a_rule = True
        linked_metadata = engine.link_metadata(result, payload.transition_metadata)
        if result.valid:
            for key in BONUS_LINK_KEYS:
                if result.mechanic and key in result.mechanic:
                    extra_metadata[key] = result.mechanic[key]
        # Collision Victory: a *valid* link to this end that also connects the opposite end.
        if (
            result.valid and isinstance(engine, MeetInTheMiddleEngine)
            and await engine.collides(movie.tmdb_id, opposing_steps, rules)
        ):
            extra_metadata["collision"] = True
            extra_metadata["collision_with"] = opposing_steps[-1].movie_id
        if result.valid and rules.get("no_consecutive_actor", True):
            # Crew & Craft links name a person (any role); the classic ones an actor.
            chosen = (
                linked_metadata if linked_metadata and "person_id" in linked_metadata
                else payload.transition_metadata) or {}
            chosen_actor_id = chosen.get("person_id", chosen.get("actor_id"))
            before = previous.transition_metadata or {}
            previous_actor_id = before.get("person_id", before.get("actor_id"))
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

    engine_class = ENGINE_REGISTRY.get(run.game_type)
    if force and broke_a_rule and engine_class is not None and engine_class.uses_lives:
        # Survival modes: a forced step costs a life, never a wildcard.
        lives, _ = rabbit_hole.lives_of(rules)
        if lives <= 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "valid": False, "blocked": True, "connections": [],
                    "reason": "No lives remaining - only a legal film can continue this run"},
            )
        run.rules_config = {**rules, rabbit_hole.LIVES_KEY: lives - 1}
        session.add(run)
        extra_metadata["life_lost"] = True
    elif force and broke_a_rule:
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

    return extra_metadata, linked_metadata


def _step_movie_info(session: Session, steps: list[RunStep]) -> dict[int, tuple]:
    """Cached per-film extras for the steps: poster colour (Aesthetic Gradient swatches) and the
    narrative setting year / era (Historical Time-Travel)."""
    movie_ids = {s.movie_id for s in steps}
    if not movie_ids:
        return {}
    rows = session.exec(
        select(
            CachedMovie.tmdb_id, CachedMovie.dominant_color,
            CachedMovie.narrative_year, CachedMovie.narrative_era_label,
        ).where(CachedMovie.tmdb_id.in_(movie_ids))  # type: ignore[attr-defined]
    ).all()
    return {row[0]: row[1:] for row in rows}


def _step_public(step: RunStep, info: dict[int, tuple]) -> RunStepPublic:
    public = RunStepPublic.model_validate(step)
    color, narrative_year, narrative_era = info.get(step.movie_id, (None, None, None))
    public.movie_dominant_color = color
    public.movie_narrative_year = narrative_year
    public.movie_narrative_era_label = narrative_era
    return public


def _to_run_detail(session: Session, run: Run) -> RunDetail:
    steps = session.exec(
        select(RunStep).where(RunStep.run_id ==
                              run.id).order_by(RunStep.logged_at)
    ).all()
    info = _step_movie_info(session, list(steps))
    participants = session.exec(
        select(RunParticipant)
        .where(RunParticipant.run_id == run.id)
        .order_by(RunParticipant.joined_at)
    ).all()
    return RunDetail(
        **RunSummary.model_validate(run).model_dump(),
        steps=[_step_public(s, info) for s in steps],
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
        .where(RunParticipant.user_id == current_user.id, Run.game_type != IMPORT_GAME_TYPE)
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
    if payload.game_type == IMPORT_GAME_TYPE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="'import' is reserved for imported history")
    participant_ids = {current_user.id, *payload.participant_user_ids}
    for user_id in participant_ids:
        if session.get(User, user_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"User {user_id} not found"
            )

    rules_config = blind_fork.strip_server_rules(
        payload.rules_config if payload.rules_config is not None else dict(DEFAULT_RULES_CONFIG))
    engine_class = ENGINE_REGISTRY.get(payload.game_type)
    if engine_class is not None:
        engine = engine_class(session, tmdb)
        problems = engine.validate_rules_config(rules_config)
        problems += _bounty_board_problems(engine_class, rules_config)
        if rules_config.get(blind_fork.BLIND_FORK_KEY):
            problems += _blind_fork_problems(engine_class)
        if problems:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="; ".join(problems))
        rules_config = engine.prepare_rules_config(rules_config)
        try:
            rules_config = await engine.prepare_run(rules_config, current_user.id)
        except RunSetupError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        if bounties.board_enabled(rules_config):
            rules_config = bounties.prepare_board(rules_config)
        if payload.game_type == MEET_IN_THE_MIDDLE:
            if payload.seed_movie_id is None or payload.tail_seed_movie_id is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Meet in the Middle needs two starting films: one for each partner")
            if payload.seed_movie_id == payload.tail_seed_movie_id:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="The two partners need different starting films")
        elif payload.tail_seed_movie_id is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A second seed film is only for Meet in the Middle runs")
        for seed_id in (payload.seed_movie_id, payload.tail_seed_movie_id):
            if seed_id is None:
                continue
            seed = await engine.validate_candidate(seed_id, rules_config)
            if not seed.valid:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Seed movie rejected: {seed.reason}")

    run = Run(
        name=payload.name,
        game_type=payload.game_type,
        rules_config=rules_config,
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    for user_id in participant_ids:
        role = "owner" if user_id == current_user.id else "member"
        session.add(RunParticipant(run_id=run.id, user_id=user_id, role=role))
    session.commit()

    seeds = [(payload.seed_movie_id, SIDE_HEAD), (payload.tail_seed_movie_id, SIDE_TAIL)]
    for seed_id, side in seeds:
        if seed_id is None:
            continue
        movie = await cache_repo.get_movie(session, tmdb, seed_id)
        step = RunStep(
            run_id=run.id,
            logged_by_user_id=current_user.id,
            status="watched",
            watched_at=utcnow(),
            transition_metadata={"tunnel_side": side} if payload.game_type == MEET_IN_THE_MIDDLE else None,
            **_step_fields_from_movie(movie),
        )
        session.add(step)
        session.commit()

    if engine_class is not None:
        engine_class(session, tmdb).sync_run_state(run, _run_history(session, run.id))
        session.commit()
        session.refresh(run)
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
        run.status = payload.status
        if payload.status == RUN_STATUS_ACTIVE:
            run.completed_at = None
            run.status_reason = None
        else:
            run.completed_at = utcnow()
            run.status_reason = MANUAL_STATUS_REASONS.get(payload.status)
            engine_class = ENGINE_REGISTRY.get(run.game_type)
            if payload.status == RUN_STATUS_FORFEITED and engine_class is not None:
                outcome = engine_class.forfeit_outcome(run, _run_history(session, run.id))
                if outcome is not None:
                    run.status, run.status_reason = outcome.status, outcome.reason
    session.add(run)
    session.commit()
    session.refresh(run)
    return _to_run_detail(session, run)


@router.patch("/{run_id}/rules", response_model=RunDetail)
def update_run_rules(
    payload: RunRulesUpdate,
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
):
    # A Bounty Board run earns its wildcards: the budget isn't editable.
    bounty_run = bounties.board_enabled(run.rules_config)
    if payload.wildcards_budget != -1 and not bounty_run:
        consumed = _count_wildcards_consumed(session, run.id)
        if payload.wildcards_budget < consumed:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Cannot set wildcard budget to {payload.wildcards_budget} - "
                    f"{consumed} wildcard(s) have already been used this run."
                ),
            )
    # Merge instead of replace so V2 keys the form doesn't know about
    # (win_condition, fail_condition, raw JSON overrides) survive an edit.
    update = payload.model_dump(exclude_none=True)
    if bounty_run:
        update.pop("wildcards_budget", None)
    # Modifiers can be switched off again: an explicit null is stored (and means "unset").
    update.update({
        key: None for key in MODIFIER_UPDATE_KEYS
        if key in payload.model_fields_set and getattr(payload, key) is None})
    merged = {**(run.rules_config or {}), **update}
    engine_class = ENGINE_REGISTRY.get(run.game_type)
    if update.get(blind_fork.BLIND_FORK_KEY) is False:
        merged = blind_fork.with_fork(merged, None)
    if engine_class is not None:
        problems = engine_class.modifier_problems(merged)
        if update.get(blind_fork.BLIND_FORK_KEY):
            problems += _blind_fork_problems(engine_class)
        if problems:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="; ".join(problems))
    run.rules_config = merged
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


async def _log_step(
    session: Session, tmdb: TMDBClient, run: Run, user: User, payload: RunStepCreate,
    omdb: OMDbClient | None = None,
) -> RunStep:
    """Validate and add one step, then evaluate the run's outcome. Caller commits."""
    movie = await cache_repo.get_movie(session, tmdb, payload.movie_id)
    split = run.game_type == RT_SPLIT
    if split:
        if payload.status != "watched" or payload.household_score is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Log a split film as watched, with the household's rating (1-100)")
        if omdb is not None:  # the scores the split is judged on come from OMDb
            await cache_repo.get_movie_ratings(session, tmdb, omdb, movie.tmdb_id)
    extra_metadata, linked_metadata = await _enforce_run_rules(
        session, tmdb, run, movie, payload)
    if split:
        extra_metadata.update(RottenTomatoesSplitEngine(session, tmdb).settle(
            movie.tmdb_id, payload.household_score))
    bounty = await bounties.evaluate(session, tmdb, run.rules_config, movie)
    if bounty is not None:
        extra_metadata[bounties.COMPLETED_METADATA_KEY] = bounty[0]
        extra_metadata[bounties.REPLACEMENT_METADATA_KEY] = bounty[1]

    transition_metadata = _without_server_keys(
        linked_metadata if linked_metadata is not None else payload.transition_metadata)
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
        logged_by_user_id=user.id,
        **_step_fields_from_movie(movie),
    )
    session.add(step)
    session.flush()
    if bounty is not None:
        run.rules_config = bounties.award(run.rules_config or {}, *bounty)
        session.add(run)
    if chaos.active(run.rules_config) is not None:
        # The handicap was for this step only.
        run.rules_config = chaos.clear(run.rules_config or {})
        session.add(run)
    _apply_run_outcome(session, tmdb, run)
    return step


@router.post("/{run_id}/steps", response_model=RunStepPublic, status_code=status.HTTP_201_CREATED)
async def create_step(
    payload: RunStepCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    omdb: OMDbClient = Depends(get_omdb_client),
):
    _ensure_run_open(run)
    _ensure_no_pending_fork(run)
    step = await _log_step(session, tmdb, run, current_user, payload, omdb)
    session.commit()
    session.refresh(step)
    return _step_public(step, _step_movie_info(session, [step]))


@router.patch("/{run_id}/steps/{step_id}/mark-watched", response_model=RunStepPublic)
def mark_step_watched(
    step_id: str,
    payload: MarkWatchedRequest,
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
):
    _ensure_run_open(run)
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
    session.flush()
    _apply_run_outcome(session, tmdb, run)
    session.commit()
    session.refresh(step)
    return step


@router.patch("/{run_id}/steps/{step_id}", response_model=RunStepPublic)
def update_step(
    step_id: str,
    payload: RunStepUpdate,
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
):
    step = session.get(RunStep, step_id)
    if step is None or step.run_id != run.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Step not found")
    if payload.watched_at is not None and step.status == "planned":
        _ensure_run_open(run)
    if payload.user_notes is not None:
        step.user_notes = payload.user_notes
    if payload.transition_metadata is not None:
        owned = {
            k: v for k, v in (step.transition_metadata or {}).items() if k in SERVER_OWNED_METADATA}
        step.transition_metadata = {**(_without_server_keys(payload.transition_metadata) or {}), **owned}
    if payload.watched_at is not None:
        step.watched_at = payload.watched_at
        # Setting a watched date IS the act of marking it watched - keep the
        # 1-click "Mark as Watched" quick action (which PATCHes only
        # watched_at, not status) from leaving stale "planned" state behind.
        if step.status == "planned":
            step.status = "watched"
    session.add(step)
    session.flush()
    _apply_run_outcome(session, tmdb, run)
    session.commit()
    session.refresh(step)
    return step


def _remove_step(session: Session, tmdb: TMDBClient, run: Run, step: RunStep) -> None:
    """Delete the latest step, undoing any outcome it caused. Caller commits."""
    metadata = step.transition_metadata or {}
    collided = bool(metadata.get("collision"))
    session.delete(step)
    session.flush()
    if metadata.get(bounties.COMPLETED_METADATA_KEY):
        # The step earned a wildcard for a bounty: take both back.
        run.rules_config = bounties.revoke(
            run.rules_config or {}, metadata[bounties.COMPLETED_METADATA_KEY],
            metadata.get(bounties.REPLACEMENT_METADATA_KEY))
        session.add(run)
    reopen = collided and run.status == RUN_STATUS_COMPLETED
    remaining = _run_history(session, run.id)
    engine_class = ENGINE_REGISTRY.get(run.game_type)
    if engine_class is not None:
        engine_class(session, tmdb).sync_run_state(run, remaining)
    if (
        run.game_type == TUG_OF_WAR
        and run.status == RUN_STATUS_COMPLETED
        and (run.status_reason or "").startswith(VICTORY_PREFIX)
        and leading_team(compute_scores(remaining, run.rules_config), run.rules_config) is None
    ):
        reopen = True
    if (
        run.game_type == RT_SPLIT
        and run.status == RUN_STATUS_COMPLETED
        and (run.status_reason or "").startswith(SPLIT_VICTORY_PREFIX)
        and split_winner(compute_split_scores(remaining), run.rules_config) is None
    ):
        reopen = True
    if reopen:
        # Undoing the deciding step reopens the run.
        run.status = RUN_STATUS_ACTIVE
        run.status_reason = None
        run.completed_at = None
        session.add(run)


@router.delete("/{run_id}/steps/{step_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_step(
    step_id: str,
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
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
    _remove_step(session, tmdb, run, step)
    session.commit()


def _bounty_board_problems(engine_class: type, rules: dict) -> list[str]:
    toggle = rules.get(bounties.BOUNTY_BOARD_KEY)
    if toggle is None or toggle is False:
        return []
    if toggle is not True:
        return [f"{bounties.BOUNTY_BOARD_KEY} must be true or false"]
    if not engine_class.supports_bounty_board:
        return [f"{engine_class.display_name} can't run with the Bounty Board"]
    return []


def _blind_fork_problems(engine_class: type) -> list[str]:
    """Blind Fork offers films from the Pick Next pool of a single chain."""
    capabilities = getattr(engine_class, "capabilities", [])
    if "discover_candidates" not in capabilities or "tunnel" in capabilities:
        return [f"The Blind Fork isn't available in {engine_class.display_name} runs"]
    return []


def _ensure_no_pending_fork(run: Run) -> None:
    if blind_fork.pending_fork(run.rules_config) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A Blind Fork offer is waiting for the partner's veto and pick",
        )


def _require_fork(run: Run) -> dict:
    fork = blind_fork.pending_fork(run.rules_config)
    if fork is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="There is no Blind Fork offer to answer")
    return fork


def _require_partner_of_offer(fork: dict, user: User) -> None:
    if fork.get("offered_by_id") == user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your partner answers your offer - you can't veto or pick from it")


@router.post("/{run_id}/fork", response_model=RunDetail, status_code=status.HTTP_201_CREATED)
async def offer_fork(
    payload: ForkOffer,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> RunDetail:
    """Blind Fork step 1: offer the partner three films that each legally follow the chain."""
    _ensure_run_open(run)
    rules = _run_rules(run)
    if not rules.get(blind_fork.BLIND_FORK_KEY):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="The Blind Fork is off for this run")
    _ensure_no_pending_fork(run)
    participants = session.exec(
        select(RunParticipant).where(RunParticipant.run_id == run.id)).all()
    if len(participants) < 2:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The Blind Fork needs a partner to answer the offer")
    if _last_step(session, run.id) is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Log a first film before offering a fork")
    for movie_id in payload.movie_ids:
        movie = await cache_repo.get_movie(session, tmdb, movie_id)
        try:
            await _enforce_run_rules(session, tmdb, run, movie, RunStepCreate(movie_id=movie_id))
        except HTTPException as exc:
            reason = exc.detail.get("reason") if isinstance(exc.detail, dict) else exc.detail
            raise HTTPException(
                status_code=exc.status_code, detail=f"{movie.title}: {reason}") from exc
    run.rules_config = blind_fork.with_fork(
        rules, blind_fork.new_offer(
            current_user.id, payload.movie_ids,
            {movie_id: _without_server_keys(meta) or {} for movie_id, meta in payload.links.items()}))
    session.add(run)
    session.commit()
    session.refresh(run)
    return _to_run_detail(session, run)


@router.delete("/{run_id}/fork", response_model=RunDetail)
def withdraw_fork(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    run: Run = Depends(run_participant_guard),
) -> RunDetail:
    """The offering player takes their offer back (e.g. the partner is away)."""
    fork = _require_fork(run)
    if fork.get("offered_by_id") != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the player who made the offer can withdraw it")
    run.rules_config = blind_fork.with_fork(run.rules_config, None)
    session.add(run)
    session.commit()
    session.refresh(run)
    return _to_run_detail(session, run)


@router.post("/{run_id}/fork/veto", response_model=RunDetail)
def veto_fork_movie(
    payload: ForkVeto,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    run: Run = Depends(run_participant_guard),
) -> RunDetail:
    """Blind Fork step 2a: the partner strikes one of the three films (free - it is part of the
    workflow; the Golden Veto token is for tearing up a whole offer or a step)."""
    _ensure_run_open(run)
    fork = _require_fork(run)
    _require_partner_of_offer(fork, current_user)
    movie_ids = list(fork["movie_ids"])
    if len(movie_ids) != blind_fork.OFFER_SIZE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="One film has already been vetoed")
    if payload.movie_id not in movie_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That film isn't part of the offer")
    movie_ids.remove(payload.movie_id)
    run.rules_config = blind_fork.with_fork(run.rules_config, {
        **fork, "movie_ids": movie_ids,
        "vetoed_movie_id": payload.movie_id, "vetoed_by_id": current_user.id})
    session.add(run)
    session.commit()
    session.refresh(run)
    return _to_run_detail(session, run)


@router.post("/{run_id}/fork/accept", response_model=RunStepPublic, status_code=status.HTTP_201_CREATED)
async def accept_fork_movie(
    payload: ForkAccept,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
):
    """Blind Fork step 2b: the partner commits one of the two remaining films as the next step."""
    _ensure_run_open(run)
    fork = _require_fork(run)
    _require_partner_of_offer(fork, current_user)
    if len(fork["movie_ids"]) != blind_fork.OFFER_SIZE - 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Veto one of the three films first")
    if payload.movie_id not in fork["movie_ids"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That film isn't left in the offer")
    link = (fork.get("links") or {}).get(str(payload.movie_id))
    step = await _log_step(session, tmdb, run, current_user, RunStepCreate(
        movie_id=payload.movie_id, transition_metadata=link, user_notes=payload.user_notes,
        status=payload.status))
    run.rules_config = blind_fork.with_fork(run.rules_config, None)
    session.add(run)
    session.commit()
    session.refresh(step)
    return _step_public(step, _step_movie_info(session, [step]))


@router.post("/{run_id}/veto", response_model=GoldenVetoResult)
def golden_veto(
    payload: GoldenVeto,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> GoldenVetoResult:
    """Spend one Golden Veto token (30-day refill) to overrule the partner: tear up their pending
    Blind Fork offer, or remove the step they just logged."""
    _ensure_run_open(run)
    if payload.target == "fork":
        fork = _require_fork(run)
        _require_partner_of_offer(fork, current_user)
    else:
        steps = _run_history(session, run.id)
        seeds = 2 if run.game_type == MEET_IN_THE_MIDDLE else 1
        if len(steps) <= seeds:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="There is no contested step to veto")
        target_step = steps[-1]
        if target_step.logged_by_user_id in (None, current_user.id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You can only veto a step your partner logged - delete your own instead")
    if not consume_veto_token(session, current_user):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No Golden Veto tokens left - you get one every 30 days")
    if payload.target == "fork":
        run.rules_config = blind_fork.with_fork(run.rules_config, None)
        session.add(run)
    else:
        _remove_step(session, tmdb, run, target_step)
    session.commit()
    session.refresh(run)
    return GoldenVetoResult(
        target=payload.target, veto_tokens=current_user.veto_tokens,
        run=_to_run_detail(session, run))


@router.post("/{run_id}/validate", response_model=ValidationResult)
async def validate_step(
    payload: StepValidateRequest,
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> ValidationResult:
    """Dry-run of the engine check `create_step` applies (link to the previous
    film + the run's own film rules), so the UI can pre-flight a pick."""
    engine = get_engine(run.game_type, session, tmdb)
    rules = _run_rules(run)
    side_steps: list[RunStep] | None = None
    opposing_steps: list[RunStep] = []
    if run.game_type == MEET_IN_THE_MIDDLE:
        side_steps, opposing_steps = _tunnel_sides(session, run, payload.tunnel_side)
        previous = side_steps[-1] if side_steps else None
    else:
        previous = _last_step(session, run.id)
    if previous is None:
        return await engine.validate_candidate(payload.movie_id, rules)
    result = await engine.validate_next_step(
        previous.movie_id, payload.movie_id, cast_limit=rules.get("max_cast_order"), rules=rules,
        previous_transition=previous.transition_metadata,
        history=side_steps if side_steps is not None else _run_history(session, run.id))
    if result.valid and isinstance(engine, MeetInTheMiddleEngine):
        result.collision = await engine.collides(payload.movie_id, opposing_steps, rules)
    return result


@router.get("/{run_id}/tunnel", response_model=TunnelState)
async def get_tunnel_state(
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> TunnelState:
    """Both frontiers of a Meet in the Middle run and the quick-BFS distance between them."""
    engine = get_engine(run.game_type, session, tmdb)
    if not isinstance(engine, MeetInTheMiddleEngine):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Only Meet in the Middle runs have a tunnel")
    steps = _run_history(session, run.id)
    head, tail = split_sides(steps)
    state = TunnelState(
        head_frontier_movie_id=head[-1].movie_id if head else None,
        tail_frontier_movie_id=tail[-1].movie_id if tail else None,
        head_steps=len(head), tail_steps=len(tail),
        collided=run.status == RUN_STATUS_COMPLETED and any(
            (s.transition_metadata or {}).get("collision") for s in steps),
    )
    if state.collided:
        return state.model_copy(update={"distance_hops": 0})
    if not head or not tail:
        return state.model_copy(update={"message": "Both partners need a starting film."})
    try:
        distance = await engine.distance(
            head[-1].movie_id, tail[-1].movie_id, {s.movie_id for s in steps},
            cast_limit=_run_rules(run).get("max_cast_order"))
    except Exception as exc:  # noqa: BLE001 - the indicator is advisory; never fail the page
        return state.model_copy(update={"message": f"Couldn't measure the distance: {exc}"})
    return state.model_copy(update={
        "distance_hops": distance.hops, "searched_depth": distance.searched_depth,
        "message": distance.message})


@router.get("/{run_id}/constraint", response_model=ConstraintInfo | None)
async def get_run_constraint(
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> ConstraintInfo | None:
    """The rule shaping this run's next hop (e.g. "must be a Director"), or null."""
    engine = get_engine(run.game_type, session, tmdb)
    tail = _last_step(session, run.id)
    return await engine.describe_run_constraint(
        tail.movie_id if tail is not None else None,
        tail.transition_metadata if tail is not None else None,
        _run_rules(run),
        _run_history(session, run.id))


@router.get("/{run_id}/suggestions", response_model=list[Suggestion])
async def get_run_suggestions(
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    country: str | None = Query(default=None),
    decade: int | None = Query(default=None),
    genre_id: int | None = Query(default=None),
    chaser: bool = Query(default=False),
    sort_by: str | None = Query(default=None, pattern="^underdog$"),
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
    suggestions = await engine.get_suggestions(
        current.movie_id, logged_movie_ids, filters, rules=_run_rules(run))
    if chaser or sort_by:
        by_id = {s.movie_id: s for s in suggestions}
        kept = await pool_options.shape_pool(
            session, tmdb, [s.movie_id for s in suggestions], chaser=chaser, sort_by=sort_by)
        suggestions = [by_id[movie_id] for movie_id in kept]
    return suggestions


def _ensure_chaos_allowed(run: Run) -> None:
    _ensure_run_open(run)
    engine_class = ENGINE_REGISTRY.get(run.game_type)
    if engine_class is None or "discover_candidates" not in engine_class.capabilities:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The Chaos Button needs a mode with a Pick Next pool")


@router.post("/{run_id}/bounties/custom", response_model=RunDetail)
async def roll_custom_bounty(
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
) -> RunDetail:
    """"✨ Roll Custom Bounty": the AI writes a new bounty (with a programmatic rule) that takes
    the oldest slot on the Bounty Board."""
    _ensure_run_open(run)
    try:
        run.rules_config = await bounties.roll_custom(session, run.rules_config)
    except bounties.BountyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    session.add(run)
    session.commit()
    session.refresh(run)
    return _to_run_detail(session, run)


@router.post("/{run_id}/chaos", response_model=RunDetail)
def roll_chaos(
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
) -> RunDetail:
    """Rolls one random handicap for the next film only: `rules_config["active_chaos"]`. It
    expires when a step is logged (or is cancelled with DELETE)."""
    _ensure_chaos_allowed(run)
    if chaos.active(run.rules_config) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="A chaos handicap is already active")
    run.rules_config = {**_run_rules(run), chaos.ACTIVE_KEY: chaos.roll()}
    session.add(run)
    session.commit()
    session.refresh(run)
    return _to_run_detail(session, run)


@router.delete("/{run_id}/chaos", response_model=RunDetail)
def cancel_chaos(
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
) -> RunDetail:
    """Backs out of the active handicap (e.g. nothing in the pool fits it)."""
    _ensure_chaos_allowed(run)
    run.rules_config = chaos.clear(_run_rules(run))
    session.add(run)
    session.commit()
    session.refresh(run)
    return _to_run_detail(session, run)


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
    chaser: bool = Query(default=False, description="Only palate cleansers: <= 95 min, Comedy/Animation"),
    sort_by: str | None = Query(
        default=None, pattern="^underdog$",
        description="underdog = least popular first (popularity >= 1.0)"),
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
    previous = _last_step(session, run.id)
    try:
        candidates = await engine.discover_with_modifiers(
            frontier_movie_id=frontier_movie_id,
            mode=mode,
            cast_limit=rules.get("max_cast_order"),
            rules=rules,
            previous_transition=(
                previous.transition_metadata if previous is not None
                and previous.movie_id == frontier_movie_id else None),
            history=_run_history(session, run.id),
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
    if chaser or sort_by:
        by_id = {c.movie_id: c for c in candidates}
        kept = await pool_options.shape_pool(
            session, tmdb, [c.movie_id for c in candidates], chaser=chaser, sort_by=sort_by)
        candidates = [by_id[movie_id] for movie_id in kept]
    for candidate in candidates:
        candidate.already_in_run = candidate.movie_id in logged_movie_ids
        candidate.existing_step_number = step_number_by_movie_id.get(
            candidate.movie_id)
        row = session.get(CachedMovie, candidate.movie_id)
        candidate.runtime = row.runtime if row is not None else None
    return candidates
