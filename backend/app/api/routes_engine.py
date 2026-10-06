import json
import time
from dataclasses import replace
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.deps import get_current_user, get_tmdb_client, run_participant_guard
from app.config import get_settings
from app.db import get_session
from app.engines import chaos, modifiers
from app.engines.registry import ENGINE_REGISTRY, get_engine
from app.engines.rulebook import GLOSSARY, RuleSection, render
from app.engines.trackers import RouletteEngine, SpinFilters
from app.models.curated import CuratedList
from app.models.run import DEFAULT_RULES_CONFIG, Run, RunParticipant, RunStep
from app.models.user import User
from app.schemas.engine import (
    LlmStatus,
    PathTagsResult,
    PitchRequest,
    PitchResult,
    RouletteMovie,
    RouletteSpinResult,
    SwapNodeResult,
    TeaserRequest,
    TeaserResult,
    ValidationResult,
)
from app.services import (
    blind_fork,
    bounties,
    bridge_paths,
    cache_repo,
    daily_puzzle,
    llm,
    settings_repo,
    veto,
)
from app.services.tmdb import TMDBClient, TMDBError
from app.services.tmdb_backoff import DeadlineReached
from app.utils.dates import parse_release_year

router = APIRouter(tags=["engine"])

# The blocking endpoint only ever attempts a shallow, cheap search; anything
# deeper is expected to go through the SSE stream instead.
FAST_MAX_DEPTH = 2
FAST_CALL_BUDGET = 20
FAST_MAX_DURATION_SECONDS = 15
# "Search Deeper" can ask for routes this many movie-hops long, at most.
MAX_EXCLUDED_MOVIES = 200
DEEP_SEARCH_MAX_HOPS = 8
SWAP_MAX_DURATION_SECONDS = 15
TAGS_MAX_MOVIES = 20


class EngineMeta(BaseModel):
    game_type: str
    display_name: str
    description: str
    capabilities: list[str]
    requires: list[str]
    seed_policy: Literal["none", "free", "derived", "pair"]
    unavailable_reason: str | None = None
    tagline: str
    tags: list[str]
    rulebook: RuleSection
    glossary: dict[str, str]


class RulebookOverlay(BaseModel):
    key: str
    title: str
    rulebook: RuleSection


class RunRulebook(BaseModel):
    game_type: str
    display_name: str
    rulebook: RuleSection
    overlays: list[RulebookOverlay]
    glossary: dict[str, str]
    settings: dict[str, str]


class ValidateRequest(BaseModel):
    game_type: str
    from_movie_id: int
    to_movie_id: int


class BridgeRequest(BaseModel):
    game_type: str = "cinechain"
    from_movie_id: int
    to_movie_id: int


@router.get("/engines", response_model=list[EngineMeta])
def list_engines(
    session: Session = Depends(get_session),
    _current_user: User = Depends(get_current_user),
) -> list[EngineMeta]:
    base = get_settings()
    overrides = settings_repo.get_overrides(session)
    return [
        EngineMeta(
            game_type=cls.game_type,
            display_name=cls.display_name,
            description=cls.description,
            capabilities=cls.capabilities,
            requires=cls.requires,
            seed_policy=cls.seed_policy,
            tagline=cls.tagline,
            tags=cls.tags,
            rulebook=render(cls.rulebook, cls.rulebook_values(DEFAULT_RULES_CONFIG)),
            glossary={key: GLOSSARY[key] for key in cls.rulebook.glossary},
            unavailable_reason=(
                "Requires OMDb integration. Ask an admin to configure it in Settings → Integrations."
                if "omdb" in cls.requires
                and not (overrides.get("omdb_api_key") or base.omdb_api_key)
                else (
                    "Requires an enabled LLM integration. Ask an admin to configure it in Settings → Integrations."
                    if "llm" in cls.requires
                    and (overrides.get("llm_provider") or base.llm_provider) == "off"
                    else None
                )
            ),
        )
        for cls in ENGINE_REGISTRY.values()
    ]


