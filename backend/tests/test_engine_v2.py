"""Challenge Engine V2: engine_version gating, run lock, win/fail conditions,
forfeit, and the Alembic backfill of legacy runs.
"""

import httpx
import pytest
import respx
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine as sa_create_engine
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app
from app.models.run import Run

TMDB_BASE = "https://api.themoviedb.org/3"


@pytest.fixture()
def db_engine(config_dir):
    engine = create_engine(
        f"sqlite:///{config_dir}/app_test.db", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture()
def client(db_engine):
    def override_get_session():
        with Session(db_engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        test_client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password123", "display_name": "Alice"},
        )
        test_client.post("/api/auth/login", json={"username": "alice", "password": "password123"})
        yield test_client
    app.dependency_overrides.clear()


def _mock_movie(tmdb_id: int, year: int, country: str = "US"):
    respx.get(f"{TMDB_BASE}/movie/{tmdb_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": tmdb_id,
                "title": f"Movie {tmdb_id}",
                "release_date": f"{year}-01-01",
                "poster_path": None,
                "overview": "",
                "origin_country": [country],
                "original_language": "en",
                "runtime": 100,
                "genres": [],
            },
        )
    )
    respx.get(f"{TMDB_BASE}/movie/{tmdb_id}/credits").mock(
        return_value=httpx.Response(200, json={"id": tmdb_id, "cast": []})
    )


def _create_run(client, rules_config=None):
    payload = {"name": "Run", "participant_user_ids": []}
    if rules_config is not None:
        payload["rules_config"] = rules_config
    return client.post("/api/runs", json=payload).json()["id"]


def _log(client, run_id, movie_id, **extra):
    return client.post(f"/api/runs/{run_id}/steps", json={"movie_id": movie_id, **extra})


def test_new_runs_are_engine_v2_and_active(client):
    run = client.get(f"/api/runs/{_create_run(client)}").json()
    assert run["engine_version"] == 2
    assert run["status"] == "active"


def test_status_only_accepts_the_four_states(client):
    run_id = _create_run(client)
    assert client.patch(f"/api/runs/{run_id}", json={"status": "abandoned"}).status_code == 422
    for status in ("completed", "failed", "forfeited", "active"):
        resp = client.patch(f"/api/runs/{run_id}", json={"status": status})
        assert resp.status_code == 200
        assert resp.json()["status"] == status


def test_forfeit_sets_reason_and_locks_the_run(client):
    run_id = _create_run(client)
    forfeited = client.patch(f"/api/runs/{run_id}", json={"status": "forfeited"}).json()
    assert forfeited["status"] == "forfeited"
    assert forfeited["completed_at"] is not None
    assert forfeited["status_reason"]

    with respx.mock:
        _mock_movie(1, 1990)
        resp = _log(client, run_id, 1)
    assert resp.status_code == 409
    assert "forfeited" in resp.json()["detail"]["reason"]

    reopened = client.patch(f"/api/runs/{run_id}", json={"status": "active"}).json()
    assert reopened["completed_at"] is None and reopened["status_reason"] is None


def test_win_condition_decades_spanned_completes_run(client):
    run_id = _create_run(
        client,
        {
            "win_condition": {"type": "decades_spanned", "count": 3},
            "wildcards_budget": -1,
            "min_runtime": 0,
        },
    )
    with respx.mock:
        for movie_id, year in ((1, 1985), (2, 1995), (3, 2005)):
            _mock_movie(movie_id, year)
        assert _log(client, run_id, 1, force=True).status_code == 201
        assert _log(client, run_id, 2, force=True).status_code == 201
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "active"
        assert _log(client, run_id, 3, force=True).status_code == 201

    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "completed"
    assert run["completed_at"] is not None
    assert "decades" in run["status_reason"]


def test_fail_condition_max_wildcards_used_fails_run(client):
    run_id = _create_run(
        client,
        {
            "fail_condition": {"type": "max_wildcards_used", "count": 1},
            "wildcards_budget": -1,
            "min_runtime": 0,
        },
    )
    with respx.mock:
        for movie_id in (1, 2, 3):
            _mock_movie(movie_id, 1990)
        assert _log(client, run_id, 1).status_code == 201
        # No shared cast -> a forced (wildcard) link.
        assert _log(client, run_id, 2, force=True).status_code == 201
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "active"
        assert _log(client, run_id, 3, force=True).status_code == 201

    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "failed"
    assert "wildcards" in run["status_reason"]

    with respx.mock:
        _mock_movie(4, 1990)
        assert _log(client, run_id, 4, force=True).status_code == 409


