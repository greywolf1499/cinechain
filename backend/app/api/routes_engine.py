import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session

from app.api.deps import get_current_user, get_tmdb_client
from app.db import get_session
from app.engines.registry import ENGINE_REGISTRY, get_engine
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
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    _current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    engine = get_engine(game_type, session, tmdb)

    async def event_source():
        agen = engine.solve_bridge(from_movie_id, to_movie_id, max_depth)
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
