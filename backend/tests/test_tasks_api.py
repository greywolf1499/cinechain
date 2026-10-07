"""Task status API: auth, per-user visibility, polling and the SSE snapshot."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app
from app.services import letterboxd


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


def _login(client, username):
    assert (
        client.post(
            "/api/auth/login", json={"username": username, "password": "password123"}
        ).status_code
        == 200
    )


def _setup_household(client):
    client.post(
        "/api/auth/register",
        json={"username": "alice", "password": "password123", "display_name": "Alice"},
    )
    _login(client, "alice")
    assert (
        client.post(
            "/api/auth/register",
            json={"username": "bob", "password": "password123", "display_name": "Bob"},
        ).status_code
        == 201
    )


def _fake_watchlist(films=None):
    films = (
        films
        if films is not None
        else [{"title": "Amelie", "year": 2001, "slug": "a", "tmdb_id": 194}]
    )

    def _scrape(
        username,
        tmdb_api_key=None,
        max_pages=None,
        no_cache=False,
        progress_callback=None,
        deep=False,
    ):
        if progress_callback:
            progress_callback(
                {"stage": "page_done", "current": len(films), "total": len(films), "message": "ok"}
            )
        return {"is_ranked": False, "total_films": len(films), "films": films}

    return _scrape


def test_tasks_require_login(client):
    assert client.get("/api/tasks").status_code == 401
    assert client.get("/api/tasks/stream?once=true").status_code == 401
    assert client.get("/api/tasks/nope").status_code == 401


def test_users_only_see_their_own_tasks_and_admin_sees_all(client, monkeypatch):
    monkeypatch.setattr(letterboxd, "scrape_letterboxd_watchlist", _fake_watchlist())
    _setup_household(client)
    alice_task = client.post(
        "/api/curated/watchlist/sync", json={"letterboxd_username": "alice_lb"}
    ).json()

    with TestClient(app) as bob_phone:
        _login(bob_phone, "bob")
        bob_task = bob_phone.post(
            "/api/curated/watchlist/sync", json={"letterboxd_username": "bob_lb"}
        ).json()

        assert [t["id"] for t in bob_phone.get("/api/tasks").json()] == [bob_task["id"]]
        assert bob_phone.get(f"/api/tasks/{alice_task['id']}").status_code == 404

    admin_view = {t["id"] for t in client.get("/api/tasks").json()}
    assert admin_view == {alice_task["id"], bob_task["id"]}
    assert client.get(f"/api/tasks/{bob_task['id']}").status_code == 200


def test_post_returns_202_with_task_id_and_polling_shows_result(client, monkeypatch):
    monkeypatch.setattr(letterboxd, "scrape_letterboxd_watchlist", _fake_watchlist())
    _setup_household(client)

    resp = client.post("/api/curated/watchlist/sync", json={"letterboxd_username": "alice_lb"})

    assert resp.status_code == 202
    created = resp.json()
    assert created["name"] == "watchlist_sync"
    assert created["status"] in {"pending", "running", "completed"}
    assert created["created_at"].endswith("Z")
    task = client.get(f"/api/tasks/{created['id']}").json()
    assert task["status"] == "completed"
    assert task["progress_data"]["result"] == {"matched": 1, "total_films": 1}
    assert client.get("/api/tasks", params={"active": True}).json() == []


def test_stream_snapshot_emits_task_events_then_closes(client, monkeypatch):
    monkeypatch.setattr(letterboxd, "scrape_letterboxd_watchlist", _fake_watchlist())
    _setup_household(client)
    task_id = client.post(
        "/api/curated/watchlist/sync", json={"letterboxd_username": "alice_lb"}
    ).json()["id"]

    resp = client.get("/api/tasks/stream", params={"once": True})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    data_lines = [
        line[6:]
        for line in resp.text.splitlines()
        if line.startswith("data: ") and line != "data: {}"
    ]
    events = [json.loads(line) for line in data_lines]
    assert [e["id"] for e in events] == [task_id]
    assert events[0]["status"] == "completed"
    assert "event: task" in resp.text and "event: done" in resp.text


def test_duplicate_watchlist_sync_reuses_the_active_task(client, monkeypatch):
    from app.models.system import SystemTask

    monkeypatch.setattr(letterboxd, "scrape_letterboxd_watchlist", _fake_watchlist())
    _setup_household(client)
    user_id = client.get("/api/auth/me").json()["id"]
    from app.main import app as fastapi_app

    override = next(iter(fastapi_app.dependency_overrides.values()))
    with next(override()) as session:
        running = SystemTask(
            name="watchlist_sync",
            status="running",
            user_id=user_id,
            dedupe_key=f"watchlist_sync:{user_id}:alice_lb",
        )
        session.add(running)
        session.commit()
        running_id = running.id

    resp = client.post("/api/curated/watchlist/sync", json={"letterboxd_username": "alice_lb"})

    assert resp.status_code == 202
    assert resp.json()["id"] == running_id
    assert resp.json()["status"] == "running"  # nothing new was scheduled


def test_failed_scrape_is_a_failed_task_with_error_code(client, monkeypatch):
    def broken(*args, **kwargs):
        raise letterboxd.CloudflareBlock("challenge")

    monkeypatch.setattr(letterboxd, "scrape_letterboxd_watchlist", broken)
    _setup_household(client)
    created = client.post(
        "/api/curated/watchlist/sync", json={"letterboxd_username": "alice_lb"}
    ).json()

    task = client.get(f"/api/tasks/{created['id']}").json()

    assert task["status"] == "failed"
    assert task["error"] == "challenge"
    assert task["progress_data"]["error"]["code"] == "cloudflare_block"


def _seed_task(client, **fields):
    from app.models.system import SystemTask

    with next(app.dependency_overrides[get_session]()) as session:
        task = SystemTask(name="demo", **fields)
        session.add(task)
        session.commit()
        return task.id


def test_cancel_owner_and_admin_permissions(client):
    _setup_household(client)
    alice = client.get("/api/auth/me").json()["id"]
    alice_task = _seed_task(client, user_id=alice, dedupe_key="alice-job", link="/lists")
    with TestClient(app) as bob:
        _login(bob, "bob")
        bob_id = bob.get("/api/auth/me").json()["id"]
        bob_task = _seed_task(bob, user_id=bob_id, status="running")
        assert bob.post(f"/api/tasks/{alice_task}/cancel").status_code == 404
        assert bob.post("/api/tasks/missing/cancel").status_code == 404
        response = bob.post(f"/api/tasks/{bob_task}/cancel")
        assert response.status_code == 200
        assert response.json()["cancel_requested"] is True
        assert response.json()["status"] == "running"
    assert client.post(f"/api/tasks/{bob_task}/cancel").status_code == 200
    own = client.post(f"/api/tasks/{alice_task}/cancel").json()
    assert own["cancel_requested"] is True
    assert own["link"] == "/lists" and own["dedupe_key"] == "alice-job"
    client.post("/api/auth/logout")
    assert client.post(f"/api/tasks/{alice_task}/cancel").status_code == 401


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_finished_task_cancel_is_conflict(client, status):
    _setup_household(client)
    task_id = _seed_task(client, status=status)
    assert client.post(f"/api/tasks/{task_id}/cancel").status_code == 409
    assert client.get(f"/api/tasks/{task_id}").json()["cancel_requested"] is False
