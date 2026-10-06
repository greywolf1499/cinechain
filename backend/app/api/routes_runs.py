import asyncio
import logging
import random
import time
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, select

from app.api.deps import get_current_user, get_omdb_client, get_tmdb_client, run_participant_guard
from app.db import get_session
from app.engines import chaos, rabbit_hole
from app.engines.base import BaseChallengeEngine, RunSetupError
from app.engines.meet_in_middle import (
    MEET_IN_THE_MIDDLE,
    SIDE_HEAD,
    SIDE_TAIL,
    MeetInTheMiddleEngine,
    split_sides,
)
from app.engines.rabbit_hole import (
    LIVES_KEY,
    TIER_OVERRIDE_KEY,
    RabbitHoleEngine,
    lives_of,
    tier_for_depth,
)
from app.engines.registry import ENGINE_REGISTRY, get_engine
from app.engines.rt_split import RT_SPLIT, RottenTomatoesSplitEngine
from app.engines.rt_split import VICTORY_PREFIX as SPLIT_VICTORY_PREFIX
from app.engines.rt_split import compute_scores as compute_split_scores
from app.engines.rt_split import winning_team as split_winner
from app.engines.tug_of_war import (
    PLAYERS_KEY,
    SEED_KEY,
    TEAM_A,
    TEAM_B,
    TUG_OF_WAR,
    TUG_RULES_VERSION_KEY,
    VICTORY_PREFIX,
    TugOfWarEngine,
    _territory,
    compute_scores,
    leading_team,
    step_turn_team,
    tally,
    team_of,
    winner,
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
from app.schemas.discovery import (
    DiscoveryCandidate,
    DiscoveryConnection,
    TugLookahead,
    TugReachable,
)
from app.schemas.engine import (
    ConstraintInfo,
    RunStats,
    Suggestion,
    SuggestionFilters,
    TunnelHintActor,
    TunnelHintFilm,
    TunnelHintRequest,
    TunnelHintResponse,
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
from app.services.bridge_paths import parse_countries
from app.services.movie_filters import is_reality_eligible
from app.services.tmdb import TMDBClient
from app.services.veto import consume_veto_token, refresh_veto_tokens
from app.utils.dates import parse_release_year
from app.utils.ids import utcnow

router = APIRouter(prefix="/runs", tags=["runs"])

TUG_LOOKAHEAD_SECONDS = 1.5
logger = logging.getLogger(__name__)


def _cached_tug_lookahead(
    session: Session, movie_ids: list[int], rules: dict, excluded: set[int],
    deadline: float, results: dict[int, TugReachable],
) -> None:
    repo = cache_repo.CacheRepo(session)
    limit = rules.get("max_cast_order") or 15
    puller = (rules.get("tug_momentum") or {}).get("next_team", TEAM_A)
    opponent = TEAM_B if puller == TEAM_A else TEAM_A
    for movie_id in movie_ids:
        if time.monotonic() >= deadline:
            return
        cast = repo.get_cached_cast(movie_id, limit)
        count = TugReachable(partial=cast is None)
        seen = {movie_id, *excluded}
        for member in cast or []:
            if time.monotonic() >= deadline:
                count.partial = True
                break
            credits = repo.get_cached_actor_credits(member["actor_id"])
            if credits is None:
                count.partial = True
            for movie in credits or []:
                if time.monotonic() >= deadline:
                    count.partial = True
                    break
                if movie.tmdb_id in seen:
                    continue
                seen.add(movie.tmdb_id)
                if not is_reality_eligible(movie):
                    continue
                if movie.runtime is not None and movie.runtime < rules.get("min_runtime", 0):
                    continue
                territory = _territory(parse_release_year(movie.release_date), movie.origin_country, rules)
                if (rules.get("dimension", "era") == "era" and movie.release_date is None) or (
                    rules.get("dimension") == "geography" and movie.origin_country is None
                ):
                    count.partial = True
                if territory is None or (territory != opponent and not rules.get("steal_enabled", True)):
                    count.neutral += 1
                else:
                    count.scoring += 1
        if time.monotonic() >= deadline:
            count.partial = True
        results[movie_id] = count


@router.get("/{run_id}/tug/lookahead", response_model=TugLookahead)
async def tug_lookahead(
    movie_ids: str = Query(..., max_length=200),
    run: Run = Depends(run_participant_guard),
    session: Session = Depends(get_session),
) -> TugLookahead:
    if run.game_type != TUG_OF_WAR:
        raise HTTPException(422, detail="Lookahead is only available for Tug of War")
    raw = movie_ids.split(",")
    if not 1 <= len(raw) <= 10 or any(not item.isdecimal() or int(item) < 1 for item in raw):
        raise HTTPException(422, detail="Supply 1 to 10 positive movie IDs")
    ids = list(dict.fromkeys(int(item) for item in raw))
    rules = dict(run.rules_config or {})
    excluded = {step.movie_id for step in _run_history(session, run.id)} if rules.get(
        "allow_repeats", "strict"
    ) != "allowed" else set()
    results = {movie_id: TugReachable(partial=True) for movie_id in ids}
    deadline = time.monotonic() + TUG_LOOKAHEAD_SECONDS
    bind = session.get_bind()

    def load() -> None:
        # A timed-out read owns its session until it exits; never share the request session.
        with Session(bind) as cache_session:
            _cached_tug_lookahead(cache_session, ids, rules, excluded, deadline, results)

    timed_out = False
    try:
        await asyncio.wait_for(asyncio.to_thread(load), timeout=TUG_LOOKAHEAD_SECONDS)
    except TimeoutError:
        timed_out = True
        logger.info("Tug lookahead reached its %.1fs cache-only deadline", TUG_LOOKAHEAD_SECONDS)
    snapshot = dict(results)
    return TugLookahead(movies=snapshot, partial=timed_out or any(value.partial for value in snapshot.values()))

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
        select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.logged_at.desc())
    ).first()


