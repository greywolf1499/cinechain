from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlmodel import Session

from app.api.deps import get_current_admin
from app.config import get_settings
from app.db import get_session
from app.integrations.jellyfin import check_jellyfin_connectivity
from app.models.user import User
from app.services import settings_repo
from app.services.tmdb import check_tmdb_connectivity

router = APIRouter(prefix="/settings", tags=["settings"])


class IntegrationConfigOut(BaseModel):
    tmdb_configured: bool
    tmdb_api_key_masked: str | None = None
    jellyfin_url: str
    jellyfin_configured: bool
    jellyfin_api_key_masked: str | None = None


class IntegrationConfigUpdate(BaseModel):
    tmdb_api_key: str | None = None
    jellyfin_url: str | None = None
    jellyfin_api_key: str | None = None


class ConnectivityTestResult(BaseModel):
    reachable: bool
    version: str | None = None
    detail: str | None = None


class TestTmdbRequest(BaseModel):
    tmdb_api_key: str


class TestJellyfinRequest(BaseModel):
    jellyfin_url: str
    jellyfin_api_key: str | None = None


def _mask(value: str) -> str | None:
    if not value:
        return None
    if len(value) <= 4:
        return "*" * len(value)
    return f"****{value[-4:]}"


def _build_config(session: Session) -> IntegrationConfigOut:
    base = get_settings()
    overrides = settings_repo.get_overrides(session)
    tmdb_key = overrides.get("tmdb_api_key") or base.tmdb_api_key
    jellyfin_url = overrides.get("jellyfin_url") or base.jellyfin_url
    jellyfin_key = overrides.get("jellyfin_api_key") or base.jellyfin_api_key
    return IntegrationConfigOut(
        tmdb_configured=bool(tmdb_key),
        tmdb_api_key_masked=_mask(tmdb_key),
        jellyfin_url=jellyfin_url,
        jellyfin_configured=bool(jellyfin_url),
        jellyfin_api_key_masked=_mask(jellyfin_key),
    )


@router.get("/integrations", response_model=IntegrationConfigOut)
def get_integration_settings(
    session: Session = Depends(get_session),
    _admin: User = Depends(get_current_admin),
) -> IntegrationConfigOut:
    return _build_config(session)


@router.patch("/integrations", response_model=IntegrationConfigOut)
def update_integration_settings(
    payload: IntegrationConfigUpdate,
    session: Session = Depends(get_session),
    _admin: User = Depends(get_current_admin),
) -> IntegrationConfigOut:
    settings_repo.set_overrides(
        session, payload.model_dump(exclude_unset=True))
    return _build_config(session)


@router.post("/integrations/test-tmdb", response_model=ConnectivityTestResult)
async def test_tmdb_connection(
    payload: TestTmdbRequest,
    request: Request,
    _admin: User = Depends(get_current_admin),
) -> ConnectivityTestResult:
    result = await check_tmdb_connectivity(
        request.app.state.http_client, payload.tmdb_api_key, get_settings().tmdb_api_base
    )
    return ConnectivityTestResult(**result)


@router.post("/integrations/test-jellyfin", response_model=ConnectivityTestResult)
async def test_jellyfin_connection(
    payload: TestJellyfinRequest,
    request: Request,
    _admin: User = Depends(get_current_admin),
) -> ConnectivityTestResult:
    result = await check_jellyfin_connectivity(
        request.app.state.http_client, payload.jellyfin_url, payload.jellyfin_api_key or ""
    )
    return ConnectivityTestResult(**result)
