from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session

from app.api.deps import get_current_user, get_tmdb_client
from app.db import get_session
from app.engines.registry import ENGINE_REGISTRY, get_engine
from app.models.user import User
from app.schemas.engine import ValidationResult
from app.services.tmdb import TMDBClient

router = APIRouter(tags=["engine"])


class EngineMeta(BaseModel):
    game_type: str
    display_name: str
    description: str
    capabilities: list[str]


class ValidateRequest(BaseModel):
    game_type: str
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
