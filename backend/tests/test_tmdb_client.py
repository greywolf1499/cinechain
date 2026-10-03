import asyncio

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
                httpx.Response(
                    200, json={"id": 1, "title": "Retried Movie", "genres": []}),
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
            return_value=httpx.Response(
                500, headers={"Retry-After": "0"}, json={})
        )
        async with httpx.AsyncClient() as client:
            tmdb = TMDBClient(client, max_retries=3)
            with pytest.raises(TMDBError):
                await tmdb.get_movie(2)

    assert route.call_count == 3


async def test_requests_are_paced_by_global_rate_limit():
    import time

    with respx.mock:
        respx.get(f"{TMDB_BASE}/movie/3").mock(
            return_value=httpx.Response(200, json={"id": 3, "title": "T", "genres": []}))
        async with httpx.AsyncClient() as client:
            tmdb = TMDBClient(client, max_requests_per_second=50.0)
            start = time.monotonic()
            await asyncio.gather(*(tmdb.get_movie(3) for _ in range(11)))
            elapsed = time.monotonic() - start

    # 11 requests at 50 rps need >= 10 intervals of 20ms
    assert elapsed >= 0.18


async def test_persistent_429_raises_rate_limit_error_with_retry_after():
    from app.services.tmdb import TMDBRateLimitError

    with respx.mock:
        respx.get(f"{TMDB_BASE}/movie/4").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "0"}, json={}))
        async with httpx.AsyncClient() as client:
            tmdb = TMDBClient(client, max_retries=2)
            with pytest.raises(TMDBRateLimitError) as excinfo:
                await tmdb.get_movie(4)

    assert excinfo.value.retry_after == 0.0


async def test_get_movie_extracts_overview_and_tagline():
    with respx.mock:
        respx.get(f"{TMDB_BASE}/movie/5").mock(return_value=httpx.Response(
            200, json={"id": 5, "title": "T", "overview": "A plot.", "tagline": "Hook line", "genres": []}))
        async with httpx.AsyncClient() as client:
            movie = await TMDBClient(client).get_movie(5)

    assert movie["overview"] == "A plot."
    assert movie["tagline"] == "Hook line"
