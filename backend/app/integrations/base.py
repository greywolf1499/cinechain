"""Protocols that homelab media-server/request clients must satisfy.

Jellyfin implements `MediaServerClient` today; Radarr/Seerr implement
`RequestClient` as signature-complete stubs until v1.1.
"""

from typing import Protocol

from app.schemas.integrations import JellyfinItemSummary


class MediaServerClient(Protocol):
    async def lookup_movies(
        self, tmdb_ids: list[int]) -> dict[int, JellyfinItemSummary]: ...

    async def check_health(self) -> dict: ...


class RequestClient(Protocol):
    async def request_movie(self, tmdb_id: int, title: str) -> bool: ...
