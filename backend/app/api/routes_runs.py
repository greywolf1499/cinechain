import asyncio
import copy
import logging
import random
import time
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import Session, select

from app.api.deps import get_current_user, get_omdb_client, get_tmdb_client, run_participant_guard
from app.api.routes_tasks import TaskOut
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
from app.engines.traversal import get_policy
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
    plane_facts_for,
    plane_for_rules,
    plane_verdict,
    step_turn_team,
    tally,
    team_of,
    winner,
)
from app.engines.tug_planes import NEUTRAL
from app.integrations.omdb import OMDbClient
from app.models.cache import CachedCrewCredit, CachedMovie, CachedMovieCast, CachedMovieDirector
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
    DiscoveryEnvelope,
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
from app.services import (
    blind_fork,
    bounties,
    cache_repo,
    embeddings,
    feasibility,
    pool_options,
    vibe_controller,
)
from app.services.bridge_paths import parse_countries
from app.services.movie_filters import is_reality_eligible, rating_of
from app.services.tmdb import TMDBClient
from app.services.veto import consume_veto_token, refresh_veto_tokens
from app.utils.dates import parse_release_year
from app.utils.ids import utcnow

router = APIRouter(prefix="/runs", tags=["runs"])

TUG_LOOKAHEAD_SECONDS = 1.5
logger = logging.getLogger(__name__)


class RabbitHolePeriscopeRequest(BaseModel):
    depth: int = Field(ge=1, strict=True)


class VerifyCandidatesRequest(BaseModel):
    movie_ids: list[int] = Field(max_length=24)


def _public_rules(run: Run, *, depth: int | None = None) -> dict:
    engine_class = ENGINE_REGISTRY.get(run.game_type)
    if engine_class is None:
        return _run_rules(run)
    if run.game_type == rabbit_hole.RABBIT_HOLE:
        return engine_class.public_rules(_run_rules(run), run, depth=depth)
    return engine_class.public_rules(_run_rules(run), run)


def _public_summary(session: Session, run: Run) -> RunSummary:
    depth = None
    if run.game_type == rabbit_hole.RABBIT_HOLE:
        depth = session.exec(
            select(func.count()).select_from(RunStep).where(RunStep.run_id == run.id)
        ).one()
    summary = RunSummary.model_validate(run)
    return summary.model_copy(update={"rules_config": _public_rules(run, depth=depth)})


def _cached_tug_lookahead(
    session: Session,
    movie_ids: list[int],
    rules: dict,
    excluded: set[int],
    deadline: float,
    results: dict[int, TugReachable],
) -> None:
    repo = cache_repo.CacheRepo(session)
    limit = rules.get("max_cast_order") or 15
    puller = (rules.get("tug_momentum") or {}).get("next_team", TEAM_A)
    opponent = TEAM_B if puller == TEAM_A else TEAM_A
    version = rules.get(TUG_RULES_VERSION_KEY)
    policy = get_policy(rules.get("tug_traversal")) if version == 4 else None
    if version == 4 and policy is not None and not policy.graph:
        from app.services import feasibility

        movies = feasibility.movies(session)
        source = (
            [entry["movie_id"] for entry in rules.get("tug_deal", [])]
            if policy.key == "draft"
            else list(movies)
        )
        for movie_id in movie_ids:
            count = TugReachable()
            for related_id in source:
                if time.monotonic() >= deadline:
                    count.partial = True
                    break
                row = movies.get(related_id)
                if row is None or related_id in excluded or related_id == movie_id:
                    continue
                if not is_reality_eligible(row):
                    continue
                territory, _ = plane_verdict(session, related_id, rules)
                if territory in (None, "unknown"):
                    count.partial = True
                elif territory == NEUTRAL:
                    count.neutral += 1
                else:
                    count.scoring += 1
            results[movie_id] = count
        return
    for movie_id in movie_ids:
        if time.monotonic() >= deadline:
            return
        related_movies: dict[int, CachedMovie] = {}
        partial = False
        if policy is None or policy.key in ("shared_cast", "shared_any_person"):
            cast = repo.get_cached_cast(movie_id, limit)
            partial |= cast is None
            for member in cast or []:
                if time.monotonic() >= deadline:
                    partial = True
                    break
                credits = repo.get_cached_actor_credits(member["actor_id"])
                partial |= credits is None
                related_movies.update((item.tmdb_id, item) for item in (credits or []))
        if policy is not None and policy.key in ("shared_director", "shared_any_person"):
            director_ids = session.exec(
                select(CachedMovieDirector.person_id).where(
                    CachedMovieDirector.movie_id == movie_id
                )
            ).all()
            for director_id in director_ids:
                linked_ids = session.exec(
                    select(CachedMovieDirector.movie_id).where(
                        CachedMovieDirector.person_id == director_id
                    )
                ).all()
                for linked_id in linked_ids:
                    linked_movie = session.get(CachedMovie, linked_id)
                    if linked_movie is not None:
                        related_movies[linked_id] = linked_movie
        if policy is not None and policy.key == "shared_any_person":
            crew = repo.get_cached_crew(movie_id)
            partial |= crew is None
            for credit in crew or []:
                linked_ids = session.exec(
                    select(CachedCrewCredit.movie_id).where(
                        CachedCrewCredit.person_id == credit.person_id
                    )
                ).all()
                for linked_id in linked_ids:
                    linked_movie = session.get(CachedMovie, linked_id)
                    if linked_movie is not None:
                        related_movies[linked_id] = linked_movie
        count = TugReachable(partial=partial)
        seen = {movie_id, *excluded}
        for related_id, movie in related_movies.items():
            if time.monotonic() >= deadline:
                count.partial = True
                break
            if related_id in seen:
                continue
            seen.add(related_id)
            if not is_reality_eligible(movie):
                continue
            if movie.runtime is not None and movie.runtime < rules.get("min_runtime", 0):
                continue
            if version == 4:
                plane = plane_for_rules(rules)
                facts = plane_facts_for(session, [related_id], rules).get(related_id, {})
                if plane.unknown(facts):
                    count.partial = True
                territory = plane.territory(facts)
            else:
                territory = _territory(
                    parse_release_year(movie.release_date), movie.origin_country, rules
                )
                if (rules.get("dimension", "era") == "era" and movie.release_date is None) or (
                    rules.get("dimension") == "geography" and movie.origin_country is None
                ):
                    count.partial = True
            territory = None if territory == NEUTRAL else territory
            if territory is None or (
                territory != opponent and not rules.get("steal_enabled", True)
            ):
                count.neutral += 1
            else:
                count.scoring += 1
        if time.monotonic() >= deadline:
            count.partial = True
        results[movie_id] = count


def _tug_portal_available(
    session: Session, run: Run, rules: dict, history: list[RunStep] | None = None
) -> bool:
    if run.status != RUN_STATUS_ACTIVE:
        return False
    if rules.get(TUG_RULES_VERSION_KEY) != 4:
        return False
    if not get_policy(rules.get("tug_traversal")).graph:
        return False
    if (rules.get("tug_portals") or {}).get("remaining", 0) < 1:
        return False
    frontier = _last_step(session, run.id)
    if frontier is None:
        return False
    steps = history if history is not None else _play_history(session, run.id)
    excluded = {step.movie_id for step in steps} - {frontier.movie_id}
    replies: dict[int, TugReachable] = {}
    _cached_tug_lookahead(
        session,
        [frontier.movie_id],
        rules,
        excluded,
        time.monotonic() + 1.5,
        replies,
    )
    count = replies.get(frontier.movie_id)
    return bool(count and not count.partial and count.scoring == 0)


def _cached_tug_link_state(
    session: Session, first_id: int, second_id: int, traversal: str
) -> bool | None:
    first = session.get(CachedMovie, first_id)
    second = session.get(CachedMovie, second_id)
    if first is None or second is None:
        return None
    first_people: set[int] = set()
    second_people: set[int] = set()
    if traversal in ("shared_cast", "shared_any_person"):
        if first.cast_fetched_at is None or second.cast_fetched_at is None:
            return None
        first_people.update(
            session.exec(
                select(CachedMovieCast.actor_id).where(CachedMovieCast.movie_id == first_id)
            ).all()
        )
        second_people.update(
            session.exec(
                select(CachedMovieCast.actor_id).where(CachedMovieCast.movie_id == second_id)
            ).all()
        )
    if traversal in ("shared_director", "shared_any_person"):
        if first.directors_fetched_at is None or second.directors_fetched_at is None:
            return None
        first_people.update(
            session.exec(
                select(CachedMovieDirector.person_id).where(
                    CachedMovieDirector.movie_id == first_id
                )
            ).all()
        )
        second_people.update(
            session.exec(
                select(CachedMovieDirector.person_id).where(
                    CachedMovieDirector.movie_id == second_id
                )
            ).all()
        )
    if traversal == "shared_any_person":
        if first.crew_fetched_at is None or second.crew_fetched_at is None:
            return None
        first_people.update(
            session.exec(
                select(CachedCrewCredit.person_id).where(CachedCrewCredit.movie_id == first_id)
            ).all()
        )
        second_people.update(
            session.exec(
                select(CachedCrewCredit.person_id).where(CachedCrewCredit.movie_id == second_id)
            ).all()
        )
    return bool(first_people & second_people)


