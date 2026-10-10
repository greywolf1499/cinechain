"""OMDb ratings client: IMDb rating, Rotten Tomatoes %, and Metacritic score.

Lookups raise ProviderLimitReachedError on API backpressure. Other failures
remain retryable; the compatibility ratings wrapper degrades to None.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TypedDict

import httpx

from app.config import Settings, get_settings
from app.services.provider_budgets import ProviderLimitReachedError

logger = logging.getLogger(__name__)


class OMDbRatings(TypedDict):
    imdb_rating: str | None
    rotten_tomatoes: str | None
    metacritic: str | None


@dataclass(frozen=True)
class OMDbLookup:
    ratings: OMDbRatings | None
    transient: bool


class OMDbClient:
    def __init__(self, client: httpx.AsyncClient, settings: Settings | None = None) -> None:
        self._client = client
        self._settings = settings or get_settings()
        self._overrides: dict[str, str] = {}

    def set_overrides(self, overrides: dict[str, str]) -> None:
        """Admin-configured DB overrides take precedence over .env."""
        self._overrides = overrides

    @property
    def _api_key(self) -> str:
        return self._overrides.get("omdb_api_key") or self._settings.omdb_api_key

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    async def get_ratings_by_title(self, title: str, year: int | None) -> OMDbRatings | None:
        """Looks up a movie by title (+optional year) - OMDb has no bulk-by-id
        endpoint, so this is always a single request per movie. This compatibility
        wrapper preserves the never-raises, ratings-or-None contract."""
        try:
            return (await self.lookup_by_title(title, year)).ratings
        except ProviderLimitReachedError:
            logger.warning("OMDb API limit reached")
            return None

    async def lookup_by_title(self, title: str, year: int | None) -> OMDbLookup:
        """Return ratings and whether a failure may be retried without negative-caching."""
        if not self.enabled or not title:
            return OMDbLookup(ratings=None, transient=False)
        params: dict[str, str] = {"apikey": self._api_key, "t": title, "type": "movie"}
        if year:
            params["y"] = str(year)
        return await self._lookup(params)

    async def lookup_by_imdb_id(self, imdb_id: str) -> OMDbLookup:
        """Look up the exact film, avoiding ambiguous/localized titles."""
        if not self.enabled or not imdb_id:
            return OMDbLookup(ratings=None, transient=False)
        return await self._lookup({"apikey": self._api_key, "i": imdb_id, "type": "movie"})

    async def _lookup(self, params: dict[str, str]) -> OMDbLookup:
        try:
            response = await self._client.get(
                self._settings.omdb_api_base, params=params, timeout=10.0
            )
            if response.status_code in (401, 403, 429):
                raise ProviderLimitReachedError("OMDb daily limit reached")
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError):
            logger.warning("OMDb ratings request failed; leaving it retryable")
            return OMDbLookup(ratings=None, transient=True)
        if not isinstance(data, dict):
            logger.warning("OMDb returned a malformed ratings response")
            return OMDbLookup(ratings=None, transient=True)
        if data.get("Response") != "True":
            error = data.get("Error")
            if isinstance(error, str) and "request limit reached" in error.casefold():
                raise ProviderLimitReachedError("OMDb daily limit reached")
            if error != "Movie not found!":
                logger.warning("OMDb could not provide ratings; leaving it retryable")
            return OMDbLookup(
                ratings=None,
                transient=error != "Movie not found!",
            )
        entries = data.get("Ratings", [])
        if (
            not isinstance(entries, list)
            or any(not isinstance(entry, dict) for entry in entries)
            or any(
                data.get(key) is not None and not isinstance(data[key], str)
                for key in ("imdbRating", "Metascore")
            )
            or any(
                entry.get("Value") is not None and not isinstance(entry["Value"], str)
                for entry in entries
            )
        ):
            logger.warning("OMDb returned malformed score fields; leaving them retryable")
            return OMDbLookup(ratings=None, transient=True)
        return OMDbLookup(ratings=_extract_ratings(data), transient=False)


def _extract_ratings(data: dict) -> OMDbRatings:
    rotten_tomatoes = None
    for entry in data.get("Ratings", []):
        if entry.get("Source") == "Rotten Tomatoes":
            rotten_tomatoes = entry.get("Value")
            break
    imdb_rating = data.get("imdbRating")
    if imdb_rating in (None, "N/A"):
        imdb_rating = None
    metacritic = data.get("Metascore")
    if metacritic in (None, "N/A"):
        metacritic = None
    return OMDbRatings(
        imdb_rating=imdb_rating, rotten_tomatoes=rotten_tomatoes, metacritic=metacritic
    )


async def check_omdb_connectivity(client: httpx.AsyncClient, api_key: str, api_base: str) -> dict:
    """Tests a candidate key directly - independent of any configured OMDbClient."""
    if not api_key:
        return {"reachable": False, "version": None, "detail": "API key is required"}
    try:
        response = await client.get(
            api_base, params={"apikey": api_key, "t": "Inception"}, timeout=10.0
        )
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as exc:
        return {"reachable": False, "version": None, "detail": str(exc)}

    if data.get("Response") == "True":
        return {"reachable": True, "version": None, "detail": None}
    return {"reachable": False, "version": None, "detail": data.get("Error") or "Invalid API key"}