def _run_history(session: Session, run_id: str) -> list[RunStep]:
    """Every step logged so far, oldest first (the history modifiers like country_cooldown read)."""
    return list(
        session.exec(
            select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.logged_at)
        ).all()
    )


# Metadata keys only the server may set: a client-supplied `collision` would be a free win.
SERVER_OWNED_METADATA = (
    "acting_participant_id",
    "tunnel_side",
    "collision",
    "collision_with",
    "golden_reunion",
    "character_hop",
    "near_miss_with",
    "tug_team",
    "seed",
    "life_lost",
    "bounty_life_awarded",
    # Bounty Board awards and Rotten Tomatoes Split settlements: a client could mint wildcards/points.
    "completed_bounty",
    "bounty_replacement",
    "household_score",
    "critic_score",
    "audience_score",
    "divergence",
    "point_to",
    "split_no_contest",
)
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
            detail="Choose which end of the tunnel you are extending (head or tail)",
        )
    head, tail = split_sides(_run_history(session, run.id))
    return (head, tail) if side == SIDE_HEAD else (tail, head)


def _run_rules(run: Run) -> dict:
    return run.rules_config or dict(DEFAULT_RULES_CONFIG)


def _acting_user(
    session: Session, run: Run, current_user: User, acting_participant_id: str | None,
) -> User:
    if acting_participant_id is None:
        return current_user
    if (
        (run.rules_config or {}).get("table_mode") is not True
        or session.get(RunParticipant, (run.id, current_user.id)) is None
        or session.get(RunParticipant, (run.id, acting_participant_id)) is None
    ):
        raise HTTPException(403, detail="Acting identity requires Table Mode and run membership")
    actor = session.get(User, acting_participant_id)
    if actor is None:
        raise HTTPException(403, detail="Acting participant is unavailable")
    return actor


def _step_actor_id(step: RunStep) -> str | None:
    return (step.transition_metadata or {}).get("acting_participant_id") or step.logged_by_user_id


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
    session: Session,
    tmdb: TMDBClient,
    run: Run,
    movie: CachedMovie,
    payload: RunStepCreate,
    user: User,
    fork_team: str | None = None,
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
    if run.game_type == TUG_OF_WAR:
        tug_engine = TugOfWarEngine(session, tmdb)
        players = tug_engine.team_players(run)
        team = fork_team or (
            team_of(players, user.id) if rules.get("table_mode") is True
            else payload.tug_team or team_of(players, user.id)
        )
        if team not in (TEAM_A, TEAM_B) or players.get(team) is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Choose a Tug team assigned to a run participant",
            )
        extra_metadata["tug_team"] = team
        rules_version = rules.get(TUG_RULES_VERSION_KEY)
        if payload.status == "watched" and rules_version in (2, 3) and all(players.values()):
            next_team = tally(_run_history(session, run.id), rules, players).next_team
            if team != next_team:
                expected_name = tug_engine.team_name(run, next_team)
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"It's {expected_name}'s pull",
                )

    tunnel = run.game_type == MEET_IN_THE_MIDDLE
    side_steps: list[RunStep] | None = None
    opposing_steps: list[RunStep] = []
    if tunnel:
        side_steps, opposing_steps = _tunnel_sides(session, run, payload.tunnel_side)
        extra_metadata["tunnel_side"] = payload.tunnel_side

    already_watched = session.exec(
        select(RunStep).where(RunStep.run_id == run.id, RunStep.movie_id == movie.tmdb_id)
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
                k: v
                for k, v in claimed.items()
                if k not in ("connection_type", "director_id", "director_name")
            } or None
    if previous is None and engine_class is not None:
        # Nothing to link from, but run-scoped film rules (canon list, decade)
        # still apply to the very first film.
        if run.game_type == RT_SPLIT and payload.no_contest:
            first = await RottenTomatoesSplitEngine(session, tmdb).validate_candidate(
                movie.tmdb_id, rules, no_contest=True
            )
        else:
            first = await engine_class(session, tmdb).validate_candidate(movie.tmdb_id, rules)
        if not first.valid:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=first.model_dump())
    if previous is not None and run.game_type == RT_SPLIT and payload.no_contest:
        engine = RottenTomatoesSplitEngine(session, tmdb)
        earlier = await cache_repo.get_movie(session, tmdb, previous.movie_id, require_detail=True)
        reason = engine.modifier_violation(earlier, movie, rules, _run_history(session, run.id))
        if reason:
            raise HTTPException(
                status_code=409,
                detail={"valid": False, "blocked": True, "reason": reason, "connections": []},
            )
    elif previous is not None:
        engine = get_engine(run.game_type, session, tmdb)
        result = await engine.validate_next_step(
            previous.movie_id,
            movie.tmdb_id,
            cast_limit=rules.get("max_cast_order"),
            rules=rules,
            previous_transition=previous.transition_metadata,
            history=side_steps if tunnel else _run_history(session, run.id),
        )
        if not result.valid and result.blocked:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=result.model_dump())
        if not result.valid:
            if not force:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail=result.model_dump()
                )
            broke_a_rule = True
        linked_metadata = engine.link_metadata(result, payload.transition_metadata)
        if result.valid:
            for key in BONUS_LINK_KEYS:
                if result.mechanic and key in result.mechanic:
                    extra_metadata[key] = result.mechanic[key]
        # Collision Victory: a *valid* link to this end that also connects the opposite end.
        if (
            result.valid
            and isinstance(engine, MeetInTheMiddleEngine)
            and await engine.collides(movie.tmdb_id, opposing_steps, rules)
        ):
            extra_metadata["collision"] = True
            extra_metadata["collision_with"] = opposing_steps[-1].movie_id
        elif result.valid and isinstance(engine, MeetInTheMiddleEngine):
            near_miss = await engine.near_miss(movie.tmdb_id, opposing_steps, rules)
            if near_miss is not None:
                extra_metadata["near_miss_with"] = near_miss.movie_id
        if result.valid and rules.get("no_consecutive_actor", True):
            # Crew & Craft links name a person (any role); the classic ones an actor.
            chosen = (
                linked_metadata
                if linked_metadata and "person_id" in linked_metadata
                else payload.transition_metadata
            ) or {}
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
                    "valid": False,
                    "blocked": True,
                    "connections": [],
                    "reason": "No lives remaining - only a legal film can continue this run",
                },
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
                    detail={"valid": False, "reason": "No wildcards remaining", "connections": []},
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
            CachedMovie.tmdb_id,
            CachedMovie.dominant_color,
            CachedMovie.narrative_year,
            CachedMovie.narrative_era_label,
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


