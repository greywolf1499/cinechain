"""Radarr / Seerr integration tests: client payloads (auto-routed vs advanced
Seerr requests, Radarr add body), state lookups, and the API routes built on them.

Clients are constructed with an explicit `Settings` and mocked via respx; route
tests override `get_radarr_client` / `get_seerr_client` the same way
test_jellyfin.py overrides the Jellyfin client.
"""

import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.api.routes_integrations import (
    get_radarr_client,
    get_seerr_client,
    merge_acquisition_status,
)
from app.config import Settings
from app.db import get_session
from app.integrations import radarr as radarr_module
from app.integrations import seerr as seerr_module
from app.integrations.base import IntegrationError
from app.integrations.radarr import RadarrClient, RadarrMovieState
from app.integrations.seerr import SeerrClient, media_state_from_info
from app.main import app

RADARR = "http://radarr.test:7878"
SEERR = "http://seerr.test:5055"


@pytest.fixture(autouse=True)
def _clear_caches():
    radarr_module._cache.clear()
    seerr_module._cache.clear()
    yield
    radarr_module._cache.clear()
    seerr_module._cache.clear()


def _radarr(**overrides: str) -> RadarrClient:
    client = RadarrClient(
        httpx.AsyncClient(), settings=Settings(radarr_url=RADARR, radarr_api_key="radarr-key")
    )
    client.set_overrides(overrides)
    return client


def _seerr(**overrides: str) -> SeerrClient:
    client = SeerrClient(
        httpx.AsyncClient(), settings=Settings(seerr_url=SEERR, seerr_api_key="seerr-key")
    )
    client.set_overrides(overrides)
    return client


def _sent_json(route) -> dict:
    return json.loads(route.calls.last.request.content)


# ---------------------------------------------------------
# Radarr client
# ---------------------------------------------------------
def test_radarr_disabled_without_api_key_even_with_default_url():
    client = RadarrClient(httpx.AsyncClient(), settings=Settings())
    assert client.enabled is False
    assert client._url == "http://radarr:7878"


async def test_radarr_profiles_folders_and_auth_header():
    with respx.mock:
        profiles = respx.get(f"{RADARR}/api/v3/qualityprofile").mock(
            return_value=httpx.Response(200, json=[{"id": 4, "name": "HD-1080p", "cutoff": 1}])
        )
        respx.get(f"{RADARR}/api/v3/rootfolder").mock(
            return_value=httpx.Response(
                200, json=[{"id": 1, "path": "/movies/hindi", "freeSpace": 123456}]
            )
        )
        client = _radarr()
        found_profiles = await client.get_quality_profiles()
        folders = await client.get_root_folders()

    assert profiles.calls.last.request.headers["X-Api-Key"] == "radarr-key"
    assert (found_profiles[0].id, found_profiles[0].name) == (4, "HD-1080p")
    assert (folders[0].path, folders[0].free_space) == ("/movies/hindi", 123456)


async def test_radarr_lookup_reports_file_queue_and_missing_states():
    with respx.mock:
        respx.get(f"{RADARR}/api/v3/movie", params={"tmdbId": "1"}).mock(
            return_value=httpx.Response(200, json=[{"id": 11, "monitored": True, "hasFile": True}])
        )
        respx.get(f"{RADARR}/api/v3/movie", params={"tmdbId": "2"}).mock(
            return_value=httpx.Response(200, json=[{"id": 22, "monitored": True, "hasFile": False}])
        )
        respx.get(f"{RADARR}/api/v3/movie", params={"tmdbId": "3"}).mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.get(f"{RADARR}/api/v3/queue").mock(
            return_value=httpx.Response(200, json={"records": [{"movieId": 22}]})
        )
        states = await _radarr().lookup_movies([1, 2, 3])

    assert states[1].has_file is True and states[1].downloading is False
    assert states[2].has_file is False and states[2].downloading is True
    assert states[3] is None


async def test_radarr_add_movie_posts_monitored_search_on_add_body():
    with respx.mock:
        respx.get(f"{RADARR}/api/v3/movie/lookup/tmdb").mock(
            return_value=httpx.Response(
                200, json={"id": 0, "title": "The Matrix", "tmdbId": 603, "year": 1999}
            )
        )
        post = respx.post(f"{RADARR}/api/v3/movie").mock(
            return_value=httpx.Response(201, json={"id": 5})
        )
        await _radarr().add_movie(603, 4, "/movies/international")

    body = _sent_json(post)
    assert body["tmdbId"] == 603
    assert body["title"] == "The Matrix"
    assert body["qualityProfileId"] == 4
    assert body["rootFolderPath"] == "/movies/international"
    assert body["monitored"] is True
    assert body["addOptions"]["searchForMovie"] is True