@router.get(
    "/{run_id}/tug/lookahead", response_model=TugLookahead, response_model_exclude_none=True
)
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
    excluded = (
        {step.movie_id for step in _run_history(session, run.id)}
        if rules.get("allow_repeats", "strict") != "allowed"
        else set()
    )
    results = {movie_id: TugReachable(partial=True) for movie_id in ids}
    deadline = time.monotonic() + TUG_LOOKAHEAD_SECONDS
    version = rules.get(TUG_RULES_VERSION_KEY)
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
    portal_available: bool | None = None
    if version == 4:
        portal_available = False
        policy = get_policy(rules.get("tug_traversal"))
        portals = rules.get("tug_portals") or {}
        frontier = _last_step(session, run.id)
        if policy.graph and portals.get("remaining", 0) > 0 and frontier is not None:
            frontier_replies: dict[int, TugReachable] = {}
            _cached_tug_lookahead(
                session,
                [frontier.movie_id],
                rules,
                excluded - {frontier.movie_id},
                deadline,
                frontier_replies,
            )
            reply_count = frontier_replies.get(frontier.movie_id)
            portal_available = bool(
                reply_count and not reply_count.partial and reply_count.scoring == 0
            )
    return TugLookahead(
        movies=snapshot,
        partial=timed_out or any(value.partial for value in snapshot.values()),
        portal_available=portal_available,
    )


@router.get(
    "/{run_id}/tug/portal-candidates",
    response_model=list[DiscoveryCandidate],
)
async def tug_portal_candidates(
    run: Run = Depends(run_participant_guard),
    session: Session = Depends(get_session),
) -> list[DiscoveryCandidate]:
    rules = dict(run.rules_config or {})
    if run.game_type != TUG_OF_WAR or not _tug_portal_available(session, run, rules):
        return []
    frontier = _last_step(session, run.id)
    if frontier is None:
        return []
    excluded = {step.movie_id for step in _run_history(session, run.id)}
    movies = feasibility.movies(session)
    candidates = sorted(
        (
            movie
            for movie_id, movie in movies.items()
            if movie_id not in excluded and is_reality_eligible(movie)
        ),
        key=lambda movie: (movie.popularity or 0, movie.tmdb_id),
        reverse=True,
    )
    traversal = rules.get("tug_traversal") or "shared_cast"
    output: list[DiscoveryCandidate] = []
    deadline = time.monotonic() + 1.5
    for movie in candidates:
        if time.monotonic() >= deadline:
            break
        territory, evidence = plane_verdict(session, movie.tmdb_id, rules)
        if territory != NEUTRAL:
            continue
        if (
            _cached_tug_link_state(session, frontier.movie_id, movie.tmdb_id, traversal)
            is not False
        ):
            continue
        output.append(
            DiscoveryCandidate(
                movie_id=movie.tmdb_id,
                title=movie.title,
                poster_path=movie.poster_path,
                release_year=parse_release_year(movie.release_date),
                origin_country=movie.origin_country,
                genre_ids=movie.genre_ids or [],
                popularity=movie.popularity,
                original_language=movie.original_language,
                runtime=movie.runtime,
                tug_territory=NEUTRAL,
                tug_territory_evidence=evidence,
                tug_portal_available=True,
            )
        )
        if len(output) >= 12:
            break
    return output


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
    history = _play_history(session, run_id)
    return history[-1] if history else None


def _run_history(session: Session, run_id: str) -> list[RunStep]:
    """Every step logged so far, oldest first (the history modifiers like country_cooldown read)."""
    return list(
        session.exec(
            select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.logged_at)
        ).all()
    )


def _play_history(session: Session, run_id: str) -> list[RunStep]:
    history = _run_history(session, run_id)
    run = session.get(Run, run_id)
    engine_class = ENGINE_REGISTRY.get(run.game_type) if run else None
    if engine_class is not None and engine_class.queue_policy == "slot":
        return [step for step in history if step.status == "watched"]
    return history


