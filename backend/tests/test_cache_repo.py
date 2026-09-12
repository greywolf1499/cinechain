import json

import httpx
import respx
from sqlmodel import Session, SQLModel

from app.services import cache_repo
from app.services.tmdb import TMDBClient

TMDB_BASE = "https://api.themoviedb.org/3"


def _session(config_dir) -> Session:
    from app.db import engine

    SQLModel.metadata.create_all(engine)
    return Session(engine)


async def test_movie_and_cast_cached_after_first_fetch_zero_http_on_repeat(config_dir):
    """Second call for the same movie/cast must hit SQLite only - 0 outbound HTTP."""
    with _session(config_dir) as session, respx.mock:
        movie_route = respx.get(f"{TMDB_BASE}/movie/603").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 603,
                    "title": "The Matrix",
                    "release_date": "1999-03-30",
                    "poster_path": "/poster.jpg",
                    "overview": "A hacker learns the truth.",
                    "origin_country": ["US"],
                    "original_language": "en",
                    "runtime": 136,
                    "genres": [{"id": 28, "name": "Action"}],
                },
            )
        )
        credits_route = respx.get(f"{TMDB_BASE}/movie/603/credits").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 603,
                    "cast": [
                        {
                            "id": 6384,
                            "name": "Keanu Reeves",
                            "profile_path": "/kr.jpg",
                            "character": "Neo",
                            "order": 0,
                        },
                        {
                            "id": 2,
                            "name": "Laurence Fishburne",
                            "profile_path": None,
                            "character": "Morpheus",
                            "order": 1,
                        },
                    ],
                },
            )
        )

        async with httpx.AsyncClient() as client:
            tmdb = TMDBClient(client)

            await cache_repo.get_movie(session, tmdb, 603)
            cast = await cache_repo.get_movie_cast(session, tmdb, 603)
            assert movie_route.call_count == 1
            assert credits_route.call_count == 1
            assert [c["name"] for c in cast] == [
                "Keanu Reeves", "Laurence Fishburne"]

            # Second round: fully cached, must not touch the network.
            movie_again = await cache_repo.get_movie(session, tmdb, 603)
            cast_again = await cache_repo.get_movie_cast(session, tmdb, 603)

        assert movie_route.call_count == 1
        assert credits_route.call_count == 1
        assert movie_again.title == "The Matrix"
        assert [c["name"] for c in cast_again] == [
            "Keanu Reeves", "Laurence Fishburne"]


async def test_obscure_regional_movie_ingested_without_filtering(config_dir):
    """No popularity/vote/language filtering: an obscure 1953 Japanese drama caches cleanly."""
    with _session(config_dir) as session, respx.mock:
        respx.get(f"{TMDB_BASE}/movie/12345").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 12345,
                    "title": "東京物語",
                    "release_date": "1953-11-03",
                    "poster_path": None,
                    "overview": "",
                    "origin_country": ["JP"],
                    "original_language": "ja",
                    "runtime": 136,
                    "genres": [{"id": 18, "name": "Drama"}],
                },
            )
        )

        async with httpx.AsyncClient() as client:
            tmdb = TMDBClient(client)
            movie = await cache_repo.get_movie(session, tmdb, 12345)

    assert movie.title == "東京物語"
    assert movie.original_language == "ja"
    assert movie.poster_path is None
    assert json.loads(movie.origin_country) == ["JP"]


async def test_actor_credits_upsert_movie_stubs_without_marking_cast_fetched(config_dir):
    """Movies discovered via a person's filmography stay cast-unfetched until
    `get_movie_cast` runs for them directly."""
    with _session(config_dir) as session, respx.mock:
        credits_route = respx.get(f"{TMDB_BASE}/person/500/movie_credits").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 500,
                    "cast": [
                        {
                            "id": 999,
                            "title": "Some Other Film",
                            "release_date": "2001-01-01",
                            "poster_path": None,
                            "character": "Extra",
                            "genre_ids": [18],
                            "original_language": "fr",
                        }
                    ],
                },
            )
        )

        async with httpx.AsyncClient() as client:
            tmdb = TMDBClient(client)
            movies = await cache_repo.get_actor_credits(session, tmdb, 500)
            assert credits_route.call_count == 1
            assert len(movies) == 1
            assert movies[0].title == "Some Other Film"
            assert movies[0].cast_fetched_at is None

            # Second call for the same actor must not re-hit TMDB.
            await cache_repo.get_actor_credits(session, tmdb, 500)
        assert credits_route.call_count == 1
