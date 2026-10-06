import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, timedelta
from threading import Barrier, Lock

import httpx
import pytest
import respx
from sqlalchemy import event
from sqlmodel import Session, SQLModel, select

from app.config import Settings
from app.integrations.omdb import OMDbClient
from app.models.cache import CachedActor, CachedMovie, CachedMovieCast, CachedMovieRating
from app.services import cache_repo
from app.services.tmdb import TMDBClient
from app.utils.ids import utcnow

TMDB_BASE = "https://api.themoviedb.org/3"
OMDB_BASE = "https://www.omdbapi.com/"


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
            assert [c["name"] for c in cast] == ["Keanu Reeves", "Laurence Fishburne"]

            # Second round: fully cached, must not touch the network.
            movie_again = await cache_repo.get_movie(session, tmdb, 603)
            cast_again = await cache_repo.get_movie_cast(session, tmdb, 603)

        assert movie_route.call_count == 1
        assert credits_route.call_count == 1
        assert movie_again.title == "The Matrix"
        assert [c["name"] for c in cast_again] == ["Keanu Reeves", "Laurence Fishburne"]


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


async def test_require_detail_hydrates_cached_movie_stub(config_dir):
    with _session(config_dir) as session, respx.mock:
        session.add(CachedMovie(tmdb_id=603, title="Matrix stub"))
        session.commit()
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
                    "genres": [],
                },
            )
        )

        async with httpx.AsyncClient() as client:
            movie = await cache_repo.get_movie(
                session, TMDBClient(client), 603, require_detail=True
            )

    assert movie.title == "The Matrix"
    assert json.loads(movie.origin_country) == ["US"]
    assert movie_route.call_count == 1


async def test_require_detail_uses_cached_stub_when_tmdb_fails(config_dir):
    with _session(config_dir) as session, respx.mock:
        stub = CachedMovie(tmdb_id=603, title="Matrix stub")
        session.add(stub)
        session.commit()
        movie_route = respx.get(f"{TMDB_BASE}/movie/603").mock(
            return_value=httpx.Response(404, json={"status_message": "Not found"})
        )

        async with httpx.AsyncClient() as client:
            movie = await cache_repo.get_movie(
                session, TMDBClient(client), 603, require_detail=True
            )

    assert movie.title == "Matrix stub"
    assert movie.origin_country is None
    assert movie_route.call_count == 1


def test_upsert_cast_deduplicates_actor_credits_before_limit(config_dir):
    with _session(config_dir) as session:
        session.add(CachedMovie(tmdb_id=603, title="The Matrix"))
        session.add(CachedActor(tmdb_id=6384, name="Keanu Reeves"))
        session.add(CachedActor(tmdb_id=2, name="Laurence Fishburne"))
        session.commit()

        entries = cache_repo.CacheRepo(session).upsert_cast(
            603,
            [
                {
                    "id": 6384,
                    "name": "Keanu Reeves",
                    "profile_path": None,
                    "character": "Neo",
                    "order": 0,
                },
                {
                    "id": 6384,
                    "name": "Keanu Reeves",
                    "profile_path": None,
                    "character": "Thomas Anderson",
                    "order": 1,
                },
                {
                    "id": 2,
                    "name": "Laurence Fishburne",
                    "profile_path": None,
                    "character": "Morpheus",
                    "order": 2,
                },
            ],
            limit=2,
        )
        cast_rows = session.exec(
            select(CachedMovieCast).where(CachedMovieCast.movie_id == 603)
        ).all()

    assert [entry["actor_id"] for entry in entries] == [6384, 2]
    assert len(cast_rows) == 2
    assert entries[0]["character_name"] == "Neo"


