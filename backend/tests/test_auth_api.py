"""Auth API tests.

DB isolation uses `app.dependency_overrides` keyed on the module-level
`get_session` import (stable across the whole test session) rather than the
config_dir module-reload trick used in test_db.py - that trick only works for
code that re-imports `app.db` fresh inside each test; route modules bind
`get_session` once at import time, so reloading `app.db` later would leave
routes pointing at a stale engine. The `config_dir` fixture is still used here
purely to isolate the persisted session secret.key file per test.
"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app


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


def _register(client, username="alice", password="password123", display_name="Alice"):
    return client.post(
        "/api/auth/register",
        json={"username": username, "password": password,
              "display_name": display_name},
    )


def _login(client, username="alice", password="password123"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_first_user_bootstraps_as_admin(client):
    resp = _register(client)
    assert resp.status_code == 201
    body = resp.json()
    assert body["username"] == "alice"
    assert body["is_admin"] is True


def test_second_registration_without_admin_session_is_rejected(client):
    _register(client)
    resp = _register(client, username="bob", display_name="Bob")
    assert resp.status_code == 401


def test_admin_session_can_register_additional_user(client):
    _register(client)
    assert _login(client).status_code == 200
    resp = _register(client, username="bob", display_name="Bob")
    assert resp.status_code == 201
    assert resp.json()["is_admin"] is False


def test_duplicate_username_returns_409(client):
    _register(client)
    assert _login(client).status_code == 200
    resp = _register(client, username="alice", display_name="Alice Again")
    assert resp.status_code == 409


def test_invalid_credentials_return_401(client):
    _register(client)
    resp = _login(client, password="wrong-password")
    assert resp.status_code == 401


def test_cookie_roundtrip_authenticates_protected_route(client):
    _register(client)
    login_resp = _login(client)
    assert login_resp.status_code == 200
    assert "cinechain_session" in login_resp.cookies

    me_resp = client.get("/api/auth/me")
    assert me_resp.status_code == 200
    assert me_resp.json()["username"] == "alice"


def test_unauthenticated_me_returns_401(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_logout_clears_session(client):
    _register(client)
    _login(client)
    logout_resp = client.post("/api/auth/logout")
    assert logout_resp.status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_users_list_requires_auth(client):
    resp = client.get("/api/users")
    assert resp.status_code == 401


def test_users_list_returns_summaries(client):
    _register(client)
    _login(client)
    resp = client.get("/api/users")
    assert resp.status_code == 200
    assert [u["username"] for u in resp.json()] == ["alice"]