async def test_radarr_add_movie_already_in_library_is_conflict():
    with respx.mock:
        respx.get(f"{RADARR}/api/v3/movie/lookup/tmdb").mock(
            return_value=httpx.Response(200, json={"id": 9, "title": "The Matrix"})
        )
        with pytest.raises(IntegrationError) as exc_info:
            await _radarr().add_movie(603, 4, "/movies")
    assert exc_info.value.status_code == 409


async def test_radarr_rejected_key_and_unreachable_map_to_bad_gateway():
    with respx.mock:
        respx.get(f"{RADARR}/api/v3/qualityprofile").mock(return_value=httpx.Response(401))
        with pytest.raises(IntegrationError, match="API key") as rejected:
            await _radarr().get_quality_profiles()
        assert rejected.value.status_code == 502

        respx.get(f"{RADARR}/api/v3/rootfolder").mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(IntegrationError, match="unreachable"):
            await _radarr().get_root_folders()


# ---------------------------------------------------------
# Seerr client
# ---------------------------------------------------------
async def test_seerr_auto_route_request_sends_only_media_fields():
    with respx.mock:
        route = respx.post(f"{SEERR}/api/v1/request").mock(
            return_value=httpx.Response(201, json={"id": 1})
        )
        await _seerr().request_movie(603)

    assert route.calls.last.request.headers["X-Api-Key"] == "seerr-key"
    assert _sent_json(route) == {"mediaType": "movie", "mediaId": 603}


async def test_seerr_auto_route_request_attributes_to_user():
    with respx.mock:
        route = respx.post(f"{SEERR}/api/v1/request").mock(
            return_value=httpx.Response(201, json={"id": 1})
        )
        await _seerr().request_movie(603, user_id=7)

    assert _sent_json(route) == {"mediaType": "movie", "mediaId": 603, "userId": 7}


async def test_seerr_advanced_request_includes_server_profile_folder_and_user():
    with respx.mock:
        route = respx.post(f"{SEERR}/api/v1/request").mock(
            return_value=httpx.Response(201, json={"id": 1})
        )
        await _seerr().request_movie(
            603, user_id=7, server_id=2, profile_id=4, root_folder="/movies/hindi"
        )

    assert _sent_json(route) == {
        "mediaType": "movie",
        "mediaId": 603,
        "userId": 7,
        "serverId": 2,
        "profileId": 4,
        "rootFolder": "/movies/hindi",
    }


async def test_seerr_duplicate_request_is_conflict():
    with respx.mock:
        respx.post(f"{SEERR}/api/v1/request").mock(
            return_value=httpx.Response(409, json={"message": "Request already exists"})
        )
        with pytest.raises(IntegrationError) as exc_info:
            await _seerr().request_movie(603)
    assert exc_info.value.status_code == 409


async def test_seerr_users_and_radarr_servers():
    with respx.mock:
        respx.get(f"{SEERR}/api/v1/user").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {"id": 1, "displayName": "Admin", "email": "a@x.io"},
                        {"id": 2, "username": "kid", "email": "k@x.io"},
                    ]
                },
            )
        )
        respx.get(f"{SEERR}/api/v1/service/radarr").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "id": 0,
                        "name": "Hindi",
                        "isDefault": False,
                        "is4k": False,
                        "activeProfileId": 4,
                        "activeDirectory": "/movies/hindi",
                    },
                    {
                        "id": 1,
                        "name": "International",
                        "isDefault": True,
                        "is4k": False,
                        "activeProfileId": 6,
                        "activeDirectory": "/movies/intl",
                    },
                ],
            )
        )
        respx.get(f"{SEERR}/api/v1/service/radarr/0").mock(
            return_value=httpx.Response(
                200,
                json={
                    "profiles": [{"id": 4, "name": "HD"}],
                    "rootFolders": [{"id": 1, "path": "/movies/hindi", "freeSpace": 10}],
                },
            )
        )
        respx.get(f"{SEERR}/api/v1/service/radarr/1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "profiles": [{"id": 6, "name": "Any"}],
                    "rootFolders": [{"id": 2, "path": "/movies/intl"}],
                },
            )
        )
        client = _seerr()
        users = await client.list_users()
        servers = await client.get_radarr_servers()

    assert [(u.id, u.display_name) for u in users] == [(1, "Admin"), (2, "kid")]
    assert [s.name for s in servers] == ["Hindi", "International"]
    assert servers[0].root_folders[0].path == "/movies/hindi"
    assert servers[1].is_default is True and servers[1].profiles[0].name == "Any"


