import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlmodel import Session

from app.api.deps import get_current_admin, get_current_user
from app.db import get_session
from app.integrations.base import IntegrationError
from app.integrations.jellyfin import JellyfinClient
from app.integrations.radarr import RadarrClient, RadarrMovieState
from app.integrations.seerr import SeerrClient, SeerrMediaState
from app.models.user import User
from app.schemas.integrations import (
    AcquisitionStatus,
    IntegrationsStatus,
    JellyfinItemSummary,
    JellyfinLookupRequest,
    JellyfinStatus,
    RadarrAddRequest,
    RadarrOptions,
    RequestClientStatus,
    RequestConfig,
    RequestResult,
    SeerrOptions,
    SeerrRequestBody,
    StatusLookupRequest,
)
from app.services import settings_repo

router = APIRouter(prefix="/integrations", tags=["integrations"])


def get_jellyfin_client(
    request: Request, session: Session = Depends(get_session)
) -> JellyfinClient:
    client = JellyfinClient(request.app.state.http_client)
    client.set_overrides(settings_repo.get_overrides(session))
    return client


def get_radarr_client(request: Request, session: Session = Depends(get_session)) -> RadarrClient:
    client = RadarrClient(request.app.state.http_client)
    client.set_overrides(settings_repo.get_overrides(session))
    return client


def get_seerr_client(request: Request, session: Session = Depends(get_session)) -> SeerrClient:
    client = SeerrClient(request.app.state.http_client)
    client.set_overrides(settings_repo.get_overrides(session))
    return client


