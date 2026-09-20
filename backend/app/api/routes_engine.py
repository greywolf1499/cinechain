import json

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
from app.schemas.engine import ValidationResult
from app.services.tmdb import TMDBClient

router = APIRouter(tags=["engine"])

# The blocking endpoint only ever attempts a shallow, cheap search; anything
# deeper is expected to go through the SSE stream instead.
FAST_MAX_DEPTH = 2
FAST_CALL_BUDGET = 20


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
        payload.from_movie_id, payload.to_movie_id, FAST_MAX_DEPTH, call_budget=FAST_CALL_BUDGET
    )
    try:
        async for event in agen:
            if event["type"] == "result":
                return {"status": "solved", **jsonable_encoder({k: v for k, v in event.items() if k != "type"})}
            if event["type"] in ("exhausted", "error"):
                return {"status": "exceeded_fast_budget"}
    finally:
        await agen.aclose()
    return {"status": "exceeded_fast_budget"}


@router.get("/engine/bridge/stream")
async def bridge_stream(
    request: Request,
    from_movie_id: int = Query(...),
    to_movie_id: int = Query(...),
    game_type: str = Query(default="cinechain"),
    max_depth: int | None = Query(default=None),
    run_id: str | None = Query(default=None),
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    engine = get_engine(game_type, session, tmdb)

    excluded_movie_ids: set[int] = set()
    cast_limit: int | None = None
    min_runtime: int | None = None
    if run_id is not None:
        # 404 (never 403) for both a missing run and a run this user isn't a
        # participant in - mirrors run_participant_guard, but that dependency
        # can't be reused as-is here since `run_id` is a query param, not a
        # path param, on this route.
        run = session.get(Run, run_id)
        if run is None or session.get(RunParticipant, (run_id, current_user.id)) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
        excluded_movie_ids = {
            step.movie_id
            for step in session.exec(select(RunStep).where(RunStep.run_id == run_id)).all()
        }
        rules = run.rules_config or dict(DEFAULT_RULES_CONFIG)
        cast_limit = rules.get("max_cast_order")
        min_runtime = rules.get("min_runtime")

    async def event_source():
        agen = engine.solve_bridge(
            from_movie_id,
            to_movie_id,
            max_depth,
            cast_limit=cast_limit,
            min_runtime=min_runtime,
            excluded_movie_ids=excluded_movie_ids,
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