def test_concurrent_upsert_cast_recovers_cast_row_race(config_dir):
    from app.db import engine

    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(CachedMovie(tmdb_id=603, title="The Matrix"))
        session.add(CachedActor(tmdb_id=6384, name="Keanu Reeves"))
        session.commit()

    barrier = Barrier(2)
    lock = Lock()
    initial_cast_lookups = 0

    def synchronize_missing_cast_lookups(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ):
        nonlocal initial_cast_lookups
        if "FROM cached_movie_cast" not in statement:
            return
        with lock:
            if initial_cast_lookups >= 2:
                return
            initial_cast_lookups += 1
        barrier.wait(timeout=10)

    member = {
        "id": 6384,
        "name": "Keanu Reeves",
        "profile_path": None,
        "character": "Neo",
        "order": 0,
    }

    def upsert():
        with Session(engine) as session:
            return cache_repo.CacheRepo(session).upsert_cast(603, [member], 15)

    event.listen(engine, "before_cursor_execute", synchronize_missing_cast_lookups)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: upsert(), range(2)))
    finally:
        event.remove(engine, "before_cursor_execute", synchronize_missing_cast_lookups)

    with Session(engine) as session:
        cast_rows = session.exec(
            select(CachedMovieCast).where(CachedMovieCast.movie_id == 603)
        ).all()

    assert all(result[0]["actor_id"] == 6384 for result in results)
    assert len(cast_rows) == 1


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


async def test_movie_ratings_skipped_entirely_when_omdb_not_configured(config_dir):
    """No OMDb key configured -> zero HTTP calls, not even a movie lookup."""
    with _session(config_dir) as session, respx.mock:
        async with httpx.AsyncClient() as client:
            tmdb = TMDBClient(client)
            omdb = OMDbClient(client, Settings(omdb_api_key=""))
            ratings = await cache_repo.get_movie_ratings(session, tmdb, omdb, 603)

    assert ratings is None


async def test_movie_ratings_cached_after_first_fetch_zero_http_on_repeat(config_dir):
    with _session(config_dir) as session, respx.mock:
        movie_route = respx.get(f"{TMDB_BASE}/movie/603").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 603,
                    "title": "The Matrix",
                    "release_date": "1999-03-30",
                    "poster_path": None,
                    "overview": "",
                    "origin_country": ["US"],
                    "original_language": "en",
                    "runtime": 136,
                    "genres": [],
                },
            )
        )
        omdb_route = respx.get(OMDB_BASE).mock(
            return_value=httpx.Response(
                200,
                json={
                    "Response": "True",
                    "imdbRating": "8.7",
                    "Ratings": [{"Source": "Rotten Tomatoes", "Value": "87%"}],
                },
            )
        )

        async with httpx.AsyncClient() as client:
            tmdb = TMDBClient(client)
            omdb = OMDbClient(client, Settings(omdb_api_key="test-key"))

            ratings = await cache_repo.get_movie_ratings(session, tmdb, omdb, 603)
            assert ratings.imdb_rating == "8.7"
            assert ratings.rotten_tomatoes == "87%"
            assert movie_route.call_count == 1
            assert omdb_route.call_count == 1

            # Second call must hit SQLite only.
            ratings_again = await cache_repo.get_movie_ratings(session, tmdb, omdb, 603)

        assert ratings_again.imdb_rating == "8.7"
        assert movie_route.call_count == 1
        assert omdb_route.call_count == 1


async def test_transient_movie_ratings_failure_is_not_cached(config_dir):
    with _session(config_dir) as session, respx.mock:
        respx.get(f"{TMDB_BASE}/movie/603").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 603,
                    "title": "The Matrix",
                    "release_date": "1999-03-30",
                    "overview": "",
                    "tagline": "",
                },
            )
        )
        omdb_route = respx.get(OMDB_BASE).mock(return_value=httpx.Response(500))

        async with httpx.AsyncClient() as client:
            tmdb = TMDBClient(client)
            omdb = OMDbClient(client, Settings(omdb_api_key="test-key"))
            ratings = await cache_repo.get_movie_ratings(session, tmdb, omdb, 603)

        assert ratings is None
        assert session.get(CachedMovieRating, 603) is None
        assert omdb_route.call_count == 1