def test_planned_steps_do_not_count_toward_win(client):
    run_id = _create_run(client, {"win_condition": {"type": "movies_watched", "count": 1}})
    with respx.mock:
        _mock_movie(1, 1990)
        planned = _log(client, run_id, 1, status="planned").json()
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "active"
        client.patch(
            f"/api/runs/{run_id}/steps/{planned['id']}", json={"watched_at": "2026-01-01T00:00:00Z"}
        )

    assert client.get(f"/api/runs/{run_id}").json()["status"] == "completed"


def test_rules_edit_preserves_condition_keys(client):
    run_id = _create_run(client, {"win_condition": {"type": "decades_spanned", "count": 3}})
    resp = client.patch(f"/api/runs/{run_id}/rules", json={"wildcards_budget": 5})
    assert resp.status_code == 200
    rules = resp.json()["rules_config"]
    assert rules["wildcards_budget"] == 5
    assert rules["win_condition"] == {"type": "decades_spanned", "count": 3}


@pytest.mark.parametrize(
    "rules",
    [
        {"win_condition": {"type": "nope", "count": 3}},
        {"win_condition": {"type": "decades_spanned", "count": 0}},
        {"fail_condition": {"type": "max_wildcards_used", "count": "3"}},
        {"fail_condition": "max_wildcards_used"},
    ],
)
def test_invalid_conditions_rejected_on_v2_create(client, rules):
    resp = client.post("/api/runs", json={"name": "Bad", "rules_config": rules})
    assert resp.status_code == 422


def test_condition_lists_are_any_of(client):
    run_id = _create_run(
        client,
        {
            "win_condition": [
                {"type": "countries_visited", "count": 9},
                {"type": "movies_watched", "count": 2},
            ],
            "wildcards_budget": -1,
            "min_runtime": 0,
        },
    )
    with respx.mock:
        _mock_movie(1, 1990)
        _mock_movie(2, 1991)
        _log(client, run_id, 1)
        _log(client, run_id, 2, force=True)
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "completed"


def test_legacy_runs_bypass_v2_rules(client, db_engine):
    run_id = _create_run(
        client,
        {
            "win_condition": {"type": "movies_watched", "count": 1},
            "wildcards_budget": -1,
            "min_runtime": 0,
        },
    )
    with Session(db_engine) as session:
        run = session.get(Run, run_id)
        run.engine_version = 1
        session.add(run)
        session.commit()

    with respx.mock:
        _mock_movie(1, 1990)
        _mock_movie(2, 1991)
        assert _log(client, run_id, 1).status_code == 201
        # Win condition is ignored for a legacy run...
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "active"
        # ...and a legacy run stays playable even when marked completed.
        client.patch(f"/api/runs/{run_id}", json={"status": "completed"})
        assert _log(client, run_id, 2, force=True).status_code == 201

    assert client.get(f"/api/runs/{run_id}").json()["engine_version"] == 1


def test_alembic_backfills_legacy_runs_as_version_1(config_dir):
    from app.config import get_settings

    backend_dir = __file__.rsplit("/tests/", 1)[0]
    cfg = Config(f"{backend_dir}/alembic.ini")
    cfg.set_main_option("script_location", f"{backend_dir}/migrations")

    command.upgrade(cfg, "b8c9d0e1f2a3")
    engine = sa_create_engine(get_settings().database_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO runs (id, name, game_type, status, created_at, rules_config) VALUES "
                "('a', 'Active', 'cinechain', 'active', '2026-01-01', '{}'),"
                "('b', 'Gave up', 'cinechain', 'abandoned', '2026-01-01', '{}'),"
                "('c', 'Done', 'cinechain', 'completed', '2026-01-01', '{}')"
            )
        )

    command.upgrade(cfg, "head")
    with engine.begin() as conn:
        rows = {
            r.id: (r.status, r.engine_version)
            for r in conn.execute(text("SELECT id, status, engine_version FROM runs"))
        }
        conn.execute(
            text(
                "INSERT INTO runs (id, name, game_type, status, created_at) "
                "VALUES ('n', 'New', 'cinechain', 'active', '2026-01-01')"
            )
        )
        new_version = conn.execute(text("SELECT engine_version FROM runs WHERE id = 'n'")).scalar()

    assert rows == {"a": ("active", 1), "b": ("forfeited", 1), "c": ("completed", 1)}
    assert new_version == 2
