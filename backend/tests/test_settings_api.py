"""Phase 10.1: dynamic in-app settings & integration management.

Covers admin-only gating, GET/PATCH masking + override/clear semantics, the
test-tmdb/test-jellyfin connectivity probes, and (end-to-end) that a saved
override is actually picked up by the shared TMDBClient on the very next
request - not just persisted to the DB.
"""

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app

TMDB_BASE = "https://api.themoviedb.org/3"
JELLYFIN_BASE = "http://jellyfin.test"
OMDB_BASE = "https://www.omdbapi.com/"


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
        json={"username": username, "password": "password123", "display_name": username.title()},
    )
    client.post("/api/auth/login", json={"username": username, "password": "password123"})


def test_non_admin_is_forbidden_from_all_settings_routes(client):
    _register_and_login(client)  # first user - is_admin True
    client.post(
        "/api/auth/register",
        json={"username": "bob", "password": "password123", "display_name": "Bob"},
    )
    client.post("/api/auth/login", json={"username": "bob", "password": "password123"})

    assert client.get("/api/settings/integrations").status_code == 403
    assert client.patch("/api/settings/integrations", json={"tmdb_api_key": "x"}).status_code == 403
    assert (
        client.post("/api/settings/integrations/test-tmdb", json={"tmdb_api_key": "x"}).status_code
        == 403
    )


def test_admin_get_reflects_no_overrides_by_default(client):
    _register_and_login(client)
    body = client.get("/api/settings/integrations").json()
    assert body["tmdb_configured"] is False
    assert body["tmdb_api_key_masked"] is None
    assert body["jellyfin_configured"] is False
    assert body["jellyfin_api_key_masked"] is None
    assert body["omdb_configured"] is False
    assert body["omdb_api_key_masked"] is None
    assert body["tvtropes_enabled"] is False