# Metadata keys only the server may set: a client-supplied `collision` would be a free win.
SERVER_OWNED_METADATA = (
    "overlay_wildcard_spent",
    "overlay_skips",
    "overlay_progress",
    "alphabet_next",
    "ascending_next",
    "acting_participant_id",
    "tunnel_side",
    "collision",
    "collision_with",
    "golden_reunion",
    "character_hop",
    "near_miss_with",
    "tug_team",
    "tug_territory",
    "tug_territory_evidence",
    "tug_link",
    "tug_portal",
    "tug_portals_before",
    "seed",
    "life_lost",
    "relic_awarded",
    "rh_resources_before",
    "bounty_life_awarded",
    # Bounty Board awards and Rotten Tomatoes Split settlements: a client could mint wildcards/points.
    "completed_bounty",
    "bounty_replacement",
    "bounty_reward",
    "bounty_expired",
    "bounty_expiry_changes",
    "bounty_expiry_reasons",
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
    session: Session,
    run: Run,
    current_user: User,
    acting_participant_id: str | None,
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
    return sum(
        int(bool((step.transition_metadata or {}).get("wildcard_used")))
        + (step.transition_metadata or {}).get("overlay_wildcard_spent", 0)
        for step in steps
    )


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
    steps = _play_history(session, run.id)
    engine = engine_class(session, tmdb)
    engine.sync_run_state(run, steps)
    outcome = engine.evaluate_run_outcome(run, list(steps))
    if outcome is not None:
        run.status = outcome.status
        run.status_reason = (
            _with_bounty_stars(run, outcome.reason)
            if outcome.status == RUN_STATUS_COMPLETED
            else outcome.reason
        )
        run.completed_at = utcnow()
        session.add(run)


def _with_bounty_stars(run: Run, reason: str) -> str:
    stars = (run.rules_config or {}).get("bounty_stars", 0)
    return f"{reason} · {stars} bounty star{'s' if stars != 1 else ''}" if stars else reason


async def _enforce_run_rules(
    session: Session,
    tmdb: TMDBClient,
    run: Run,
    movie: CachedMovie,
    payload: RunStepCreate,
    user: User,
    fork_team: str | None = None,
) -> tuple[dict, dict | None]:
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
    overlay_engine = get_engine(run.game_type, session, tmdb)
    overlay_history = _play_history(session, run.id)
    checks = overlay_engine.overlay_checks(movie, rules, overlay_history)
    skipping = set(payload.skip_overlays)
    if skipping:
        if not force or payload.status != "watched":
            raise HTTPException(
                422,
                detail="Overlay skips require a watched film and explicit wildcard confirmation",
            )
        options = await _overlay_skip_options(session, overlay_engine, run)
        if not skipping <= {item["key"] for item in options if item["can_skip"]}:
            raise HTTPException(
                409, detail="An overlay can only be skipped when no reachable film satisfies it"
            )
        if any(checks.get(key) is None or checks[key].ok is not False for key in skipping):
            raise HTTPException(409, detail="Only an unmet overlay requirement can be skipped")
        budget = rules.get("wildcards_budget", 0)
        if budget != -1 and budget < len(skipping):
            raise HTTPException(409, detail="No wildcards remaining for this overlay skip")
        extra_metadata["overlay_skips"] = sorted(skipping)
        extra_metadata["overlay_wildcard_spent"] = len(skipping) if budget != -1 else 0
        rules = {
            **rules,
            "modifiers": [
                entry for entry in rules.get("modifiers", []) if entry["key"] not in skipping
            ],
        }
    for key, verdict in checks.items():
        if key not in skipping and verdict.ok is False:
            raise HTTPException(
                409,
                detail={
                    "valid": False,
                    "blocked": True,
                    "reason": verdict.reason,
                    "connections": [],
                },
            )
    if run.game_type == TUG_OF_WAR:
        tug_engine = TugOfWarEngine(session, tmdb)
        players = tug_engine.team_players(run)
        team = fork_team or (
            team_of(players, user.id)
            if rules.get("table_mode") is True
            else payload.tug_team or team_of(players, user.id)
        )
        if team not in (TEAM_A, TEAM_B) or players.get(team) is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Choose a Tug team assigned to a run participant",
            )
        extra_metadata["tug_team"] = team
        rules_version = rules.get(TUG_RULES_VERSION_KEY)
        if payload.status == "watched" and rules_version in (2, 3, 4) and all(players.values()):
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
        reason = engine.modifier_violation(earlier, movie, rules, _play_history(session, run.id))
        if reason:
            raise HTTPException(
                status_code=409,
                detail={"valid": False, "blocked": True, "reason": reason, "connections": []},
            )
    elif previous is not None:
        if (
            payload.use_tug_portal
            and _cached_tug_link_state(
                session,
                previous.movie_id,
                movie.tmdb_id,
                rules.get("tug_traversal") or "shared_cast",
            )
            is not False
        ):
            raise HTTPException(409, detail="Portal requires a cache-verified unlinked hop")
        engine = get_engine(run.game_type, session, tmdb)
        result = await engine.validate_next_step(
            previous.movie_id,
            movie.tmdb_id,
            cast_limit=rules.get("max_cast_order"),
            rules=rules,
            previous_transition=previous.transition_metadata,
            history=side_steps if tunnel else _play_history(session, run.id),
        )
        if payload.use_tug_portal:
            if not get_policy(rules.get("tug_traversal")).graph:
                raise HTTPException(409, detail="Portals are only available on graph traversals")
            if not _tug_portal_available(session, run, rules, _play_history(session, run.id)):
                raise HTTPException(
                    409, detail="A Portal is available only when no scoring reply exists"
                )
            territory, _ = plane_verdict(session, movie.tmdb_id, rules)
            if territory != NEUTRAL:
                raise HTTPException(409, detail="A Portal must land in the known neutral band")
            if result.valid:
                raise HTTPException(409, detail="A Portal is only for an unlinked hop")
            if result.blocked:
                raise HTTPException(status_code=409, detail=result.model_dump())
            portals_before = dict(rules.get("tug_portals") or {})
            run.rules_config = {
                **(run.rules_config or {}),
                "tug_portals": {**portals_before, "remaining": 0},
            }
            session.add(run)
            extra_metadata["tug_portal"] = True
            extra_metadata["tug_portals_before"] = portals_before
            extra_metadata["tug_link"] = {"kind": "portal", "unlinked": True}
            result = result.model_copy(update={"valid": True, "reason": None})
        if not result.valid and result.blocked:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=result.model_dump())
        if not result.valid:
            if skipping:
                raise HTTPException(409, detail=result.model_dump())
            if not force:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail=result.model_dump()
                )
            broke_a_rule = True
        linked_metadata = engine.link_metadata(result, payload.transition_metadata)
        if (
            skipping
            and result.connections
            and linked_metadata is None
            and not payload.transition_metadata
        ):
            linked_metadata = result.connections[0].model_dump()
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
                if linked_metadata
                and ("person_id" in linked_metadata or "actor_id" in linked_metadata)
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
    if skipping and broke_a_rule:
        raise HTTPException(409, detail="An overlay substitute must satisfy every other run rule")
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

    if skipping:
        current = run.rules_config or {}
        budget = current.get("wildcards_budget", 0)
        spent = extra_metadata["overlay_wildcard_spent"]
        if budget != -1 and budget < spent:
            raise HTTPException(
                409, detail="Not enough wildcards for both the overlay skip and other violations"
            )
        run.rules_config = {**current, "wildcards_budget": budget - spent if budget != -1 else -1}
        session.add(run)
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
        **_public_summary(session, run).model_dump(),
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
    return [_public_summary(session, run) for run in session.exec(statement).all()]


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

    submitted_rules = (
        payload.rules_config if payload.rules_config is not None else dict(DEFAULT_RULES_CONFIG)
    )
    rules_config = blind_fork.strip_server_rules(submitted_rules)
    if payload.game_type == rabbit_hole.RABBIT_HOLE and "fog" in submitted_rules:
        rules_config["fog"] = submitted_rules["fog"]
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
        if isinstance(engine, RabbitHoleEngine) and payload.seed_movie_id is not None:
            await cache_repo.get_movie(session, tmdb, payload.seed_movie_id, require_detail=True)
            engine.setup_seed_movie_id = payload.seed_movie_id
            feasibility.invalidate(session)
        try:
            rules_config = await engine.prepare_run(rules_config, current_user.id)
        except RunSetupError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        overlay_seed_history = []
        for seed_id in (payload.seed_movie_id, payload.tail_seed_movie_id):
            if seed_id is None:
                continue
            seed = await engine.validate_candidate(seed_id, rules_config)
            if not seed.valid:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Seed movie rejected: {seed.reason}",
                )
            movie = await engine._load(seed_id)
            checks = engine.overlay_checks(movie, rules_config, [])
            number_check = checks.get("number_in_title")
            if number_check and number_check.ok is False:
                raise HTTPException(422, detail=f"Seed movie rejected: {number_check.reason}")
            overlay_seed_history.append(
                RunStep(
                    run_id="",
                    movie_id=seed_id,
                    movie_title=movie.title,
                    transition_metadata={SEED_KEY: True},
                    status="watched",
                )
            )
        try:
            rules_config = engine.prepare_overlays(rules_config, overlay_seed_history)
        except RunSetupError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        if rules_config.get("career_eras"):
            problems = engine.validate_rules_config(rules_config)
            if problems:
                raise HTTPException(422, detail="; ".join(problems))

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
        engine = engine_class(session, tmdb)
        history = _run_history(session, run.id)
        engine.sync_run_state(run, history)
        if bounties.board_enabled(run.rules_config):
            rules = run.rules_config or {}
            run.rules_config = bounties.prepare_board(
                rules,
                difficulty=lambda bounty_id: engine.bounty_difficulty(
                    rules, history, bounties.BOUNTIES[bounty_id]
                ),
                feasible=lambda bounty_id: (
                    engine.bounty_feasible(
                        rules,
                        history,
                        bounties.BOUNTIES[bounty_id],
                    ).drawable
                ),
            )
        session.commit()
        session.refresh(run)
    return _to_run_detail(session, run)


@router.get("/{run_id}", response_model=RunDetail)
def get_run(session: Session = Depends(get_session), run: Run = Depends(run_participant_guard)):
    return _to_run_detail(session, run)


@router.get("/{run_id}/coach")
def get_run_coach(
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> dict[str, str | None]:
    engine = get_engine(run.game_type, session, tmdb)
    line = (
        engine.coach_line(run, _play_history(session, run.id))
        if run.status == RUN_STATUS_ACTIVE
        else None
    )
    return {"line": line}


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
            if payload.status == RUN_STATUS_COMPLETED:
                run.status_reason = _with_bounty_stars(run, run.status_reason or "Completed")
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
    if (
        "wildcards_budget" in payload.model_fields_set
        and payload.wildcards_budget != -1
        and not bounty_run
    ):
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
    update = payload.model_dump(exclude_none=True, exclude_unset=True)
    if "fog" in payload.model_fields_set:
        raise HTTPException(422, detail="Fog of War can only be chosen when creating a run")
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
    for key in ("track_length", "max_lives", "daily", "curses"):
        if key in update and update[key] != (run.rules_config or {}).get(key):
            raise HTTPException(422, detail=f"{key} can only be chosen when creating a run")
    engine_class = ENGINE_REGISTRY.get(run.game_type)
    if "career_eras" in update and run.game_type not in ("method_actor", "auteur_marathon"):
        raise HTTPException(422, detail="Career eras are only available on career marathons")
    if update.get(blind_fork.BLIND_FORK_KEY) is False:
        merged = blind_fork.with_fork(merged, None)
    if engine_class is not None:
        engine = engine_class(session, tmdb)
        problems = engine.validate_rules_config(merged)
        if update.get(blind_fork.BLIND_FORK_KEY):
            problems += _blind_fork_problems(engine_class)
        if problems:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="; ".join(problems)
            )
        if engine.bounty_ids(merged, []) is not None and (
            engine.active_modifiers(run.rules_config).get("number_in_title")
            != engine.active_modifiers(merged).get("number_in_title")
        ):
            raise HTTPException(
                422, detail="Number in title on a filtered checklist can only be chosen at creation"
            )
        try:
            merged = engine.prepare_overlays(merged, _play_history(session, run.id))
        except RunSetupError as exc:
            raise HTTPException(exc.status_code, detail=str(exc)) from exc
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