def test_seerr_media_state_mapping():
    assert media_state_from_info(None) == "missing"
    assert media_state_from_info({"status": 2}) == "requested"
    assert media_state_from_info({"status": 3}) == "requested"
    assert media_state_from_info({"status": 3, "downloadStatus": [{"size": 1}]}) == "downloading"
    assert media_state_from_info({"status": 5}) == "available"
    assert media_state_from_info({"status": 6}) == "missing"


async def test_seerr_lookup_states_treats_404_as_missing():
    with respx.mock:
        respx.get(f"{SEERR}/api/v1/movie/1").mock(
            return_value=httpx.Response(200, json={"mediaInfo": {"status": 2}})
        )
        respx.get(f"{SEERR}/api/v1/movie/2").mock(return_value=httpx.Response(404, json={}))
        states = await _seerr().lookup_states([1, 2])

    assert states == {1: "requested", 2: "missing"}


def test_merge_acquisition_status_precedence():
    queued = RadarrMovieState(radarr_id=1, monitored=True, has_file=False, downloading=True)
    wanted = RadarrMovieState(radarr_id=1, monitored=True, has_file=False, downloading=False)
    done = RadarrMovieState(radarr_id=1, monitored=True, has_file=True, downloading=False)

    assert merge_acquisition_status(True, queued, "requested").source == "jellyfin"
    assert merge_acquisition_status(False, done, None).state == "available"
    assert merge_acquisition_status(False, queued, "requested").state == "downloading"
    assert merge_acquisition_status(False, wanted, "requested").source == "seerr"
    assert merge_acquisition_status(False, wanted, None).state == "requested"
    assert merge_acquisition_status(None, None, None).state == "missing"


# ---------------------------------------------------------
# Routes
# ---------------------------------------------------------
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


def _login(client, username="alice"):
    client.post(
        "/api/auth/register",
        json={"username": username, "password": "password123", "display_name": username.title()},
    )
    client.post("/api/auth/login", json={"username": username, "password": "password123"})


def _use(radarr: RadarrClient | None = None, seerr: SeerrClient | None = None):
    app.dependency_overrides[get_radarr_client] = lambda: (
        radarr or RadarrClient(httpx.AsyncClient(), settings=Settings())
    )
    app.dependency_overrides[get_seerr_client] = lambda: (
        seerr or SeerrClient(httpx.AsyncClient(), settings=Settings())
    )


def test_seerr_request_route_auto_routes_with_default_user(client):
    _login(client)
    _use(seerr=_seerr(seerr_user_id="9"))
    with respx.mock:
        route = respx.post(f"{SEERR}/api/v1/request").mock(
            return_value=httpx.Response(201, json={"id": 1})
        )
        resp = client.post("/api/integrations/seerr/request", json={"tmdb_id": 603})

    assert resp.status_code == 200
    assert resp.json()["mode"] == "auto"
    assert _sent_json(route) == {"mediaType": "movie", "mediaId": 603, "userId": 9}


def test_seerr_request_route_advanced_payload(client):
    _login(client)
    _use(seerr=_seerr())
    with respx.mock:
        route = respx.post(f"{SEERR}/api/v1/request").mock(
            return_value=httpx.Response(201, json={"id": 1})
        )
        resp = client.post(
            "/api/integrations/seerr/request",
            json={
                "tmdb_id": 603,
                "server_id": 1,
                "profile_id": 6,
                "root_folder": "/movies/intl",
                "user_id": 3,
            },
        )

    assert resp.status_code == 200
    assert resp.json()["mode"] == "advanced"
    assert _sent_json(route) == {
        "mediaType": "movie",
        "mediaId": 603,
        "userId": 3,
        "serverId": 1,
        "profileId": 6,
        "rootFolder": "/movies/intl",
    }


def test_seerr_request_route_ignores_user_override_from_non_admin(client):
    _login(client, "alice")
    _login(client, "bob")  # second account is not an admin
    _use(seerr=_seerr(seerr_user_id="9"))
    with respx.mock:
        route = respx.post(f"{SEERR}/api/v1/request").mock(
            return_value=httpx.Response(201, json={"id": 1})
        )
        resp = client.post("/api/integrations/seerr/request", json={"tmdb_id": 603, "user_id": 1})

    assert resp.status_code == 200
    assert _sent_json(route)["userId"] == 9


