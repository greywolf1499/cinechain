"""Runs/participants/steps API tests.

Reuses the dependency_overrides pattern from test_auth_api.py (module-level
`get_session` imported once, overridden with a fresh per-test SQLite engine)
plus respx to mock TMDB for step logging - see test_auth_api.py's module
docstring for why the config_dir module-reload trick doesn't work here.
"""

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app

TMDB_BASE = "https://api.themoviedb.org/3"


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


def _register_and_login(client, username, password="password123", display_name=None):
    client.post(
        "/api/auth/register",
        json={
            "username": username,
            "password": password,
            "display_name": display_name or username.title(),
        },
    )
    client.post("/api/auth/login",
                json={"username": username, "password": password})


def _new_client_for(client, username):
    """Same app/engine, independent cookie jar - simulates a second logged-in user."""
    other = TestClient(app)
    other.post("/api/auth/login",
               json={"username": username, "password": "password123"})
    return other


def _mock_movie(tmdb_id: int, title: str, release_date: str = "1999-03-30"):
    return respx.get(f"{TMDB_BASE}/movie/{tmdb_id}").mock(
        return_value=httpx.Response(
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
    )


def test_create_run_with_participants(client):
    # bootstrap admin (alice), then bob as a regular user (admin-gated register)
    _register_and_login(client, "alice")
    alice_id = client.get("/api/auth/me").json()["id"]
    client.post(
        "/api/auth/register",
        json={"username": "bob", "password": "password123", "display_name": "Bob"},
    )
    bob_id = client.get("/api/users").json()[-1]["id"]

    resp = client.post(
        "/api/runs",
        json={"name": "Bacon Run", "game_type": "cinechain",
              "participant_user_ids": [bob_id]},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Bacon Run"
    roles = {p["user_id"]: p["role"] for p in body["participants"]}
    assert roles[alice_id] == "owner"
    assert roles[bob_id] == "member"
    assert body["steps"] == []


def test_non_participant_gets_404(client):
    _register_and_login(client, "alice")
    client.post(
        "/api/auth/register",
        json={"username": "carol", "password": "password123",
              "display_name": "Carol"},
    )
    run_id = client.post(
        "/api/runs", json={"name": "Alice Only", "participant_user_ids": []}
    ).json()["id"]

    carol_client = _new_client_for(client, "carol")
    resp = carol_client.get(f"/api/runs/{run_id}")
    assert resp.status_code == 404


def test_adding_step_denormalizes_metadata_and_sets_logger(client):
    _register_and_login(client, "alice")
    alice_id = client.get("/api/auth/me").json()["id"]
    run_id = client.post("/api/runs", json={"name": "Run", "participant_user_ids": []}).json()[
        "id"
    ]

    with respx.mock:
        _mock_movie(603, "The Matrix", "1999-03-30")
        resp = client.post(f"/api/runs/{run_id}/steps", json={"movie_id": 603})

    assert resp.status_code == 201
    step = resp.json()
    assert step["movie_title"] == "The Matrix"
    assert step["movie_release_year"] == 1999
    assert step["movie_poster_path"] == "/poster.jpg"
    assert step["logged_by_user_id"] == alice_id


def test_deleting_run_cascades(client):
    _register_and_login(client, "alice")
    run_id = client.post("/api/runs", json={"name": "Run", "participant_user_ids": []}).json()[
        "id"
    ]
    with respx.mock:
        _mock_movie(603, "The Matrix")
        client.post(f"/api/runs/{run_id}/steps", json={"movie_id": 603})

    resp = client.delete(f"/api/runs/{run_id}")
    assert resp.status_code == 204
    assert client.get(f"/api/runs/{run_id}").status_code == 404


def test_only_last_step_can_be_deleted(client):
    _register_and_login(client, "alice")
    run_id = client.post("/api/runs", json={"name": "Run", "participant_user_ids": []}).json()[
        "id"
    ]
    with respx.mock:
        _mock_movie(603, "The Matrix", "1999-03-30")
        _mock_movie(604, "Matrix Reloaded", "2003-05-15")
        # force=True still runs validation (to decide wildcard spend), so both
        # movies' credits need a mock even with no shared cast expected.
        respx.get(f"{TMDB_BASE}/movie/603/credits").mock(
            return_value=httpx.Response(200, json={"id": 603, "cast": []})
        )
        respx.get(f"{TMDB_BASE}/movie/604/credits").mock(
            return_value=httpx.Response(200, json={"id": 604, "cast": []})
        )
        step1 = client.post(
            f"/api/runs/{run_id}/steps", json={"movie_id": 603}).json()
        # force=True: this test is about delete ordering, not chain validity.
        step2 = client.post(
            f"/api/runs/{run_id}/steps", json={"movie_id": 604, "force": True}
        ).json()

    resp = client.delete(f"/api/runs/{run_id}/steps/{step1['id']}")
    assert resp.status_code == 409

    resp = client.delete(f"/api/runs/{run_id}/steps/{step2['id']}")
    assert resp.status_code == 204


def test_fetching_run_timeline_makes_zero_http_calls(client):
    _register_and_login(client, "alice")
    run_id = client.post("/api/runs", json={"name": "Run", "participant_user_ids": []}).json()[
        "id"
    ]
    with respx.mock:
        movie_route = _mock_movie(603, "The Matrix")
        client.post(f"/api/runs/{run_id}/steps", json={"movie_id": 603})
        assert movie_route.call_count == 1

    with respx.mock:
        resp = client.get(f"/api/runs/{run_id}")
        assert resp.status_code == 200
        assert len(resp.json()["steps"]) == 1


def test_update_step_accepts_watched_at_and_notes(client):
    _register_and_login(client, "alice")
    run_id = client.post("/api/runs", json={"name": "Run", "participant_user_ids": []}).json()[
        "id"
    ]
    with respx.mock:
        _mock_movie(603, "The Matrix", "1999-03-30")
        step = client.post(
            f"/api/runs/{run_id}/steps", json={"movie_id": 603}).json()

    resp = client.patch(
        f"/api/runs/{run_id}/steps/{step['id']}",
        json={"user_notes": "Rewatched with commentary",
              "watched_at": "2020-05-01T00:00:00"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_notes"] == "Rewatched with commentary"
    assert body["watched_at"].startswith("2020-05-01")


def test_update_step_watched_at_promotes_planned_to_watched(client):
    _register_and_login(client, "alice")
    run_id = client.post("/api/runs", json={"name": "Run", "participant_user_ids": []}).json()[
        "id"
    ]
    with respx.mock:
        _mock_movie(603, "The Matrix", "1999-03-30")
        step = client.post(
            f"/api/runs/{run_id}/steps", json={"movie_id": 603, "status": "planned"}
        ).json()
    assert step["status"] == "planned"
    assert step["watched_at"] is None

    resp = client.patch(
        f"/api/runs/{run_id}/steps/{step['id']}",
        json={"watched_at": "2020-05-01T00:00:00"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "watched"
    assert body["watched_at"].startswith("2020-05-01")


def test_discover_endpoint_flags_movies_already_in_run(client):
    _register_and_login(client, "alice")
    run_id = client.post("/api/runs", json={"name": "Run", "participant_user_ids": []}).json()[
        "id"
    ]
    with respx.mock:
        _mock_movie(1, "Frontier Film")
        respx.get(f"{TMDB_BASE}/movie/1/credits").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 1,
                    "cast": [{"id": 10, "name": "Actor X",
                             "profile_path": None, "character": "Hero", "order": 0}],
                },
            )
        )
        _mock_movie(2, "Already Logged Film")
        step = client.post(
            f"/api/runs/{run_id}/steps", json={"movie_id": 1}).json()
        respx.get(f"{TMDB_BASE}/movie/2/credits").mock(
            return_value=httpx.Response(200, json={"id": 2, "cast": []})
        )
        client.post(
            f"/api/runs/{run_id}/steps",
            json={"movie_id": 2, "force": True, "transition_metadata": None},
        )
        respx.get(f"{TMDB_BASE}/person/10/movie_credits").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 10,
                    "cast": [
                        {"id": 2, "title": "Already Logged Film", "release_date": "2001-01-01",
                            "poster_path": None, "character": "Cameo", "genre_ids": []},
                        {"id": 3, "title": "Brand New Film", "release_date": "2005-01-01",
                            "poster_path": None, "character": "Lead", "genre_ids": []},
                    ],
                },
            )
        )

        resp = client.get(
            f"/api/runs/{run_id}/discover", params={"frontier_movie_id": step["movie_id"]}
        )

    assert resp.status_code == 200
    by_id = {c["movie_id"]: c for c in resp.json()}
    assert by_id[2]["already_in_run"] is True
    assert by_id[2]["existing_step_number"] == 2
    assert by_id[3]["already_in_run"] is False
    assert by_id[3]["existing_step_number"] is None