def _heal_step_countries(session: Session, steps: list[RunStep]) -> None:
    missing_steps = [step for step in steps if step.movie_origin_country is None]
    if not missing_steps:
        return

    movie_ids = {step.movie_id for step in missing_steps}
    rows = session.exec(
        select(CachedMovie.tmdb_id, CachedMovie.origin_country).where(
            CachedMovie.tmdb_id.in_(movie_ids)
        )
    ).all()
    countries_by_movie = {
        movie_id: country for movie_id, country in rows if country and parse_countries(country)
    }
    changed = False
    for step in missing_steps:
        country = countries_by_movie.get(step.movie_id)
        if country is not None:
            step.movie_origin_country = country
            session.add(step)
            changed = True
    if changed:
        session.commit()


def _to_run_detail(session: Session, run: Run) -> RunDetail:
    steps = session.exec(
        select(RunStep).where(RunStep.run_id == run.id).order_by(RunStep.logged_at)
    ).all()
    _heal_step_countries(session, list(steps))
    info = _step_movie_info(session, list(steps))
    participants = session.exec(
        select(RunParticipant)
        .where(RunParticipant.run_id == run.id)
        .order_by(RunParticipant.joined_at)
    ).all()
    return RunDetail(
        **RunSummary.model_validate(run).model_dump(),
        steps=[_step_public(s, info) for s in steps],
        participants=[ParticipantPublic.model_validate(p) for p in participants],
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
            detail="'import' is reserved for imported history",
        )
    participant_ids = list(dict.fromkeys([current_user.id, *payload.participant_user_ids]))
    for user_id in participant_ids:
        if session.get(User, user_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"User {user_id} not found"
            )

    rules_config = blind_fork.strip_server_rules(
        payload.rules_config if payload.rules_config is not None else dict(DEFAULT_RULES_CONFIG)
    )
    if "table_mode" in rules_config and not isinstance(rules_config["table_mode"], bool):
        raise HTTPException(422, detail="table_mode must be a boolean")
    engine_class = ENGINE_REGISTRY.get(payload.game_type)
    if engine_class is not None:
        engine = engine_class(session, tmdb)
        if engine.seed_policy == "none" and (
            payload.seed_movie_id is not None or payload.tail_seed_movie_id is not None
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{engine.display_name} doesn't use a seed film",
            )
        problems = engine.validate_rules_config(rules_config)
        problems += _bounty_board_problems(engine_class, rules_config)
        if rules_config.get(blind_fork.BLIND_FORK_KEY):
            problems += _blind_fork_problems(engine_class)
        if problems:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="; ".join(problems)
            )
        allowed_seeds = await engine.seed_candidates(rules_config)
        if allowed_seeds is not None:
            for seed_id in (payload.seed_movie_id, payload.tail_seed_movie_id):
                if seed_id is not None and seed_id not in allowed_seeds:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"Seed movie rejected: not in the configured {engine.display_name} slice",
                    )
        if engine.seed_policy == "pair":
            if payload.seed_movie_id is None or payload.tail_seed_movie_id is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Meet in the Middle needs two starting films: one for each partner",
                )
            if payload.seed_movie_id == payload.tail_seed_movie_id:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="The two partners need different starting films",
                )
        elif payload.tail_seed_movie_id is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A second seed film is only for Meet in the Middle runs",
            )
        rules_config = engine.prepare_rules_config(rules_config)
        try:
            rules_config = await engine.prepare_run(rules_config, current_user.id)
        except RunSetupError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        if bounties.board_enabled(rules_config):
            rules_config = bounties.prepare_board(rules_config)
        for seed_id in (payload.seed_movie_id, payload.tail_seed_movie_id):
            if seed_id is None:
                continue
            seed = await engine.validate_candidate(seed_id, rules_config)
            if not seed.valid:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Seed movie rejected: {seed.reason}",
                )

    run = Run(
        name=payload.name,
        game_type=payload.game_type,
        rules_config=rules_config,
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    participant_joined_at = utcnow()
    for index, user_id in enumerate(participant_ids):
        role = "owner" if user_id == current_user.id else "member"
        session.add(
            RunParticipant(
                run_id=run.id,
                user_id=user_id,
                role=role,
                joined_at=participant_joined_at + timedelta(microseconds=index),
            )
        )
    session.commit()

    seeds = [(payload.seed_movie_id, SIDE_HEAD), (payload.tail_seed_movie_id, SIDE_TAIL)]
    for seed_id, side in seeds:
        if seed_id is None:
            continue
        movie = await cache_repo.get_movie(session, tmdb, seed_id, require_detail=True)
        step = RunStep(
            run_id=run.id,
            logged_by_user_id=current_user.id,
            status="watched",
            watched_at=utcnow(),
            transition_metadata={
                **({"tunnel_side": side} if payload.game_type == MEET_IN_THE_MIDDLE else {}),
                SEED_KEY: True,
            },
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

@router.post("/{run_id}/wrap", response_model=RunDetail)
def wrap_marathon(
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
) -> RunDetail:
    if run.game_type not in ("method_actor", "auteur_marathon") or (
        _run_rules(run).get("track_length") != "endless"
    ):
        raise HTTPException(422, detail="Only endless marathons can be wrapped")
    if run.status != RUN_STATUS_ACTIVE:
        raise HTTPException(409, detail="This marathon is no longer active")
    film_ids = {film["movie_id"] for film in _run_rules(run).get("filmography", [])}
    watched = {step.movie_id for step in _run_history(session, run.id) if step.status == "watched"}
    count, total = len(film_ids & watched), len(film_ids)
    percent = round(100 * count / total) if total else 0
    run.status = RUN_STATUS_COMPLETED
    run.status_reason = f"Marathon wrapped: {count} of {total} films ({percent}%)"
    run.completed_at = utcnow()
    session.add(run)
    session.commit()
    session.refresh(run)
    return _to_run_detail(session, run)


@router.patch("/{run_id}/rules", response_model=RunDetail)
def update_run_rules(
    payload: RunRulesUpdate,
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
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
    update.update(
        {
            key: None
            for key in MODIFIER_UPDATE_KEYS
            if key in payload.model_fields_set and getattr(payload, key) is None
        }
    )
    merged = {**(run.rules_config or {}), **update}
    for key in ("track_length", "max_lives"):
        if key in update and update[key] != (run.rules_config or {}).get(key):
            raise HTTPException(422, detail=f"{key} can only be chosen when creating a run")
    engine_class = ENGINE_REGISTRY.get(run.game_type)
    if update.get(blind_fork.BLIND_FORK_KEY) is False:
        merged = blind_fork.with_fork(merged, None)
    if engine_class is not None:
        problems = engine_class(session, tmdb).validate_rules_config(merged)
        if update.get(blind_fork.BLIND_FORK_KEY):
            problems += _blind_fork_problems(engine_class)
        if problems:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="; ".join(problems)
            )
    if run.game_type in ("method_actor", "auteur_marathon") and "order" in update:
        from app.engines.method_actor import marathon_skip
        merged["max_skip"] = marathon_skip(merged)
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if session.get(RunParticipant, (run.id, payload.user_id)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already a participant")
    participant = RunParticipant(run_id=run.id, user_id=payload.user_id, role=payload.role)
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Participant not found")
    session.delete(participant)
    session.commit()


async def _log_step(
    session: Session,
    tmdb: TMDBClient,
    run: Run,
    user: User,
    payload: RunStepCreate,
    omdb: OMDbClient | None = None,
    fork_team: str | None = None,
) -> RunStep:
    """Validate and add one step, then evaluate the run's outcome. Caller commits."""
    actor = _acting_user(session, run, user, payload.acting_participant_id)
    split = run.game_type == RT_SPLIT
    if payload.no_contest and not split:
        raise HTTPException(422, detail="No-contest is only available on Rotten Tomatoes Split runs")
    if payload.no_contest and payload.status != "watched":
        raise HTTPException(422, detail="Log a no-contest film as watched")
    movie = await cache_repo.get_movie(session, tmdb, payload.movie_id, require_detail=True)
    if split and not payload.no_contest:
        if payload.status != "watched" or payload.household_score is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Log a split film as watched, with the household's rating (1-100)",
            )
        if omdb is not None:  # the scores the split is judged on come from OMDb
            await cache_repo.get_movie_ratings(session, tmdb, omdb, movie.tmdb_id)
    extra_metadata, linked_metadata = await _enforce_run_rules(
        session, tmdb, run, movie, payload, actor, fork_team
    )
    if (run.rules_config or {}).get("table_mode") is True:
        extra_metadata["acting_participant_id"] = actor.id
    if split:
        if payload.no_contest:
            extra_metadata["split_no_contest"] = True
        else:
            assert payload.household_score is not None
            extra_metadata.update(
                RottenTomatoesSplitEngine(session, tmdb).settle(movie.tmdb_id, payload.household_score)
            )
    bounty = await bounties.evaluate(session, tmdb, run.rules_config, movie)
    if bounty is not None:
        extra_metadata[bounties.COMPLETED_METADATA_KEY] = bounty[0]
        extra_metadata[bounties.REPLACEMENT_METADATA_KEY] = bounty[1]

    transition_metadata = _without_server_keys(
        linked_metadata if linked_metadata is not None else payload.transition_metadata
    )
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
        before_lives = (
            lives_of(run.rules_config)[0] if run.game_type == rabbit_hole.RABBIT_HOLE else None
        )
        awarded_rules = get_engine(run.game_type, session, tmdb).award_bounty(
            run.rules_config or {}, *bounty
        )
        if before_lives is not None:
            extra_metadata["bounty_life_awarded"] = (
                awarded_rules.get(LIVES_KEY, before_lives) > before_lives
            )
            step.transition_metadata = {
                **(step.transition_metadata or {}),
                "bounty_life_awarded": extra_metadata["bounty_life_awarded"],
            }
        run.rules_config = awarded_rules
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
    current_user: User = Depends(get_current_user),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
):
    _ensure_run_open(run)
    step = session.get(RunStep, step_id)
    if step is None or step.run_id != run.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Step not found")
    if step.status == "watched":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Step is already marked as watched"
        )
    actor = _acting_user(session, run, current_user, payload.acting_participant_id)
    if (run.rules_config or {}).get("table_mode") is True and _step_actor_id(step) != actor.id:
        raise HTTPException(403, detail="Switch to the participant who queued this film")
    if run.game_type == TUG_OF_WAR and (run.rules_config or {}).get(TUG_RULES_VERSION_KEY) in (2, 3):
        players = TugOfWarEngine(session, tmdb).team_players(run)
        team = step_turn_team(step, players)
        if team is not None and all(players.values()):
            next_team = tally(_run_history(session, run.id), run.rules_config, players).next_team
            if team != next_team:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"It's {TugOfWarEngine(session, tmdb).team_name(run, next_team)}'s pull",
                )
    step.status = "watched"
    if run.game_type == TUG_OF_WAR and (run.rules_config or {}).get(TUG_RULES_VERSION_KEY) == 3:
        step.logged_at = utcnow()
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
    current_user: User = Depends(get_current_user),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
):
    step = session.get(RunStep, step_id)
    if step is None or step.run_id != run.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Step not found")
    actor = _acting_user(session, run, current_user, payload.acting_participant_id)
    if payload.watched_at is not None and step.status == "planned":
        _ensure_run_open(run)
        if (run.rules_config or {}).get("table_mode") is True and _step_actor_id(step) != actor.id:
            raise HTTPException(403, detail="Switch to the participant who queued this film")
        if run.game_type == TUG_OF_WAR and (run.rules_config or {}).get(TUG_RULES_VERSION_KEY) in (2, 3):
            players = TugOfWarEngine(session, tmdb).team_players(run)
            team = step_turn_team(step, players)
            if team is not None and all(players.values()):
                next_team = tally(
                    _run_history(session, run.id), run.rules_config, players
                ).next_team
                if team != next_team:
                    name = TugOfWarEngine(session, tmdb).team_name(run, next_team)
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"It's {name}'s pull",
                    )
        if run.game_type == TUG_OF_WAR and (run.rules_config or {}).get(TUG_RULES_VERSION_KEY) == 3:
            step.logged_at = utcnow()
    if payload.user_notes is not None:
        step.user_notes = payload.user_notes
    if payload.transition_metadata is not None:
        owned = {
            k: v for k, v in (step.transition_metadata or {}).items() if k in SERVER_OWNED_METADATA
        }
        step.transition_metadata = {
            **(_without_server_keys(payload.transition_metadata) or {}),
            **owned,
        }
    if payload.watched_at is not None:
        step.watched_at = payload.watched_at
        # Setting a watched date IS the act of marking it watched - keep the
        # 1-click "Mark as Watched" quick action (which PATCHes only
        # watched_at, not status) from leaving stale "planned" state behind.
        if step.status == "planned":
            step.status = "watched"
        if (step.transition_metadata or {}).get(SEED_KEY):
            metadata = dict(step.transition_metadata or {})
            metadata.pop(SEED_KEY, None)
            step.transition_metadata = metadata or None
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
    rules_before_revoke = dict(run.rules_config or {})
    if metadata.get(bounties.COMPLETED_METADATA_KEY):
        # The step earned a wildcard for a bounty: take both back.
        run.rules_config = bounties.revoke(
            run.rules_config or {},
            metadata[bounties.COMPLETED_METADATA_KEY],
            metadata.get(bounties.REPLACEMENT_METADATA_KEY),
        )
        session.add(run)
    if run.game_type == rabbit_hole.RABBIT_HOLE:
        lives, max_lives = lives_of(rules_before_revoke)
        if metadata.get("bounty_life_awarded"):
            lives = max(0, lives - 1)
        if metadata.get("life_lost"):
            lives = min(max_lives, lives + 1)
        restored_rules = {
            **(run.rules_config or rules_before_revoke),
            "wildcards_budget": rules_before_revoke.get("wildcards_budget", 0),
            LIVES_KEY: lives,
        }
        run.rules_config = restored_rules
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
        and (
            winner(
                tally(
                    remaining,
                    run.rules_config,
                    (run.rules_config or {}).get(PLAYERS_KEY),
                )
            )
            is None
            if (run.rules_config or {}).get(TUG_RULES_VERSION_KEY) in (2, 3)
            else leading_team(compute_scores(remaining, run.rules_config), run.rules_config) is None
        )
    ):
        reopen = True
    if (
        run.game_type == RT_SPLIT
        and run.status == RUN_STATUS_COMPLETED
        and (run.status_reason or "").startswith(SPLIT_VICTORY_PREFIX)
        and split_winner(compute_split_scores(remaining), run.rules_config) is None
    ):
        reopen = True
    if (
        run.game_type == rabbit_hole.RABBIT_HOLE
        and run.status == RUN_STATUS_COMPLETED
        and (run.status_reason or "").startswith("Escaped the Rabbit Hole at Depth ")
        and len(remaining) < (run.rules_config or {}).get("escape_depth", len(remaining) + 1)
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Step not found")

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
            status_code=status.HTTP_409_CONFLICT, detail="There is no Blind Fork offer to answer"
        )
    return fork