async def test_stale_negative_movie_ratings_are_refetched(config_dir):
    with _session(config_dir) as session, respx.mock:
        session.add(
            CachedMovie(
                tmdb_id=603,
                title="The Matrix",
                release_date="1999-03-30",
                overview="",
                tagline="",
                origin_country='["US"]',
            )
        )
        session.add(
            CachedMovieRating(
                movie_id=603,
                fetched_at=utcnow() - timedelta(hours=25),
            )
        )
        session.commit()
        omdb_route = respx.get(OMDB_BASE).mock(
            return_value=httpx.Response(
                200,
                json={"Response": "False", "Error": "Movie not found!"},
            )
        )

        async with httpx.AsyncClient() as client:
            tmdb = TMDBClient(client)
            omdb = OMDbClient(client, Settings(omdb_api_key="test-key"))
            ratings = await cache_repo.get_movie_ratings(session, tmdb, omdb, 603)

        assert ratings is not None
        assert ratings.imdb_rating is None
        assert omdb_route.call_count == 1
        assert ratings.fetched_at.replace(tzinfo=UTC) > utcnow() - timedelta(minutes=1)


async def test_fresh_negative_movie_ratings_are_not_refetched(config_dir):
    with _session(config_dir) as session, respx.mock:
        session.add(
            CachedMovie(
                tmdb_id=603,
                title="The Matrix",
                release_date="1999-03-30",
                overview="",
                tagline="",
            )
        )
        session.add(
            CachedMovieRating(
                movie_id=603,
                fetched_at=utcnow() - timedelta(hours=1),
            )
        )
        session.commit()
        omdb_route = respx.get(OMDB_BASE).mock(
            return_value=httpx.Response(200, json={"Response": "True", "imdbRating": "8.7"})
        )

        async with httpx.AsyncClient() as client:
            tmdb = TMDBClient(client)
            omdb = OMDbClient(client, Settings(omdb_api_key="test-key"))
            ratings = await cache_repo.get_movie_ratings(session, tmdb, omdb, 603)

        assert ratings is not None
        assert ratings.imdb_rating is None
        assert omdb_route.call_count == 0


async def test_movie_imdb_id_is_preferred_and_preserved(config_dir):
    with _session(config_dir) as session, respx.mock:
        repo = cache_repo.CacheRepo(session)
        movie = repo.upsert_movie(
            {
                "id": 603,
                "title": "Localized title",
                "imdb_id": "tt0133093",
                "origin_country": ["US"],
            }
        )
        assert movie.imdb_id == "tt0133093"
        repo.upsert_movie({"id": 603, "title": "Localized title", "imdb_id": None})
        assert session.get(CachedMovie, 603).imdb_id == "tt0133093"
        route = respx.get(OMDB_BASE).mock(
            return_value=httpx.Response(200, json={"Response": "True", "imdbRating": "8.7"})
        )
        async with httpx.AsyncClient() as client:
            rating = await cache_repo.get_movie_ratings(
                session, TMDBClient(client), OMDbClient(client, Settings(omdb_api_key="test")), 603
            )
        assert rating.imdb_rating == "8.7"
        assert route.calls[0].request.url.params["i"] == "tt0133093"
        assert "t" not in route.calls[0].request.url.params


@pytest.mark.parametrize("imdb_id", ["tt0133093", None])
async def test_stub_detail_hydrated_before_ratings_lookup(config_dir, imdb_id):
    with _session(config_dir) as session, respx.mock:
        session.add(CachedMovie(tmdb_id=603, title="Stub", release_date="1999-01-01"))
        session.commit()
        detail = respx.get(f"{TMDB_BASE}/movie/603").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 603,
                    "title": "The Matrix",
                    "release_date": "1999-03-30",
                    "imdb_id": imdb_id,
                    "origin_country": ["US"],
                },
            )
        )
        route = respx.get(OMDB_BASE).mock(
            return_value=httpx.Response(200, json={"Response": "True", "imdbRating": "8.7"})
        )
        async with httpx.AsyncClient() as client:
            await cache_repo.get_movie_ratings(
                session, TMDBClient(client), OMDbClient(client, Settings(omdb_api_key="test")), 603
            )
        assert detail.call_count == 1
        params = route.calls[0].request.url.params
        if imdb_id:
            assert params["i"] == imdb_id and "t" not in params
        else:
            assert params["t"] == "The Matrix" and params["y"] == "1999" and "i" not in params
