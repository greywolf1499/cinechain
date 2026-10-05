"""Jellyfin integration tests.

Unit tests construct `JellyfinClient` directly with an explicit `Settings`
override (bypassing env/config_dir entirely - simplest for a client that
takes its own `settings` kwarg). Route-level tests use the same
`app.dependency_overrides` pattern as test_runs_api.py, additionally
overriding `get_jellyfin_client` so routes don't depend on real env vars.
"""

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.api.routes_integrations import get_jellyfin_client
from app.config import Settings
from app.db import get_session
from app.integrations.jellyfin import JellyfinClient, _cache, _normalize_title
from app.main import app
from app.models.cache import CachedMovie

JELLYFIN_BASE = "http://jellyfin.test"


@pytest.fixture(autouse=True)
def _clear_jellyfin_cache():
    _cache.clear()
    yield
    _cache.clear()


def _configured_settings(**overrides) -> Settings:
    return Settings(jellyfin_url=JELLYFIN_BASE, jellyfin_api_key="secret-token", **overrides)


async def test_batched_lookup_matches_tmdb_ids():
    with respx.mock:
        respx.get(f"{JELLYFIN_BASE}/Items").mock(
            return_value=httpx.Response(
                200,
                json={
                    "Items": [
                        {"Id": "abc123", "ProviderIds": {"Tmdb": "603"}},
                        {"Id": "def456", "ProviderIds": {"Tmdb": "27205"}},
                    ]
                },
            )
        )
        async with httpx.AsyncClient() as client:
            jellyfin = JellyfinClient(client, settings=_configured_settings())
            results = await jellyfin.lookup_movies([603, 27205, 999])

    assert results[603].on_server is True
    assert results[603].item_id == "abc123"
    assert "abc123" in results[603].play_url
    assert results[27205].on_server is True
    assert results[999].on_server is False


async def test_unreachable_jellyfin_degrades_to_unknown():
    with respx.mock:
        respx.get(f"{JELLYFIN_BASE}/Items").mock(
            return_value=httpx.Response(500, json={"error": "boom"})
        )
        async with httpx.AsyncClient() as client:
            jellyfin = JellyfinClient(client, settings=_configured_settings())
            results = await jellyfin.lookup_movies([603])

    assert results[603].on_server is None


async def test_unconfigured_jellyfin_makes_zero_http_calls():
    with respx.mock:
        # Zero routes registered - any HTTP attempt raises AllMockedAssertionError.
        async with httpx.AsyncClient() as client:
            # jellyfin_url="" by default
            jellyfin = JellyfinClient(client, settings=Settings())
            results = await jellyfin.lookup_movies([603])

    assert results[603].on_server is None


def test_normalize_title_strips_punctuation_and_case():
    assert _normalize_title("Se7en") == _normalize_title("SE7EN")
    assert _normalize_title("The Matrix: Reloaded") == _normalize_title("the matrix reloaded")
    assert _normalize_title("Spider-Man") == _normalize_title("spiderman")


async def test_title_year_fallback_matches_when_provider_id_missing(config_dir):
    """Provider-id pass misses (no Tmdb id tagged on the local file), but the
    normalized title+year fallback finds it via a Jellyfin SearchTerm match."""
    from app.db import engine as app_engine

    SQLModel.metadata.create_all(app_engine)
    with Session(app_engine) as session:
        session.add(CachedMovie(tmdb_id=603, title="The Matrix", release_date="1999-03-30"))
        session.commit()

        with respx.mock:
            respx.get(f"{JELLYFIN_BASE}/Items").mock(
                side_effect=[
                    # provider-id pass: no match
                    httpx.Response(200, json={"Items": []}),
                    httpx.Response(
                        200,
                        json={
                            "Items": [
                                {
                                    "Id": "xyz789",
                                    "Name": "The Matrix (1999)",
                                    "ProductionYear": 1999,
                                    "ProviderIds": {},
                                }
                            ]
                        },
                    ),
                ]
            )
            async with httpx.AsyncClient() as client:
                jellyfin = JellyfinClient(client, settings=_configured_settings())
                results = await jellyfin.lookup_movies([603], session=session)

    assert results[603].on_server is True
    assert results[603].item_id == "xyz789"


async def test_title_year_fallback_rejects_wrong_year(config_dir):
    from app.db import engine as app_engine

    SQLModel.metadata.create_all(app_engine)
    with Session(app_engine) as session:
        session.add(CachedMovie(tmdb_id=603, title="The Matrix", release_date="1999-03-30"))
        session.commit()

        with respx.mock:
            respx.get(f"{JELLYFIN_BASE}/Items").mock(
                side_effect=[
                    httpx.Response(200, json={"Items": []}),
                    httpx.Response(
                        200,
                        json={
                            "Items": [
                                {
                                    "Id": "wrong-year",
                                    "Name": "The Matrix",
                                    "ProductionYear": 2021,  # Resurrections, not the original
                                    "ProviderIds": {},
                                }
                            ]
                        },
                    ),
                ]
            )
            async with httpx.AsyncClient() as client:
                jellyfin = JellyfinClient(client, settings=_configured_settings())
                results = await jellyfin.lookup_movies([603], session=session)

    assert results[603].on_server is False


