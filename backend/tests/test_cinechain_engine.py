"""CineChainEngine tests.

Engine-level tests construct `CineChainEngine` directly against a raw
SQLModel session + TMDBClient (no FastAPI app needed - mirrors
test_cache_repo.py's pattern). The 409/force API-level test reuses the
dependency_overrides pattern from test_runs_api.py.
"""

import json

import httpx
import pytest
import respx
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.engines.cinechain import CineChainEngine
from app.engines.registry import get_engine_class
from app.main import app
from app.models.run import RunStep
from app.schemas.engine import SuggestionFilters
from app.services.tmdb import TMDBClient

TMDB_BASE = "https://api.themoviedb.org/3"


def _session(config_dir) -> Session:
    from app.db import engine as app_engine

    SQLModel.metadata.create_all(app_engine)
    return Session(app_engine)


def _movie_response(tmdb_id: int, title: str, release_date: str = "1999-03-30"):
    return httpx.Response(
        200,
        json={
            "id": tmdb_id,
            "title": title,
            "release_date": release_date,
            "poster_path": "/poster.jpg",
            "overview": "",
            "origin_country": ["US"],
            "original_language": "en",
            "runtime": 100,
            "genres": [],
        },
    )


def _credits_response(cast: list[dict]):
    return httpx.Response(200, json={"id": 0, "cast": cast})


async def test_valid_1hop_pair_returns_connections(config_dir):
    with _session(config_dir) as session, respx.mock:
        respx.get(
            f"{TMDB_BASE}/movie/1").mock(return_value=_movie_response(1, "Movie A"))
        respx.get(
            f"{TMDB_BASE}/movie/2").mock(return_value=_movie_response(2, "Movie B"))
        respx.get(f"{TMDB_BASE}/movie/1/credits").mock(
            return_value=_credits_response(
                [{"id": 10, "name": "Actor X", "profile_path": None,
                    "character": "Hero", "order": 0}]
            )
        )
        respx.get(f"{TMDB_BASE}/movie/2/credits").mock(
            return_value=_credits_response(
                [{"id": 10, "name": "Actor X", "profile_path": None,
                    "character": "Villain", "order": 0}]
            )
        )

        async with httpx.AsyncClient() as client:
            engine = CineChainEngine(session, TMDBClient(client))
            result = await engine.validate_next_step(1, 2)

    assert result.valid is True
    assert len(result.connections) == 1
    connection = result.connections[0]
    assert connection.actor_id == 10
    assert connection.character_in_from == "Hero"
    assert connection.character_in_to == "Villain"


async def test_non_connected_pair_returns_invalid(config_dir):
    with _session(config_dir) as session, respx.mock:
        respx.get(
            f"{TMDB_BASE}/movie/1").mock(return_value=_movie_response(1, "Movie A"))
        respx.get(
            f"{TMDB_BASE}/movie/2").mock(return_value=_movie_response(2, "Movie B"))
        respx.get(f"{TMDB_BASE}/movie/1/credits").mock(
            return_value=_credits_response(
                [{"id": 10, "name": "Actor X", "profile_path": None,
                    "character": "Hero", "order": 0}]
            )
        )
        respx.get(f"{TMDB_BASE}/movie/2/credits").mock(
            return_value=_credits_response(
                [{"id": 20, "name": "Actor Y", "profile_path": None,
                    "character": "Villain", "order": 0}]
            )
        )

        async with httpx.AsyncClient() as client:
            engine = CineChainEngine(session, TMDBClient(client))
            result = await engine.validate_next_step(1, 2)

    assert result.valid is False
    assert result.connections == []


async def test_suggestions_exclude_already_watched(config_dir):
    with _session(config_dir) as session, respx.mock:
        respx.get(
            f"{TMDB_BASE}/movie/1").mock(return_value=_movie_response(1, "Movie A"))
        respx.get(f"{TMDB_BASE}/movie/1/credits").mock(
            return_value=_credits_response(
                [{"id": 10, "name": "Actor X", "profile_path": None,
                    "character": "Hero", "order": 0}]
            )
        )
        respx.get(f"{TMDB_BASE}/person/10/movie_credits").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 10,
                    "cast": [
                        {
                            "id": 1,
                            "title": "Movie A",
                            "release_date": "1999-01-01",
                            "poster_path": None,
                            "character": "Hero",
                            "genre_ids": [],
                            "original_language": "en",
                        },
                        {
                            "id": 2,
                            "title": "Movie B (already logged)",
                            "release_date": "2001-01-01",
                            "poster_path": None,
                            "character": "Sidekick",
                            "genre_ids": [],
                            "original_language": "en",
                        },
                        {
                            "id": 3,
                            "title": "Movie C",
                            "release_date": "2005-01-01",
                            "poster_path": None,
                            "character": "Lead",
                            "genre_ids": [],
                            "original_language": "en",
                        },
                    ],
                },
            )
        )

        async with httpx.AsyncClient() as client:
            engine = CineChainEngine(session, TMDBClient(client))
            suggestions = await engine.get_suggestions(
                current_movie_id=1, exclude_movie_ids=[2], filters=SuggestionFilters()
            )

    movie_ids = {s.movie_id for s in suggestions}
    assert 1 not in movie_ids  # the current movie itself
    assert 2 not in movie_ids  # already logged in the run
    assert 3 in movie_ids


def test_unknown_engine_raises_400():
    with pytest.raises(HTTPException) as exc_info:
        get_engine_class("does-not-exist")
    assert exc_info.value.status_code == 400


