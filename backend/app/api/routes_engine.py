import json
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.deps import get_current_user, get_tmdb_client
from app.db import get_session
from app.engines.registry import ENGINE_REGISTRY, get_engine
from app.models.run import DEFAULT_RULES_CONFIG, Run, RunParticipant, RunStep
from app.models.user import User
from app.schemas.engine import PathTagsResult, SwapNodeResult, ValidationResult
from app.services import bridge_paths, settings_repo
from app.services.tmdb import TMDBClient, TMDBError
from app.services.tmdb_backoff import DeadlineReached

router = APIRouter(tags=["engine"])

# The blocking endpoint only ever attempts a shallow, cheap search; anything
# deeper is expected to go through the SSE stream instead.
FAST_MAX_DEPTH = 2
FAST_CALL_BUDGET = 20
FAST_MAX_DURATION_SECONDS = 15
# "Search Deeper" can ask for routes this many movie-hops long, at most.
DEEP_SEARCH_MAX_HOPS = 8
SWAP_MAX_DURATION_SECONDS = 15
TAGS_MAX_MOVIES = 20


class EngineMeta(BaseModel):
    game_type: str
    display_name: str
    description: str
    capabilities: list[str]


class ValidateRequest(BaseModel):
    game_type: str
    from_movie_id: int
    to_movie_id: int


class BridgeRequest(BaseModel):
    game_type: str = "cinechain"
    from_movie_id: int
    to_movie_id: int


@router.get("/engines", response_model=list[EngineMeta])
def list_engines(_current_user: User = Depends(get_current_user)) -> list[EngineMeta]:
    return [
        EngineMeta(
            game_type=cls.game_type,
            display_name=cls.display_name,
            description=cls.description,
            capabilities=cls.capabilities,
        )
        for cls in ENGINE_REGISTRY.values()
    ]


@router.post("/engine/validate", response_model=ValidationResult)
async def validate(
    payload: ValidateRequest,
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    _current_user: User = Depends(get_current_user),
) -> ValidationResult:
    engine = get_engine(payload.game_type, session, tmdb)
    return await engine.validate_next_step(payload.from_movie_id, payload.to_movie_id)


@router.post("/engine/bridge")
async def bridge_fast(
    payload: BridgeRequest,
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    _current_user: User = Depends(get_current_user),
) -> dict:
    engine = get_engine(payload.game_type, session, tmdb)
    agen = engine.solve_bridge(
        payload.from_movie_id, payload.to_movie_id, FAST_MAX_DEPTH,
        call_budget=FAST_CALL_BUDGET, max_duration_seconds=FAST_MAX_DURATION_SECONDS,
    )
    try:
        async for event in agen:
            if event["type"] == "result":
                return {"status": "solved", **jsonable_encoder({k: v for k, v in event.items() if k != "type"})}
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    excluded = {
        step.movie_id
        for step in session.exec(select(RunStep).where(RunStep.run_id == run_id)).all()
    }
    rules = run.rules_config or dict(DEFAULT_RULES_CONFIG)
    return excluded, rules.get("max_cast_order"), rules.get("min_runtime")


def _parse_id_list(raw: str | None, name: str, max_items: int) -> list[int]:
    if not raw:
        return []
    try:
        ids = [int(part) for part in raw.split(",") if part.strip()]
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{name} must be a comma-separated list of integers") from None
    if len(ids) > max_items:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{name} accepts at most {max_items} ids")
    return ids


def _tmdb_unavailable(exc: Exception) -> HTTPException:
    if isinstance(exc, DeadlineReached):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TMDB is rate limiting us right now - try again in a moment")
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY, detail=f"TMDB lookup failed: {exc}")


@router.get("/engine/bridge/swap-node", response_model=SwapNodeResult)
async def bridge_swap_node(
    movie_id: int = Query(..., description="The path film to replace (Movie_B)"),
    from_movie_id: int = Query(..., description="The film before it (Movie_A)"),
    to_movie_id: int = Query(..., description="The film after it (Movie_C)"),
    actor_in_id: int = Query(..., description="Actor_X: links Movie_A to Movie_B"),
    actor_out_id: int = Query(..., description="Actor_Y: links Movie_B to Movie_C"),
    exclude_movie_ids: str | None = Query(
        default=None, description="Comma-separated films already on the path"),
    game_type: str = Query(default="cinechain"),
    run_id: str | None = Query(default=None),
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    current_user: User = Depends(get_current_user),
) -> SwapNodeResult:
    """The Same-Actor Swap: alternatives for `movie_id` starring the exact same
    two connecting actors, i.e. Intersection(Actor_X_Movies, Actor_Y_Movies)."""
    engine = get_engine(game_type, session, tmdb)
    if "bridge_swap" not in engine.capabilities:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{game_type} doesn't support node swapping")
    run_excluded, _, _ = _run_solve_context(session, run_id, current_user)
    exclude = {
        movie_id, from_movie_id, to_movie_id,
        *_parse_id_list(exclude_movie_ids, "exclude_movie_ids", 50),
    }
    # A run's own history is excluded, but never the path's endpoints themselves.
    exclude |= run_excluded - {from_movie_id, to_movie_id}

    try:
        await bridge_paths.ensure_filmographies(
            session, tmdb, [actor_in_id, actor_out_id],
            time.monotonic() + SWAP_MAX_DURATION_SECONDS)
    except (DeadlineReached, TMDBError) as exc:
        raise _tmdb_unavailable(exc) from exc

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
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="movie_ids is required")
    await bridge_paths.hydrate_movies(session, tmdb, ids)
    nodes = bridge_paths.build_nodes(session, ids)
    return PathTagsResult(tags=bridge_paths.analyze_path_tags(session, nodes), nodes=nodes)


@router.get("/engine/bridge/stream")
async def bridge_stream(
    request: Request,
    from_movie_id: int = Query(...),
    to_movie_id: int = Query(...),
    game_type: str = Query(default="cinechain"),
    max_depth: int | None = Query(default=None),
    run_id: str | None = Query(default=None),
    # "Search Deeper": only offer routes of at least this many movie-hops.
    min_hops: int | None = Query(default=None, ge=2, le=DEEP_SEARCH_MAX_HOPS),
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    engine = get_engine(game_type, session, tmdb)
    excluded_movie_ids, cast_limit, min_runtime = _run_solve_context(
        session, run_id, current_user)

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
        )
        try:
            async for event in agen:
                if await request.is_disconnected():
                    break
                event_type = event["type"]
                payload = jsonable_encoder(
                    {k: v for k, v in event.items() if k != "type"})
                yield f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"
        finally:
            await agen.aclose()

    return StreamingResponse(event_source(), media_type="text/event-stream")