@router.get("/runs/{run_id}/rulebook", response_model=RunRulebook)
def run_rulebook(
    run: Run = Depends(run_participant_guard),
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> RunRulebook:
    engine = get_engine(run.game_type, session, tmdb)
    rules = run.rules_config or {}
    render_rules = rules
    if run.game_type == "tug_of_war" and rules.get("tug_rules_version") != 2:
        render_rules = {**rules, "tug_rules_version": 1}
    values = engine.rulebook_values(render_rules)
    if run.engine_version <= 1:
        values.update({
            "win_goal": "Legacy run: complete it manually when you are done.",
            "fail_goal": "Legacy run: configured automatic fail conditions are not enforced.",
        })
    active = engine.active_modifiers(rules)
    overlays: list[RulebookOverlay] = []

    def add(key: str, title: str, section: RuleSection, extra: dict | None = None) -> None:
        overlays.append(RulebookOverlay(
            key=key, title=title, rulebook=render(section, {**values, **(extra or {})}),
        ))

    if rules.get(bounties.BOUNTY_BOARD_KEY) and engine.supports_bounty_board:
        add("bounty_board", "Bounty Board", bounties.RULEBOOK, {
            "bounty_reward": "one life, capped at your maximum" if engine.uses_lives else "one wildcard",
        })
    handicap = chaos.active(rules)
    if handicap:
        add("chaos", "Chaos handicap", chaos.RULEBOOK, {"chaos_label": handicap.label})
    for key, value in active.items():
        add(key, key.replace("_", " ").title(), modifiers.RULEBOOK[key], {
            key: value, "chrono_word": "before" if value == "descent" else "after",
            "runtime_word": "shorter" if value == "descending" else "longer",
        })
    if getattr(engine, "optional_cast_link", False) and rules.get(modifiers.CAST_LINK_KEY):
        add(modifiers.CAST_LINK_KEY, "Shared cast", modifiers.RULEBOOK[modifiers.CAST_LINK_KEY])
    if rules.get(blind_fork.BLIND_FORK_KEY):
        add("blind_fork", "Blind Fork", blind_fork.RULEBOOK)
    participants = session.exec(select(RunParticipant).where(RunParticipant.run_id == run.id)).all()
    if len(participants) > 1 and (
        run.game_type != "meet_in_the_middle" or rules.get(blind_fork.BLIND_FORK_KEY)
    ):
        add("veto", "Golden Veto", veto.RULEBOOK)
    section = render(engine.rulebook, values)
    if run.game_type == "tug_of_war" and render_rules.get("tug_rules_version") == 1:
        section = replace(section, glossary=["seed", "wildcard"])
    if run.engine_version <= 1:
        section = replace(section, scoring=[
            *section.scoring,
            "Legacy engine: progress is recorded, but automatic win/fail completion is not enforced; finish the run manually.",
        ])
    terms = dict.fromkeys([*section.glossary, *(key for overlay in overlays for key in overlay.rulebook.glossary)])
    settings = {
        "Seed policy": engine.seed_policy,
        "Engine version": str(run.engine_version),
        "Repeat policy": str(values["allow_repeats"]),
        "Minimum runtime": f"{values['min_runtime']} minutes",
        "Wildcard budget": "Unlimited" if values["wildcards_budget"] == -1 else str(values["wildcards_budget"]),
    }
    mode_settings = {
        "tug_of_war": ("target_lead", "effective_target", "territory_a", "territory_b",
                       "momentum_cap", "sudden_death_after", "sudden_death_every", "steal_enabled"),
        "rt_split": ("target_points",),
        "decade_sieve": ("target_decade",),
        "regional_deep_dive": ("slice_name",),
        "method_actor": ("person_name", "max_skip"),
        "auteur_marathon": ("person_name", "max_skip"),
        "rabbit_hole": ("max_lives", "lives_remaining", "escape_depth"),
        "meet_in_the_middle": ("hints_remaining",),
        "genre_pendulum": ("genre_cycle", "swing_frequency"),
        "historical_time_travel": ("setting_direction",),
        "chrono_climb": ("chrono_word",),
        "aesthetic_gradient": ("color_threshold",),
        "semantic_trope": ("similarity_threshold",),
    }
    for key in mode_settings.get(run.game_type, ()):
        if key in values and values[key] is not None:
            settings[key.replace("_", " ").title()] = str(values[key])
    if engine.supports_json_rules:
        settings["Win conditions"] = values["win_goal"]
        settings["Fail conditions"] = values["fail_goal"]
        settings["Cast depth"] = str(values["cast_depth"])
        settings["Consecutive actor restriction"] = str(values.get("no_consecutive_actor", False))
    list_keys = {
        "canon_island": ("allowed_curated_list_id",),
        "regional_deep_dive": ("curated_list_id",),
    }
    for key in list_keys.get(run.game_type, ()):
        if rules.get(key):
            curated_list = session.get(CuratedList, rules[key])
            settings["Canon list"] = curated_list.title if curated_list else f"Unavailable list ({rules[key]})"
    settings.update({key.replace("_", " ").title(): str(value) for key, value in active.items()})
    settings["Bounty Board"] = "On" if any(overlay.key == "bounty_board" for overlay in overlays) else "Off"
    settings["Blind Fork"] = "On" if rules.get(blind_fork.BLIND_FORK_KEY) else "Off"
    return RunRulebook(
        game_type=run.game_type, display_name=engine.display_name, rulebook=section,
        overlays=overlays, glossary={key: GLOSSARY[key] for key in terms}, settings=settings,
    )


@router.post("/engine/validate", response_model=ValidationResult)
async def validate(
    payload: ValidateRequest,
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    _current_user: User = Depends(get_current_user),
) -> ValidationResult:
    engine = get_engine(payload.game_type, session, tmdb)
    return await engine.validate_next_step(payload.from_movie_id, payload.to_movie_id)


ANTI_CHEAT_LOCKED = {
    "code": "anti_cheat_locked",
    "message": "Bridge Solver is locked for today's Daily Puzzle until solved or forfeited!",
}


def _anti_cheat_response(
    session: Session, user: User, from_id: int, to_id: int
) -> JSONResponse | None:
    """403 when this pair is today's Daily Puzzle and the user hasn't solved or forfeited it."""
    if daily_puzzle.is_locked(session, user.id, from_id, to_id):
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content=ANTI_CHEAT_LOCKED)
    return None