def test_seerr_request_route_validation_and_errors(client):
    _login(client)
    _use(seerr=_seerr())
    assert (
        client.post(
            "/api/integrations/seerr/request", json={"tmdb_id": 603, "root_folder": "/movies"}
        ).status_code
        == 422
    )

    with respx.mock:
        respx.post(f"{SEERR}/api/v1/request").mock(
            return_value=httpx.Response(409, json={"message": "already requested"})
        )
        assert (
            client.post("/api/integrations/seerr/request", json={"tmdb_id": 603}).status_code == 409
        )

    _use()  # unconfigured
    assert client.post("/api/integrations/seerr/request", json={"tmdb_id": 603}).status_code == 400


def test_seerr_options_hides_users_from_non_admins(client):
    _login(client, "alice")
    _use(seerr=_seerr())
    with respx.mock:
        respx.get(f"{SEERR}/api/v1/user").mock(
            return_value=httpx.Response(
                200, json={"results": [{"id": 1, "displayName": "Admin", "email": "a@x.io"}]}
            )
        )
        respx.get(f"{SEERR}/api/v1/service/radarr").mock(return_value=httpx.Response(200, json=[]))
        admin_view = client.get("/api/integrations/seerr/options").json()
        _login(client, "bob")
        member_view = client.get("/api/integrations/seerr/options").json()

    assert [u["display_name"] for u in admin_view["users"]] == ["Admin"]
    assert member_view["users"] == []
    assert member_view["enabled"] is True


def test_radarr_profiles_and_add_with_defaults(client):
    _login(client)
    _use(
        radarr=_radarr(
            radarr_default_quality_profile_id="4", radarr_default_root_folder_path="/movies/intl"
        )
    )
    with respx.mock:
        respx.get(f"{RADARR}/api/v3/qualityprofile").mock(
            return_value=httpx.Response(200, json=[{"id": 4, "name": "HD"}])
        )
        respx.get(f"{RADARR}/api/v3/rootfolder").mock(
            return_value=httpx.Response(200, json=[{"id": 1, "path": "/movies/intl"}])
        )
        options = client.get("/api/integrations/radarr/profiles").json()

        respx.get(f"{RADARR}/api/v3/movie/lookup/tmdb").mock(
            return_value=httpx.Response(200, json={"id": 0, "title": "The Matrix"})
        )
        post = respx.post(f"{RADARR}/api/v3/movie").mock(
            return_value=httpx.Response(201, json={"id": 5})
        )
        resp = client.post("/api/integrations/radarr/add", json={"tmdb_id": 603})

    assert options["default_quality_profile_id"] == 4
    assert options["root_folders"][0]["path"] == "/movies/intl"
    assert resp.status_code == 200
    assert _sent_json(post)["qualityProfileId"] == 4
    assert _sent_json(post)["rootFolderPath"] == "/movies/intl"


def test_radarr_add_requires_choice_when_no_defaults(client):
    _login(client)
    _use(radarr=_radarr())
    resp = client.post("/api/integrations/radarr/add", json={"tmdb_id": 603})
    assert resp.status_code == 422


def test_request_config_prefers_seerr_and_derives_radarr_mode(client):
    _login(client)
    _use()
    assert client.get("/api/integrations/request-config").json()["service"] is None

    _use(radarr=_radarr())
    config = client.get("/api/integrations/request-config").json()
    assert (config["service"], config["request_mode"]) == ("radarr", "prompt")

    _use(
        radarr=_radarr(radarr_default_quality_profile_id="4", radarr_default_root_folder_path="/m")
    )
    assert client.get("/api/integrations/request-config").json()["request_mode"] == "auto"

    _use(radarr=_radarr(), seerr=_seerr(seerr_request_mode="prompt"))
    config = client.get("/api/integrations/request-config").json()
    assert (config["service"], config["request_mode"]) == ("seerr", "prompt")