async def test_test_lookup_by_tmdb_id():
    with respx.mock:
        respx.get(f"{JELLYFIN_BASE}/Items").mock(
            return_value=httpx.Response(
                200,
                json={
                    "Items": [
                        {
                            "Id": "abc123",
                            "Name": "The Matrix",
                            "ProductionYear": 1999,
                            "ProviderIds": {"Tmdb": "603"},
                        }
                    ]
                },
            )
        )
        async with httpx.AsyncClient() as client:
            jellyfin = JellyfinClient(client, settings=_configured_settings())
            result = await jellyfin.test_lookup("603")

    assert result["query_type"] == "tmdb_id"
    assert result["matches"][0]["name"] == "The Matrix"


async def test_test_lookup_by_title():
    with respx.mock:
        respx.get(f"{JELLYFIN_BASE}/Items").mock(
            return_value=httpx.Response(
                200,
                json={"Items": [{"Id": "abc123", "Name": "The Matrix", "ProductionYear": 1999}]},
            )
        )
        async with httpx.AsyncClient() as client:
            jellyfin = JellyfinClient(client, settings=_configured_settings())
            result = await jellyfin.test_lookup("The Matrix")

    assert result["query_type"] == "title"
    assert len(result["matches"]) == 1


async def test_test_lookup_disabled_makes_zero_http_calls():
    with respx.mock:
        async with httpx.AsyncClient() as client:
            jellyfin = JellyfinClient(client, settings=Settings())
            result = await jellyfin.test_lookup("The Matrix")

    assert result["enabled"] is False
    assert result["matches"] == []


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


def _override_jellyfin(settings: Settings):
    def _get():
        return JellyfinClient(httpx.AsyncClient(), settings=settings)

    app.dependency_overrides[get_jellyfin_client] = _get


def test_status_reports_unreachable_and_suggestions_still_200(client):
    _register_and_login(client)
    run_id = client.post("/api/runs", json={"name": "Run", "participant_user_ids": []}).json()["id"]

    _override_jellyfin(_configured_settings())
    try:
        with respx.mock:
            respx.get(f"{JELLYFIN_BASE}/System/Info/Public").mock(
                return_value=httpx.Response(500, json={})
            )
            status_resp = client.get("/api/integrations/status")
    finally:
        app.dependency_overrides.pop(get_jellyfin_client, None)

    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["jellyfin"]["enabled"] is True
    assert body["jellyfin"]["reachable"] is False

    # A run with no steps yet has no "current movie" to suggest from, but the
    # route itself must never fail just because Jellyfin is down.
    suggestions_resp = client.get(f"/api/runs/{run_id}/suggestions")
    assert suggestions_resp.status_code == 200


def test_backend_boots_with_jellyfin_url_unset(client):
    # No JELLYFIN_URL configured anywhere in this test's environment/settings.
    resp = client.get("/api/health")
    assert resp.status_code == 200

    _register_and_login(client)
    status_resp = client.get("/api/integrations/status")
    assert status_resp.status_code == 200
    assert status_resp.json()["jellyfin"]["enabled"] is False


def test_jellyfin_lookup_route(client):
    _register_and_login(client)
    _override_jellyfin(_configured_settings())
    try:
        with respx.mock:
            respx.get(f"{JELLYFIN_BASE}/Items").mock(
                return_value=httpx.Response(
                    200, json={"Items": [{"Id": "abc123", "ProviderIds": {"Tmdb": "603"}}]}
                )
            )
            resp = client.post("/api/integrations/jellyfin/lookup", json={"tmdb_ids": [603]})
    finally:
        app.dependency_overrides.pop(get_jellyfin_client, None)

    assert resp.status_code == 200
    assert resp.json()["603"]["on_server"] is True


def test_jellyfin_test_lookup_requires_admin(client):
    _register_and_login(client, "alice")  # first user - admin
    client.post(
        "/api/auth/register",
        json={"username": "bob", "password": "password123", "display_name": "Bob"},
    )
    client.post("/api/auth/login", json={"username": "bob", "password": "password123"})

    resp = client.post("/api/integrations/jellyfin/test-lookup", json={"query": "The Matrix"})
    assert resp.status_code == 403


def test_jellyfin_test_lookup_route_returns_raw_matches(client):
    _register_and_login(client)
    _override_jellyfin(_configured_settings())
    try:
        with respx.mock:
            respx.get(f"{JELLYFIN_BASE}/Items").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "Items": [
                            {
                                "Id": "abc123",
                                "Name": "The Matrix",
                                "ProductionYear": 1999,
                                "ProviderIds": {"Tmdb": "603"},
                            }
                        ]
                    },
                )
            )
            resp = client.post(
                "/api/integrations/jellyfin/test-lookup", json={"query": "The Matrix"}
            )
    finally:
        app.dependency_overrides.pop(get_jellyfin_client, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["query_type"] == "title"
    assert body["matches"][0]["name"] == "The Matrix"


async def test_lookup_sends_all_supported_auth_headers():
    with respx.mock:
        route = respx.get(f"{JELLYFIN_BASE}/Items").mock(
            return_value=httpx.Response(200, json={"Items": []})
        )
        async with httpx.AsyncClient() as client:
            jellyfin = JellyfinClient(client, settings=_configured_settings())
            await jellyfin.test_lookup("603")

    headers = route.calls.last.request.headers
    assert headers["Authorization"] == 'MediaBrowser Token="secret-token"'
    assert headers["X-MediaBrowser-Token"] == "secret-token"
    assert headers["X-Emby-Token"] == "secret-token"