def _require_partner_of_offer(fork: dict, user: User) -> None:
    if fork.get("offered_by_id") == user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your partner answers your offer - you can't veto or pick from it",
        )


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
    actor = _acting_user(session, run, current_user, payload.acting_participant_id)
    rules = _run_rules(run)
    if not rules.get(blind_fork.BLIND_FORK_KEY):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="The Blind Fork is off for this run"
        )
    _ensure_no_pending_fork(run)
    participants = session.exec(select(RunParticipant).where(RunParticipant.run_id == run.id)).all()
    if len(participants) < 2:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The Blind Fork needs a partner to answer the offer",
        )
    if _last_step(session, run.id) is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Log a first film before offering a fork"
        )
    for movie_id in payload.movie_ids:
        movie = await cache_repo.get_movie(session, tmdb, movie_id)
        try:
            await _enforce_run_rules(
                session, tmdb, run, movie, RunStepCreate(movie_id=movie_id), actor
            )
        except HTTPException as exc:
            reason = exc.detail.get("reason") if isinstance(exc.detail, dict) else exc.detail
            raise HTTPException(
                status_code=exc.status_code, detail=f"{movie.title}: {reason}"
            ) from exc
    run.rules_config = blind_fork.with_fork(
        rules,
        blind_fork.new_offer(
            actor.id,
            payload.movie_ids,
            {
                movie_id: _without_server_keys(meta) or {}
                for movie_id, meta in payload.links.items()
            },
        ),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return _to_run_detail(session, run)


@router.delete("/{run_id}/fork", response_model=RunDetail)
def withdraw_fork(
    acting_participant_id: str | None = Query(default=None),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    run: Run = Depends(run_participant_guard),
) -> RunDetail:
    """The offering player takes their offer back (e.g. the partner is away)."""
    actor = _acting_user(session, run, current_user, acting_participant_id)
    fork = _require_fork(run)
    if fork.get("offered_by_id") != actor.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the player who made the offer can withdraw it",
        )
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
    actor = _acting_user(session, run, current_user, payload.acting_participant_id)
    _require_partner_of_offer(fork, actor)
    movie_ids = list(fork["movie_ids"])
    if len(movie_ids) != blind_fork.OFFER_SIZE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="One film has already been vetoed"
        )
    if payload.movie_id not in movie_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That film isn't part of the offer"
        )
    movie_ids.remove(payload.movie_id)
    run.rules_config = blind_fork.with_fork(
        run.rules_config,
        {
            **fork,
            "movie_ids": movie_ids,
            "vetoed_movie_id": payload.movie_id,
            "vetoed_by_id": actor.id,
        },
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return _to_run_detail(session, run)


@router.post(
    "/{run_id}/fork/accept", response_model=RunStepPublic, status_code=status.HTTP_201_CREATED
)
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
    actor = _acting_user(session, run, current_user, payload.acting_participant_id)
    _require_partner_of_offer(fork, actor)
    if len(fork["movie_ids"]) != blind_fork.OFFER_SIZE - 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Veto one of the three films first"
        )
    if payload.movie_id not in fork["movie_ids"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That film isn't left in the offer"
        )
    link = (fork.get("links") or {}).get(str(payload.movie_id))
    tug_team = None
    if run.game_type == TUG_OF_WAR:
        players = TugOfWarEngine(session, tmdb).team_players(run)
        tug_team = team_of(players, fork["offered_by_id"])
    step = await _log_step(
        session,
        tmdb,
        run,
        current_user,
        RunStepCreate(
            acting_participant_id=payload.acting_participant_id,
            movie_id=payload.movie_id,
            transition_metadata=link,
            user_notes=payload.user_notes,
            status=payload.status,
            tug_team=tug_team,
        ),
        fork_team=tug_team,
    )
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
    actor = _acting_user(session, run, current_user, payload.acting_participant_id)
    if payload.target == "fork":
        fork = _require_fork(run)
        _require_partner_of_offer(fork, actor)
    else:
        if run.game_type == MEET_IN_THE_MIDDLE:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Meet in the Middle is co-op - use Undo on your own side",
            )
        steps = _run_history(session, run.id)
        seeds = 2 if run.game_type == MEET_IN_THE_MIDDLE else 1
        if len(steps) <= seeds:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="There is no contested step to veto"
            )
        target_step = steps[-1]
        if run.game_type == TUG_OF_WAR:
            players = TugOfWarEngine(session, tmdb).team_players(run)
            target_team = step_turn_team(target_step, players)
            current_team = team_of(players, actor.id)
            is_own_team = target_team is None or current_team is None or target_team == current_team
        else:
            is_own_team = _step_actor_id(target_step) in (None, actor.id)
        if is_own_team:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You can only veto a step your partner logged - delete your own instead",
            )
    refresh_veto_tokens(session, actor)
    if not consume_veto_token(session, actor):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No Golden Veto tokens left - you get one every 30 days",
        )
    if payload.target == "fork":
        run.rules_config = blind_fork.with_fork(run.rules_config, None)
        session.add(run)
    else:
        _remove_step(session, tmdb, run, target_step)
    session.commit()
    session.refresh(run)
    return GoldenVetoResult(
        target=payload.target,
        veto_tokens=actor.veto_tokens,
        run=_to_run_detail(session, run),
    )


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
        previous.movie_id,
        payload.movie_id,
        cast_limit=rules.get("max_cast_order"),
        rules=rules,
        previous_transition=previous.transition_metadata,
        history=side_steps if side_steps is not None else _run_history(session, run.id),
    )
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
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only Meet in the Middle runs have a tunnel",
        )
    steps = _run_history(session, run.id)
    head, tail = split_sides(steps)
    state = TunnelState(
        head_frontier_movie_id=head[-1].movie_id if head else None,
        tail_frontier_movie_id=tail[-1].movie_id if tail else None,
        head_steps=len(head),
        tail_steps=len(tail),
        collided=run.status == RUN_STATUS_COMPLETED
        and any((s.transition_metadata or {}).get("collision") for s in steps),
        hints_remaining=_run_rules(run).get("tunnel_hints_remaining", 2),
    )
    if state.collided:
        return state.model_copy(update={"distance_hops": 0})
    if not head or not tail:
        return state.model_copy(update={"message": "Both partners need a starting film."})
    head_id, tail_id = head[-1].movie_id, tail[-1].movie_id
    cached = _run_rules(run).get("tunnel_distance")
    if (
        isinstance(cached, dict)
        and cached.get("head_id") == head_id
        and cached.get("tail_id") == tail_id
    ):
        return state.model_copy(
            update={
                "distance_hops": cached.get("hops"),
                "searched_depth": cached.get("depth_reached", 0),
                "message": cached.get("message"),
            }
        )
    try:
        distance = await engine.distance(
            head_id,
            tail_id,
            {s.movie_id for s in steps},
            cast_limit=_run_rules(run).get("max_cast_order"),
        )
    except Exception as exc:  # noqa: BLE001 - the indicator is advisory; never fail the page
        return state.model_copy(update={"message": f"Couldn't measure the distance: {exc}"})
    rules = _run_rules(run)
    run.rules_config = {
        **rules,
        "tunnel_distance": {
            "head_id": head_id,
            "tail_id": tail_id,
            "hops": distance.hops,
            "depth_reached": distance.searched_depth,
            "message": distance.message,
        },
    }
    session.add(run)
    session.commit()
    return state.model_copy(
        update={
            "distance_hops": distance.hops,
            "searched_depth": distance.searched_depth,
            "message": distance.message,
        }
    )


