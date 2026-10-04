from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.api.deps import get_current_admin
from app.config import get_settings
from app.db import get_session
from app.integrations.jellyfin import check_jellyfin_connectivity
from app.integrations.omdb import check_omdb_connectivity
from app.integrations.radarr import DEFAULT_URL as RADARR_DEFAULT_URL
from app.integrations.radarr import check_radarr_connectivity
from app.integrations.seerr import DEFAULT_URL as SEERR_DEFAULT_URL
from app.integrations.seerr import check_seerr_connectivity
from app.models.user import User
from app.services import embeddings, settings_repo
from app.services.tmdb import check_tmdb_connectivity

router = APIRouter(prefix="/settings", tags=["settings"])


class IntegrationConfigOut(BaseModel):
    tmdb_configured: bool
    tmdb_api_key_masked: str | None = None
    jellyfin_url: str
    jellyfin_configured: bool
    jellyfin_api_key_masked: str | None = None
    omdb_configured: bool
    omdb_api_key_masked: str | None = None
    radarr_url: str
    radarr_configured: bool
    radarr_api_key_masked: str | None = None
    radarr_default_quality_profile_id: int | None = None
    radarr_default_root_folder_path: str | None = None
    seerr_url: str
    seerr_configured: bool
    seerr_api_key_masked: str | None = None
    seerr_request_mode: Literal["auto", "prompt"] = "auto"
    seerr_user_id: int | None = None
    embedding_provider: Literal["local_onnx", "ollama", "openai"] = "local_onnx"
    embedding_base_url: str = ""
    embedding_model: str = ""
    embedding_api_key_masked: str | None = None


class IntegrationConfigUpdate(BaseModel):
    tmdb_api_key: str | None = None
    jellyfin_url: str | None = None
    jellyfin_api_key: str | None = None
    omdb_api_key: str | None = None
    radarr_url: str | None = None
    radarr_api_key: str | None = None
    radarr_default_quality_profile_id: int | None = None
    radarr_default_root_folder_path: str | None = None
    seerr_url: str | None = None
    seerr_api_key: str | None = None
    seerr_request_mode: Literal["auto", "prompt"] | None = None
    seerr_user_id: int | None = None
    embedding_provider: Literal["local_onnx", "ollama", "openai"] | None = None
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    embedding_model: str | None = None


class ConnectivityTestResult(BaseModel):
    reachable: bool
    version: str | None = None
    detail: str | None = None


class EmbeddingTestRequest(BaseModel):
    """Candidate provider settings; a blank key falls back to the stored one."""

    embedding_provider: Literal["local_onnx", "ollama", "openai"]
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    embedding_model: str | None = None


class EmbeddingTestResult(BaseModel):
    ok: bool
    latency_ms: int | None = None
    dimension: int | None = None
    provider: str
    model: str
    detail: str | None = None


class TestTmdbRequest(BaseModel):
    tmdb_api_key: str


class TestJellyfinRequest(BaseModel):
    jellyfin_url: str
    jellyfin_api_key: str | None = None


class TestOmdbRequest(BaseModel):
    omdb_api_key: str


class TestArrRequest(BaseModel):
    """Candidate URL + key; a blank key falls back to the stored one."""

    url: str
    api_key: str | None = None


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
    omdb_key = overrides.get("omdb_api_key") or base.omdb_api_key
    radarr_key = overrides.get("radarr_api_key") or base.radarr_api_key
    seerr_key = overrides.get("seerr_api_key") or base.seerr_api_key
    embedding = embeddings.load_config(session)
    profile_id = overrides.get("radarr_default_quality_profile_id")
    seerr_user = overrides.get("seerr_user_id")
    return IntegrationConfigOut(
        tmdb_configured=bool(tmdb_key),
        tmdb_api_key_masked=_mask(tmdb_key),
        jellyfin_url=jellyfin_url,
        jellyfin_configured=bool(jellyfin_url),
        jellyfin_api_key_masked=_mask(jellyfin_key),
        omdb_configured=bool(omdb_key),
        omdb_api_key_masked=_mask(omdb_key),
        radarr_url=overrides.get(
            "radarr_url") or base.radarr_url or RADARR_DEFAULT_URL,
        radarr_configured=bool(radarr_key),
        radarr_api_key_masked=_mask(radarr_key),
        radarr_default_quality_profile_id=int(
            profile_id) if profile_id else None,
        radarr_default_root_folder_path=overrides.get(
            "radarr_default_root_folder_path"),
        seerr_url=overrides.get(
            "seerr_url") or base.seerr_url or SEERR_DEFAULT_URL,
        seerr_configured=bool(seerr_key),
        seerr_api_key_masked=_mask(seerr_key),
        seerr_request_mode="prompt" if overrides.get(
            "seerr_request_mode") == "prompt" else "auto",
        seerr_user_id=int(seerr_user) if seerr_user else None,
        embedding_provider=embedding.provider,  # type: ignore[arg-type]
        embedding_base_url=embedding.base_url,
        embedding_model=embedding.model,
        embedding_api_key_masked=_mask(embedding.api_key),
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
        session,
        {k: None if v is None else str(v)
         for k, v in payload.model_dump(exclude_unset=True).items()},
    )
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