def test_tvtropes_can_be_enabled_and_disabled_as_an_admin(client):
    _register_and_login(client)
    enabled = client.patch("/api/settings/integrations", json={"tvtropes_enabled": True})
    assert enabled.status_code == 200
    assert enabled.json()["tvtropes_enabled"] is True
    disabled = client.patch("/api/settings/integrations", json={"tvtropes_enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["tvtropes_enabled"] is False


def test_omdb_soft_cap_saved_validated_and_auto_overrides_env(client, monkeypatch):
    _register_and_login(client)
    assert client.get("/api/settings/integrations").json()["omdb_soft_cap"] == 0
    response = client.patch("/api/settings/integrations", json={"omdb_soft_cap": 2500})
    assert response.status_code == 200 and response.json()["omdb_soft_cap"] == 2500
    assert client.get("/api/system/cache/health").json()["budgets"][0]["limit"] == 2500
    assert client.patch("/api/settings/integrations", json={"omdb_soft_cap": -1}).status_code == 422
    from app.config import get_settings

    monkeypatch.setenv("OMDB_SOFT_CAP", "100")
    get_settings.cache_clear()
    response = client.patch("/api/settings/integrations", json={"omdb_soft_cap": 0})
    assert response.status_code == 200 and response.json()["omdb_soft_cap"] == 0
    assert client.get("/api/system/cache/health").json()["budgets"][0]["remaining"] is None
    response = client.patch("/api/settings/integrations", json={"omdb_soft_cap": None})
    assert response.json()["omdb_soft_cap"] == 100


def test_admin_patch_sets_and_masks_override(client):
    _register_and_login(client)
    resp = client.patch(
        "/api/settings/integrations",
        json={
            "tmdb_api_key": "abcdefgh1234",
            "jellyfin_url": "http://jf.local",
            "jellyfin_api_key": "supersecretwxyz",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tmdb_configured"] is True
    assert body["tmdb_api_key_masked"] == "****1234"
    assert body["jellyfin_url"] == "http://jf.local"
    assert body["jellyfin_configured"] is True
    assert body["jellyfin_api_key_masked"] == "****wxyz"

    # Persisted - a fresh GET reflects the same masked values.
    get_body = client.get("/api/settings/integrations").json()
    assert get_body == body


def test_admin_patch_empty_string_clears_override(client):
    _register_and_login(client)
    client.patch("/api/settings/integrations", json={"tmdb_api_key": "abcdefgh1234"})
    resp = client.patch("/api/settings/integrations", json={"tmdb_api_key": ""})
    assert resp.status_code == 200
    body = resp.json()
    assert body["tmdb_configured"] is False
    assert body["tmdb_api_key_masked"] is None


async def test_test_tmdb_endpoint_success(client):
    _register_and_login(client)
    with respx.mock:
        respx.get(f"{TMDB_BASE}/authentication").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        resp = client.post(
            "/api/settings/integrations/test-tmdb", json={"tmdb_api_key": "good-token"}
        )
    assert resp.status_code == 200
    assert resp.json() == {"reachable": True, "version": None, "detail": None}


async def test_test_tmdb_endpoint_failure(client):
    _register_and_login(client)
    with respx.mock:
        respx.get(f"{TMDB_BASE}/authentication").mock(
            return_value=httpx.Response(
                401, json={"success": False, "status_message": "Invalid API key"}
            )
        )
        resp = client.post(
            "/api/settings/integrations/test-tmdb", json={"tmdb_api_key": "bad-token"}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reachable"] is False
    assert body["detail"] == "Invalid API key"


async def test_test_jellyfin_endpoint_success(client):
    _register_and_login(client)
    with respx.mock:
        respx.get(f"{JELLYFIN_BASE}/System/Info/Public").mock(
            return_value=httpx.Response(200, json={"Version": "10.9.0"})
        )
        resp = client.post(
            "/api/settings/integrations/test-jellyfin",
            json={"jellyfin_url": JELLYFIN_BASE, "jellyfin_api_key": "token"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"reachable": True, "version": "10.9.0", "detail": None}


async def test_test_jellyfin_endpoint_unreachable(client):
    _register_and_login(client)
    with respx.mock:
        respx.get(f"{JELLYFIN_BASE}/System/Info/Public").mock(
            return_value=httpx.Response(500, json={})
        )
        resp = client.post(
            "/api/settings/integrations/test-jellyfin",
            json={"jellyfin_url": JELLYFIN_BASE},
        )
    assert resp.status_code == 200
    assert resp.json()["reachable"] is False


async def test_saved_tmdb_override_is_used_on_the_next_request(client):
    """End-to-end proof of the deps.py wiring: PATCH now, next request uses it."""
    _register_and_login(client)
    client.patch("/api/settings/integrations", json={"tmdb_api_key": "brand-new-token"})

    with respx.mock:
        route = respx.get(f"{TMDB_BASE}/search/movie").mock(
            return_value=httpx.Response(200, json={"results": [], "total_results": 0})
        )
        resp = client.get("/api/movies/search?q=matrix")

    assert resp.status_code == 200
    assert route.calls.last.request.headers["Authorization"] == "Bearer brand-new-token"


def test_admin_patch_sets_and_masks_omdb_override(client):
    _register_and_login(client)
    resp = client.patch("/api/settings/integrations", json={"omdb_api_key": "abcdefgh1234"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["omdb_configured"] is True
    assert body["omdb_api_key_masked"] == "****1234"


async def test_test_omdb_endpoint_success(client):
    _register_and_login(client)
    with respx.mock:
        respx.get(OMDB_BASE).mock(
            return_value=httpx.Response(200, json={"Response": "True", "imdbRating": "8.8"})
        )
        resp = client.post(
            "/api/settings/integrations/test-omdb", json={"omdb_api_key": "good-key"}
        )
    assert resp.status_code == 200
    assert resp.json() == {"reachable": True, "version": None, "detail": None}


async def test_test_omdb_endpoint_failure(client):
    _register_and_login(client)
    with respx.mock:
        respx.get(OMDB_BASE).mock(
            return_value=httpx.Response(
                200, json={"Response": "False", "Error": "Invalid API key!"}
            )
        )
        resp = client.post("/api/settings/integrations/test-omdb", json={"omdb_api_key": "bad-key"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reachable"] is False
    assert body["detail"] == "Invalid API key!"


async def test_saved_omdb_override_is_used_by_ratings_lookup(client):
    """End-to-end proof the OMDb override flows through get_omdb_client into
    an actual movie-detail ratings fetch, not just persisted to the DB."""
    _register_and_login(client)
    client.patch("/api/settings/integrations", json={"omdb_api_key": "brand-new-omdb-key"})

    with respx.mock:
        respx.get(f"{TMDB_BASE}/movie/603").mock(
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
            return_value=httpx.Response(200, json={"Response": "True", "imdbRating": "8.7"})
        )
        resp = client.get("/api/movies/603")

    assert resp.status_code == 200
    assert resp.json()["ratings"]["imdb_rating"] == "8.7"
    assert omdb_route.calls.last.request.url.params["apikey"] == "brand-new-omdb-key"


def test_solver_settings_requires_admin(client):
    _register_and_login(client, "alice")  # first user - admin
    client.post(
        "/api/auth/register",
        json={"username": "bob", "password": "password123", "display_name": "Bob"},
    )
    client.post("/api/auth/login", json={"username": "bob", "password": "password123"})

    assert client.get("/api/settings/solver").status_code == 403
    assert (
        client.patch("/api/settings/solver", json={"bridge_max_duration_seconds": 60}).status_code
        == 403
    )


def test_solver_settings_default_and_update(client):
    _register_and_login(client)
    assert client.get("/api/settings/solver").json() == {"bridge_max_duration_seconds": 45}

    resp = client.patch("/api/settings/solver", json={"bridge_max_duration_seconds": 90})
    assert resp.status_code == 200
    assert resp.json()["bridge_max_duration_seconds"] == 90

    # Persisted - a fresh GET reflects the saved override.
    assert client.get("/api/settings/solver").json()["bridge_max_duration_seconds"] == 90


def test_solver_settings_rejects_out_of_range_values(client):
    _register_and_login(client)
    resp = client.patch("/api/settings/solver", json={"bridge_max_duration_seconds": 1})
    assert resp.status_code == 422


def test_solver_timeout_allows_up_to_600_seconds(client):
    _register_and_login(client)
    assert (
        client.patch("/api/settings/solver", json={"bridge_max_duration_seconds": 600}).status_code
        == 200
    )
    assert (
        client.patch("/api/settings/solver", json={"bridge_max_duration_seconds": 601}).status_code
        == 422
    )