@router.post("/{run_id}/tunnel/hint", response_model=TunnelHintResponse)
async def request_tunnel_hint(
    payload: TunnelHintRequest,
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> TunnelHintResponse:
    """Search deeper toward the opposite frontier and spend a hint only if a path is found."""
    engine = get_engine(run.game_type, session, tmdb)
    if not isinstance(engine, MeetInTheMiddleEngine):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hints are only available in Meet in the Middle runs",
        )
    steps = _run_history(session, run.id)
    head, tail = split_sides(steps)
    collided = any((step.transition_metadata or {}).get("collision") for step in steps)
    if run.status != RUN_STATUS_ACTIVE or collided:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This tunnel is already closed",
        )
    if not head or not tail:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Both partners need a starting film before using a hint",
        )

    cost = 1 if payload.level == "actor" else 2
    rules = _run_rules(run)
    remaining = rules.get("tunnel_hints_remaining", 2)
    if remaining < cost:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Not enough hint tokens ({cost} required, {remaining} remaining)",
        )

    head_id, tail_id = head[-1].movie_id, tail[-1].movie_id
    distance = await engine.distance(
        head_id,
        tail_id,
        {step.movie_id for step in steps},
        cast_limit=rules.get("max_cast_order"),
        max_depth=7,
        max_seconds=20,
    )
    if distance.hops is None or not distance.path_movie_ids:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{distance.message} No token spent."
                if distance.message
                else "No route found within the hint search budget. No token spent."
            ),
        )
    if payload.level == "film" and distance.hops < 2:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The shortest route has no hidden film to reveal. No token spent.",
        )

    session.refresh(run)
    latest_steps = _run_history(session, run.id)
    latest_head, latest_tail = split_sides(latest_steps)
    if (
        run.status != RUN_STATUS_ACTIVE
        or not latest_head
        or not latest_tail
        or latest_head[-1].movie_id != head_id
        or latest_tail[-1].movie_id != tail_id
        or any((step.transition_metadata or {}).get("collision") for step in latest_steps)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The tunnel changed during the search; no token spent. Try again.",
        )
    rules = _run_rules(run)
    remaining = rules.get("tunnel_hints_remaining", 2)
    if remaining < cost:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another hint used the remaining tokens during the search; no token spent",
        )

    path_ids = distance.path_movie_ids
    edge_index = 0 if payload.side == SIDE_HEAD else -1
    actor_hint = None
    film_hint = None
    if payload.level == "actor":
        connections = distance.connections or []
        if not connections:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The found route has no actor connection to reveal; no token spent",
            )
        connection = connections[edge_index]
        actor_hint = TunnelHintActor(actor_id=connection.actor_id, actor_name=connection.actor_name)
    else:
        film_id = path_ids[1] if payload.side == SIDE_HEAD else path_ids[-2]
        film = session.get(CachedMovie, film_id)
        film_hint = TunnelHintFilm(
            movie_id=film_id, title=film.title if film is not None else str(film_id)
        )

    run.rules_config = {**rules, "tunnel_hints_remaining": remaining - cost}
    session.add(run)
    session.commit()
    return TunnelHintResponse(
        level=payload.level,
        actor=actor_hint,
        film=film_hint,
        tokens_remaining=remaining - cost,
    )


