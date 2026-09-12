"""Run ruleset enforcement tests: repeats, consecutive-actor, runtime,
wildcard budget, and the plan/watch (mark-watched) lifecycle.
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


def _register_and_login(client, username="alice"):
    client.post(
        "/api/auth/register",
        json={"username": username, "password": "password123",
              "display_name": username.title()},
    )
    client.post("/api/auth/login",
                json={"username": username, "password": "password123"})


def _mock_movie(tmdb_id: int, title: str, runtime: int = 100, release_date: str = "1999-03-30"):
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
                "runtime": runtime,
                "genres": [],
            },
        )
    )


def _mock_credits(tmdb_id: int, cast: list[dict]):
    return respx.get(f"{TMDB_BASE}/movie/{tmdb_id}/credits").mock(
        return_value=httpx.Response(200, json={"id": tmdb_id, "cast": cast})
    )


def _cast_member(actor_id: int, name: str, order: int = 0):
    return {"id": actor_id, "name": name, "profile_path": None, "character": "Role", "order": order}


def _create_run(client, rules_config=None):
    payload = {"name": "Run", "participant_user_ids": []}
    if rules_config is not None:
        payload["rules_config"] = rules_config
    return client.post("/api/runs", json=payload).json()["id"]


def test_run_defaults_to_standard_rules(client):
    _register_and_login(client)
    run_id = _create_run(client)
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["rules_config"]["preset"] == "standard"
    assert run["rules_config"]["allow_repeats"] == "strict"
    assert run["rules_config"]["wildcards_budget"] == 2


def test_strict_repeat_rejected_without_force(client):
    _register_and_login(client)
    run_id = _create_run(
        client, {"allow_repeats": "strict", "wildcards_budget": 2})
    with respx.mock:
        _mock_movie(603, "The Matrix")
        _mock_credits(603, [])
        first = client.post(
            f"/api/runs/{run_id}/steps", json={"movie_id": 603})
        assert first.status_code == 201

        again = client.post(
            f"/api/runs/{run_id}/steps", json={"movie_id": 603})
        assert again.status_code == 409
        assert "already watched" in again.json()["detail"]["reason"].lower()


def test_penalty_repeat_flags_metadata_instead_of_rejecting(client):
    _register_and_login(client)
    run_id = _create_run(
        client, {"allow_repeats": "penalty", "wildcards_budget": 2})
    with respx.mock:
        _mock_movie(603, "The Matrix")
        # Non-empty cast: a movie always shares its own cast with itself, so
        # the chain-link check naturally passes and only the repeat rule
        # (under test here) is exercised.
        _mock_credits(603, [_cast_member(100, "Keanu Reeves")])
        client.post(f"/api/runs/{run_id}/steps", json={"movie_id": 603})
        again = client.post(
            f"/api/runs/{run_id}/steps", json={"movie_id": 603})

    assert again.status_code == 201
    assert again.json()["transition_metadata"]["repeat_penalty"] is True


def test_no_consecutive_actor_rule_rejects_same_connector_twice(client):
    _register_and_login(client)
    run_id = _create_run(
        client, {"no_consecutive_actor": True, "wildcards_budget": 2})
    with respx.mock:
        _mock_movie(1, "Movie A")
        _mock_movie(2, "Movie B")
        _mock_movie(3, "Movie C")
        _mock_credits(1, [_cast_member(100, "Actor X")])
        _mock_credits(2, [_cast_member(100, "Actor X"),
                      _cast_member(200, "Actor Y")])
        _mock_credits(3, [_cast_member(100, "Actor X")])

        client.post(f"/api/runs/{run_id}/steps", json={"movie_id": 1})
        step2 = client.post(
            f"/api/runs/{run_id}/steps",
            json={"movie_id": 2, "transition_metadata": {
                "actor_id": 100, "actor_name": "Actor X"}},
        )
        assert step2.status_code == 201

        # Movie 3 shares actor 100 with movie 2 - but that's the SAME actor
        # used to connect movie 1 -> movie 2, which is disallowed back-to-back.
        step3 = client.post(
            f"/api/runs/{run_id}/steps",
            json={"movie_id": 3, "transition_metadata": {
                "actor_id": 100, "actor_name": "Actor X"}},
        )
        assert step3.status_code == 409
        assert "consecutive" in step3.json()["detail"]["reason"].lower()


def test_runtime_below_minimum_rejected(client):
    _register_and_login(client)
    run_id = _create_run(client, {"min_runtime": 40, "wildcards_budget": 2})
    with respx.mock:
        _mock_movie(50, "Short Film", runtime=20)
        resp = client.post(f"/api/runs/{run_id}/steps", json={"movie_id": 50})

    assert resp.status_code == 409
    assert "minimum" in resp.json()["detail"]["reason"].lower()


def test_force_spends_wildcard_budget_and_runs_out(client):
    _register_and_login(client)
    run_id = _create_run(client, {"min_runtime": 40, "wildcards_budget": 1})
    with respx.mock:
        _mock_movie(50, "Short Film A", runtime=20)
        _mock_movie(51, "Short Film B", runtime=20)
        _mock_credits(50, [])
        _mock_credits(51, [])

        first = client.post(
            f"/api/runs/{run_id}/steps", json={"movie_id": 50, "force": True}
        )
        assert first.status_code == 201
        assert first.json()["transition_metadata"]["wildcard_used"] is True

        run = client.get(f"/api/runs/{run_id}").json()
        assert run["rules_config"]["wildcards_budget"] == 0

        second = client.post(
            f"/api/runs/{run_id}/steps", json={"movie_id": 51, "force": True}
        )
        assert second.status_code == 409
        assert "wildcard" in second.json()["detail"]["reason"].lower()


def test_unlimited_wildcards_never_exhausted(client):
    _register_and_login(client)
    run_id = _create_run(client, {"min_runtime": 40, "wildcards_budget": -1})
    with respx.mock:
        _mock_movie(50, "Short Film", runtime=20)
        resp = client.post(
            f"/api/runs/{run_id}/steps", json={"movie_id": 50, "force": True})

    assert resp.status_code == 201
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["rules_config"]["wildcards_budget"] == -1


def test_planned_step_has_null_watched_at(client):
    _register_and_login(client)
    run_id = _create_run(client)
    with respx.mock:
        _mock_movie(603, "The Matrix")
        resp = client.post(
            f"/api/runs/{run_id}/steps", json={"movie_id": 603, "status": "planned"}
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "planned"
    assert body["watched_at"] is None


def test_watched_step_defaults_watched_at_to_now(client):
    _register_and_login(client)
    run_id = _create_run(client)
    with respx.mock:
        _mock_movie(603, "The Matrix")
        resp = client.post(f"/api/runs/{run_id}/steps", json={"movie_id": 603})

    assert resp.json()["status"] == "watched"
    assert resp.json()["watched_at"] is not None


def test_mark_watched_promotes_planned_step(client):
    _register_and_login(client)
    run_id = _create_run(client)
    with respx.mock:
        _mock_movie(603, "The Matrix")
        step = client.post(
            f"/api/runs/{run_id}/steps", json={"movie_id": 603, "status": "planned"}
        ).json()

    resp = client.patch(
        f"/api/runs/{run_id}/steps/{step['id']}/mark-watched",
        json={"watched_at": "2026-01-15T00:00:00",
              "user_notes": "Finally watched it!"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "watched"
    assert body["watched_at"] is not None
    assert body["user_notes"] == "Finally watched it!"


def test_mark_watched_rejects_already_watched_step(client):
    _register_and_login(client)
    run_id = _create_run(client)
    with respx.mock:
        _mock_movie(603, "The Matrix")
        step = client.post(
            f"/api/runs/{run_id}/steps", json={"movie_id": 603}).json()

    resp = client.patch(
        f"/api/runs/{run_id}/steps/{step['id']}/mark-watched", json={})
    assert resp.status_code == 409
