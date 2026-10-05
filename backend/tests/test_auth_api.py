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
        json={"username": username, "password": password, "display_name": display_name},
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


def test_session_cookie_is_an_httponly_jwt(client):
    import jwt

    _register(client)
    resp = _login(client)
    header = resp.headers["set-cookie"].lower()
    assert "httponly" in header and "samesite=lax" in header
    token = client.cookies.get("cinechain_session")
    assert token.count(".") == 2
    claims = jwt.decode(token, options={"verify_signature": False})
    assert {"sub", "iat", "exp", "jti"} <= set(claims)
    assert jwt.get_unverified_header(token)["alg"] == "HS256"


def test_forged_unsigned_and_expired_tokens_are_rejected(client):
    import time

    import jwt

    from app.config import get_settings
    from app.services.security import _get_secret_key

    _register(client)
    user_id = _login(client).json()["id"]
    secret = _get_secret_key(get_settings())

    def me_with(token):
        client.cookies.set("cinechain_session", token)
        return client.get("/api/auth/me").status_code

    now = int(time.time())
    good = {"sub": user_id, "iat": now, "exp": now + 60}
    assert me_with(jwt.encode(good, secret, algorithm="HS256")) == 200
    assert me_with(jwt.encode(good, "x" * 64, algorithm="HS256")) == 401  # wrong key
    assert me_with(jwt.encode(good, None, algorithm="none")) == 401  # alg=none
    assert me_with(jwt.encode({**good, "exp": now - 10}, secret, algorithm="HS256")) == 401
    assert me_with(jwt.encode({"iat": now, "exp": now + 60}, secret, algorithm="HS256")) == 401
    assert me_with("garbage") == 401


def test_two_devices_are_logged_in_simultaneously_and_independently(client, config_dir):
    from fastapi.testclient import TestClient

    from app.main import app

    _register(client, username="partner_a", display_name="Partner A")
    # Partner B's account is created by the (logged-in) admin.
    _login(client, "partner_a")
    assert _register(client, username="partner_b", display_name="Partner B").status_code == 201

    phone_a = client
    with TestClient(app) as phone_b:
        assert _login(phone_a, "partner_a").status_code == 200
        assert _login(phone_b, "partner_b").status_code == 200

        assert phone_a.get("/api/auth/me").json()["username"] == "partner_a"
        assert phone_b.get("/api/auth/me").json()["username"] == "partner_b"

        # A second device for the same account stays valid when the first logs out.
        with TestClient(app) as tablet_a:
            _login(tablet_a, "partner_a")
            assert phone_a.post("/api/auth/logout").status_code == 204
            assert tablet_a.get("/api/auth/me").status_code == 200
        assert phone_b.get("/api/auth/me").status_code == 200


def test_me_renews_an_aging_session_cookie(client):
    import time

    import jwt

    from app.config import get_settings
    from app.services.security import _get_secret_key

    _register(client)
    user_id = _login(client).json()["id"]
    old = int(time.time()) - 8 * 24 * 3600
    stale = jwt.encode(
        {"sub": user_id, "iat": old, "exp": old + 30 * 24 * 3600},
        _get_secret_key(get_settings()),
        algorithm="HS256",
    )
    client.cookies.clear()

    resp = client.get("/api/auth/me", headers={"Cookie": f"cinechain_session={stale}"})

    assert resp.status_code == 200
    assert "cinechain_session=" in resp.headers["set-cookie"]
    assert stale not in resp.headers["set-cookie"]