async def _settle_on_watch(
    session: Session,
    run: Run,
    step: RunStep,
    payload: RunStepCreate | MarkWatchedRequest | RunStepUpdate,
    tmdb: TMDBClient,
    omdb: OMDbClient | None,
) -> None:
    if run.game_type != RT_SPLIT:
        if payload.no_contest:
            raise HTTPException(
                422, detail="No-contest is only available on Rotten Tomatoes Split runs"
            )
        return
    if not payload.no_contest and payload.household_score is None:
        raise HTTPException(
            422, detail="Log a split film with the household's rating (1-100), or as no-contest"
        )
    metadata = dict(step.transition_metadata or {})
    for key in (
        "household_score",
        "critic_score",
        "audience_score",
        "divergence",
        "point_to",
        "split_no_contest",
    ):
        metadata.pop(key, None)
    if payload.no_contest:
        metadata["split_no_contest"] = True
    else:
        if omdb is not None:
            await cache_repo.get_movie_ratings(session, tmdb, omdb, step.movie_id)
        assert payload.household_score is not None
        engine = RottenTomatoesSplitEngine(session, tmdb)
        result = await engine.validate_candidate(step.movie_id, _run_rules(run))
        if not result.valid:
            raise HTTPException(409, detail=result.model_dump())
        metadata.update(engine.settle(step.movie_id, payload.household_score))
    step.transition_metadata = metadata


async def _award_step_bounty(
    session: Session, tmdb: TMDBClient, run: Run, step: RunStep, movie: CachedMovie
) -> None:
    rules = run.rules_config or {}
    if not bounties.board_enabled(rules):
        return
    engine = get_engine(run.game_type, session, tmdb)
    if engine.queue_policy == "slot" and step.status != "watched":
        return
    history = _play_history(session, run.id)
    bounty = await bounties.evaluate(
        session,
        tmdb,
        rules,
        movie,
        feasible=lambda quest: engine.bounty_feasible(rules, history, quest).drawable,
        context=_bounty_context(engine, rules, history),
        difficulty=lambda quest: engine.bounty_difficulty(rules, history, quest),
    )
    if bounty is None:
        return
    metadata = {
        **(step.transition_metadata or {}),
        bounties.COMPLETED_METADATA_KEY: bounty[0],
        bounties.REPLACEMENT_METADATA_KEY: bounty[1],
        "bounty_reward": engine.bounty_reward,
    }
    before_lives = lives_of(rules)[0] if run.game_type == rabbit_hole.RABBIT_HOLE else None
    run.rules_config = engine.award_bounty(rules, *bounty)
    if before_lives is not None:
        metadata["bounty_life_awarded"] = (
            run.rules_config.get(LIVES_KEY, before_lives) > before_lives
        )
    step.transition_metadata = metadata
    session.add(run)
    session.add(step)


async def _check_slot_watch(session: Session, tmdb: TMDBClient, run: Run, step: RunStep) -> None:
    engine = get_engine(run.game_type, session, tmdb)
    if engine.queue_policy != "slot" or run.game_type == RT_SPLIT:
        return
    history = _play_history(session, run.id)
    previous = history[-1] if history else None
    result = (
        await engine.validate_candidate(step.movie_id, _run_rules(run))
        if previous is None
        else await engine.validate_next_step(
            previous.movie_id,
            step.movie_id,
            rules=_run_rules(run),
            previous_transition=previous.transition_metadata,
            history=history,
        )
    )
    if not result.valid:
        raise HTTPException(409, detail=result.model_dump())


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
    if payload.status not in ("planned", "watched"):
        raise HTTPException(422, detail="Choose planned or watched")
    if (
        payload.status == "planned"
        and get_engine(run.game_type, session, tmdb).queue_policy == "none"
    ):
        raise HTTPException(422, detail="This mode does not support queueing films")
    split = run.game_type == RT_SPLIT
    if payload.no_contest and not split:
        raise HTTPException(
            422, detail="No-contest is only available on Rotten Tomatoes Split runs"
        )
    if payload.no_contest and payload.status != "watched":
        raise HTTPException(422, detail="Log a no-contest film as watched")
    if payload.use_tug_portal and (
        run.game_type != TUG_OF_WAR
        or (run.rules_config or {}).get(TUG_RULES_VERSION_KEY) != 4
        or payload.status != "watched"
    ):
        raise HTTPException(422, detail="A Tug Portal is only available for a watched F9 pull")
    if payload.use_tug_portal and _last_step(session, run.id) is None:
        raise HTTPException(409, detail="A Tug Portal cannot be used on the first film")
    rh_resources = (
        {key: run.rules_config[key] for key in rabbit_hole.RESOURCE_KEYS if key in run.rules_config}
        if run.game_type == rabbit_hole.RABBIT_HOLE and rabbit_hole.procedural(run.rules_config)
        else None
    )
    movie = await cache_repo.get_movie(session, tmdb, payload.movie_id, require_detail=True)
    settlement = RunStep(run_id=run.id, **_step_fields_from_movie(movie))
    if payload.status == "watched":
        await _settle_on_watch(session, run, settlement, payload, tmdb, omdb)
    extra_metadata, linked_metadata = await _enforce_run_rules(
        session, tmdb, run, movie, payload, actor, fork_team
    )
    if (run.rules_config or {}).get("table_mode") is True:
        extra_metadata["acting_participant_id"] = actor.id
    extra_metadata.update(settlement.transition_metadata or {})
    if run.game_type == TUG_OF_WAR and (run.rules_config or {}).get(TUG_RULES_VERSION_KEY) == 4:
        territory, evidence = plane_verdict(session, movie.tmdb_id, run.rules_config)
        extra_metadata["tug_territory"] = territory or "unknown"
        extra_metadata["tug_territory_evidence"] = evidence
        if _last_step(session, run.id) is not None and "tug_link" not in extra_metadata:
            extra_metadata["tug_link"] = {
                "kind": (run.rules_config or {}).get("tug_traversal", "shared_cast"),
                "linked": bool(linked_metadata),
            }
    transition_metadata = _without_server_keys(
        linked_metadata if linked_metadata is not None else payload.transition_metadata
    )
    if extra_metadata:
        transition_metadata = {**(transition_metadata or {}), **extra_metadata}
    if rh_resources is not None:
        transition_metadata = {**(transition_metadata or {}), "rh_resources_before": rh_resources}

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
    await _award_step_bounty(session, tmdb, run, step, movie)
    engine = get_engine(run.game_type, session, tmdb)
    if chaos.active(run.rules_config) is not None:
        # The handicap was for this step only.
        run.rules_config = chaos.clear(run.rules_config or {})
        session.add(run)
    if bounties.board_enabled(run.rules_config) and (
        engine.queue_policy != "slot" or step.status == "watched"
    ):
        _expire_bounties(session, engine, run, step)
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
async def mark_step_watched(
    step_id: str,
    payload: MarkWatchedRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    omdb: OMDbClient = Depends(get_omdb_client),
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
    _check_watched_overlays(session, tmdb, run, step)
    await _check_slot_watch(session, tmdb, run, step)
    if run.game_type == TUG_OF_WAR and (run.rules_config or {}).get(TUG_RULES_VERSION_KEY) in (
        2,
        3,
        4,
    ):
        players = TugOfWarEngine(session, tmdb).team_players(run)
        team = step_turn_team(step, players)
        if team is not None and all(players.values()):
            next_team = tally(_run_history(session, run.id), run.rules_config, players).next_team
            if team != next_team:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"It's {TugOfWarEngine(session, tmdb).team_name(run, next_team)}'s pull",
                )
    await _settle_on_watch(session, run, step, payload, tmdb, omdb)
    step.status = "watched"
    if get_engine(run.game_type, session, tmdb).queue_policy == "slot" or (
        run.game_type == TUG_OF_WAR
        and (run.rules_config or {}).get(TUG_RULES_VERSION_KEY) in (3, 4)
    ):
        step.logged_at = utcnow()
    step.watched_at = payload.watched_at or utcnow()
    if payload.user_notes is not None:
        step.user_notes = payload.user_notes
    session.add(step)
    session.flush()
    if get_engine(run.game_type, session, tmdb).queue_policy == "slot":
        movie = await cache_repo.get_movie(session, tmdb, step.movie_id, require_detail=True)
        await _award_step_bounty(session, tmdb, run, step, movie)
    if bounties.board_enabled(run.rules_config):
        _expire_bounties(session, get_engine(run.game_type, session, tmdb), run, step)
    _apply_run_outcome(session, tmdb, run)
    session.commit()
    session.refresh(step)
    return step


