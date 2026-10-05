"""Jellyfin media-server client: batched TMDB-id lookups with graceful degradation.

Never raises to callers - if Jellyfin is unconfigured or unreachable, every
requested tmdb_id resolves to `on_server: None` so the rest of the app (run
suggestions, bridge results, etc.) never depends on Jellyfin being up.
"""

from __future__ import annotations

import logging
import re
import time

import httpx
from sqlmodel import Session

from app.config import Settings, get_settings
from app.models.cache import CachedMovie
from app.schemas.integrations import JellyfinItemSummary
from app.utils.dates import parse_release_year

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
        self._overrides: dict[str, str] = {}

    def set_overrides(self, overrides: dict[str, str]) -> None:
        """Admin-configured DB overrides (Phase 10.1) take precedence over .env."""
        self._overrides = overrides

    @property
    def _url(self) -> str:
        url = self._overrides.get("jellyfin_url") or self._settings.jellyfin_url
        return url.strip().rstrip("/")

    @property
    def _api_key(self) -> str:
        key = self._overrides.get("jellyfin_api_key") or self._settings.jellyfin_api_key
        return key.strip()

    @property
    def _enabled(self) -> bool:
        return bool(self._url)

    def _headers(self) -> dict[str, str]:
        return _auth_headers(self._api_key)

    async def check_health(self) -> dict:
        if not self._enabled:
            return {"enabled": False, "reachable": False, "version": None}
        result = await check_jellyfin_connectivity(self._client, self._url, self._api_key)
        return {"enabled": True, "reachable": result["reachable"], "version": result["version"]}

    async def lookup_movies(
        self, tmdb_ids: list[int], session: Session | None = None
    ) -> dict[int, JellyfinItemSummary]:
        if not tmdb_ids:
            return {}
        if not self._enabled:
            logger.warning("Jellyfin lookup skipped - JELLYFIN_URL is not configured")
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
                f"{self._url}/Items",
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
            logger.warning("Jellyfin lookup failed, degrading to unknown: %s", exc)
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
                play_url=f"{self._url}/web/index.html#!/details?id={item_id}",
            )

        # Local media isn't always tagged with a TMDB provider id (ripped or
        # renamed files, older Jellyfin scans, etc) - fall back to normalized
        # title+year matching for anything the provider-id pass missed, using
        # whatever title/year we already have cached locally (no extra TMDB call).
        still_unmatched = [tid for tid in to_query if tid not in found]
        if still_unmatched and session is not None:
            for tmdb_id in still_unmatched:
                movie = session.get(CachedMovie, tmdb_id)
                if movie is None or not movie.title:
                    continue
                fallback = await self._title_year_fallback(
                    movie.title, parse_release_year(movie.release_date)
                )
                if fallback is not None:
                    found[tmdb_id] = fallback

        for tmdb_id in to_query:
            summary = found.get(tmdb_id, JellyfinItemSummary(on_server=False))
            _cache[tmdb_id] = (now, summary)
            results[tmdb_id] = summary

        return results

    async def _title_year_fallback(
        self, title: str, year: int | None
    ) -> JellyfinItemSummary | None:
        """Secondary match pass: search Jellyfin by title, then confirm the
        best candidate via normalized (punctuation/case-insensitive) title
        equality + a +/-1 year tolerance, since local files are often renamed
        or missing precise release-year metadata."""
        try:
            response = await self._client.get(
                f"{self._url}/Items",
                headers=self._headers(),
                params={
                    "IncludeItemTypes": "Movie",
                    "Recursive": "true",
                    "SearchTerm": title,
                    "Fields": "ProviderIds,ProductionYear",
                },
                timeout=10.0,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError:
            return None

        target = _normalize_title(title)
        for item in payload.get("Items", []):
            if _normalize_title(_strip_display_year(item.get("Name") or "")) != target:
                continue
            candidate_year = item.get("ProductionYear")
            if year and candidate_year and abs(int(candidate_year) - year) > 1:
                continue
            item_id = item.get("Id")
            return JellyfinItemSummary(
                on_server=True,
                item_id=item_id,
                play_url=f"{self._url}/web/index.html#!/details?id={item_id}",
            )
        return None

    async def test_lookup(self, query: str) -> dict:
        """Admin diagnostics: run the exact matching logic above against a
        single ad-hoc title or TMDB id and return the raw candidates found,
        so a mismatch can be visually inspected instead of guessed at."""
        if not self._enabled:
            return {"query_type": "none", "enabled": False, "matches": []}

        query = query.strip()
        is_tmdb_id = query.isdigit()
        params = (
            {
                "IncludeItemTypes": "Movie",
                "Recursive": "true",
                "Fields": "ProviderIds,ProductionYear",
                "AnyProviderIdEquals": f"Tmdb.{query}",
            }
            if is_tmdb_id
            else {
                "IncludeItemTypes": "Movie",
                "Recursive": "true",
                "SearchTerm": query,
                "Fields": "ProviderIds,ProductionYear",
            }
        )
        query_type = "tmdb_id" if is_tmdb_id else "title"
        try:
            response = await self._client.get(
                f"{self._url}/Items", headers=self._headers(), params=params, timeout=10.0
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            return {"query_type": query_type, "enabled": True, "matches": [], "error": str(exc)}
        return {
            "query_type": query_type,
            "enabled": True,
            "matches": [_summarize_item(item) for item in payload.get("Items", [])],
        }


def _auth_headers(api_key: str) -> dict[str, str]:
    """Send every header flavor Jellyfin accepts - newer servers reject the
    legacy X-Emby-Token alone with a 401."""
    if not api_key:
        return {}
    return {
        "Authorization": f'MediaBrowser Token="{api_key}"',
        "X-MediaBrowser-Token": api_key,
        "X-Emby-Token": api_key,
    }


def _normalize_title(value: str) -> str:
    """Lowercase + strip all non-alphanumeric characters, so "Se7en" and
    "Se7en:" both compare equal to "se7en"."""
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _strip_display_year(name: str) -> str:
    """Jellyfin item names often embed the release year, e.g. "The Matrix
    (1999)" - strip it before normalizing so it compares equal to a bare
    cached title like "The Matrix"."""
    return re.sub(r"\s*\(\d{4}\)\s*$", "", name.strip())


def _summarize_item(item: dict) -> dict:
    return {
        "item_id": item.get("Id"),
        "name": item.get("Name"),
        "production_year": item.get("ProductionYear"),
        "provider_ids": item.get("ProviderIds", {}),
    }


def _extract_tmdb_id(provider_ids: dict) -> int | None:
    for key in ("Tmdb", "TMDb", "tmdb"):
        raw = provider_ids.get(key)
        if raw is not None:
            try:
                return int(raw)
            except (TypeError, ValueError):
                return None
    return None


async def check_jellyfin_connectivity(client: httpx.AsyncClient, url: str, api_key: str) -> dict:
    """Tests a candidate URL/token directly - independent of any configured JellyfinClient."""
    if not url:
        return {"reachable": False, "version": None, "detail": "Jellyfin URL is required"}
    headers = _auth_headers(api_key.strip())
    try:
        response = await client.get(
            f"{url.rstrip('/')}/System/Info/Public", headers=headers, timeout=5.0
        )
        response.raise_for_status()
        return {"reachable": True, "version": response.json().get("Version"), "detail": None}
    except httpx.HTTPError as exc:
        return {"reachable": False, "version": None, "detail": str(exc)}
