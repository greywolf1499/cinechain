"""Overseerr/Jellyseerr (API v1) client: users, Radarr service options, movie
requests (auto-routed or explicitly targeted) and request-state lookups."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

import httpx

from app.config import Settings, get_settings
from app.integrations.base import IntegrationError, request_json
from app.schemas.integrations import QualityProfile, RootFolder, SeerrServer, SeerrUser

DEFAULT_URL = "http://seerr:5055"
_CACHE_TTL_SECONDS = 30

# Seerr MediaStatus values: 1 unknown, 2 pending, 3 processing, 4 partially available,
# 5 available, 6 blacklisted, 7 deleted.
SeerrMediaState = Literal["available", "downloading", "requested", "missing"]
_cache: dict[int, tuple[float, SeerrMediaState]] = {}


def invalidate_cache(tmdb_id: int) -> None:
    _cache.pop(tmdb_id, None)


def media_state_from_info(media_info: dict | None) -> SeerrMediaState:
    if not media_info:
        return "missing"
    status = media_info.get("status")
    if status in (4, 5):
        return "available"
    if status in (2, 3):
        return "downloading" if media_info.get("downloadStatus") else "requested"
    return "missing"


class SeerrClient:
    def __init__(self, client: httpx.AsyncClient, settings: Settings | None = None) -> None:
        self._client = client
        self._settings = settings or get_settings()
        self._overrides: dict[str, str] = {}

    def set_overrides(self, overrides: dict[str, str]) -> None:
        self._overrides = overrides

    @property
    def _url(self) -> str:
        url = self._overrides.get(
            "seerr_url") or self._settings.seerr_url or DEFAULT_URL
        return url.rstrip("/")

    @property
    def _api_key(self) -> str:
        return self._overrides.get("seerr_api_key") or self._settings.seerr_api_key

    @property
    def enabled(self) -> bool:
        # The default URL only applies once a key is configured.
        return bool(self._api_key)

    @property
    def request_mode(self) -> Literal["auto", "prompt"]:
        return "prompt" if self._overrides.get("seerr_request_mode") == "prompt" else "auto"

    @property
    def default_user_id(self) -> int | None:
        raw = self._overrides.get("seerr_user_id")
        return int(raw) if raw and raw.isdigit() else None

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        return await request_json(self._client, method, f"{self._url}{path}", self._api_key, **kwargs)

    async def check_health(self) -> dict:
        if not self.enabled:
            return {"enabled": False, "reachable": False, "version": None}
        result = await check_seerr_connectivity(self._client, self._url, self._api_key)
        return {"enabled": True, "reachable": result["reachable"], "version": result["version"]}

    async def list_users(self) -> list[SeerrUser]:
        data = await self._request("GET", "/api/v1/user", params={"take": 100, "skip": 0})
        return [
            SeerrUser(
                id=u["id"],
                display_name=u.get("displayName") or u.get(
                    "username") or u.get("email") or str(u["id"]),
                email=u.get("email"),
            )
            for u in (data or {}).get("results", [])
        ]

    async def get_radarr_servers(self) -> list[SeerrServer]:
        """Radarr instances configured inside Seerr, each with its profiles and root folders."""
        services = await self._request("GET", "/api/v1/service/radarr") or []

        async def detail(service: dict) -> SeerrServer:
            info = await self._request("GET", f"/api/v1/service/radarr/{service['id']}") or {}
            return SeerrServer(
                id=service["id"],
                name=service.get("name") or f"Radarr {service['id']}",
                is_default=bool(service.get("isDefault")),
                is_4k=bool(service.get("is4k")),
                active_profile_id=service.get("activeProfileId"),
                active_directory=service.get("activeDirectory"),
                profiles=[QualityProfile(id=p["id"], name=p["name"])
                          for p in info.get("profiles", [])],
                root_folders=[
                    RootFolder(id=f.get("id"),
                               path=f["path"], free_space=f.get("freeSpace"))
                    for f in info.get("rootFolders", [])
                ],
            )

        return list(await asyncio.gather(*(detail(s) for s in services)))

    async def request_movie(
        self,
        tmdb_id: int,
        user_id: int | None = None,
        server_id: int | None = None,
        profile_id: int | None = None,
        root_folder: str | None = None,
    ) -> dict:
        """Auto-route when no target is given (Seerr applies its own language rules);
        otherwise an explicit server/profile/folder request."""
        payload: dict[str, Any] = {"mediaType": "movie", "mediaId": tmdb_id}
        if user_id is not None:
            payload["userId"] = user_id
        if server_id is not None:
            payload["serverId"] = server_id
        if profile_id is not None:
            payload["profileId"] = profile_id
        if root_folder is not None:
            payload["rootFolder"] = root_folder
        result = await self._request("POST", "/api/v1/request", json=payload)
        invalidate_cache(tmdb_id)
        return result or {}

    async def lookup_states(self, tmdb_ids: list[int]) -> dict[int, SeerrMediaState]:
        now = time.monotonic()
        results: dict[int, SeerrMediaState] = {}
        to_query: list[int] = []
        for tmdb_id in dict.fromkeys(tmdb_ids):
            cached = _cache.get(tmdb_id)
            if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
                results[tmdb_id] = cached[1]
            else:
                to_query.append(tmdb_id)

        semaphore = asyncio.Semaphore(5)

        async def fetch(tmdb_id: int) -> tuple[int, SeerrMediaState]:
            async with semaphore:
                try:
                    data = await self._request("GET", f"/api/v1/movie/{tmdb_id}")
                except IntegrationError as exc:
                    if exc.status_code != 404:  # unknown to TMDB/Seerr == simply not requested
                        raise
                    data = None
            return tmdb_id, media_state_from_info((data or {}).get("mediaInfo"))

        for tmdb_id, state in await asyncio.gather(*(fetch(t) for t in to_query)):
            _cache[tmdb_id] = (now, state)
            results[tmdb_id] = state
        return results


async def check_seerr_connectivity(client: httpx.AsyncClient, url: str, api_key: str) -> dict:
    """Tests a candidate URL/key directly - independent of any configured SeerrClient."""
    if not url:
        return {"reachable": False, "version": None, "detail": "Seerr URL is required"}
    try:
        data = await request_json(client, "GET", f"{url.rstrip('/')}/api/v1/status", api_key)
        # /status is public, so also hit an authenticated endpoint to validate the key.
        await request_json(client, "GET", f"{url.rstrip('/')}/api/v1/auth/me", api_key)
    except IntegrationError as exc:
        return {"reachable": False, "version": None, "detail": exc.detail}
    return {"reachable": True, "version": (data or {}).get("version"), "detail": None}