@router.patch("/{run_id}/steps/{step_id}", response_model=RunStepPublic)
async def update_step(
    step_id: str,
    payload: RunStepUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    omdb: OMDbClient = Depends(get_omdb_client),
):
    step = session.get(RunStep, step_id)
    if step is None or step.run_id != run.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Step not found")
    actor = _acting_user(session, run, current_user, payload.acting_participant_id)
    became_watched = payload.watched_at is not None and step.status == "planned"
    if became_watched:
        _ensure_run_open(run)
        _check_watched_overlays(session, tmdb, run, step)
        await _check_slot_watch(session, tmdb, run, step)
        if (run.rules_config or {}).get("table_mode") is True and _step_actor_id(step) != actor.id:
            raise HTTPException(403, detail="Switch to the participant who queued this film")
        if run.game_type == TUG_OF_WAR and (run.rules_config or {}).get(TUG_RULES_VERSION_KEY) in (
            2,
            3,
            4,
        ):
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
        await _settle_on_watch(session, run, step, payload, tmdb, omdb)
        if get_engine(run.game_type, session, tmdb).queue_policy == "slot" or (
            run.game_type == TUG_OF_WAR
            and (run.rules_config or {}).get(TUG_RULES_VERSION_KEY) in (3, 4)
        ):
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
    if became_watched and get_engine(run.game_type, session, tmdb).queue_policy == "slot":
        movie = await cache_repo.get_movie(session, tmdb, step.movie_id, require_detail=True)
        await _award_step_bounty(session, tmdb, run, step, movie)
    if became_watched and bounties.board_enabled(run.rules_config):
        _expire_bounties(session, get_engine(run.game_type, session, tmdb), run, step)
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
    rules = dict(run.rules_config or {})
    if metadata.get("overlay_wildcard_spent"):
        rules["wildcards_budget"] = (
            rules.get("wildcards_budget", 0) + metadata["overlay_wildcard_spent"]
        )
        run.rules_config = rules
    board = bounties.active_bounties(rules)
    for change in reversed(metadata.get("bounty_expiry_changes") or []):
        board = [b for b in board if b != change["replacement"]]
        if change["id"] not in board:
            board.insert(min(change["index"], len(board)), change["id"])
    if metadata.get("bounty_expiry_changes"):
        run.rules_config = {**rules, bounties.ACTIVE_KEY: board}
    if metadata.get(bounties.COMPLETED_METADATA_KEY):
        # Undo the earned reward and board swap.
        revoke = (
            get_engine(run.game_type, session, tmdb).revoke_bounty
            if metadata.get("bounty_reward")
            else bounties.revoke
        )
        run.rules_config = revoke(
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
        if rabbit_hole.procedural(run.rules_config) and "rh_resources_before" in metadata:
            restored = {
                key: value
                for key, value in run.rules_config.items()
                if key not in rabbit_hole.RESOURCE_KEYS
            }
            restored.update(metadata["rh_resources_before"])
            restored[LIVES_KEY] = min(restored[rabbit_hole.MAX_LIVES_KEY], restored[LIVES_KEY])
            run.rules_config = restored
        session.add(run)
    if run.game_type == TUG_OF_WAR and metadata.get("tug_portals_before") is not None:
        restored_rules = dict(run.rules_config or rules_before_revoke)
        restored_rules["tug_portals"] = metadata["tug_portals_before"]
        run.rules_config = restored_rules
        session.add(run)
    reopen = collided and run.status == RUN_STATUS_COMPLETED
    remaining = _play_history(session, run.id)
    engine_class = ENGINE_REGISTRY.get(run.game_type)
    if engine_class is not None:
        engine_class(session, tmdb).sync_run_state(run, remaining)
        if (
            run.status == RUN_STATUS_COMPLETED
            and run.status_reason
            and ("conquered in" in run.status_reason)
        ):
            candidate = Run.model_validate(run.model_dump())
            candidate.status = RUN_STATUS_ACTIVE
            if engine_class(session, tmdb).overlay_outcome(candidate, remaining) is None:
                reopen = True
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
            if (run.rules_config or {}).get(TUG_RULES_VERSION_KEY) in (2, 3, 4)
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
    slot_queue = (
        get_engine(run.game_type, session, tmdb).queue_policy == "slot" and step.status == "planned"
    )
    if not slot_queue and (last_step is None or last_step.id != step.id):
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
    movie = await engine._load(payload.movie_id)
    failed = [
        key
        for key, verdict in engine.overlay_checks(
            movie, rules, _play_history(session, run.id)
        ).items()
        if verdict.ok is False
    ]
    if failed:
        connections = []
        options = await _overlay_skip_options(session, engine, run)
        skippable = [item["key"] for item in options if item["can_skip"] and item["key"] in failed]
        if set(skippable) == set(failed):
            skip_rules = {
                **rules,
                "modifiers": [
                    entry for entry in rules.get("modifiers", []) if entry["key"] not in skippable
                ],
            }
            other = (
                await engine.validate_candidate(payload.movie_id, skip_rules)
                if previous is None
                else await engine.validate_next_step(
                    previous.movie_id,
                    payload.movie_id,
                    cast_limit=rules.get("max_cast_order"),
                    rules=skip_rules,
                    previous_transition=previous.transition_metadata,
                    history=_play_history(session, run.id),
                )
            )
            if not other.valid:
                return other.model_copy(update={"blocked": True})
            connections = other.connections
            if movie.runtime is not None and movie.runtime < rules.get("min_runtime", 0):
                return ValidationResult(
                    valid=False,
                    blocked=True,
                    reason="Overlay substitute is under the runtime minimum",
                )
        return ValidationResult(
            valid=False,
            blocked=set(skippable) != set(failed),
            reason="Overlay requirement does not match this title",
            connections=connections,
            overlay_skippable=skippable,
        )
    if previous is None:
        return await engine.validate_candidate(payload.movie_id, rules)
    result = await engine.validate_next_step(
        previous.movie_id,
        payload.movie_id,
        cast_limit=rules.get("max_cast_order"),
        rules=rules,
        previous_transition=previous.transition_metadata,
        history=side_steps if side_steps is not None else _play_history(session, run.id),
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
    if constraint and constraint.overlay_progress:
        constraint = constraint.model_copy(
            update={
                "overlay_progress": await _overlay_skip_options(session, engine, run),
            }
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
    if (
        isinstance(engine, RabbitHoleEngine)
        and constraint is not None
        and constraint.rabbit_hole is not None
    ):
        public = engine.public_rules(
            {**rules, "rabbit_hole": constraint.rabbit_hole.model_dump()},
            run,
            depth=len(history),
        )
        constraint = constraint.model_copy(
            update={
                "rabbit_hole": rabbit_hole.RabbitHoleState.model_validate(public["rabbit_hole"])
            }
        )
    return constraint


@router.post("/{run_id}/rabbit-hole/reroll", response_model=RunDetail)
async def reroll_rabbit_hole_tier(
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> RunDetail:
    """Spend a free token, or one life, to replace the requirement for this depth."""
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
    tokens = rules.get("reroll_tokens", 0) if rabbit_hole.procedural(rules) else 0
    if lives < 2 and tokens < 1:
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

    current_tier = tier_for_depth(depth, rules)
    engine = get_engine(run.game_type, session, tmdb)
    if rabbit_hole.procedural(rules):
        _ensure_no_pending_fork(run)
    ids = await _reachable_pool(session, engine, run, off_tier=True)
    if rabbit_hole.procedural(rules):
        feasibility.invalidate(session)
        alternatives = [
            test
            for test in (
                rabbit_hole.facet_options(session)
                if rules.get(rabbit_hole.RH_VERSION_KEY) == 3
                else rabbit_hole.tier_options()
            )
            if current_tier.predicate is not None
            and (test.id, test.params) != (current_tier.predicate.id, current_tier.predicate.params)
            and (
                feasibility.pass_rate(
                    session,
                    rabbit_hole.TierPredicates((test, *current_tier.curses)),
                    ids,
                )
                or 0
            )
            >= 0.03
            and (
                rules.get(rabbit_hole.RH_VERSION_KEY) != 3
                or all(
                    rabbit_hole.TIER_PASS_BANDS[min(current_tier.number - 2, 3)][0]
                    <= (rate or 0)
                    <= rabbit_hole.TIER_PASS_BANDS[min(current_tier.number - 2, 3)][1]
                    for rate in (
                        feasibility.pass_rate(session, test, ids),
                        feasibility.cache_pass_rate(session, test),
                    )
                )
            )
        ]
        if not alternatives:
            raise HTTPException(
                409, detail="No feasible reachable alternative tier; no resource was spent"
            )
        replacement = random.Random(f"{rules[rabbit_hole.RH_SEED_KEY]}:{depth}").choice(
            sorted(
                alternatives, key=lambda test: feasibility.measured_difficulty(session, test, ids)
            )
        )
        rules[TIER_OVERRIDE_KEY] = {
            "depth": depth,
            "predicate": rabbit_hole.predicate_data(replacement),
        }
        if tokens:
            rules["reroll_tokens"] = tokens - 1
        else:
            rules[LIVES_KEY] = lives - 1
        run.rules_config = rules
        session.add(run)
        session.commit()
        session.refresh(run)
        return _to_run_detail(session, run)
    choices = [
        tier.number
        for tier in rabbit_hole.TIERS
        if tier.number > 1
        and tier.number != current_tier.number
        and tier.predicate is not None
        and feasibility.check(session, tier.predicate, ids).drawable
    ]
    if not choices:
        raise HTTPException(409, detail="No fair reachable alternative tier; no life was spent")
    rules[TIER_OVERRIDE_KEY] = {"depth": depth, "tier": random.choice(choices)}
    rules[LIVES_KEY] = lives - 1
    run.rules_config = rules
    session.add(run)
    session.commit()
    session.refresh(run)
    return _to_run_detail(session, run)


@router.post("/{run_id}/rabbit-hole/periscope", response_model=RunDetail)
def reveal_rabbit_hole_tier(
    payload: RabbitHolePeriscopeRequest,
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
) -> RunDetail:
    if run.game_type != rabbit_hole.RABBIT_HOLE:
        raise HTTPException(400, detail="The Periscope is only available in Rabbit Hole runs")
    _ensure_run_open(run)
    _ensure_no_pending_fork(run)
    rules = dict(_run_rules(run))
    if rules.get("fog") != "fog" or not rabbit_hole.procedural(rules):
        raise HTTPException(409, detail="This run has no Periscope")
    depth = len(_run_history(session, run.id))
    boundary = next(
        (
            entry
            for entry in rules.get("tier_deck", [])
            if entry.get("start_depth") == payload.depth
        ),
        None,
    )
    if boundary is None or payload.depth <= depth + 1:
        raise HTTPException(409, detail="Choose a future unrevealed tier boundary")
    revealed = set(rules.get("revealed_depths") or [])
    if payload.depth in revealed:
        raise HTTPException(409, detail="That tier has already been revealed")
    charges = rules.get("periscope_charges", 0)
    if charges < 1:
        raise HTTPException(409, detail="No Periscope charges remaining")
    rules["periscope_charges"] = charges - 1
    rules["revealed_depths"] = sorted([*revealed, payload.depth])
    run.rules_config = rules
    session.add(run)
    session.commit()
    session.refresh(run)
    return _to_run_detail(session, run)


@router.post("/{run_id}/rabbit-hole/skip-curse", response_model=RunDetail)
def skip_rabbit_hole_curse(
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
) -> RunDetail:
    rules = dict(_run_rules(run))
    if run.game_type != rabbit_hole.RABBIT_HOLE or not rabbit_hole.procedural(rules):
        raise HTTPException(
            400, detail="Skip-curse relics are only available on procedural Rabbit Hole runs"
        )
    _ensure_run_open(run)
    _ensure_no_pending_fork(run)
    depth = len(_run_history(session, run.id))
    if rules.get("curse_skip") == depth:
        raise HTTPException(409, detail="A curse is already skipped at this depth")
    if not tier_for_depth(depth, rules).curses:
        raise HTTPException(409, detail="No active curse to skip; no relic was spent")
    relics = rules.get("relics", {})
    if relics.get("skip_curse", 0) < 1:
        raise HTTPException(409, detail="No skip-curse relics remaining")
    rules["relics"] = {**relics, "skip_curse": relics["skip_curse"] - 1}
    rules["curse_skip"] = depth
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
        current = next(
            (step for step in reversed(history) if step.movie_id == frontier_movie_id), None
        )
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
        raise HTTPException(
            status_code=504, detail="Search took too long. Try again or narrow the filters."
        ) from exc


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
            current.movie_id,
            suggestion.movie_id,
            cast_limit=_run_rules(run).get("max_cast_order"),
            rules=_run_rules(run),
            previous_transition=current.transition_metadata,
            history=history,
        )
        if validation.valid:
            suggestion.connections = [
                DiscoveryConnection(
                    kind=connection.kind,
                    actor_id=connection.actor_id,
                    actor_name=connection.actor_name,
                    profile_path=connection.profile_path,
                    character_in_frontier=connection.character_in_from,
                    character_in_candidate=connection.character_in_to,
                    role_in_frontier=connection.role_in_from,
                    role_in_candidate=connection.role_in_to,
                )
                for connection in validation.connections
            ]
            row = session.get(CachedMovie, suggestion.movie_id)
            if row is not None:
                suggestion.origin_country = row.origin_country
                suggestion.genre_ids = row.genre_ids or []
                suggestion.runtime = row.runtime
                suggestion.original_language = row.original_language
                suggestion.popularity = row.popularity
                suggestion.narrative_year = row.narrative_year
                suggestion.narrative_era_label = row.narrative_era_label
            checked.append(suggestion)
    suggestions = checked
    if chaser or sort_by:
        by_id = {s.movie_id: s for s in suggestions}
        active_vibe = engine.active_modifiers(_run_rules(run)).get("vibe_control")
        setpoint = vibe_controller.SETPOINTS.get(
            active_vibe.get("comfort", "balanced") if isinstance(active_vibe, dict) else "balanced",
            vibe_controller.SETPOINTS["balanced"],
        )
        if chaser:
            await pool_options.hydrate_candidate_runtimes(
                session,
                engine.tmdb,
                [suggestion.movie_id for suggestion in suggestions],
                pool_options.HYDRATE_BUDGET,
            )
            for suggestion in suggestions:
                row = session.get(CachedMovie, suggestion.movie_id)
                if row is not None:
                    suggestion.runtime = row.runtime
                    suggestion.original_language = row.original_language
        try:
            loads = await vibe_controller.candidate_loads(
                session, [suggestion.movie_id for suggestion in suggestions]
            )
        except embeddings.EmbeddingUnavailable:
            loads = {}
        for suggestion in suggestions:
            suggestion.vibe_load = loads.get(suggestion.movie_id)
        kept = await pool_options.shape_pool(
            session,
            engine.tmdb,
            [s.movie_id for s in suggestions],
            chaser=chaser,
            sort_by=sort_by,
            load_by_id=loads,
            runtime_medians=vibe_controller.cached_runtime_medians(session),
            setpoint=setpoint,
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
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> RunDetail:
    """ "✨ Roll Custom Bounty": the AI writes a new bounty (with a programmatic rule) that takes
    the oldest slot on the Bounty Board."""
    _ensure_run_open(run)
    try:
        engine = get_engine(run.game_type, session, tmdb)
        rules = run.rules_config or {}
        history = _play_history(session, run.id)
        run.rules_config = await bounties.roll_custom(
            session,
            rules,
            feasible=lambda quest: engine.bounty_feasible(rules, history, quest).drawable,
            context=_bounty_context(engine, rules, history),
            difficulty=lambda quest: engine.bounty_difficulty(rules, history, quest),
        )
    except bounties.BountyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    session.add(run)
    session.commit()
    session.refresh(run)
    return _to_run_detail(session, run)


def _bounty_context(engine: BaseChallengeEngine, rules: dict, history: list[RunStep]) -> str:
    ids = engine.bounty_ids(rules, history)
    remaining = (
        len(set(ids) - {step.movie_id for step in history}) if ids is not None else "open pool"
    )
    tier = (
        tier_for_depth(len(history), rules).rule if isinstance(engine, RabbitHoleEngine) else "none"
    )
    expedition = rules.get("expedition") or {}
    universe = engine.bounty_universe(rules, history)
    return (
        f"mode={engine.game_type}; bounds={engine.bounty_bounds(rules, history)}; "
        f"tier={tier}; depth={len(history)}; remaining checklist={remaining}; "
        f"slice country={expedition.get('country')}; decade={expedition.get('decade')}; "
        f"reward={engine.bounty_reward}. Avoid impossible or universally satisfied quests. "
        f"Best-covered facets for this run: {feasibility.covered_facets(engine.session, universe)}"
    )


def _expire_bounties(
    session: Session, engine: BaseChallengeEngine, run: Run, step: RunStep
) -> None:
    rules = run.rules_config or {}
    if not bounties.board_enabled(rules):
        return
    history = _play_history(session, run.id)
    feasibility.invalidate(session)
    active = bounties.active_bounties(rules)
    changes = []
    reasons = {}
    for bounty_id in list(active):
        quest = bounties.resolve(rules, bounty_id)
        if quest is None:
            continue
        result = engine.bounty_feasible(rules, history, quest)
        if result.ok:
            continue
        index = active.index(bounty_id)
        active.remove(bounty_id)
        replacement = bounties.draw_replacement(
            [*active, bounty_id],
            rules.get(bounties.COMPLETED_KEY) or [],
            random.Random(),
            difficulty=lambda candidate: engine.bounty_difficulty(
                rules, history, bounties.BOUNTIES[candidate]
            ),
            feasible=lambda candidate: (
                engine.bounty_feasible(
                    rules,
                    history,
                    bounties.BOUNTIES[candidate],
                ).drawable
            ),
        )
        if replacement:
            active.insert(index, replacement)
        changes.append({"id": bounty_id, "replacement": replacement, "index": index})
        reasons[bounty_id] = result.reason
    if changes:
        run.rules_config = {**rules, bounties.ACTIVE_KEY: active}
        metadata = step.transition_metadata or {}
        prior_reasons = metadata.get("bounty_expiry_reasons") or {}
        step.transition_metadata = {
            **(step.transition_metadata or {}),
            "bounty_expired": list(
                dict.fromkeys([*(metadata.get("bounty_expired") or []), *reasons])
            ),
            "bounty_expiry_reasons": {**prior_reasons, **reasons},
            "bounty_expiry_changes": [*(metadata.get("bounty_expiry_changes") or []), *changes],
        }
        session.add(run)
        session.add(step)


@router.post("/{run_id}/bounties/{bounty_id}/discard", response_model=RunDetail)
def discard_bounty(
    bounty_id: str,
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> RunDetail:
    _ensure_run_open(run)
    rules = run.rules_config or {}
    active = bounties.active_bounties(rules)
    if not bounties.board_enabled(rules) or bounty_id not in active:
        raise HTTPException(409, detail="That bounty is not active on this board")
    if rules.get("bounty_discards_left", 1) <= 0:
        raise HTTPException(409, detail="The free discard has already been used")
    engine = get_engine(run.game_type, session, tmdb)
    history = _play_history(session, run.id)
    replacement = bounties.draw_replacement(
        active,
        rules.get(bounties.COMPLETED_KEY) or [],
        random.Random(),
        difficulty=lambda candidate: engine.bounty_difficulty(
            rules, history, bounties.BOUNTIES[candidate]
        ),
        feasible=lambda candidate: (
            engine.bounty_feasible(rules, history, bounties.BOUNTIES[candidate]).drawable
        ),
    )
    board = [replacement if item == bounty_id else item for item in active]
    run.rules_config = {
        **rules,
        bounties.ACTIVE_KEY: [item for item in board if item],
        "bounty_discards_left": 0,
    }
    session.add(run)
    session.commit()
    session.refresh(run)
    return _to_run_detail(session, run)


async def _reachable_pool(
    session: Session,
    engine: BaseChallengeEngine,
    run: Run,
    *,
    off_tier: bool = False,
    include_overlay_failures: bool = False,
) -> list[int]:
    history = _play_history(session, run.id)
    rules = _run_rules(run)
    if not history:
        seeds = await engine.seed_candidates(rules)
        return [
            row.tmdb_id
            for row in session.exec(select(CachedMovie)).all()
            if (seeds is None or row.tmdb_id in seeds)
            and is_reality_eligible(row)
            and (row.runtime is None or row.runtime >= rules.get("min_runtime", 0))
            and (
                include_overlay_failures
                or all(
                    verdict.ok is not False
                    for verdict in engine.overlay_checks(row, rules, history).values()
                )
            )
        ]
    tail = history[-1]
    candidates = await engine.discover_with_modifiers(
        frontier_movie_id=tail.movie_id,
        mode="or",
        cast_limit=rules.get("max_cast_order"),
        rules=rules,
        previous_transition=tail.transition_metadata,
        history=history,
        **({"include_off_tier": off_tier} if isinstance(engine, RabbitHoleEngine) else {}),
    )
    await engine._hydrate_pool(
        candidates,
        rules,
        needs=frozenset({"runtime", "original_language", "vote_average"}),
    )
    used = {step.movie_id for step in history}
    return [
        candidate.movie_id
        for candidate in candidates
        if (rules.get("allow_repeats") == "allowed" or candidate.movie_id not in used)
        and (row := session.get(CachedMovie, candidate.movie_id)) is not None
        and is_reality_eligible(row)
        and (row.runtime is None or row.runtime >= rules.get("min_runtime", 0))
        and (
            include_overlay_failures or all(ok is not False for ok in candidate.overlay_ok.values())
        )
    ]


async def _overlay_skip_options(
    session: Session, engine: BaseChallengeEngine, run: Run
) -> list[dict]:
    from app.engines.modifier_registry import contexts, film_values

    history = _play_history(session, run.id)
    rules = _run_rules(run)
    specs = [
        (spec, ctx)
        for spec, ctx in contexts(engine.active_modifiers(rules), history)
        if getattr(spec, "overlay", False)
    ]
    if not specs:
        return []
    if "discover_candidates" in engine.capabilities:
        ids = await _reachable_pool(session, engine, run, include_overlay_failures=True)
        exact = None
    else:
        exact = engine.bounty_ids(rules, history)
        ids = exact if exact is not None else list(session.exec(select(CachedMovie.tmdb_id)).all())
    used = {step.movie_id for step in history}
    bounds = engine.bounty_bounds(rules, history)
    checklist = {
        film["movie_id"]: film
        for film in (rules.get("filmography") or (rules.get("expedition") or {}).get("films") or [])
    }
    rows = []
    unknown = False
    for movie_id in ids:
        if movie_id in used and rules.get("allow_repeats") != "allowed":
            continue
        row = session.get(CachedMovie, movie_id)
        if row is None:
            if exact is not None and movie_id in checklist:
                rows.append(CachedMovie(tmdb_id=movie_id, title=checklist[movie_id]["title"]))
            else:
                unknown = True
        elif is_reality_eligible(row) and feasibility.within_movie(row, bounds):
            rows.append(row)
    budget = rules.get("wildcards_budget", 0)
    result = []
    for spec, ctx in specs:
        progress = spec.progress(ctx)
        assert progress is not None
        coverage = spec.coverage(ctx)
        cached = [row.tmdb_id for row in rows if session.get(CachedMovie, row.tmdb_id) is not None]
        available = (
            unknown
            or bool(cached and feasibility.check(session, coverage, cached, exact=False).ok)
            or any(
                coverage.query.evaluate(film_values(row)) is not False
                for row in rows
                if row.tmdb_id not in cached
            )
        )
        result.append(
            {
                **progress,
                "available": available,
                "can_skip": spec.scope == "sequence"
                and bool(rows)
                and not available
                and (budget == -1 or budget > 0)
                and run.status == RUN_STATUS_ACTIVE,
            }
        )
    return result


def _check_watched_overlays(session: Session, tmdb: TMDBClient, run: Run, step: RunStep) -> None:
    from app.engines.modifier_registry import registry

    if step.status == "watched":
        return
    overlays = {key for key, spec in registry().items() if getattr(spec, "overlay", False)}
    sequences = {key for key in overlays if registry()[key].scope == "sequence"}
    if not any(entry.get("key") in overlays for entry in _run_rules(run).get("modifiers", [])):
        return
    engine = get_engine(run.game_type, session, tmdb)
    history = [other for other in _play_history(session, run.id) if other.id != step.id]
    movie = session.get(CachedMovie, step.movie_id)
    if movie is None:
        raise HTTPException(
            409, detail="Film details are missing; reload the film before marking watched"
        )
    skips = (step.transition_metadata or {}).get("overlay_skips") or []
    for key, verdict in engine.overlay_checks(movie, _run_rules(run), history).items():
        if key not in skips and verdict.ok is False:
            raise HTTPException(409, detail=verdict.reason)
    if step.status == "planned" and any(
        entry.get("key") in sequences for entry in _run_rules(run).get("modifiers", [])
    ):
        step.logged_at = utcnow()


@router.post("/{run_id}/chaos", response_model=RunDetail)
async def roll_chaos(
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> RunDetail:
    """Rolls one random handicap for the next film only: `rules_config["active_chaos"]`. It
    expires when a step is logged (or is cancelled with DELETE)."""
    _ensure_chaos_allowed(run)
    if chaos.active(run.rules_config) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="A chaos handicap is already active"
        )
    engine = get_engine(run.game_type, session, tmdb)
    ids = await _reachable_pool(session, engine, run)
    feasible_ids = [
        handicap.id
        for handicap in chaos.HANDICAPS.values()
        if feasibility.check(session, handicap.predicate, ids).drawable
        and not feasibility.contradicts(
            handicap.predicate,
            engine.bounty_bounds(_run_rules(run), _play_history(session, run.id)),
        )
    ]
    feasible_ids.sort(
        key=lambda key: feasibility.measured_difficulty(
            session, chaos.HANDICAPS[key].predicate, ids
        )
    )
    if not feasible_ids:
        raise HTTPException(
            409, detail="No nontrivial feasible handicap fits the current pool; no roll was applied"
        )
    rolled = chaos.roll(feasible=feasible_ids)
    rolled["skipped"] = [h.label for h in chaos.HANDICAPS.values() if h.id not in feasible_ids]
    run.rules_config = {**_run_rules(run), chaos.ACTIVE_KEY: rolled}
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


@router.get("/{run_id}/discover", response_model=list[DiscoveryCandidate] | DiscoveryEnvelope)
async def discover_next_movies(
    frontier_movie_id: int = Query(...),
    mode: str = Query(default="or", pattern="^(or|and)$"),
    chaser: bool = Query(
        default=False, description="Only palate cleansers: <= 95 min, Comedy/Animation"
    ),
    include_off_tier: bool = Query(
        default=False, description="Rabbit Hole only: include linked films that cost a life"
    ),
    envelope: bool = Query(default=False),
    wider: bool = Query(default=False),
    sort_by: str | None = Query(
        default=None,
        pattern="^underdog$",
        description="underdog = least popular first (popularity >= 1.0)",
    ),
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> list[DiscoveryCandidate] | DiscoveryEnvelope:
    """Unified "Pick Next" pool: every top-billed cast member's filmography,
    pooled into one set of candidates (Phase 13). Movies already logged in
    this run are NOT excluded from the pool - they're flagged
    `already_in_run` instead, so the frontend can show/disable them rather
    than silently hiding them.
    """
    engine = get_engine(run.game_type, session, tmdb)
    rules = _run_rules(run)
    if include_off_tier and not isinstance(engine, RabbitHoleEngine):
        raise HTTPException(
            status_code=422, detail="Off-tier discovery is only supported by Rabbit Hole."
        )
    previous = _last_step(session, run.id)
    if engine.queue_policy == "slot":
        if previous is None:
            raise HTTPException(422, detail="Discovery needs a watched frontier film")
        frontier_movie_id = previous.movie_id
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
            history=_play_history(session, run.id),
            wider=wider,
            **(
                {"include_off_tier": include_off_tier}
                if isinstance(engine, RabbitHoleEngine)
                else {}
            ),
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
    for candidate in candidates:
        candidate.already_in_run = candidate.movie_id in logged_movie_ids
        candidate.existing_step_number = step_number_by_movie_id.get(candidate.movie_id)
        row = session.get(CachedMovie, candidate.movie_id)
        candidate.runtime = row.runtime if row is not None else None
        candidate.original_language = row.original_language if row is not None else None
        candidate.rating = rating_of(session, row) if row is not None else None
    history = _play_history(session, run.id)
    active_vibe = engine.active_modifiers(rules).get("vibe_control")
    setpoint = vibe_controller.SETPOINTS.get(
        active_vibe.get("comfort", "balanced") if isinstance(active_vibe, dict) else "balanced",
        vibe_controller.SETPOINTS["balanced"],
    )
    loads: dict[int, float] = {}
    if active_vibe is not None or chaser:
        if chaser:
            await pool_options.hydrate_candidate_runtimes(
                session,
                tmdb,
                [candidate.movie_id for candidate in candidates],
                engine._hydration_left
                if engine._hydration_left is not None
                else pool_options.HYDRATE_BUDGET,
                engine._hydration_deadline or None,
            )
            for candidate in candidates:
                row = session.get(CachedMovie, candidate.movie_id)
                candidate.runtime = row.runtime if row is not None else None
                candidate.original_language = row.original_language if row is not None else None
        tracked_ids = [step.movie_id for step in history if step.status == "watched"]
        if active_vibe is not None or chaser:
            tracked_ids.extend(candidate.movie_id for candidate in candidates)
        if tracked_ids:
            try:
                loads = await vibe_controller.candidate_loads(session, tracked_ids)
            except embeddings.EmbeddingUnavailable:
                loads = {}
        if active_vibe is not None or chaser:
            for candidate in candidates:
                candidate.vibe_load = loads.get(candidate.movie_id)
    if active_vibe is not None or chaser:
        state: dict = {"state": "steady", "integral": 0.0}
        for step in history:
            if step.status == "watched":
                state = vibe_controller.update_controller(state, loads.get(step.movie_id), setpoint)
        recent_loads = [
            loads[step.movie_id]
            for step in history[-5:]
            if step.status == "watched" and step.movie_id in loads
        ]
        state["rolling_load"] = sum(recent_loads) / len(recent_loads) if recent_loads else None
        state["chaser_recommended"] = pool_options.needs_chaser(recent_loads, setpoint)
        server_rules = copy.deepcopy(run.rules_config or {})
        server_rules["vibe_state"] = state
        if server_rules != run.rules_config:
            run.rules_config = server_rules
            session.add(run)
            session.commit()
            session.refresh(run)
    if chaser or sort_by:
        by_id = {candidate.movie_id: candidate for candidate in candidates}
        kept = await pool_options.shape_pool(
            session,
            tmdb,
            [candidate.movie_id for candidate in candidates],
            chaser=chaser,
            sort_by=sort_by,
            load_by_id=loads,
            runtime_medians=vibe_controller.cached_runtime_medians(session),
            setpoint=setpoint,
            hydrate_budget=engine._hydration_left or 0,
            deadline=engine._hydration_deadline,
        )
        candidates = [by_id[movie_id] for movie_id in kept]
        for candidate in candidates:
            row = session.get(CachedMovie, candidate.movie_id)
            candidate.runtime = row.runtime if row is not None else None
            candidate.original_language = row.original_language if row is not None else None
            candidate.rating = rating_of(session, row) if row is not None else None
    if active_vibe is not None:
        state = (run.rules_config or {}).get("vibe_state", {})
        if active_vibe.get("mode", "soft") == "strict" and state.get("state") == "fatigued":
            candidates = [
                candidate
                for candidate in candidates
                if candidate.vibe_load is None or candidate.vibe_load <= setpoint
            ]
        elif active_vibe.get("mode", "soft") == "soft":
            candidates.sort(
                key=lambda candidate: (
                    candidate.vibe_load is None,
                    candidate.vibe_load if candidate.vibe_load is not None else 0.0,
                )
            )
    candidates = engine.annotate_candidates(candidates, rules, _play_history(session, run.id))
    engine.discovery_diagnostics.after_filters = len(candidates)
    if not candidates and engine.discovery_diagnostics.after_modifiers:
        engine.discovery_diagnostics.reason = (
            "The discovery filters hid every film. Turn off the filters."
        )
    return (
        DiscoveryEnvelope(candidates=candidates, diagnostics=engine.discovery_diagnostics)
        if envelope
        else candidates
    )


class RunPrepareRequest(BaseModel):
    treatment: Literal["details", "people", "ratings", "embeddings", "facets", "fix_all"] = (
        "fix_all"
    )


@router.post("/{run_id}/prepare", response_model=TaskOut)
async def prepare_run_pool(
    background_tasks: BackgroundTasks,
    payload: RunPrepareRequest | None = None,
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
    user: User = Depends(get_current_user),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> TaskOut:
    from app.services import data_spa

    payload = payload or RunPrepareRequest()
    frontier = _last_step(session, run.id)
    if frontier is None:
        raise HTTPException(422, detail="Log or queue a frontier film before preparing this run.")
    engine = get_engine(run.game_type, session, tmdb)
    try:
        candidates = await engine.discover_with_modifiers(
            frontier.movie_id,
            cast_limit=_run_rules(run).get("max_cast_order"),
            rules=_run_rules(run),
            previous_transition=frontier.transition_metadata,
            history=_play_history(session, run.id),
        )
    except NotImplementedError:
        raise HTTPException(422, detail="This mode has no discovery pool to prepare.") from None
    ids = list(
        dict.fromkeys([frontier.movie_id, *(candidate.movie_id for candidate in candidates)])
    )[:200]
    task = data_spa.submit(
        background_tasks,
        session,
        payload.treatment,
        user.id,
        movie_ids=ids,
        run_id=run.id,
        batch_cap=200,
    )
    return TaskOut.from_model(task)


@router.post("/{run_id}/verify-candidates", response_model=list[DiscoveryCandidate])
async def verify_candidates(
    payload: VerifyCandidatesRequest,
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> list[DiscoveryCandidate]:
    if run.game_type != rabbit_hole.RABBIT_HOLE:
        raise HTTPException(422, detail="Candidate verification is only available in Rabbit Hole.")
    _ensure_no_pending_fork(run)
    frontier = _last_step(session, run.id)
    if frontier is None:
        raise HTTPException(422, detail="Log or queue a frontier film before verifying candidates.")
    engine = get_engine(run.game_type, session, tmdb)
    rules = _run_rules(run)
    try:
        pool = await engine.discover_with_modifiers(
            frontier.movie_id,
            cast_limit=rules.get("max_cast_order"),
            rules=rules,
            previous_transition=frontier.transition_metadata,
            history=_play_history(session, run.id),
        )
    except NotImplementedError:
        raise HTTPException(422, detail="This mode has no candidate pool to verify.") from None
    requested = set(payload.movie_ids)
    visible = [candidate for candidate in pool if candidate.movie_id in requested]
    rows = await engine._hydrate_pool(visible, rules)
    for candidate in visible:
        row = rows.get(candidate.movie_id)
        if row is not None:
            candidate.runtime = row.runtime
            candidate.original_language = row.original_language
            candidate.rating = rating_of(session, row)
    return engine.annotate_candidates(visible, rules, _play_history(session, run.id))
