"""Radarr v3 client: connectivity, quality profiles, root folders, per-movie
state lookups and direct "add movie" (monitored + search on add)."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.integrations.base import IntegrationError, request_json
from app.schemas.integrations import QualityProfile, RootFolder

DEFAULT_URL = "http://radarr:7878"
_CACHE_TTL_SECONDS = 30


@dataclass(frozen=True)
class RadarrMovieState:
    radarr_id: int
    monitored: bool
    has_file: bool
    downloading: bool


# tmdb_id -> (fetched_at, state); None means "looked up, not in Radarr".
_cache: dict[int, tuple[float, RadarrMovieState | None]] = {}


def invalidate_cache(tmdb_id: int) -> None:
    _cache.pop(tmdb_id, None)


class RadarrClient:
    def __init__(self, client: httpx.AsyncClient, settings: Settings | None = None) -> None:
        self._client = client
        self._settings = settings or get_settings()
        self._overrides: dict[str, str] = {}

    def set_overrides(self, overrides: dict[str, str]) -> None:
        self._overrides = overrides

    @property
    def _url(self) -> str:
        url = self._overrides.get("radarr_url") or self._settings.radarr_url or DEFAULT_URL
        return url.rstrip("/")

    @property
    def _api_key(self) -> str:
        return self._overrides.get("radarr_api_key") or self._settings.radarr_api_key

    @property
    def enabled(self) -> bool:
        # The default URL only applies once a key is configured, so an
        # unconfigured install never makes outbound calls.
        return bool(self._api_key)

    @property
    def default_quality_profile_id(self) -> int | None:
        raw = self._overrides.get("radarr_default_quality_profile_id")
        return int(raw) if raw and raw.isdigit() else None

    @property
    def default_root_folder_path(self) -> str | None:
        return self._overrides.get("radarr_default_root_folder_path") or None

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        return await request_json(
            self._client, method, f"{self._url}{path}", self._api_key, **kwargs
        )

    async def check_health(self) -> dict:
        if not self.enabled:
            return {"enabled": False, "reachable": False, "version": None}
        result = await check_radarr_connectivity(self._client, self._url, self._api_key)
        return {"enabled": True, "reachable": result["reachable"], "version": result["version"]}

    async def get_quality_profiles(self) -> list[QualityProfile]:
        data = await self._request("GET", "/api/v3/qualityprofile")
        return [QualityProfile(id=p["id"], name=p["name"]) for p in data or []]

    async def get_root_folders(self) -> list[RootFolder]:
        data = await self._request("GET", "/api/v3/rootfolder")
        return [
            RootFolder(id=f.get("id"), path=f["path"], free_space=f.get("freeSpace"))
            for f in data or []
        ]

    async def _queued_movie_ids(self) -> set[int]:
        data = await self._request(
            "GET", "/api/v3/queue", params={"page": 1, "pageSize": 500, "includeMovie": "false"}
        )
        return {r["movieId"] for r in (data or {}).get("records", []) if r.get("movieId")}

    async def lookup_movies(self, tmdb_ids: list[int]) -> dict[int, RadarrMovieState | None]:
        """Per-tmdb_id library state (None = not in Radarr); short-TTL cached."""
        now = time.monotonic()
        results: dict[int, RadarrMovieState | None] = {}
        to_query: list[int] = []
        for tmdb_id in dict.fromkeys(tmdb_ids):
            cached = _cache.get(tmdb_id)
            if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
                results[tmdb_id] = cached[1]
            else:
                to_query.append(tmdb_id)
        if not to_query:
            return results

        semaphore = asyncio.Semaphore(5)

        async def fetch(tmdb_id: int) -> tuple[int, dict | None]:
            async with semaphore:
                data = await self._request("GET", "/api/v3/movie", params={"tmdbId": tmdb_id})
            return tmdb_id, (data[0] if data else None)

        fetched = await asyncio.gather(*(fetch(t) for t in to_query))
        needs_queue = any(item and not item.get("hasFile") for _, item in fetched)
        queued = await self._queued_movie_ids() if needs_queue else set()

        for tmdb_id, item in fetched:
            state = None
            if item is not None:
                state = RadarrMovieState(
                    radarr_id=item["id"],
                    monitored=bool(item.get("monitored")),
                    has_file=bool(item.get("hasFile")),
                    downloading=item["id"] in queued,
                )
            _cache[tmdb_id] = (now, state)
            results[tmdb_id] = state
        return results

    async def add_movie(
        self, tmdb_id: int, quality_profile_id: int, root_folder_path: str, title: str | None = None
    ) -> None:
        try:
            body: dict[str, Any] = dict(
                await self._request("GET", "/api/v3/movie/lookup/tmdb", params={"tmdbId": tmdb_id})
            )
        except IntegrationError:
            if not title:
                raise
            body = {"title": title}
        if body.get("id"):
            raise IntegrationError("This movie has already been added to Radarr", 409)

        body.update(
            tmdbId=tmdb_id,
            qualityProfileId=quality_profile_id,
            rootFolderPath=root_folder_path,
            monitored=True,
            addOptions={"searchForMovie": True},
        )
        await self._request("POST", "/api/v3/movie", json=body)
        invalidate_cache(tmdb_id)


async def check_radarr_connectivity(client: httpx.AsyncClient, url: str, api_key: str) -> dict:
    """Tests a candidate URL/key directly - independent of any configured RadarrClient."""
    if not url:
        return {"reachable": False, "version": None, "detail": "Radarr URL is required"}
    try:
        data = await request_json(client, "GET", f"{url.rstrip('/')}/api/v3/system/status", api_key)
    except IntegrationError as exc:
        return {"reachable": False, "version": None, "detail": exc.detail}
    return {"reachable": True, "version": (data or {}).get("version"), "detail": None}