@router.get("/{run_id}/constraint", response_model=ConstraintInfo | None)
async def get_run_constraint(
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> ConstraintInfo | None:
    """The rule shaping this run's next hop (e.g. "must be a Director"), or null."""
    engine = get_engine(run.game_type, session, tmdb)
    tail = _last_step(session, run.id)
    rules = _run_rules(run)
    history = _run_history(session, run.id)
    constraint = await engine.describe_run_constraint(
        tail.movie_id if tail is not None else None,
        tail.transition_metadata if tail is not None else None,
        rules,
        history,
    )
    if (
        isinstance(engine, RabbitHoleEngine)
        and constraint is not None
        and constraint.rabbit_hole is not None
        and constraint.rabbit_hole.lives_remaining == 0
        and tail is not None
    ):
        candidates = await engine.discover_with_modifiers(
            frontier_movie_id=tail.movie_id,
            cast_limit=rules.get("max_cast_order"),
            rules=rules,
            previous_transition=tail.transition_metadata,
            history=history,
        )
        constraint = constraint.model_copy(
            update={
                "rabbit_hole": constraint.rabbit_hole.model_copy(
                    update={"dead_end": not candidates}
                )
            }
        )
    return constraint


@router.post("/{run_id}/rabbit-hole/reroll", response_model=RunDetail)
def reroll_rabbit_hole_tier(
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
) -> RunDetail:
    """Spend one life to replace the tier requirement for the next depth only."""
    if run.game_type != rabbit_hole.RABBIT_HOLE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tier re-rolls are only available in Rabbit Hole runs",
        )
    if run.status != RUN_STATUS_ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This run is no longer active"
        )

    rules = dict(_run_rules(run))
    if not rules.get("allow_reroll", True):
        raise HTTPException(409, detail="Tier re-rolls are disabled for this ruleset")
    depth = len(_run_history(session, run.id))
    lives, _ = lives_of(rules)
    if lives < 2:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="At least two lives are needed to re-roll a tier",
        )
    if tier_for_depth(depth, rules).number <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tier re-rolls are available from Tier 2 onward",
        )
    override = rules.get(TIER_OVERRIDE_KEY)
    if isinstance(override, dict) and override.get("depth") == depth:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This depth already has a re-rolled tier",
        )

    current_tier = tier_for_depth(depth).number
    choices = [tier for tier in range(2, 6) if tier != current_tier]
    rules[TIER_OVERRIDE_KEY] = {"depth": depth, "tier": random.choice(choices)}
    rules[LIVES_KEY] = lives - 1
    run.rules_config = rules
    session.add(run)
    session.commit()
    session.refresh(run)
    return _to_run_detail(session, run)


