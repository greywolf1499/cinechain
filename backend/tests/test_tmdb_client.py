import httpx
import pytest
import respx

from app.services.tmdb import TMDBClient, TMDBError

TMDB_BASE = "https://api.themoviedb.org/3"


async def test_retries_on_429_then_succeeds():
    with respx.mock:
        route = respx.get(f"{TMDB_BASE}/movie/1").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "0"}, json={}),
                httpx.Response(200, json={"id": 1, "title": "Retried Movie", "genres": []}),
            ]
        )
        async with httpx.AsyncClient() as client:
            tmdb = TMDBClient(client, max_retries=3)
            movie = await tmdb.get_movie(1)

    assert route.call_count == 2
    assert movie["title"] == "Retried Movie"


async def test_gives_up_after_exhausting_retries():
    with respx.mock:
        route = respx.get(f"{TMDB_BASE}/movie/2").mock(
            return_value=httpx.Response(500, headers={"Retry-After": "0"}, json={})
        )
        async with httpx.AsyncClient() as client:
            tmdb = TMDBClient(client, max_retries=3)
            with pytest.raises(TMDBError):
                await tmdb.get_movie(2)

    assert route.call_count == 3