async def test_discover_candidates_pools_across_cast_and_tracks_all_connections(config_dir):
    with _session(config_dir) as session, respx.mock:
        respx.get(
            f"{TMDB_BASE}/movie/1").mock(return_value=_movie_response(1, "Frontier Film"))
        respx.get(f"{TMDB_BASE}/movie/1/credits").mock(
            return_value=_credits_response(
                [
                    {"id": 10, "name": "Actor X", "profile_path": None,
                        "character": "Hero", "order": 0},
                    {"id": 20, "name": "Actor Y", "profile_path": None,
                        "character": "Sidekick", "order": 1},
                ]
            )
        )
        respx.get(f"{TMDB_BASE}/person/10/movie_credits").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 10,
                    "cast": [
                        {"id": 2, "title": "Solo Film X", "release_date": "2001-01-01",
                            "poster_path": None, "character": "X in Solo", "genre_ids": [],
                            "original_language": "en", "popularity": 5.0},
                        {"id": 4, "title": "Reunion Film", "release_date": "2010-01-01",
                            "poster_path": None, "character": "X in Reunion", "genre_ids": [],
                            "original_language": "en", "popularity": 9.5},
                    ],
                },
            )
        )
        respx.get(f"{TMDB_BASE}/person/20/movie_credits").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 20,
                    "cast": [
                        {"id": 3, "title": "Solo Film Y", "release_date": "2003-01-01",
                            "poster_path": None, "character": "Y in Solo", "genre_ids": [],
                            "original_language": "en"},
                        {"id": 4, "title": "Reunion Film", "release_date": "2010-01-01",
                            "poster_path": None, "character": "Y in Reunion", "genre_ids": [],
                            "original_language": "en"},
                    ],
                },
            )
        )

        async with httpx.AsyncClient() as client:
            engine = CineChainEngine(session, TMDBClient(client))
            or_results = await engine.discover_candidates(frontier_movie_id=1, mode="or")
            and_results = await engine.discover_candidates(frontier_movie_id=1, mode="and")

    by_id = {c.movie_id: c for c in or_results}
    assert {2, 3, 4} == set(by_id.keys())
    assert 1 not in by_id  # never suggest the frontier movie itself

    reunion = by_id[4]
    assert len(reunion.connections) == 2
    actor_ids = {c.actor_id for c in reunion.connections}
    assert actor_ids == {10, 20}
    by_actor = {c.actor_id: c for c in reunion.connections}
    assert by_actor[10].character_in_frontier == "Hero"
    assert by_actor[10].character_in_candidate == "X in Reunion"
    assert by_actor[20].character_in_frontier == "Sidekick"
    assert by_actor[20].character_in_candidate == "Y in Reunion"
    assert reunion.popularity == 9.5

    solo_x = by_id[2]
    assert len(solo_x.connections) == 1
    assert solo_x.connections[0].actor_id == 10

    # AND mode narrows to only the true co-star reunion (2+ connections).
    and_ids = {c.movie_id for c in and_results}
    assert and_ids == {4}


async def test_compute_stats_counts_countries_decades_and_hops(config_dir):
    with _session(config_dir) as session:
        steps = [
            RunStep(
                run_id="run-1",
                movie_id=1,
                movie_title="Movie A",
                movie_release_year=1999,
                movie_origin_country=json.dumps(["US"]),
            ),
            RunStep(
                run_id="run-1",
                movie_id=2,
                movie_title="Movie B",
                movie_release_year=2003,
                movie_origin_country=json.dumps(["JP"]),
                transition_metadata={"actor_id": 10, "actor_name": "Actor X"},
            ),
            RunStep(
                run_id="run-1",
                movie_id=3,
                movie_title="Movie C",
                movie_release_year=2004,
                movie_origin_country=json.dumps(["JP", "US"]),
                transition_metadata={"actor_id": 10, "actor_name": "Actor X"},
            ),
        ]
        async with httpx.AsyncClient() as client:
            engine = CineChainEngine(session, TMDBClient(client))
            stats = await engine.compute_stats(steps)

    assert stats.total_hops == 2
    assert stats.countries == ["JP", "US"]
    assert stats.decades == [1990, 2000]
    assert stats.keystone_actors[0].actor_id == 10
    assert stats.keystone_actors[0].appearances == 2


@pytest.fixture()
def client(config_dir):
    engine = create_engine(
        f"sqlite:///{config_dir}/app_test.db", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)

    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _register_and_login(client, username="alice"):
    client.post(
        "/api/auth/register",
        json={"username": username, "password": "password123",
              "display_name": username.title()},
    )
    client.post("/api/auth/login",
                json={"username": username, "password": "password123"})


def test_invalid_step_returns_409_but_force_overrides(client):
    _register_and_login(client)
    run_id = client.post(
        "/api/runs", json={"name": "Run", "participant_user_ids": []}).json()["id"]

    with respx.mock:
        respx.get(
            f"{TMDB_BASE}/movie/1").mock(return_value=_movie_response(1, "Movie A"))
        respx.get(f"{TMDB_BASE}/movie/1/credits").mock(
            return_value=_credits_response(
                [{"id": 10, "name": "Actor X", "profile_path": None,
                    "character": "Hero", "order": 0}]
            )
        )
        first = client.post(f"/api/runs/{run_id}/steps", json={"movie_id": 1})
        assert first.status_code == 201

        respx.get(
            f"{TMDB_BASE}/movie/2").mock(return_value=_movie_response(2, "Movie B"))
        respx.get(f"{TMDB_BASE}/movie/2/credits").mock(
            return_value=_credits_response(
                [{"id": 20, "name": "Actor Y", "profile_path": None,
                    "character": "Villain", "order": 0}]
            )
        )

        rejected = client.post(
            f"/api/runs/{run_id}/steps", json={"movie_id": 2})
        assert rejected.status_code == 409
        assert rejected.json()["detail"]["valid"] is False

        forced = client.post(
            f"/api/runs/{run_id}/steps", json={"movie_id": 2, "force": True})
        assert forced.status_code == 201