@router.get("/{run_id}/suggestions", response_model=list[Suggestion])
async def get_run_suggestions(
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    country: str | None = Query(default=None, pattern="^[A-Z]{2}$"),
    decade: int | None = Query(default=None, ge=1880, le=2100, multiple_of=10),
    genre_id: int | None = Query(default=None),
    chaser: bool = Query(default=False),
    sort_by: str | None = Query(default=None, pattern="^underdog$"),
    frontier_movie_id: int | None = Query(default=None),
) -> list[Suggestion]:
    current = _last_step(session, run.id)
    if current is None:
        return []
    history = _run_history(session, run.id)
    if frontier_movie_id is not None and frontier_movie_id != current.movie_id:
        current = next((step for step in reversed(history) if step.movie_id == frontier_movie_id), None)
        if current is None:
            raise HTTPException(status_code=422, detail="Search frontier must belong to this run.")
    logged_movie_ids = [
        step.movie_id
        for step in session.exec(select(RunStep).where(RunStep.run_id == run.id)).all()
    ]
    engine = get_engine(run.game_type, session, tmdb)
    filters = SuggestionFilters(country=country, decade=decade, genre_id=genre_id)
    try:
        async with asyncio.timeout(20):
            return await _checked_suggestions(
                session, run, engine, current, history, logged_movie_ids, filters, chaser, sort_by
            )
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Search took too long. Try again or narrow the filters.") from exc


