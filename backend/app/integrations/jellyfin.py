"""Jellyfin media-server client: batched TMDB-id lookups with graceful degradation.

Never raises to callers - if Jellyfin is unconfigured or unreachable, every
requested tmdb_id resolves to `on_server: None` so the rest of the app (run
suggestions, bridge results, etc.) never depends on Jellyfin being up.
"""

from __future__ import annotations

import logging
import time

import httpx

from app.config import Settings, get_settings
from app.schemas.integrations import JellyfinItemSummary

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 300

# Module-level so the cache is shared across requests within this single-worker
# process; entries are simply overwritten on refresh, no active eviction - the
# distinct-tmdb-id count for a couple of personal users stays trivially small.
_cache: dict[int, tuple[float, JellyfinItemSummary]] = {}


class JellyfinClient:
    def __init__(self, client: httpx.AsyncClient, settings: Settings | None = None) -> None:
        self._client = client
        self._settings = settings or get_settings()

    @property
    def _enabled(self) -> bool:
        return bool(self._settings.jellyfin_url)

    def _headers(self) -> dict[str, str]:
        if not self._settings.jellyfin_api_key:
            return {}
        return {"X-Emby-Token": self._settings.jellyfin_api_key}

    async def check_health(self) -> dict:
        if not self._enabled:
            return {"enabled": False, "reachable": False, "version": None}
        try:
            response = await self._client.get(
                f"{self._settings.jellyfin_url}/System/Info/Public",
                headers=self._headers(),
                timeout=5.0,
            )
            response.raise_for_status()
            return {
                "enabled": True,
                "reachable": True,
                "version": response.json().get("Version"),
            }
        except httpx.HTTPError as exc:
            logger.warning("Jellyfin health check failed: %s", exc)
            return {"enabled": True, "reachable": False, "version": None}

    async def lookup_movies(self, tmdb_ids: list[int]) -> dict[int, JellyfinItemSummary]:
        if not tmdb_ids:
            return {}
        if not self._enabled:
            logger.warning(
                "Jellyfin lookup skipped - JELLYFIN_URL is not configured")
            return dict.fromkeys(tmdb_ids, JellyfinItemSummary(on_server=None))

        now = time.monotonic()
        results: dict[int, JellyfinItemSummary] = {}
        to_query: list[int] = []
        for tmdb_id in tmdb_ids:
            cached = _cache.get(tmdb_id)
            if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
                results[tmdb_id] = cached[1]
            else:
                to_query.append(tmdb_id)

        if not to_query:
            return results

        try:
            response = await self._client.get(
                f"{self._settings.jellyfin_url}/Items",
                headers=self._headers(),
                params={
                    "IncludeItemTypes": "Movie",
                    "Recursive": "true",
                    "Fields": "ProviderIds",
                    # Best-effort server-side filter; matching below is
                    # authoritative regardless of whether the server honors it.
                    "AnyProviderIdEquals": ",".join(f"Tmdb.{tid}" for tid in to_query),
                },
                timeout=10.0,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            logger.warning(
                "Jellyfin lookup failed, degrading to unknown: %s", exc)
            for tmdb_id in to_query:
                results[tmdb_id] = JellyfinItemSummary(on_server=None)
            return results

        found: dict[int, JellyfinItemSummary] = {}
        for item in payload.get("Items", []):
            tmdb_id = _extract_tmdb_id(item.get("ProviderIds", {}))
            if tmdb_id is None or tmdb_id not in to_query:
                continue
            item_id = item.get("Id")
            found[tmdb_id] = JellyfinItemSummary(
                on_server=True,
                item_id=item_id,
                play_url=f"{self._settings.jellyfin_url}/web/index.html#!/details?id={item_id}",
            )

        for tmdb_id in to_query:
            summary = found.get(tmdb_id, JellyfinItemSummary(on_server=False))
            _cache[tmdb_id] = (now, summary)
            results[tmdb_id] = summary

        return results


def _extract_tmdb_id(provider_ids: dict) -> int | None:
    for key in ("Tmdb", "TMDb", "tmdb"):
        raw = provider_ids.get(key)
        if raw is not None:
            try:
                return int(raw)
            except (TypeError, ValueError):
                return None
    return None