@router.post("/engine/bridge", response_model=None)
async def bridge_fast(
    payload: BridgeRequest,
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    current_user: User = Depends(get_current_user),
) -> dict | Response:
    if locked := _anti_cheat_response(
        session, current_user, payload.from_movie_id, payload.to_movie_id
    ):
        return locked
    engine = get_engine(payload.game_type, session, tmdb)
    agen = engine.solve_bridge(
        payload.from_movie_id,
        payload.to_movie_id,
        FAST_MAX_DEPTH,
        call_budget=FAST_CALL_BUDGET,
        max_duration_seconds=FAST_MAX_DURATION_SECONDS,
    )
    try:
        async for event in agen:
            if event["type"] == "result":
                return {
                    "status": "solved",
                    **jsonable_encoder({k: v for k, v in event.items() if k != "type"}),
                }
            if event["type"] in ("exhausted", "timeout", "error"):
                return {"status": "exceeded_fast_budget"}
    finally:
        await agen.aclose()
    return {"status": "exceeded_fast_budget"}


def _run_solve_context(
    session: Session, run_id: str | None, current_user: User
) -> tuple[set[int], int | None, int | None]:
    """(excluded movie ids, cast limit, min runtime) scoped by a run's history and rules."""
    if run_id is None:
        return set(), None, None
    # 404 (never 403) for both a missing run and a run this user isn't a
    # participant in - mirrors run_participant_guard, but that dependency
    # can't be reused as-is here since `run_id` is a query param, not a
    # path param, on these routes.
    run = session.get(Run, run_id)
    if run is None or session.get(RunParticipant, (run_id, current_user.id)) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    excluded = {
        step.movie_id
        for step in session.exec(select(RunStep).where(RunStep.run_id == run_id)).all()
    }
    rules = run.rules_config or dict(DEFAULT_RULES_CONFIG)
    return excluded, rules.get("max_cast_order"), rules.get("min_runtime")


