from pydantic import BaseModel


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
    implemented: bool = False


class IntegrationsStatus(BaseModel):
    jellyfin: JellyfinStatus
    radarr: RequestClientStatus
    seerr: RequestClientStatus


class JellyfinLookupRequest(BaseModel):
    tmdb_ids: list[int]


class RadarrRequestBody(BaseModel):
    tmdb_id: int
    title: str