@router.post("/integrations/test-omdb", response_model=ConnectivityTestResult)
async def test_omdb_connection(
    payload: TestOmdbRequest,
    request: Request,
    _admin: User = Depends(get_current_admin),
) -> ConnectivityTestResult:
    result = await check_omdb_connectivity(
        request.app.state.http_client, payload.omdb_api_key, get_settings().omdb_api_base
    )
    return ConnectivityTestResult(**result)


@router.post("/integrations/test-radarr", response_model=ConnectivityTestResult)
async def test_radarr_connection(
    payload: TestArrRequest,
    request: Request,
    session: Session = Depends(get_session),
    _admin: User = Depends(get_current_admin),
) -> ConnectivityTestResult:
    stored = settings_repo.get_overrides(session).get(
        "radarr_api_key") or get_settings().radarr_api_key
    result = await check_radarr_connectivity(
        request.app.state.http_client, payload.url, payload.api_key or stored)
    return ConnectivityTestResult(**result)


@router.post("/integrations/test-seerr", response_model=ConnectivityTestResult)
async def test_seerr_connection(
    payload: TestArrRequest,
    request: Request,
    session: Session = Depends(get_session),
    _admin: User = Depends(get_current_admin),
) -> ConnectivityTestResult:
    stored = settings_repo.get_overrides(session).get(
        "seerr_api_key") or get_settings().seerr_api_key
    result = await check_seerr_connectivity(
        request.app.state.http_client, payload.url, payload.api_key or stored)
    return ConnectivityTestResult(**result)


@router.post("/integrations/test-embeddings", response_model=EmbeddingTestResult)
async def test_embedding_provider(
    payload: EmbeddingTestRequest,
    session: Session = Depends(get_session),
    _admin: User = Depends(get_current_admin),
) -> EmbeddingTestResult:
    """Embeds a dummy sentence with the candidate settings: latency + vector width."""
    stored = embeddings.load_config(session)
    config = embeddings.EmbeddingConfig(
        provider=payload.embedding_provider,
        base_url=payload.embedding_base_url or "",
        api_key=payload.embedding_api_key or stored.api_key,
        model=payload.embedding_model or "",
    )
    return EmbeddingTestResult(**await embeddings.check_connection(config))


class SolverConfigOut(BaseModel):
    bridge_max_duration_seconds: int


class SolverConfigUpdate(BaseModel):
    bridge_max_duration_seconds: int = Field(ge=5, le=600)


@router.get("/solver", response_model=SolverConfigOut)
def get_solver_settings(
    session: Session = Depends(get_session),
    _admin: User = Depends(get_current_admin),
) -> SolverConfigOut:
    overrides = settings_repo.get_overrides(session)
    raw = overrides.get("bridge_max_duration_seconds")
    return SolverConfigOut(
        bridge_max_duration_seconds=int(
            raw) if raw else get_settings().bridge_max_duration_seconds
    )


@router.patch("/solver", response_model=SolverConfigOut)
def update_solver_settings(
    payload: SolverConfigUpdate,
    session: Session = Depends(get_session),
    _admin: User = Depends(get_current_admin),
) -> SolverConfigOut:
    settings_repo.set_overrides(
        session, {"bridge_max_duration_seconds": str(
            payload.bridge_max_duration_seconds)}
    )
    return SolverConfigOut(bridge_max_duration_seconds=payload.bridge_max_duration_seconds)