def _tail_connection_type(session: Session, run_id: str | None, from_movie_id: int) -> str | None:
    """Auteur Relay: the link kind that led into the run's last film, when the
    bridge starts from it - so the first bridge hop continues the alternation."""
    if run_id is None:
        return None
    tail = session.exec(
        select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.logged_at.desc())
    ).first()
    if tail is None or tail.movie_id != from_movie_id:
        return None
    return (tail.transition_metadata or {}).get("connection_type")


def _parse_id_list(raw: str | None, name: str, max_items: int) -> list[int]:
    if not raw:
        return []
    try:
        ids = [int(part) for part in raw.split(",") if part.strip()]
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{name} must be a comma-separated list of integers",
        ) from None
    if len(ids) > max_items:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{name} accepts at most {max_items} ids",
        )
    return ids


def _tmdb_unavailable(exc: Exception) -> HTTPException:
    if isinstance(exc, DeadlineReached):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TMDB is rate limiting us right now - try again in a moment",
        )
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY, detail=f"TMDB lookup failed: {exc}"
    )


def _llm_config(session: Session) -> llm.LlmConfig:
    config = llm.load_config(session)
    if not config.enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The generative model is off - an admin can enable it under "
            "Settings > Integrations > AI & Embeddings.",
        )
    return config


@router.get("/engine/llm/status", response_model=LlmStatus)
def llm_status(
    session: Session = Depends(get_session),
    _user: User = Depends(get_current_user),
) -> LlmStatus:
    """Whether the opt-in generative features (pitches, teasers) are switched on."""
    config = llm.load_config(session)
    return LlmStatus(enabled=config.enabled, provider=config.provider)


@router.post("/engine/pitch", response_model=PitchResult)
async def pitch_transition(
    payload: PitchRequest,
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    _user: User = Depends(get_current_user),
) -> PitchResult:
    """A one-sentence cinephile pitch for why `candidate` follows `previous`."""
    config = _llm_config(session)
    try:
        previous = await cache_repo.get_movie(session, tmdb, payload.previous_movie_id)
        candidate = await cache_repo.get_movie(session, tmdb, payload.candidate_movie_id)
    except (TMDBError, DeadlineReached) as exc:
        raise _tmdb_unavailable(exc) from exc
    try:
        text = await llm.pitch(
            config, previous, candidate, payload.link_label, critic=payload.style == "critic"
        )
    except llm.LlmUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return PitchResult(pitch=text)


@router.post("/engine/teasers", response_model=TeaserResult)
async def blind_draft_teasers(
    payload: TeaserRequest,
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    _user: User = Depends(get_current_user),
) -> TeaserResult:
    """Cryptic, spoiler-free one-sentence teasers standing in for the raw TMDB overview."""
    config = _llm_config(session)
    teasers: dict[int, str] = {}
    failure: llm.LlmUnavailable | None = None
    for movie_id in dict.fromkeys(payload.movie_ids):
        try:
            movie = await cache_repo.get_movie(session, tmdb, movie_id)
            teasers[movie_id] = await llm.teaser(config, movie)
        except llm.LlmUnavailable as exc:
            failure = exc
        except (TMDBError, DeadlineReached) as exc:
            raise _tmdb_unavailable(exc) from exc
    if not teasers and failure is not None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(failure))
    return TeaserResult(teasers=teasers)


