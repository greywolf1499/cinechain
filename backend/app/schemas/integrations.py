from typing import Literal

from pydantic import BaseModel, Field


class JellyfinItemSummary(BaseModel):
    on_server: bool | None = None
    item_id: str | None = None
    play_url: str | None = None


class JellyfinStatus(BaseModel):
    enabled: bool
    reachable: bool
    version: str | None = None


class RequestClientStatus(BaseModel):
    enabled: bool
    implemented: bool = True
    reachable: bool | None = None
    version: str | None = None


class IntegrationsStatus(BaseModel):
    jellyfin: JellyfinStatus
    radarr: RequestClientStatus
    seerr: RequestClientStatus


class JellyfinLookupRequest(BaseModel):
    tmdb_ids: list[int]


class QualityProfile(BaseModel):
    id: int
    name: str


class RootFolder(BaseModel):
    id: int | None = None
    path: str
    free_space: int | None = None


class RadarrOptions(BaseModel):
    enabled: bool
    profiles: list[QualityProfile] = []
    root_folders: list[RootFolder] = []
    default_quality_profile_id: int | None = None
    default_root_folder_path: str | None = None


class RadarrAddRequest(BaseModel):
    tmdb_id: int
    title: str | None = None
    quality_profile_id: int | None = None
    root_folder_path: str | None = None


class SeerrUser(BaseModel):
    id: int
    display_name: str
    email: str | None = None


class SeerrServer(BaseModel):
    id: int
    name: str
    is_default: bool = False
    is_4k: bool = False
    active_profile_id: int | None = None
    active_directory: str | None = None
    profiles: list[QualityProfile] = []
    root_folders: list[RootFolder] = []


class SeerrOptions(BaseModel):
    enabled: bool
    request_mode: Literal["auto", "prompt"] = "auto"
    default_user_id: int | None = None
    users: list[SeerrUser] = []
    servers: list[SeerrServer] = []


class SeerrRequestBody(BaseModel):
    tmdb_id: int
    server_id: int | None = None
    profile_id: int | None = None
    root_folder: str | None = None
    user_id: int | None = None


class RequestResult(BaseModel):
    success: bool = True
    service: Literal["seerr", "radarr"]
    mode: Literal["auto", "advanced"] = "auto"
    state: Literal["requested", "downloading"] = "requested"


class RequestConfig(BaseModel):
    """What the movie UI needs to decide whether/how to offer a Request button."""

    service: Literal["seerr", "radarr"] | None = None
    request_mode: Literal["auto", "prompt"] = "auto"


AcquisitionState = Literal["available", "downloading", "requested", "missing"]


class AcquisitionStatus(BaseModel):
    state: AcquisitionState = "missing"
    source: Literal["jellyfin", "radarr", "seerr"] | None = None


class StatusLookupRequest(BaseModel):
    tmdb_ids: list[int] = Field(max_length=100)
