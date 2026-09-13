"""OMDbClient tests: ratings extraction, graceful degradation when unconfigured,
and the standalone connectivity probe used by the Settings "Test & Save" flow.
"""

import httpx
import pytest
import respx

from app.config import Settings
from app.integrations.omdb import OMDbClient, check_omdb_connectivity

OMDB_BASE = "https://www.omdbapi.com/"


@pytest.fixture()
def settings() -> Settings:
    return Settings(omdb_api_key="test-key", omdb_api_base=OMDB_BASE)


async def test_get_ratings_by_title_extracts_imdb_rt_and_metacritic(settings):
    with respx.mock:
        respx.get(OMDB_BASE).mock(
            return_value=httpx.Response(
                200,
                json={
                    "Response": "True",
                    "imdbRating": "8.7",
                    "Metascore": "73",
                    "Ratings": [
                        {"Source": "Internet Movie Database", "Value": "8.7/10"},
                        {"Source": "Rotten Tomatoes", "Value": "87%"},
                        {"Source": "Metacritic", "Value": "73/100"},
                    ],
                },
            )
        )
        async with httpx.AsyncClient() as client:
            omdb = OMDbClient(client, settings)
            ratings = await omdb.get_ratings_by_title("Inception", 2010)

    assert ratings == {
        "imdb_rating": "8.7",
        "rotten_tomatoes": "87%",
        "metacritic": "73",
    }


async def test_get_ratings_by_title_returns_none_when_not_found(settings):
    with respx.mock:
        respx.get(OMDB_BASE).mock(
            return_value=httpx.Response(
                200, json={"Response": "False", "Error": "Movie not found!"}
            )
        )
        async with httpx.AsyncClient() as client:
            omdb = OMDbClient(client, settings)
            ratings = await omdb.get_ratings_by_title("Some Obscure Title", None)

    assert ratings is None


async def test_disabled_without_api_key_makes_zero_http_calls():
    with respx.mock:
        async with httpx.AsyncClient() as client:
            omdb = OMDbClient(client, Settings(omdb_api_key=""))
            ratings = await omdb.get_ratings_by_title("Inception", 2010)

    assert ratings is None
    assert omdb.enabled is False


async def test_check_omdb_connectivity_success():
    with respx.mock:
        respx.get(OMDB_BASE).mock(
            return_value=httpx.Response(
                200, json={"Response": "True", "imdbRating": "8.8"})
        )
        async with httpx.AsyncClient() as client:
            result = await check_omdb_connectivity(client, "good-key", OMDB_BASE)

    assert result["reachable"] is True


async def test_check_omdb_connectivity_failure_invalid_key():
    with respx.mock:
        respx.get(OMDB_BASE).mock(
            return_value=httpx.Response(
                200, json={"Response": "False", "Error": "Invalid API key!"})
        )
        async with httpx.AsyncClient() as client:
            result = await check_omdb_connectivity(client, "bad-key", OMDB_BASE)

    assert result["reachable"] is False
    assert result["detail"] == "Invalid API key!"


async def test_check_omdb_connectivity_requires_a_key():
    async with httpx.AsyncClient() as client:
        result = await check_omdb_connectivity(client, "", OMDB_BASE)

    assert result["reachable"] is False