@router.get("/engine/roulette/spin", response_model=RouletteSpinResult)
async def roulette_spin(
    max_runtime: int | None = Query(default=None, ge=1, le=1000),
    min_runtime: int | None = Query(default=None, ge=1, le=1000),
    min_rating: float | None = Query(default=None, ge=0, le=10, description="IMDb rating"),
    max_rating: float | None = Query(default=None, ge=0, le=10, description="IMDb rating"),
    genre: int | None = Query(default=None, description="Legacy single TMDB genre id"),
    genre_ids: list[int] = Query(default=[], description="TMDB genre ids (repeat the param)"),
    genre_operator: Literal["AND", "OR"] = Query(default="OR"),
    run_id: str | None = Query(default=None, description="Skip films already in this run"),
    count: int = Query(
        default=1, ge=1, le=5, description="Distinct films to draw (Blind Draft: 3)"
    ),
    game_type: str = Query(default="roulette"),
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    current_user: User = Depends(get_current_user),
) -> RouletteSpinResult:
    """Movie Night Roulette: one random film from the local cache (`cached_movies`)
    that matches the filters. Pure SQLite - never calls TMDB."""
    engine = get_engine(game_type, session, tmdb)
    if not isinstance(engine, RouletteEngine):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{game_type} doesn't support roulette spins",
        )
    for low, high, label in (
        (min_runtime, max_runtime, "runtime"),
        (min_rating, max_rating, "rating"),
    ):
        if low is not None and high is not None and low > high:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"min_{label} can't be above max_{label}",
            )
    excluded, _, _ = _run_solve_context(session, run_id, current_user)

    drawn = engine.draw(
        SpinFilters(
            max_runtime=max_runtime,
            min_runtime=min_runtime,
            min_rating=min_rating,
            max_rating=max_rating,
            genre_id=genre,
            genre_ids=genre_ids,
            genre_operator=genre_operator,
            exclude_movie_ids=sorted(excluded),
        ),
        count,
    )
    if drawn is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No movies found in cache matching criteria - loosen the filters, or browse "
            "a few lists/actors to grow your local cache",
        )
    picks, pool_size = drawn
    movies = [
        RouletteMovie(
            tmdb_id=movie.tmdb_id,
            title=movie.title,
            poster_path=movie.poster_path,
            release_year=parse_release_year(movie.release_date),
            origin_country=movie.origin_country,
            runtime=movie.runtime,
            overview=movie.overview,
            tagline=movie.tagline or None,
            genre_ids=movie.genre_ids or [],
            imdb_rating=imdb_rating if imdb_rating and imdb_rating != "N/A" else None,
        )
        for movie, imdb_rating in picks
    ]
    return RouletteSpinResult(movie=movies[0], pool_size=pool_size, movies=movies)


@router.get("/engine/bridge/swap-node", response_model=SwapNodeResult)
async def bridge_swap_node(
    movie_id: int = Query(..., description="The path film to replace (Movie_B)"),
    from_movie_id: int = Query(..., description="The film before it (Movie_A)"),
    to_movie_id: int = Query(..., description="The film after it (Movie_C)"),
    actor_in_id: int | None = Query(
        default=None, description="Actor_X: links Movie_A to Movie_B (required for mode=same)"
    ),
    actor_out_id: int | None = Query(
        default=None, description="Actor_Y: links Movie_B to Movie_C (required for mode=same)"
    ),
    mode: Literal["same", "broad"] = Query(
        default="same",
        description="same = the exact same two actors; broad = any actor shared with A and with C",
    ),
    exclude_movie_ids: str | None = Query(
        default=None, description="Comma-separated films already on the path"
    ),
    game_type: str = Query(default="cinechain"),
    run_id: str | None = Query(default=None),
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    current_user: User = Depends(get_current_user),
) -> SwapNodeResult:
    """Alternatives for `movie_id` on a bridge path. "The Recast" (`mode=same`) keeps
    both connecting actors: Intersection(Actor_X_Movies, Actor_Y_Movies). "The Broad
    Detour" (`mode=broad`) only needs *some* actor shared with Movie_A and *some*
    actor shared with Movie_C, so entirely different cast members may link the path."""
    engine = get_engine(game_type, session, tmdb)
    if "bridge_swap" not in engine.capabilities:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{game_type} doesn't support node swapping",
        )
    if mode == "same" and (actor_in_id is None or actor_out_id is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="actor_in_id and actor_out_id are required for mode=same",
        )
    run_excluded, _, _ = _run_solve_context(session, run_id, current_user)
    exclude = {
        movie_id,
        from_movie_id,
        to_movie_id,
        *_parse_id_list(exclude_movie_ids, "exclude_movie_ids", 50),
    }
    # A run's own history is excluded, but never the path's endpoints themselves.
    exclude |= run_excluded - {from_movie_id, to_movie_id}
    deadline = time.monotonic() + SWAP_MAX_DURATION_SECONDS

    try:
        if mode == "broad":
            actors_from, actors_to = await bridge_paths.ensure_broad_pool(
                session, tmdb, from_movie_id, to_movie_id, deadline
            )
        else:
            await bridge_paths.ensure_filmographies(
                session, tmdb, [actor_in_id, actor_out_id], deadline
            )
    except (DeadlineReached, TMDBError) as exc:
        raise _tmdb_unavailable(exc) from exc

    if mode == "broad":
        candidates, total = bridge_paths.find_broad_detours(
            session,
            from_movie_id=from_movie_id,
            to_movie_id=to_movie_id,
            actors_from=actors_from,
            actors_to=actors_to,
            exclude_movie_ids=exclude,
            same_pair=(actor_in_id, actor_out_id)
            if actor_in_id is not None and actor_out_id is not None
            else None,
        )
    else:
        candidates, total = bridge_paths.find_same_actor_swaps(
            session,
            from_movie_id=from_movie_id,
            to_movie_id=to_movie_id,
            actor_in_id=actor_in_id,
            actor_out_id=actor_out_id,
            exclude_movie_ids=exclude,
        )
    return SwapNodeResult(candidates=candidates, total=total)