async def _checked_suggestions(
    session: Session,
    run: Run,
    engine: BaseChallengeEngine,
    current: RunStep,
    history: list[RunStep],
    logged_movie_ids: list[int],
    filters: SuggestionFilters,
    chaser: bool,
    sort_by: str | None,
) -> list[Suggestion]:
    suggestions = await engine.get_suggestions(
        current.movie_id, logged_movie_ids, filters, rules=_run_rules(run), history=history
    )
    checked: list[Suggestion] = []
    for suggestion in suggestions[:20]:
        validation = await engine.validate_next_step(
            current.movie_id, suggestion.movie_id,
            cast_limit=_run_rules(run).get("max_cast_order"),
            rules=_run_rules(run),
            previous_transition=current.transition_metadata,
            history=history,
        )
        if validation.valid:
            suggestion.connections = [
                DiscoveryConnection(
                    kind=connection.kind, actor_id=connection.actor_id,
                    actor_name=connection.actor_name, profile_path=connection.profile_path,
                    character_in_frontier=connection.character_in_from,
                    character_in_candidate=connection.character_in_to,
                    role_in_frontier=connection.role_in_from, role_in_candidate=connection.role_in_to,
                )
                for connection in validation.connections
            ]
            row = session.get(CachedMovie, suggestion.movie_id)
            if row is not None:
                suggestion.origin_country = row.origin_country
                suggestion.genre_ids = row.genre_ids or []
                suggestion.runtime = row.runtime
                suggestion.popularity = row.popularity
                suggestion.narrative_year = row.narrative_year
                suggestion.narrative_era_label = row.narrative_era_label
            checked.append(suggestion)
    suggestions = checked
    if chaser or sort_by:
        by_id = {s.movie_id: s for s in suggestions}
        kept = await pool_options.shape_pool(
            session, engine.tmdb, [s.movie_id for s in suggestions], chaser=chaser, sort_by=sort_by
        )
        suggestions = [by_id[movie_id] for movie_id in kept]
    return suggestions


def _ensure_chaos_allowed(run: Run) -> None:
    _ensure_run_open(run)
    engine_class = ENGINE_REGISTRY.get(run.game_type)
    if engine_class is None or "discover_candidates" not in engine_class.capabilities:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The Chaos Button needs a mode with a Pick Next pool",
        )


@router.post("/{run_id}/bounties/custom", response_model=RunDetail)
async def roll_custom_bounty(
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
) -> RunDetail:
    """ "✨ Roll Custom Bounty": the AI writes a new bounty (with a programmatic rule) that takes
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
            status_code=status.HTTP_409_CONFLICT, detail="A chaos handicap is already active"
        )
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
            select(RunStep).where(RunStep.run_id == run.id).order_by(RunStep.logged_at)
        ).all()
    )
    engine = get_engine(run.game_type, session, tmdb)
    return await engine.compute_stats(steps)


@router.get("/{run_id}/discover", response_model=list[DiscoveryCandidate])
async def discover_next_movies(
    frontier_movie_id: int = Query(...),
    mode: str = Query(default="or", pattern="^(or|and)$"),
    chaser: bool = Query(
        default=False, description="Only palate cleansers: <= 95 min, Comedy/Animation"
    ),
    include_off_tier: bool = Query(
        default=False, description="Rabbit Hole only: include linked films that cost a life"
    ),
    sort_by: str | None = Query(
        default=None,
        pattern="^underdog$",
        description="underdog = least popular first (popularity >= 1.0)",
    ),
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
    if include_off_tier and not isinstance(engine, RabbitHoleEngine):
        raise HTTPException(status_code=422, detail="Off-tier discovery is only supported by Rabbit Hole.")
    previous = _last_step(session, run.id)
    try:
        candidates = await engine.discover_with_modifiers(
            frontier_movie_id=frontier_movie_id,
            mode=mode,
            cast_limit=rules.get("max_cast_order"),
            rules=rules,
            previous_transition=(
                previous.transition_metadata
                if previous is not None and previous.movie_id == frontier_movie_id
                else None
            ),
            history=_run_history(session, run.id),
            **({"include_off_tier": include_off_tier} if isinstance(engine, RabbitHoleEngine) else {}),
        )
    except NotImplementedError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Discovery is not supported for game_type '{run.game_type}'",
        ) from None

    logged_movie_ids = {
        step.movie_id
        for step in session.exec(select(RunStep).where(RunStep.run_id == run.id)).all()
    }
    ordered_steps = session.exec(
        select(RunStep).where(RunStep.run_id == run.id).order_by(RunStep.logged_at)
    ).all()
    step_number_by_movie_id: dict[int, int] = {}
    for index, step in enumerate(ordered_steps):
        step_number_by_movie_id.setdefault(step.movie_id, index + 1)
    if chaser or sort_by:
        by_id = {c.movie_id: c for c in candidates}
        kept = await pool_options.shape_pool(
            session, tmdb, [c.movie_id for c in candidates], chaser=chaser, sort_by=sort_by
        )
        candidates = [by_id[movie_id] for movie_id in kept]
    for candidate in candidates:
        candidate.already_in_run = candidate.movie_id in logged_movie_ids
        candidate.existing_step_number = step_number_by_movie_id.get(candidate.movie_id)
        row = session.get(CachedMovie, candidate.movie_id)
        candidate.runtime = row.runtime if row is not None else None
    return candidates
