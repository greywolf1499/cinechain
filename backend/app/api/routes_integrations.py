from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlmodel import Session

from app.api.deps import get_current_admin, get_current_user
from app.config import get_settings
from app.db import get_session
from app.integrations.jellyfin import JellyfinClient
from app.integrations.radarr import RadarrClient
from app.models.user import User
from app.schemas.integrations import (
    IntegrationsStatus,
    JellyfinItemSummary,
    JellyfinLookupRequest,
    JellyfinStatus,
    RadarrRequestBody,
    RequestClientStatus,
)
from app.services import settings_repo

router = APIRouter(prefix="/integrations", tags=["integrations"])


def get_jellyfin_client(
    request: Request, session: Session = Depends(get_session)
) -> JellyfinClient:
    client = JellyfinClient(request.app.state.http_client)
    client.set_overrides(settings_repo.get_overrides(session))
    return client


@router.get("/status", response_model=IntegrationsStatus)
async def integrations_status(
    jellyfin: JellyfinClient = Depends(get_jellyfin_client),
    _current_user: User = Depends(get_current_user),
) -> IntegrationsStatus:
    settings = get_settings()
    health = await jellyfin.check_health()
    return IntegrationsStatus(
        jellyfin=JellyfinStatus(**health),
        radarr=RequestClientStatus(enabled=bool(
            settings.radarr_url), implemented=False),
        seerr=RequestClientStatus(enabled=bool(
            settings.seerr_url), implemented=False),
    )


@router.post("/jellyfin/lookup", response_model=dict[int, JellyfinItemSummary])
async def jellyfin_lookup(
    payload: JellyfinLookupRequest,
    session: Session = Depends(get_session),
    jellyfin: JellyfinClient = Depends(get_jellyfin_client),
    _current_user: User = Depends(get_current_user),
) -> dict[int, JellyfinItemSummary]:
    return await jellyfin.lookup_movies(payload.tmdb_ids, session=session)


class JellyfinTestLookupRequest(BaseModel):
    query: str


class JellyfinTestLookupResult(BaseModel):
    query_type: str
    enabled: bool
    matches: list[dict]
    error: str | None = None


@router.post("/jellyfin/test-lookup", response_model=JellyfinTestLookupResult)
async def jellyfin_test_lookup(
    payload: JellyfinTestLookupRequest,
    jellyfin: JellyfinClient = Depends(get_jellyfin_client),
    _admin: User = Depends(get_current_admin),
) -> JellyfinTestLookupResult:
    result = await jellyfin.test_lookup(payload.query)
    return JellyfinTestLookupResult(**result)


@router.post("/radarr/request")
async def radarr_request(
    payload: RadarrRequestBody,
    _current_user: User = Depends(get_current_user),
) -> bool:
    client = RadarrClient()
    return await client.request_movie(payload.tmdb_id, payload.title)