@router.get("/engine/bridge/tags", response_model=PathTagsResult)
async def bridge_path_tags(
    movie_ids: str = Query(..., description="Comma-separated films of the path, in order"),
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    _current_user: User = Depends(get_current_user),
) -> PathTagsResult:
    """Re-analyses a (swapped) path: hydrates any film still missing runtime /
    country detail, then returns its highlight tags and the refreshed nodes."""
    ids = _parse_id_list(movie_ids, "movie_ids", TAGS_MAX_MOVIES)
    if not ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="movie_ids is required"
        )
    await bridge_paths.hydrate_movies(session, tmdb, ids)
    nodes = bridge_paths.build_nodes(session, ids)
    return PathTagsResult(tags=bridge_paths.analyze_path_tags(session, nodes), nodes=nodes)


@router.get("/engine/bridge/stream", response_model=None)
async def bridge_stream(
    request: Request,
    from_movie_id: int = Query(...),
    to_movie_id: int = Query(...),
    game_type: str = Query(default="cinechain"),
    max_depth: int | None = Query(default=None),
    run_id: str | None = Query(default=None),
    # "Search Deeper": only offer routes of at least this many movie-hops.
    min_hops: int | None = Query(default=None, ge=2, le=DEEP_SEARCH_MAX_HOPS),
    # "Find Alternative Routes": films the route may not pass through (the intermediates of
    # routes already shown), forcing a distinct corridor. The two endpoints are never excluded.
    exclude_movie_ids: list[int] = Query(default=[], max_length=MAX_EXCLUDED_MOVIES),
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    current_user: User = Depends(get_current_user),
) -> Response:
    if locked := _anti_cheat_response(session, current_user, from_movie_id, to_movie_id):
        return locked
    engine = get_engine(game_type, session, tmdb)
    excluded_movie_ids, cast_limit, min_runtime = _run_solve_context(session, run_id, current_user)
    excluded_movie_ids = (excluded_movie_ids | set(exclude_movie_ids)) - {
        from_movie_id,
        to_movie_id,
    }
    start_connection_type = _tail_connection_type(session, run_id, from_movie_id)
    run = session.get(Run, run_id) if run_id else None
    run_rules = run.rules_config if run is not None else None

    overrides = settings_repo.get_overrides(session)
    raw_max_duration = overrides.get("bridge_max_duration_seconds")
    max_duration_seconds = int(raw_max_duration) if raw_max_duration else None

    async def event_source():
        agen = engine.solve_bridge(
            from_movie_id,
            to_movie_id,
            max_depth,
            cast_limit=cast_limit,
            min_runtime=min_runtime,
            excluded_movie_ids=excluded_movie_ids,
            max_duration_seconds=max_duration_seconds,
            min_hops=min_hops,
            start_connection_type=start_connection_type,
            rules=run_rules,
        )
        try:
            async for event in agen:
                if await request.is_disconnected():
                    break
                event_type = event["type"]
                payload = jsonable_encoder({k: v for k, v in event.items() if k != "type"})
                yield f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"
        finally:
            await agen.aclose()

    return StreamingResponse(event_source(), media_type="text/event-stream")
