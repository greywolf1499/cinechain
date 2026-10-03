"""Async TMDB API client: concurrency-limited, retrying, and deliberately free
of any popularity/vote-count/language filtering - CineChain supports every
film TMDB catalogs.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, TypedDict

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class TMDBError(RuntimeError):
    """Raised when a TMDB request fails after exhausting retries."""


class TMDBMovie(TypedDict, total=False):
    id: int
    title: str
    release_date: str | None
    poster_path: str | None
    overview: str | None
    origin_country: list[str]
    original_language: str
    runtime: int | None
    genre_ids: list[int]
    popularity: float | None
    status: str | None


class TMDBCastMember(TypedDict):
    id: int
    name: str
    profile_path: str | None
    character: str | None
    order: int


class TMDBPersonCredit(TypedDict, total=False):
    id: int
    title: str
    release_date: str | None
    poster_path: str | None
    character: str | None
    genre_ids: list[int]
    original_language: str
    popularity: float | None


class TMDBGenre(TypedDict):
    id: int
    name: str


class TMDBClient:
    """Thin async wrapper around the TMDB v3 API."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        settings: Settings | None = None,
        max_concurrency: int = 15,
        max_retries: int = 5,
    ) -> None:
        self._client = client
        self._settings = settings or get_settings()
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._max_retries = max_retries
        self._overrides: dict[str, str] = {}
        # Adaptive pacing state: counts recent 429s, decays on clean requests.
        self._consecutive_429s = 0

    def set_overrides(self, overrides: dict[str, str]) -> None:
        """Admin-configured DB overrides (Phase 10.1) take precedence over .env."""
        self._overrides = overrides

    @property
    def pacing_delay_seconds(self) -> float:
        """Adaptive backoff for BURSTS of uncached requests (e.g. the bridge
        pathfinder's graph expansion) - scales with recently observed 429s so
        the caller slows down proactively instead of only reacting after
        already being rate-limited again."""
        return min(0.2 * self._consecutive_429s, 2.0)

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self._settings.tmdb_api_base}{path}"
        api_key = self._overrides.get(
            "tmdb_api_key") or self._settings.tmdb_api_key
        headers = {"Authorization": f"Bearer {api_key}"}
        backoff = 0.5
        hit_429 = False

        async with self._semaphore:
            response = await self._client.get(url, params=params, headers=headers)
            hit_429 = hit_429 or response.status_code == 429
            for attempt in range(1, self._max_retries + 1):
                if response.status_code not in RETRYABLE_STATUS_CODES:
                    break
                if attempt == self._max_retries:
                    break
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else backoff
                logger.warning(
                    "TMDB %s returned %s (attempt %d/%d), retrying in %.1fs",
                    path,
                    response.status_code,
                    attempt,
                    self._max_retries,
                    delay,
                )
                await asyncio.sleep(delay)
                backoff *= 2
                response = await self._client.get(url, params=params, headers=headers)
                hit_429 = hit_429 or response.status_code == 429

            self._consecutive_429s = (
                min(self._consecutive_429s + 1,
                    10) if hit_429 else max(self._consecutive_429s - 1, 0)
            )

        if response.status_code >= 400:
            raise TMDBError(
                f"TMDB request to {path} failed with {response.status_code}: {response.text}"
            )
        return response.json()

    async def search_movies(self, query: str, page: int = 1) -> dict[str, Any]:
        """Raw TMDB search results - no popularity/vote/language filters applied."""
        return await self._get("/search/movie", params={"query": query, "page": page})

    async def get_movie(self, tmdb_id: int) -> TMDBMovie:
        data = await self._get(f"/movie/{tmdb_id}")
        return _normalize_movie_detail(data)

    async def get_movie_credits(self, tmdb_id: int) -> list[TMDBCastMember]:
        data = await self._get(f"/movie/{tmdb_id}/credits")
        return [
            TMDBCastMember(
                id=member["id"],
                name=member["name"],
                profile_path=member.get("profile_path"),
                character=member.get("character"),
                order=member.get("order", 9999),
            )
            for member in data.get("cast", [])
        ]

    async def get_person_movie_credits(self, person_id: int) -> list[TMDBPersonCredit]:
        data = await self._get(f"/person/{person_id}/movie_credits")
        return [_normalize_person_credit(entry) for entry in data.get("cast", [])]

    async def get_genres(self) -> list[TMDBGenre]:
        data = await self._get("/genre/movie/list")
        return [TMDBGenre(id=g["id"], name=g["name"]) for g in data.get("genres", [])]


def _normalize_movie_detail(data: dict[str, Any]) -> TMDBMovie:
    return TMDBMovie(
        id=data["id"],
        title=data["title"],
        release_date=data.get("release_date") or None,
        poster_path=data.get("poster_path"),
        overview=data.get("overview"),
        origin_country=data.get("origin_country", []),
        original_language=data.get("original_language", ""),
        runtime=data.get("runtime"),
        genre_ids=[g["id"] for g in data.get("genres", [])],
        popularity=data.get("popularity"),
        status=data.get("status"),
    )


def _normalize_person_credit(entry: dict[str, Any]) -> TMDBPersonCredit:
    return TMDBPersonCredit(
        id=entry["id"],
        title=entry.get("title", ""),
        release_date=entry.get("release_date") or None,
        poster_path=entry.get("poster_path"),
        character=entry.get("character"),
        genre_ids=entry.get("genre_ids", []),
        original_language=entry.get("original_language", ""),
        popularity=entry.get("popularity"),
    )


async def check_tmdb_connectivity(
    client: httpx.AsyncClient, api_key: str, api_base: str
) -> dict[str, Any]:
    """Tests a candidate token directly - independent of any configured TMDBClient."""
    if not api_key:
        return {"reachable": False, "version": None, "detail": "API key is required"}
    try:
        response = await client.get(
            f"{api_base}/authentication",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        return {"reachable": False, "version": None, "detail": str(exc)}

    if response.status_code == 200 and response.json().get("success"):
        return {"reachable": True, "version": None, "detail": None}
    detail = None
    if response.headers.get("content-type", "").startswith("application/json"):
        detail = response.json().get("status_message")
    return {"reachable": False, "version": None, "detail": detail or f"HTTP {response.status_code}"}