def test_status_lookup_merges_services_and_degrades_when_one_is_down(client):
    _login(client)
    _use(radarr=_radarr(), seerr=_seerr())
    with respx.mock:
        respx.get(f"{RADARR}/api/v3/movie", params={"tmdbId": "1"}).mock(
            return_value=httpx.Response(200, json=[{"id": 1, "monitored": True, "hasFile": False}])
        )
        respx.get(f"{RADARR}/api/v3/movie", params={"tmdbId": "2"}).mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.get(f"{RADARR}/api/v3/movie", params={"tmdbId": "3"}).mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.get(f"{RADARR}/api/v3/queue").mock(
            return_value=httpx.Response(200, json={"records": [{"movieId": 1}]})
        )
        respx.get(f"{SEERR}/api/v1/movie/1").mock(
            return_value=httpx.Response(200, json={"mediaInfo": {"status": 3}})
        )
        respx.get(f"{SEERR}/api/v1/movie/2").mock(
            return_value=httpx.Response(200, json={"mediaInfo": {"status": 2}})
        )
        respx.get(f"{SEERR}/api/v1/movie/3").mock(return_value=httpx.Response(404, json={}))
        resp = client.post("/api/integrations/status/lookup", json={"tmdb_ids": [1, 2, 3]})

    assert resp.status_code == 200
    body = resp.json()
    assert body["1"] == {"state": "downloading", "source": "radarr"}
    assert body["2"] == {"state": "requested", "source": "seerr"}

    radarr_module._cache.clear()
    seerr_module._cache.clear()
    with respx.mock:
        respx.get(url__startswith=f"{RADARR}/api/v3/movie").mock(
            side_effect=httpx.ConnectError("x")
        )
        respx.get(f"{SEERR}/api/v1/movie/3").mock(return_value=httpx.Response(404, json={}))
        degraded = client.post("/api/integrations/status/lookup", json={"tmdb_ids": [3]})
    assert degraded.json()["3"] == {"state": "missing", "source": None}


def test_status_lookup_empty_and_oversized_batches(client):
    _login(client)
    _use()
    assert client.post("/api/integrations/status/lookup", json={"tmdb_ids": []}).json() == {}
    assert (
        client.post(
            "/api/integrations/status/lookup", json={"tmdb_ids": list(range(101))}
        ).status_code
        == 422
    )


# ---------------------------------------------------------
# Settings
# ---------------------------------------------------------
def test_settings_store_masked_keys_request_mode_and_defaults(client):
    _login(client)

    initial = client.get("/api/settings/integrations").json()
    assert initial["radarr_configured"] is False
    assert initial["radarr_url"] == "http://radarr:7878"
    assert initial["seerr_url"] == "http://seerr:5055"
    assert initial["seerr_request_mode"] == "auto"

    resp = client.patch(
        "/api/settings/integrations",
        json={
            "radarr_url": RADARR,
            "radarr_api_key": "abcdef123456",
            "radarr_default_quality_profile_id": 4,
            "radarr_default_root_folder_path": "/movies/intl",
            "seerr_url": SEERR,
            "seerr_api_key": "seerrsecret99",
            "seerr_request_mode": "prompt",
            "seerr_user_id": 12,
        },
    )

    body = resp.json()
    assert resp.status_code == 200
    assert body["radarr_api_key_masked"] == "****3456"
    assert body["seerr_api_key_masked"] == "****et99"
    assert "abcdef123456" not in resp.text and "seerrsecret99" not in resp.text
    assert body["radarr_default_quality_profile_id"] == 4
    assert body["seerr_request_mode"] == "prompt"
    assert body["seerr_user_id"] == 12

    cleared = client.patch("/api/settings/integrations", json={"seerr_user_id": None}).json()
    assert cleared["seerr_user_id"] is None
    assert (
        client.patch("/api/settings/integrations", json={"seerr_request_mode": "bogus"}).status_code
        == 422
    )


def test_settings_connection_tests_use_stored_key_when_blank(client):
    _login(client)
    client.patch("/api/settings/integrations", json={"seerr_api_key": "stored-seerr-key"})
    with respx.mock:
        status_route = respx.get(f"{SEERR}/api/v1/status").mock(
            return_value=httpx.Response(200, json={"version": "2.5.0"})
        )
        respx.get(f"{SEERR}/api/v1/auth/me").mock(return_value=httpx.Response(200, json={"id": 1}))
        resp = client.post("/api/settings/integrations/test-seerr", json={"url": SEERR})

        respx.get(f"{RADARR}/api/v3/system/status").mock(
            return_value=httpx.Response(200, json={"version": "5.1.0"})
        )
        radarr_resp = client.post(
            "/api/settings/integrations/test-radarr", json={"url": RADARR, "api_key": "typed-key"}
        )

    assert resp.json() == {"reachable": True, "version": "2.5.0", "detail": None}
    assert status_route.calls.last.request.headers["X-Api-Key"] == "stored-seerr-key"
    assert radarr_resp.json()["version"] == "5.1.0"


def test_settings_connection_test_reports_rejected_seerr_key(client):
    _login(client)
    with respx.mock:
        respx.get(f"{SEERR}/api/v1/status").mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{SEERR}/api/v1/auth/me").mock(return_value=httpx.Response(403, json={}))
        resp = client.post(
            "/api/settings/integrations/test-seerr", json={"url": SEERR, "api_key": "wrong"}
        )

    assert resp.json()["reachable"] is False
    assert "API key" in resp.json()["detail"]