def _http_error(exc: IntegrationError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


async def _request_status(client: RadarrClient | SeerrClient) -> RequestClientStatus:
    health = await client.check_health()
    return RequestClientStatus(
        enabled=health["enabled"],
        reachable=health["reachable"] if health["enabled"] else None,
        version=health["version"],
    )


@router.get("/status", response_model=IntegrationsStatus)
async def integrations_status(
    jellyfin: JellyfinClient = Depends(get_jellyfin_client),
    radarr: RadarrClient = Depends(get_radarr_client),
    seerr: SeerrClient = Depends(get_seerr_client),
    _current_user: User = Depends(get_current_user),
) -> IntegrationsStatus:
    health, radarr_status, seerr_status = await asyncio.gather(
        jellyfin.check_health(), _request_status(radarr), _request_status(seerr)
    )
    return IntegrationsStatus(
        jellyfin=JellyfinStatus(**health), radarr=radarr_status, seerr=seerr_status
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


def _effective_service(radarr: RadarrClient, seerr: SeerrClient) -> str | None:
    if seerr.enabled:
        return "seerr"
    return "radarr" if radarr.enabled else None


@router.get("/request-config", response_model=RequestConfig)
async def request_config(
    radarr: RadarrClient = Depends(get_radarr_client),
    seerr: SeerrClient = Depends(get_seerr_client),
    _current_user: User = Depends(get_current_user),
) -> RequestConfig:
    """Seerr wins when configured; Radarr-only installs one-click only if defaults are set."""
    service = _effective_service(radarr, seerr)
    if service == "seerr":
        return RequestConfig(service="seerr", request_mode=seerr.request_mode)
    if service == "radarr":
        has_defaults = (
            radarr.default_quality_profile_id is not None
            and radarr.default_root_folder_path is not None
        )
        return RequestConfig(service="radarr", request_mode="auto" if has_defaults else "prompt")
    return RequestConfig()


@router.get("/seerr/options", response_model=SeerrOptions)
async def seerr_options(
    seerr: SeerrClient = Depends(get_seerr_client),
    current_user: User = Depends(get_current_user),
) -> SeerrOptions:
    if not seerr.enabled:
        return SeerrOptions(enabled=False)

    async def no_users() -> list:
        return []

    try:
        # Seerr user emails are only shown to admins (they pick who to request as).
        users, servers = await asyncio.gather(
            seerr.list_users() if current_user.is_admin else no_users(),
            seerr.get_radarr_servers(),
        )
    except IntegrationError as exc:
        raise _http_error(exc) from exc
    return SeerrOptions(
        enabled=True,
        request_mode=seerr.request_mode,
        default_user_id=seerr.default_user_id,
        users=users,
        servers=servers,
    )


@router.post("/seerr/request", response_model=RequestResult)
async def seerr_request(
    payload: SeerrRequestBody,
    seerr: SeerrClient = Depends(get_seerr_client),
    current_user: User = Depends(get_current_user),
) -> RequestResult:
    if not seerr.enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Seerr is not configured")
    targeted = any(
        v is not None for v in (payload.server_id, payload.profile_id, payload.root_folder)
    )
    if targeted and payload.server_id is None:
        raise HTTPException(
            422,
            detail="server_id is required when choosing a profile or root folder",
        )
    # Only admins may attribute a request to a specific Seerr user.
    user_id = (
        payload.user_id
        if current_user.is_admin and payload.user_id is not None
        else seerr.default_user_id
    )
    try:
        await seerr.request_movie(
            payload.tmdb_id,
            user_id=user_id,
            server_id=payload.server_id,
            profile_id=payload.profile_id,
            root_folder=payload.root_folder,
        )
    except IntegrationError as exc:
        raise _http_error(exc) from exc
    return RequestResult(service="seerr", mode="advanced" if targeted else "auto")


@router.get("/radarr/profiles", response_model=RadarrOptions)
async def radarr_profiles(
    radarr: RadarrClient = Depends(get_radarr_client),
    _current_user: User = Depends(get_current_user),
) -> RadarrOptions:
    if not radarr.enabled:
        return RadarrOptions(enabled=False)
    try:
        profiles, folders = await asyncio.gather(
            radarr.get_quality_profiles(), radarr.get_root_folders()
        )
    except IntegrationError as exc:
        raise _http_error(exc) from exc
    return RadarrOptions(
        enabled=True,
        profiles=profiles,
        root_folders=folders,
        default_quality_profile_id=radarr.default_quality_profile_id,
        default_root_folder_path=radarr.default_root_folder_path,
    )


@router.post("/radarr/add", response_model=RequestResult)
async def radarr_add(
    payload: RadarrAddRequest,
    radarr: RadarrClient = Depends(get_radarr_client),
    _current_user: User = Depends(get_current_user),
) -> RequestResult:
    if not radarr.enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Radarr is not configured")
    profile_id = payload.quality_profile_id or radarr.default_quality_profile_id
    root_folder = payload.root_folder_path or radarr.default_root_folder_path
    if profile_id is None or not root_folder:
        raise HTTPException(
            422,
            detail="Choose a quality profile and root folder (or set Radarr defaults in Settings)",
        )
    try:
        await radarr.add_movie(payload.tmdb_id, profile_id, root_folder, payload.title)
    except IntegrationError as exc:
        raise _http_error(exc) from exc
    return RequestResult(
        service="radarr", mode="advanced" if payload.quality_profile_id else "auto"
    )


def merge_acquisition_status(
    on_server: bool | None,
    radarr: RadarrMovieState | None,
    seerr: SeerrMediaState | None,
) -> AcquisitionStatus:
    """Most-complete state wins: available > downloading > requested > missing."""
    if on_server:
        return AcquisitionStatus(state="available", source="jellyfin")
    if radarr is not None and radarr.has_file:
        return AcquisitionStatus(state="available", source="radarr")
    if seerr == "available":
        return AcquisitionStatus(state="available", source="seerr")
    if radarr is not None and radarr.downloading:
        return AcquisitionStatus(state="downloading", source="radarr")
    if seerr == "downloading":
        return AcquisitionStatus(state="downloading", source="seerr")
    if seerr == "requested":
        return AcquisitionStatus(state="requested", source="seerr")
    if radarr is not None and radarr.monitored:
        return AcquisitionStatus(state="requested", source="radarr")
    return AcquisitionStatus()


@router.post("/status/lookup", response_model=dict[int, AcquisitionStatus])
async def status_lookup(
    payload: StatusLookupRequest,
    session: Session = Depends(get_session),
    jellyfin: JellyfinClient = Depends(get_jellyfin_client),
    radarr: RadarrClient = Depends(get_radarr_client),
    seerr: SeerrClient = Depends(get_seerr_client),
    _current_user: User = Depends(get_current_user),
) -> dict[int, AcquisitionStatus]:
    """Batched acquisition state across Jellyfin, Radarr and Seerr; an
    unreachable service simply contributes nothing."""
    ids = list(dict.fromkeys(payload.tmdb_ids))
    if not ids:
        return {}

    async def radarr_states() -> dict[int, RadarrMovieState | None]:
        if not radarr.enabled:
            return {}
        try:
            return await radarr.lookup_movies(ids)
        except IntegrationError:
            return {}

    async def seerr_states() -> dict[int, SeerrMediaState]:
        if not seerr.enabled:
            return {}
        try:
            return await seerr.lookup_states(ids)
        except IntegrationError:
            return {}

    on_server, radarr_map, seerr_map = await asyncio.gather(
        jellyfin.lookup_movies(ids, session=session), radarr_states(), seerr_states()
    )
    return {
        tmdb_id: merge_acquisition_status(
            on_server[tmdb_id].on_server if tmdb_id in on_server else None,
            radarr_map.get(tmdb_id),
            seerr_map.get(tmdb_id),
        )
        for tmdb_id in ids
    }
