"""OMDb ratings client: IMDb rating, Rotten Tomatoes %, and Metacritic score.

Never raises to callers - if OMDb is unconfigured or the request fails, the
caller gets `None` and the rest of the app degrades gracefully (no ratings
shown), exactly like the Jellyfin client's approach.
"""

from __future__ import annotations

from typing import TypedDict

import httpx

from app.config import Settings, get_settings


class OMDbRatings(TypedDict):
    imdb_rating: str | None
    rotten_tomatoes: str | None
    metacritic: str | None


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
        endpoint, so this is always a single request per movie."""
        if not self.enabled or not title:
            return None
        params: dict[str, str] = {
            "apikey": self._api_key, "t": title, "type": "movie"}
        if year:
            params["y"] = str(year)
        try:
            response = await self._client.get(
                self._settings.omdb_api_base, params=params, timeout=10.0
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError:
            return None
        if data.get("Response") != "True":
            return None
        return _extract_ratings(data)


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
