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

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class TMDBRateLimitError(TMDBError):
    """TMDB kept answering 429 after the client's own retries; callers that can
    afford to wait (e.g. the bridge solver) should pause and try again."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message, status_code=429)
        self.retry_after = retry_after


class TMDBNotFoundError(TMDBError):
    """TMDB has no such resource (HTTP 404)."""


class TMDBMovie(TypedDict, total=False):
    id: int
    title: str
    release_date: str | None
    poster_path: str | None
    overview: str | None
    tagline: str | None
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


class TMDBDirector(TypedDict, total=False):
    id: int
    name: str
    gender: int  # TMDB: 0 unspecified, 1 female, 2 male, 3 non-binary


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
        max_requests_per_second: float = 35.0,
    ) -> None:
        self._client = client
        self._settings = settings or get_settings()
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._max_retries = max_retries
        self._overrides: dict[str, str] = {}
        # Adaptive pacing state: counts recent 429s, decays on clean requests.
        self._consecutive_429s = 0
        # Global request-start pacing (TMDB tolerates ~50 rps; stay below it).
        self._min_interval = 1.0 / max_requests_per_second
        self._next_slot = 0.0

    async def _send(
        self, url: str, params: dict[str, Any] | None, headers: dict[str, str]
    ) -> httpx.Response:
        """Every outbound request (including retries) reserves a start slot."""
        now = asyncio.get_running_loop().time()
        slot = max(now, self._next_slot)
        self._next_slot = slot + self._min_interval
        if slot > now:
            await asyncio.sleep(slot - now)
        return await self._client.get(url, params=params, headers=headers)

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
            response = await self._send(url, params, headers)
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
                response = await self._send(url, params, headers)
                hit_429 = hit_429 or response.status_code == 429

            self._consecutive_429s = (
                min(self._consecutive_429s + 1,
                    10) if hit_429 else max(self._consecutive_429s - 1, 0)
            )

        if response.status_code >= 400:
            message = f"TMDB request to {path} failed with {response.status_code}: {response.text}"
            if response.status_code == 429:
                raw_retry_after = response.headers.get("Retry-After")
                try:
                    retry_after = float(raw_retry_after) if raw_retry_after else None
                except ValueError:
                    retry_after = None
                raise TMDBRateLimitError(message, retry_after)
            if response.status_code == 404:
                raise TMDBNotFoundError(message, status_code=404)
            raise TMDBError(message, status_code=response.status_code)
        return response.json()

    async def search_movies(
        self, query: str, page: int = 1, year: int | None = None
    ) -> dict[str, Any]:
        """Raw TMDB search results - no popularity/vote/language filters applied.
        `year` narrows to that primary release year (used by the diary resolver)."""
        params: dict[str, Any] = {"query": query, "page": page}
        if year is not None:
            params["primary_release_year"] = year
        return await self._get("/search/movie", params=params)

    async def get_poster_bytes(
        self, poster_path: str, size: str = "w185", max_bytes: int = 2 * 1024 * 1024
    ) -> bytes | None:
        """A poster's raw bytes from the TMDB image CDN (no API key, not rate limited
        like the API). None when it can't be fetched or is implausibly large."""
        url = f"{self._settings.tmdb_image_base}/{size}/{poster_path.lstrip('/')}"
        try:
            response = await self._client.get(url, follow_redirects=True)
        except httpx.HTTPError:
            return None
        if response.status_code != 200 or len(response.content) > max_bytes:
            return None
        return response.content

    async def discover_movies(self, pages: int = 1, **params: Any) -> list[TMDBPersonCredit]:
        """`/discover/movie`, most popular first, as credit-shaped stubs. `params` are raw
        TMDB filters (`with_origin_country`, `primary_release_date.gte`, `with_genres`...)."""
        query: dict[str, Any] = {
            "sort_by": "popularity.desc", "include_adult": "false", "include_video": "false",
            **params,
        }
        stubs: list[TMDBPersonCredit] = []
        for page in range(1, pages + 1):
            data = await self._get("/discover/movie", params={**query, "page": page})
            stubs.extend(_normalize_person_credit(entry) for entry in data.get("results", []))
            if page >= data.get("total_pages", 1):
                break
        return stubs

    async def get_related_movies(self, tmdb_id: int) -> list[TMDBPersonCredit]:
        """TMDB's recommendations for a film, then its "similar" titles (deduped)."""
        seen: set[int] = set()
        related: list[TMDBPersonCredit] = []
        for path in (f"/movie/{tmdb_id}/recommendations", f"/movie/{tmdb_id}/similar"):
            try:
                data = await self._get(path)
            except TMDBNotFoundError:
                continue
            for entry in data.get("results", []):
                if entry["id"] not in seen and entry["id"] != tmdb_id:
                    seen.add(entry["id"])
                    related.append(_normalize_person_credit(entry))
        return related

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

    async def get_movie_directors(self, tmdb_id: int) -> list[TMDBDirector]:
        """Crew members with job "Director" (the credits payload carries cast and crew)."""
        data = await self._get(f"/movie/{tmdb_id}/credits")
        seen: set[int] = set()
        directors: list[TMDBDirector] = []
        for member in data.get("crew", []):
            if member.get("job") == "Director" and member["id"] not in seen:
                seen.add(member["id"])
                directors.append(TMDBDirector(
                    id=member["id"], name=member.get("name", ""),
                    gender=member.get("gender", 0)))
        return directors

    async def get_person_movie_credits(self, person_id: int) -> list[TMDBPersonCredit]:
        data = await self._get(f"/person/{person_id}/movie_credits")
        return [_normalize_person_credit(entry) for entry in data.get("cast", [])]

    async def get_person_directed_credits(self, person_id: int) -> list[TMDBPersonCredit]:
        """A person's films as *director* (crew entries with job "Director")."""
        data = await self._get(f"/person/{person_id}/movie_credits")
        seen: set[int] = set()
        credits_: list[TMDBPersonCredit] = []
        for entry in data.get("crew", []):
            if entry.get("job") == "Director" and entry["id"] not in seen:
                seen.add(entry["id"])
                credits_.append(_normalize_person_credit(entry))
        return credits_

    async def get_genres(self) -> list[TMDBGenre]:
        data = await self._get("/genre/movie/list")
        return [TMDBGenre(id=g["id"], name=g["name"]) for g in data.get("genres", [])]


def _normalize_movie_detail(data: dict[str, Any]) -> TMDBMovie:
    return TMDBMovie(
        id=data["id"],
        title=data["title"],
        release_date=data.get("release_date") or None,
        poster_path=data.get("poster_path"),
        overview=(data.get("overview") or "").strip(),
        tagline=(data.get("tagline") or "").strip(),
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
