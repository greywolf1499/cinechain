"""Curated canon API tests: admin gating, preset/custom list management, SSE
sync persistence (via a monkeypatched scraper - no real network), and the
bulk badges lookup used by movie cards across the app.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import routes_curated
from app.api.routes_curated import _queue_canon_hydration
from app.db import get_session
from app.engines.regional_deep_dive import RegionalDeepDiveEngine
from app.main import app
from app.models.cache import CachedMovie
from app.models.curated import CanonMovieBadge, CuratedList, CuratedListEntry
from app.models.system import SystemTask
from app.models.user import User
from app.services import letterboxd
from app.services.tmdb import TMDBClient, TMDBNotFoundError


@pytest.fixture()
def client(config_dir, monkeypatch):
    async def movie_detail(_self, movie_id):
        return {
            "id": movie_id,
            "title": f"Film {movie_id}",
            "release_date": "1975-06-01",
            "origin_country": ["AU"],
            "runtime": 100,
            "genres": [],
            "overview": "",
            "tagline": "",
            "status": "Released",
        }

    monkeypatch.setattr(TMDBClient, "get_movie", movie_detail)
    engine = create_engine(
        f"sqlite:///{config_dir}/app_test.db", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)

    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        test_client.db_engine = engine
        yield test_client
    app.dependency_overrides.clear()


def _register_and_login(client, username="alice"):
    client.post(
        "/api/auth/register",
        json={"username": username, "password": "password123", "display_name": username.title()},
    )
    client.post("/api/auth/login", json={"username": username, "password": "password123"})


def _fake_scrape(films):
    def _scrape(url, tmdb_api_key=None, max_pages=None, no_cache=False, progress_callback=None):
        if progress_callback:
            progress_callback(
                {"stage": "fetch_page", "current": 1, "total": None, "message": "..."}
            )
            progress_callback(
                {"stage": "page_done", "current": len(films), "total": None, "message": "..."}
            )
        return {"is_ranked": True, "total_films": len(films), "films": films}

    return _scrape


def _run_task(client, path, body=None, expected="completed"):
    """POSTs a task-backed endpoint (202 + task id) and returns the finished task.
    TestClient runs FastAPI BackgroundTasks to completion before returning."""
    resp = client.post(path, json=body) if body is not None else client.post(path)
    assert resp.status_code == 202, resp.text
    task = client.get(f"/api/tasks/{resp.json()['id']}").json()
    assert task["status"] == expected, task
    return task


def test_slice_counts_match_prepared_checklists_without_network(client, monkeypatch):
    _register_and_login(client)
    with Session(client.db_engine) as session:
        session.add(
            CuratedList(
                id="slice",
                title="Slice",
                url="https://letterboxd.com/test/list/slice/",
                badge_prefix="TEST",
            )
        )
        session.flush()
        for movie_id, country, release in (
            (1, '["AU", "JP"]', "1975-01-01"),
            (2, '["AU"]', "1980-01-01"),
            (3, None, "1978-01-01"),
            (4, "AU", "1979-01-01"),  # legacy bare code shares the canonical parser
        ):
            session.add(
                CachedMovie(
                    tmdb_id=movie_id,
                    title=f"Film {movie_id}",
                    origin_country=country,
                    release_date=release,
                )
            )
        for rank, movie_id in enumerate([1, 1, 2, 3, 4, 5], 1):
            session.add(
                CanonMovieBadge(
                    curated_list_id="slice", movie_id=movie_id, badge_label="TEST", rank=rank
                )
            )
        session.commit()
    hydrate = AsyncMock(side_effect=AssertionError("Slices must be cache-only"))
    monkeypatch.setattr(RegionalDeepDiveEngine, "_hydrate", hydrate)
    response = client.get("/api/curated-lists/slice/slices")
    assert response.status_code == 200, response.text
    counts = response.json()
    assert counts == {
        "hydrated": 3,
        "total": 5,
        "countries": {"AU": 3, "JP": 1},
        "decades": {"1970": 3, "1980": 1},
        "pairs": {"AU:1970": 2, "AU:1980": 1, "JP:1970": 1},
        "indexing": False,
        "indexing_error": None,
    }
    assert client.get("/api/curated/lists/slice/slices").json() == counts
    hydrate.assert_not_awaited()
    monkeypatch.setattr(RegionalDeepDiveEngine, "_hydrate", AsyncMock())
    with Session(client.db_engine) as session:
        engine = RegionalDeepDiveEngine(session, None)
        for country, count in counts["countries"].items():
            prepared = asyncio.run(
                engine.prepare_run({"curated_list_id": "slice", "target_country": country}, "test")
            )
            assert len(prepared["expedition"]["movie_ids"]) == count
        for decade, count in counts["decades"].items():
            prepared = asyncio.run(
                engine.prepare_run(
                    {"curated_list_id": "slice", "target_decade": int(decade)}, "test"
                )
            )
            assert len(prepared["expedition"]["movie_ids"]) == count
        for pair, count in counts["pairs"].items():
            country, decade = pair.split(":")
            prepared = asyncio.run(
                engine.prepare_run(
                    {
                        "curated_list_id": "slice",
                        "target_country": country,
                        "target_decade": int(decade),
                    },
                    "test",
                )
            )
            assert len(prepared["expedition"]["movie_ids"]) == count


def test_enable_queues_hydration_once_and_populates_slices(client):
    _register_and_login(client)
    with Session(client.db_engine) as session:
        session.add(
            CuratedList(
                id="enable",
                title="Enable",
                url="https://letterboxd.com/test/list/enable/",
                badge_prefix="TEST",
                is_enabled=False,
            )
        )
        session.flush()
        session.add(CanonMovieBadge(curated_list_id="enable", movie_id=20, badge_label="TEST"))
        session.commit()
    for _ in range(2):
        assert (
            client.patch("/api/curated/lists/enable", json={"is_enabled": True}).status_code == 200
        )
    with Session(client.db_engine) as session:
        tasks = session.exec(select(SystemTask).where(SystemTask.name == "canon_hydrate")).all()
        assert len(tasks) == 1
        assert tasks[0].status == "completed"
        assert tasks[0].progress_data["result"] == {
            "list_id": "enable",
            "hydrated": 1,
            "total": 1,
            "indexed": 1,
            "no_country": 0,
            "undated": 0,
            "not_found": 0,
            "message": "Indexed 1/1 films.",
        }
    assert client.get("/api/curated-lists/enable/slices").json()["pairs"] == {"AU:1970": 1}


def test_sync_runs_batched_hydration_after_badges_are_persisted(client, monkeypatch):
    _register_and_login(client)
    films = [
        {"title": f"Film {i}", "year": 1975, "slug": f"film-{i}", "tmdb_id": i, "rank": i}
        for i in range(1, 24)
    ]
    monkeypatch.setattr(letterboxd, "scrape_letterboxd_list", _fake_scrape(films))
    _run_task(client, "/api/curated/sync/sight-and-sound-2022")
    with Session(client.db_engine) as session:
        task = session.exec(select(SystemTask).where(SystemTask.name == "canon_hydrate")).one()
        assert task.status == "completed"
        result = task.progress_data["result"]
        assert result["hydrated"] == result["total"] == 23
        assert task.progress_data["progress"] == {"current": 23, "total": 23}
        assert session.get(CachedMovie, 23).origin_country == '["AU"]'
    assert client.get(f"/api/curated-lists/{result['list_id']}/slices").json()["countries"] == {
        "AU": 23
    }


def test_hydration_deduplicates_pending_work(client):
    _register_and_login(client)
    background = BackgroundTasks()
    with Session(client.db_engine) as session:
        curated = CuratedList(
            id="dedupe",
            title="Dedupe",
            url="https://letterboxd.com/test/list/dedupe/",
            badge_prefix="TEST",
        )
        session.add(curated)
        session.commit()
        user = session.exec(select(User)).one()
        for _ in range(2):
            _queue_canon_hydration(background, session, curated, user.id, None)
        assert len(background.tasks) == 1
        assert (
            len(session.exec(select(SystemTask).where(SystemTask.name == "canon_hydrate")).all())
            == 1
        )
    asyncio.run(background())


def test_unavailable_details_fail_indexing_without_false_progress(client, monkeypatch):
    _register_and_login(client)
    monkeypatch.setattr(
        TMDBClient, "get_movie", AsyncMock(side_effect=TMDBNotFoundError("No film"))
    )
    monkeypatch.setattr(
        letterboxd,
        "scrape_letterboxd_list",
        _fake_scrape(
            [
                {"title": "Missing", "year": 1975, "slug": "missing", "tmdb_id": 999, "rank": 1},
            ]
        ),
    )
    _run_task(client, "/api/curated/sync/sight-and-sound-2022")
    with Session(client.db_engine) as session:
        task = session.exec(select(SystemTask).where(SystemTask.name == "canon_hydrate")).one()
        assert task.status == "failed"
        assert task.progress_data["progress"] == {"current": 0, "total": 1}
        list_id = session.exec(select(CuratedList)).one().id
    counts = client.get(f"/api/curated-lists/{list_id}/slices").json()
    assert counts["indexing"] is False
    assert "Indexed 0/1" in counts["indexing_error"]
    assert counts["pairs"] == {}


def test_curated_lists_returns_all_four_presets_unsynced_by_default(client):
    _register_and_login(client)
    resp = client.get("/api/curated/lists")
    assert resp.status_code == 200
    body = resp.json()
    preset_keys = {row["preset_key"] for row in body}
    assert preset_keys == {
        "sight-and-sound-2022",
        "sight-and-sound-directors",
        "letterboxd-top-250",
        "letterboxd-docs-250",
    }
    assert all(row["total_items"] == 0 for row in body)
    assert all(row["last_synced_at"] is None for row in body)


def test_sync_and_custom_require_admin(client):
    _register_and_login(client, "alice")  # first user - admin
    client.post(
        "/api/auth/register",
        json={"username": "bob", "password": "password123", "display_name": "Bob"},
    )
    client.post("/api/auth/login", json={"username": "bob", "password": "password123"})

    assert client.post("/api/curated/sync/sight-and-sound-2022").status_code == 403
    assert (
        client.post(
            "/api/curated/custom",
            json={"url": "https://letterboxd.com/someone/list/my-list/"},
        ).status_code
        == 403
    )


def test_create_custom_list_derives_badge_prefix(client):
    _register_and_login(client)
    resp = client.post(
        "/api/curated/custom",
        json={
            "url": "https://letterboxd.com/someone/list/cannes-palme-dor-winners/",
            "badge_color": "#ff0000",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["preset_key"] is None
    assert body["badge_color"] == "#ff0000"
    assert body["badge_prefix"]  # derived, non-empty


def test_custom_list_badge_prefix_can_be_overridden(client):
    _register_and_login(client)
    resp = client.post(
        "/api/curated/custom",
        json={"url": "https://letterboxd.com/someone/list/anything/", "badge_prefix": "palme"},
    )
    assert resp.json()["badge_prefix"] == "PALME"


def test_sync_preset_persists_badges_and_updates_list_metadata(client, monkeypatch):
    _register_and_login(client)
    films = [
        {"title": "Parasite", "year": 2019, "slug": "parasite-2019", "tmdb_id": 496243, "rank": 1},
        {"title": "Stalker", "year": 1979, "slug": "stalker-1979", "tmdb_id": 10543, "rank": 2},
    ]
    monkeypatch.setattr(letterboxd, "scrape_letterboxd_list", _fake_scrape(films))

    task = _run_task(client, "/api/curated/sync/sight-and-sound-2022")
    assert task["name"] == "curated_list_sync"
    assert task["progress_data"]["result"] == {
        "matched": 2,
        "total_films": 2,
        "tv_titles": 0,
        "unmatched": 0,
        "ambiguous": 0,
        "is_ranked": True,
    }
    assert task["progress_data"]["progress"]["stage"] == "page_done"

    lists_resp = client.get("/api/curated/lists")
    ss22 = next(row for row in lists_resp.json() if row["preset_key"] == "sight-and-sound-2022")
    assert ss22["total_items"] == 2
    assert ss22["is_ranked"] is True
    assert ss22["last_synced_at"] is not None

    badges_resp = client.post("/api/curated/badges/bulk", json={"movie_ids": [496243, 10543, 603]})
    body = badges_resp.json()
    assert body["496243"][0]["badge_label"] == "SS22 #1"
    assert body["10543"][0]["badge_label"] == "SS22 #2"
    assert "603" not in body


def test_resync_replaces_stale_badges(client, monkeypatch):
    _register_and_login(client)
    monkeypatch.setattr(
        letterboxd,
        "scrape_letterboxd_list",
        _fake_scrape(
            [
                {
                    "title": "Parasite",
                    "year": 2019,
                    "slug": "parasite-2019",
                    "tmdb_id": 496243,
                    "rank": 1,
                }
            ]
        ),
    )
    client.post("/api/curated/sync/sight-and-sound-2022")

    monkeypatch.setattr(
        letterboxd,
        "scrape_letterboxd_list",
        _fake_scrape(
            [
                {
                    "title": "Stalker",
                    "year": 1979,
                    "slug": "stalker-1979",
                    "tmdb_id": 10543,
                    "rank": 1,
                }
            ]
        ),
    )
    client.post("/api/curated/sync/sight-and-sound-2022")

    badges_resp = client.post("/api/curated/badges/bulk", json={"movie_ids": [496243, 10543]})
    body = badges_resp.json()
    assert "496243" not in body
    assert body["10543"][0]["badge_label"] == "SS22 #1"


def test_watchlist_sync_persists_for_current_user(client, monkeypatch):
    _register_and_login(client)
    films = [{"title": "Amelie", "year": 2001, "slug": "amelie-2001", "tmdb_id": 194, "rank": 1}]
    monkeypatch.setattr(letterboxd, "scrape_letterboxd_watchlist", _fake_scrape(films))

    task = _run_task(client, "/api/curated/watchlist/sync", {"letterboxd_username": "alice_lb"})
    assert task["name"] == "watchlist_sync"
    assert task["progress_data"]["result"] == {"matched": 1, "total_films": 1}


def test_watchlist_status_is_persistent_isolated_and_keeps_last_success_on_failure(
    client, monkeypatch
):
    _register_and_login(client, "alice")
    never_synced = client.get("/api/curated/watchlist/status")
    assert never_synced.status_code == 200
    assert never_synced.json() == {
        "letterboxd_username": None,
        "synced_at": None,
        "total_items": 0,
        "last_error": None,
    }

    monkeypatch.setattr(
        letterboxd,
        "scrape_letterboxd_watchlist",
        _fake_scrape([{"title": "Amelie", "year": 2001, "tmdb_id": 194}]),
    )
    _run_task(client, "/api/curated/watchlist/sync", {"letterboxd_username": "alice_lb"})
    successful = client.get("/api/curated/watchlist/status").json()
    assert successful["letterboxd_username"] == "alice_lb"
    assert successful["synced_at"] is not None
    assert successful["total_items"] == 1
    assert successful["last_error"] is None

    monkeypatch.setattr(
        letterboxd,
        "scrape_letterboxd_watchlist",
        _fake_scrape([]),
    )
    _run_task(client, "/api/curated/watchlist/sync", {"letterboxd_username": "alice_empty"})
    empty = client.get("/api/curated/watchlist/status").json()
    assert empty["letterboxd_username"] == "alice_empty"
    assert empty["synced_at"] is not None
    assert empty["total_items"] == 0

    def not_found(*args, **kwargs):
        raise letterboxd.WatchlistNotFound("alice_missing")

    monkeypatch.setattr(letterboxd, "scrape_letterboxd_watchlist", not_found)
    _run_task(
        client,
        "/api/curated/watchlist/sync",
        {"letterboxd_username": "alice_missing"},
        expected="failed",
    )
    after_failure = client.get("/api/curated/watchlist/status").json()
    assert after_failure["letterboxd_username"] == empty["letterboxd_username"]
    assert after_failure["synced_at"] == empty["synced_at"]
    assert after_failure["total_items"] == empty["total_items"]
    assert "not found" in after_failure["last_error"]

    _register_and_login(client, "bob")
    bob_status = client.get("/api/curated/watchlist/status").json()
    assert bob_status["letterboxd_username"] is None
    assert bob_status["total_items"] == 0

    _register_and_login(client, "alice")
    alice_status = client.get("/api/curated/watchlist/status").json()
    assert alice_status == after_failure


# ---------------------------------------------------------
# 3-tier architecture: curator accounts -> published lists -> enabled canons
# ---------------------------------------------------------
def _discovered_lists():
    return {
        "username": "sightsoundmag",
        "pages": 1,
        "partial": False,
        "error": None,
        "lists": [
            {
                "title": "Sight & Sound Greatest Films",
                "slug": "x",
                "url": letterboxd.PRESETS["sight-and-sound-2022"]["url"],
                "total_films": 100,
                "description": "The poll.",
                "preview_posters": ["https://letterboxd.com/p/1.jpg"],
                "preview_slugs": [],
            },
            {
                "title": "Essential Women Directors",
                "slug": "essential-women-directors",
                "url": "https://letterboxd.com/sightsoundmag/list/essential-women-directors/",
                "total_films": 40,
                "description": None,
                "preview_posters": [],
                "preview_slugs": [],
            },
        ],
    }


def _bob(client):
    """Switch to a non-admin user (alice, the first user, is admin)."""
    _register_and_login(client, "bob")


def test_accounts_are_seeded_with_curators(client):
    _register_and_login(client)
    resp = client.get("/api/curated/accounts")
    assert resp.status_code == 200
    assert {a["username"] for a in resp.json()} == {
        "criterion",
        "sightsoundmag",
        "bfi",
        "mubi",
        "a24",
    }
    assert all(a["is_hq"] for a in resp.json())
    assert len(client.get("/api/curated/accounts").json()) == 5  # seeding is idempotent


def test_discover_hq_persists_accounts_and_requires_admin(client, monkeypatch):
    _register_and_login(client)

    def fake_discover(
        target=None, max_pages=None, no_cache=False, include_all=False, progress_callback=None
    ):
        return {
            "source": "letterboxd_hq_directory",
            "total_hqs": 2,
            "partial": False,
            "error": None,
            "accounts": [
                {
                    "username": "Criterion",
                    "display_name": "Criterion",
                    "avatar_url": None,
                    "bio": None,
                    "is_hq": True,
                },
                {
                    "username": "neon",
                    "display_name": "NEON",
                    "avatar_url": None,
                    "bio": "Distributor",
                    "is_hq": True,
                },
            ],
        }

    monkeypatch.setattr(letterboxd, "discover_hq_accounts", fake_discover)

    task = _run_task(client, "/api/curated/accounts/discover-hq")
    assert task["name"] == "discover_hq"
    result = task["progress_data"]["result"]
    assert (result["discovered"], result["new"], result["partial"]) == (
        2,
        1,
        False,
    )  # criterion pre-seeded
    usernames = {a["username"] for a in client.get("/api/curated/accounts").json()}
    assert "neon" in usernames

    _bob(client)
    assert client.post("/api/curated/accounts/discover-hq").status_code == 403


def test_inspect_account_persists_profile(client, monkeypatch):
    _register_and_login(client)
    monkeypatch.setattr(
        letterboxd,
        "inspect_account",
        lambda username, no_cache=False, progress_callback=None: {
            "username": username,
            "display_name": "BFI",
            "avatar_url": "https://x/a.jpg",
            "bio": "British Film Institute",
            "account_tier": "HQ",
            "total_public_lists": 17,
        },
    )

    resp = client.post("/api/curated/accounts/BFI/inspect")

    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "bfi"
    assert body["total_public_lists"] == 17
    assert body["account_tier"] == "HQ"
    assert body["last_inspected_at"] is not None


def test_inspect_account_rejects_bad_username_and_maps_errors(client, monkeypatch):
    _register_and_login(client)
    assert client.post("/api/curated/accounts/bad name/inspect").status_code == 400

    def blocked(username, no_cache=False, progress_callback=None):
        raise letterboxd.CloudflareBlock("challenge")

    monkeypatch.setattr(letterboxd, "inspect_account", blocked)
    assert client.post("/api/curated/accounts/bfi/inspect").status_code == 502


def test_account_lists_discovery_saves_disabled_lists_and_links_presets(client, monkeypatch):
    _register_and_login(client)
    monkeypatch.setattr(
        letterboxd,
        "discover_user_lists",
        lambda username, max_pages=None, no_cache=False, progress_callback=None: (
            _discovered_lists()
        ),
    )

    resp = client.get("/api/curated/accounts/sightsoundmag/lists")

    assert resp.status_code == 200
    body = resp.json()
    assert body["discovered"] is True
    assert body["account"]["discovered_lists"] == 2
    by_title = {row["title"]: row for row in body["lists"]}
    women = by_title["Essential Women Directors"]
    assert women["is_enabled"] is False
    assert women["film_count"] == 40
    assert women["badge_prefix"]
    preset = by_title["Sight & Sound Top 100 (2022)"]  # matched to the SS22 preset by URL
    assert preset["preset_key"] == "sight-and-sound-2022"
    assert preset["is_enabled"] is True
    assert preset["preview_posters"] == ["https://letterboxd.com/p/1.jpg"]

    # Re-discovery updates in place rather than duplicating rows.
    again = client.get("/api/curated/accounts/sightsoundmag/lists?refresh=true").json()
    assert len(again["lists"]) == 2

    # Disabled discovered lists stay out of the default canon list; the preset appears once.
    canon_titles = [row["title"] for row in client.get("/api/curated/lists").json()]
    assert "Essential Women Directors" not in canon_titles
    assert canon_titles.count("Sight & Sound Top 100 (2022)") == 1
    with_disabled = [
        row["title"] for row in client.get("/api/curated/lists?include_disabled=true").json()
    ]
    assert "Essential Women Directors" in with_disabled


def test_non_admin_reads_stored_catalog_without_scraping(client, monkeypatch):
    _register_and_login(client)
    calls = []

    def discover(username, max_pages=None, no_cache=False, progress_callback=None):
        calls.append(username)
        return _discovered_lists()

    monkeypatch.setattr(letterboxd, "discover_user_lists", discover)
    _bob(client)

    undiscovered = client.get("/api/curated/accounts/sightsoundmag/lists?refresh=true").json()
    assert undiscovered["discovered"] is False
    assert undiscovered["lists"] == []
    assert calls == []
    assert client.get("/api/curated/accounts/nobody/lists").status_code == 404

    _register_and_login(client, "alice")
    client.get("/api/curated/accounts/sightsoundmag/lists")
    _bob(client)
    stored = client.get("/api/curated/accounts/sightsoundmag/lists?refresh=true").json()
    assert len(stored["lists"]) == 2
    assert calls == ["sightsoundmag"]


def test_enabling_syncing_and_disabling_a_discovered_list(client, monkeypatch):
    _register_and_login(client)
    monkeypatch.setattr(
        letterboxd,
        "discover_user_lists",
        lambda username, max_pages=None, no_cache=False, progress_callback=None: (
            _discovered_lists()
        ),
    )
    lists = client.get("/api/curated/accounts/sightsoundmag/lists").json()["lists"]
    women = next(row for row in lists if row["title"] == "Essential Women Directors")

    monkeypatch.setattr(
        letterboxd,
        "scrape_letterboxd_list",
        _fake_scrape(
            [
                {
                    "title": "Cleo from 5 to 7",
                    "year": 1962,
                    "slug": "cleo-1962",
                    "tmdb_id": 4325,
                    "rank": 1,
                }
            ]
        ),
    )
    _run_task(client, f"/api/curated/sync/{women['id']}")

    enabled = next(
        row for row in client.get("/api/curated/lists").json() if row["id"] == women["id"]
    )
    assert enabled["is_enabled"] is True
    assert enabled["total_items"] == 1
    assert client.post("/api/curated/badges/bulk", json={"movie_ids": [4325]}).json()["4325"]

    patched = client.patch(f"/api/curated/lists/{women['id']}", json={"is_enabled": False})
    assert patched.status_code == 200
    assert patched.json()["is_enabled"] is False
    assert patched.json()["total_items"] == 0
    assert client.post("/api/curated/badges/bulk", json={"movie_ids": [4325]}).json() == {}

    assert (
        client.patch(f"/api/curated/lists/{women['id']}", json={"badge_color": "red"}).status_code
        == 422
    )
    _bob(client)
    assert (
        client.patch(f"/api/curated/lists/{women['id']}", json={"is_enabled": True}).status_code
        == 403
    )


def test_custom_import_of_discovered_url_enables_existing_row(client, monkeypatch):
    _register_and_login(client)
    monkeypatch.setattr(
        letterboxd,
        "discover_user_lists",
        lambda username, max_pages=None, no_cache=False, progress_callback=None: (
            _discovered_lists()
        ),
    )
    client.get("/api/curated/accounts/sightsoundmag/lists")

    resp = client.post(
        "/api/curated/custom",
        json={
            "url": "https://letterboxd.com/sightsoundmag/list/essential-women-directors",
            "badge_prefix": "ewd",
        },
    )

    assert resp.status_code == 201
    assert resp.json()["is_enabled"] is True
    assert resp.json()["badge_prefix"] == "EWD"
    rows = client.get("/api/curated/lists?include_disabled=true").json()
    assert [r["title"] for r in rows].count("Essential Women Directors") == 1


def test_watchlist_sync_rejects_invalid_username(client):
    _register_and_login(client)
    resp = client.post("/api/curated/watchlist/sync", json={"letterboxd_username": "../admin"})
    assert resp.status_code == 400


def test_watchlist_sync_tolerates_missing_title_and_duplicates(client, monkeypatch):
    _register_and_login(client)
    films = [
        {"title": None, "year": None, "slug": "amelie-2001", "tmdb_id": 194},
        {"title": "Amelie again", "year": "2001", "slug": "amelie-dup", "tmdb_id": 194},
        {"title": "No match", "year": 2000, "slug": "nope", "tmdb_id": None},
    ]
    monkeypatch.setattr(letterboxd, "scrape_letterboxd_watchlist", _fake_scrape(films))

    task = _run_task(client, "/api/curated/watchlist/sync", {"letterboxd_username": "alice_lb"})
    assert "error" not in task["progress_data"]
    assert task["progress_data"]["result"]["matched"] == 1


def test_watchlist_sync_404_emits_structured_error(client, monkeypatch):
    import types

    from curl_cffi import requests as curl_requests

    _register_and_login(client)

    def not_found(*args, **kwargs):
        raise curl_requests.exceptions.HTTPError(
            "404", response=types.SimpleNamespace(status_code=404)
        )

    monkeypatch.setattr(letterboxd, "fetch_html", not_found)

    task = _run_task(
        client,
        "/api/curated/watchlist/sync",
        {"letterboxd_username": "ghost_user"},
        expected="failed",
    )

    error = task["progress_data"]["error"]
    assert error["code"] == "watchlist_not_found"
    assert error["status"] == 404
    assert error["username"] == "ghost_user"
    assert "private" in error["message"]
    assert "result" not in task["progress_data"]


def test_image_proxy_requires_login_and_blocks_foreign_hosts(client):
    assert (
        client.get("/api/images/proxy", params={"url": "https://a.ltrbxd.com/x.jpg"}).status_code
        == 401
    )
    _register_and_login(client)
    blocked = client.get(
        "/api/images/proxy", params={"url": "https://169.254.169.254/latest/meta-data"}
    )
    assert blocked.status_code == 400


def test_image_proxy_fetches_once_and_returns_file(client, config_dir):
    import httpx
    import respx

    _register_and_login(client)
    url = "https://a.ltrbxd.com/resized/poster.jpg"
    with respx.mock:
        route = respx.get(url).mock(
            return_value=httpx.Response(
                200, content=b"\xff\xd8\xff\xe0jpegbytes", headers={"content-type": "image/jpeg"}
            )
        )
        first = client.get("/api/images/proxy", params={"url": url})
        second = client.get("/api/images/proxy", params={"url": url})

    assert first.status_code == second.status_code == 200
    assert first.headers["content-type"] == "image/jpeg"
    assert first.content == b"\xff\xd8\xff\xe0jpegbytes"
    assert route.call_count == 1
    assert list((config_dir / "cache_images").glob("*.img"))


def _seed_curator(client, username="criterion"):
    from app.models.curated import CuratedSourceAccount

    with Session(client.db_engine) as session:
        session.add(
            CuratedSourceAccount(username=username, display_name=username.title(), is_hq=True)
        )
        session.commit()


def test_custom_url_from_known_curator_is_linked_and_gets_slug(client, monkeypatch):
    monkeypatch.setattr(
        letterboxd,
        "discover_user_lists",
        lambda username, max_pages=None, no_cache=False, progress_callback=None: {
            "lists": [],
            "partial": False,
            "error": None,
        },
    )
    _register_and_login(client)
    _seed_curator(client, "criterion")

    resp = client.post(
        "/api/curated/custom",
        json={
            "url": "https://letterboxd.com/criterion/list/the-collection/",
            "title": "The Collection",
        },
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["account_username"] == "criterion"
    assert body["slug"] == "the-collection"
    profile = client.get("/api/curated/accounts/criterion/lists").json()
    assert [row["title"] for row in profile["lists"]] == ["The Collection"]

    other = client.post(
        "/api/curated/custom",
        json={"url": "https://letterboxd.com/nobody_known/list/the-collection/"},
    ).json()
    assert other["account_username"] is None
    assert other["slug"] == "the-collection-2"


def test_patch_customizes_preset_and_custom_lists(client):
    _register_and_login(client)
    preset = next(
        r
        for r in client.get("/api/curated/lists").json()
        if r["preset_key"] == "sight-and-sound-2022"
    )

    resp = client.patch(
        f"/api/curated/lists/{preset['id']}",
        json={
            "badge_emoji": "🏆",
            "slug": "ss-greatest",
            "badge_color": "#112233",
            "image_url": "/api/images/proxy?url=https%3A%2F%2Fa.ltrbxd.com%2Fp%2F1.jpg",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert (body["badge_emoji"], body["slug"], body["badge_color"]) == (
        "🏆",
        "ss-greatest",
        "#112233",
    )
    assert body["image_url"] == "https://a.ltrbxd.com/p/1.jpg"  # proxy wrapper unwrapped

    # Addressable by slug, and slugs are unique.
    again = client.patch("/api/curated/lists/ss-greatest", json={"badge_emoji": ""})
    assert again.status_code == 200 and again.json()["badge_emoji"] is None
    custom = client.post(
        "/api/curated/custom", json={"url": "https://letterboxd.com/x/list/y/"}
    ).json()
    clash = client.patch(f"/api/curated/lists/{custom['id']}", json={"slug": "ss-greatest"})
    assert clash.status_code == 409
    assert (
        client.patch(f"/api/curated/lists/{custom['id']}", json={"slug": "Bad Slug!"}).status_code
        == 422
    )
    assert (
        client.patch(
            f"/api/curated/lists/{custom['id']}",
            json={"image_url": "https://evil.example.com/x.jpg"},
        ).status_code
        == 422
    )


def test_list_image_upload_serve_and_clear(client, config_dir):
    _register_and_login(client)
    custom = client.post(
        "/api/curated/custom", json={"url": "https://letterboxd.com/x/list/y/"}
    ).json()
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32

    assert (
        client.put(f"/api/curated/lists/{custom['id']}/image", content=b"not an image").status_code
        == 415
    )
    too_big = png + b"0" * (2 * 1024 * 1024)
    assert (
        client.put(f"/api/curated/lists/{custom['id']}/image", content=too_big).status_code == 413
    )

    resp = client.put(f"/api/curated/lists/{custom['id']}/image", content=png)
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_custom_image"] is True
    assert body["image_url"].startswith(f"/api/curated/lists/{custom['id']}/image?v=")
    served = client.get(f"/api/curated/lists/{custom['id']}/image")
    assert served.status_code == 200 and served.content == png
    assert served.headers["content-type"] == "image/png"

    cleared = client.patch(f"/api/curated/lists/{custom['id']}", json={"clear_image": True}).json()
    assert cleared["has_custom_image"] is False and cleared["image_url"] is None
    assert not list((config_dir / "list_images").glob("*"))
    assert client.get(f"/api/curated/lists/{custom['id']}/image").status_code == 404


def test_browse_lists_search_sort_filter_and_paginate(client):
    _register_and_login(client)
    for slug, title in (("a", "Alpha Noir"), ("b", "Beta Noir"), ("c", "Gamma Westerns")):
        created = client.post(
            "/api/curated/custom",
            json={"url": f"https://letterboxd.com/u/list/{slug}/", "title": title},
        ).json()
        client.patch(f"/api/curated/lists/{created['id']}", json={"is_enabled": slug != "c"})

    everything = client.get("/api/curated/lists/browse", params={"page_size": 100}).json()
    assert everything["total"] == 7  # 4 presets + 3 custom
    assert everything["pages"] == 1

    noir = client.get("/api/curated/lists/browse", params={"q": "noir", "sort": "name"}).json()
    assert [r["title"] for r in noir["items"]] == ["Alpha Noir", "Beta Noir"]
    assert client.get("/api/curated/lists/browse", params={"q": "100%"}).json()["total"] == 0

    disabled = client.get("/api/curated/lists/browse", params={"state": "disabled"}).json()
    assert [r["title"] for r in disabled["items"]] == ["Gamma Westerns"]
    enabled = client.get(
        "/api/curated/lists/browse", params={"state": "enabled", "page_size": 100}
    ).json()
    assert enabled["total"] == 6

    page_two = client.get(
        "/api/curated/lists/browse", params={"sort": "name", "page_size": 3, "page": 2}
    ).json()
    assert page_two["pages"] == 3 and len(page_two["items"]) == 3
    assert client.get("/api/curated/lists/browse", params={"sort": "bogus"}).status_code == 422


def test_browse_lists_popularity_counts_watched_films(client):
    from app.models.curated import CanonMovieBadge
    from app.models.run import Run, RunStep

    _register_and_login(client)
    first = client.post(
        "/api/curated/custom", json={"url": "https://letterboxd.com/u/list/one/", "title": "One"}
    ).json()
    second = client.post(
        "/api/curated/custom", json={"url": "https://letterboxd.com/u/list/two/", "title": "Two"}
    ).json()
    with Session(client.db_engine) as session:
        run = Run(name="Watch")
        session.add(run)
        session.commit()
        for movie_id, status_ in ((1, "watched"), (2, "watched"), (4, "planned")):
            session.add(
                RunStep(
                    run_id=run.id, movie_id=movie_id, movie_title=f"M{movie_id}", status=status_
                )
            )
        for list_id, movies in ((first["id"], (1, 2, 3)), (second["id"], (3, 4))):
            for movie_id in movies:
                session.add(
                    CanonMovieBadge(curated_list_id=list_id, movie_id=movie_id, badge_label="X")
                )
        session.commit()

    items = client.get(
        "/api/curated/lists/browse", params={"sort": "popularity", "page_size": 2}
    ).json()["items"]
    assert (items[0]["title"], items[0]["watched_count"]) == ("One", 2)
    assert items[1]["watched_count"] == 0


def test_browse_accounts_paginates_and_filters(client):
    _register_and_login(client)
    for name in ("amc", "mubi_extra"):
        _seed_curator(client, name)

    page = client.get("/api/curated/accounts/browse", params={"q": "mu"}).json()
    assert [a["username"] for a in page["items"]] == ["mubi", "mubi_extra"]
    small = client.get("/api/curated/accounts/browse", params={"page_size": 2, "kind": "hq"}).json()
    assert len(small["items"]) == 2 and small["pages"] >= 2


def test_account_enabled_counts_are_real_numbers_not_booleans(client):
    from app.models.curated import CuratedList, CuratedSourceAccount

    _register_and_login(client)
    _seed_curator(client, "countme")
    with Session(client.db_engine) as session:
        account = session.exec(
            __import__("sqlmodel")
            .select(CuratedSourceAccount)
            .where(CuratedSourceAccount.username == "countme")
        ).one()
        for index in range(3):
            session.add(
                CuratedList(
                    title=f"L{index}",
                    url=f"https://letterboxd.com/countme/list/l{index}/",
                    badge_prefix="L",
                    is_enabled=index != 2,
                    source_account_id=account.id,
                    slug=f"countme-{index}",
                )
            )
        session.commit()

    browsed = client.get("/api/curated/accounts/browse", params={"q": "countme"}).json()["items"][0]
    listed = next(
        a for a in client.get("/api/curated/accounts").json() if a["username"] == "countme"
    )
    assert (browsed["discovered_lists"], browsed["enabled_lists"]) == (3, 2)
    assert (listed["discovered_lists"], listed["enabled_lists"]) == (3, 2)


def test_blank_stored_titles_are_never_serialized_empty():
    from app.api.routes_curated import CuratedListOut
    from app.models.curated import CuratedList

    row = CuratedList(
        title="  ",
        url="https://letterboxd.com/a/list/hidden-gems/",
        badge_prefix="HG",
        slug="hidden-gems",
    )
    assert CuratedListOut.from_model(row).title == "Hidden Gems"


# --- Phase F0: partial success, TV entries and atomic list persistence ---


def _detail_with(movie_id, **overrides):
    return {
        "id": movie_id,
        "title": f"Film {movie_id}",
        "release_date": "1975-06-01",
        "origin_country": ["AU"],
        "runtime": 100,
        "genres": [],
        "overview": "",
        "tagline": "",
        "status": "Released",
        **overrides,
    }


def test_indexing_reports_per_film_outcomes_and_still_completes(client, monkeypatch):
    """98 of 100 films are indexable: the task completes and names the two that aren't."""
    _register_and_login(client)

    async def movie_detail(_self, movie_id):
        if movie_id == 50:
            return _detail_with(movie_id, origin_country=[])
        if movie_id == 51:
            return _detail_with(movie_id, release_date=None)
        return _detail_with(movie_id)

    monkeypatch.setattr(TMDBClient, "get_movie", movie_detail)
    films = [
        {"title": f"Film {i}", "year": 1975, "slug": f"film-{i}", "tmdb_id": i, "rank": i}
        for i in range(1, 101)
    ]
    monkeypatch.setattr(letterboxd, "scrape_letterboxd_list", _fake_scrape(films))

    _run_task(client, "/api/curated/sync/sight-and-sound-2022")
    with Session(client.db_engine) as session:
        task = session.exec(select(SystemTask).where(SystemTask.name == "canon_hydrate")).one()
        assert task.status == "completed"
        result = task.progress_data["result"]
    assert result["total"] == 100
    assert result["indexed"] == result["hydrated"] == 98
    assert (result["no_country"], result["undated"], result["not_found"]) == (1, 1, 0)
    assert "2 films not indexable" in result["message"]
    counts = client.get(f"/api/curated-lists/{result['list_id']}/slices").json()
    assert counts["hydrated"] == 98 and counts["indexing_error"] is None


def test_indexing_fails_only_when_no_film_can_be_placed(client, monkeypatch):
    _register_and_login(client)
    monkeypatch.setattr(
        TMDBClient, "get_movie", AsyncMock(side_effect=TMDBNotFoundError("No film"))
    )
    films = [
        {"title": f"Film {i}", "year": 1975, "slug": f"film-{i}", "tmdb_id": i, "rank": i}
        for i in range(1, 101)
    ]
    monkeypatch.setattr(letterboxd, "scrape_letterboxd_list", _fake_scrape(films))

    _run_task(client, "/api/curated/sync/sight-and-sound-2022")
    with Session(client.db_engine) as session:
        task = session.exec(select(SystemTask).where(SystemTask.name == "canon_hydrate")).one()
        assert task.status == "failed"
        assert "Indexed 0/100" in task.error


def test_tv_entries_are_counted_but_never_become_movie_badges(client, monkeypatch):
    _register_and_login(client)
    films = [
        {
            "title": "Parasite",
            "year": 2019,
            "slug": "parasite-2019",
            "tmdb_id": 496243,
            "tmdb_type": "movie",
            "rank": 1,
        },
        {
            "title": "Twin Peaks",
            "year": 1990,
            "slug": "twin-peaks",
            "tmdb_id": 1920,
            "tmdb_type": "tv",
            "rank": 2,
        },
    ]
    monkeypatch.setattr(letterboxd, "scrape_letterboxd_list", _fake_scrape(films))

    task = _run_task(client, "/api/curated/sync/sight-and-sound-2022")
    assert task["progress_data"]["result"] == {
        "matched": 1,
        "total_films": 2,
        "tv_titles": 1,
        "unmatched": 0,
        "ambiguous": 0,
        "is_ranked": True,
    }
    with Session(client.db_engine) as session:
        badges = session.exec(select(CanonMovieBadge)).all()
        assert [badge.movie_id for badge in badges] == [496243]


def test_a_failure_after_the_scrape_keeps_the_previous_badges(client, monkeypatch):
    _register_and_login(client)
    first = [
        {"title": "Parasite", "year": 2019, "slug": "parasite-2019", "tmdb_id": 496243, "rank": 1},
        {"title": "Stalker", "year": 1979, "slug": "stalker-1979", "tmdb_id": 10543, "rank": 2},
    ]
    monkeypatch.setattr(letterboxd, "scrape_letterboxd_list", _fake_scrape(first))
    _run_task(client, "/api/curated/sync/sight-and-sound-2022")

    replacement = [
        {"title": "Vertigo", "year": 1958, "slug": "vertigo", "tmdb_id": 426, "rank": 1},
    ]
    monkeypatch.setattr(letterboxd, "scrape_letterboxd_list", _fake_scrape(replacement))
    monkeypatch.setattr(
        routes_curated, "utcnow", lambda: (_ for _ in ()).throw(RuntimeError("disk full"))
    )
    _run_task(client, "/api/curated/sync/sight-and-sound-2022", expected="failed")

    with Session(client.db_engine) as session:
        badges = session.exec(select(CanonMovieBadge)).all()
        assert sorted(badge.movie_id for badge in badges) == [10543, 496243]
        row = session.exec(select(CuratedList).where(CuratedList.preset_key is not None)).first()
        assert row.total_items == 2


def _sync_review_fixture(client, monkeypatch):
    films = [
        {
            "title": "Matched",
            "year": 2000,
            "slug": "matched",
            "tmdb_id": 1,
            "match_tier": "exact",
            "status": "matched",
        },
        {
            "title": "Missing",
            "year": 2001,
            "slug": "missing",
            "tmdb_id": None,
            "status": "unmatched",
            "reason": "No movie found.",
            "imdb_id": "tt1234567",
        },
        {"title": "TV", "slug": "tv", "tmdb_id": 3, "tmdb_type": "tv"},
        {
            "title": "Ambiguous",
            "slug": "ambiguous",
            "status": "ambiguous",
            "reason": "Multiple movies.",
        },
    ]
    monkeypatch.setattr(letterboxd, "scrape_letterboxd_list", _fake_scrape(films))
    result = _run_task(client, "/api/curated/sync/sight-and-sound-2022")
    with Session(client.db_engine) as session:
        list_id = (
            session.exec(
                select(CuratedList).where(CuratedList.preset_key == "sight-and-sound-2022")
            )
            .one()
            .id
        )
    return list_id, films, result


def test_entries_and_exact_stats_are_persisted_and_filterable(client, monkeypatch):
    _register_and_login(client)
    list_id, _, task = _sync_review_fixture(client, monkeypatch)
    assert task["progress_data"]["result"] == {
        "matched": 1,
        "unmatched": 1,
        "tv_titles": 1,
        "ambiguous": 1,
        "total_films": 4,
        "is_ranked": True,
    }
    entries = client.get(f"/api/curated/lists/{list_id}/entries").json()
    assert [entry["position"] for entry in entries] == [1, 2, 3, 4]
    assert entries[1]["imdb_id"] == "tt1234567"
    assert entries[2]["tmdb_id"] is None and entries[2]["status"] == "tv_title"
    assert all(entry["attempted_at"] for entry in entries)
    missing = client.get(f"/api/curated/lists/{list_id}/entries?status=unmatched").json()
    assert [entry["title"] for entry in missing] == ["Missing"]
    assert client.get(f"/api/curated/lists/{list_id}/entries?status=bogus").status_code == 422
    assert client.get("/api/curated/lists/missing/entries").status_code == 404
    for path in ("/api/curated/lists", "/api/curated/lists/browse"):
        data = client.get(path).json()
        rows = data if isinstance(data, list) else data["items"]
        row = next(row for row in rows if row["id"] == list_id)
        assert (row["matched"], row["unmatched"], row["tv_titles"], row["ambiguous"]) == (
            1,
            1,
            1,
            1,
        )


def test_manual_match_survives_reorder_by_slug_and_replaces_badges(client, monkeypatch):
    _register_and_login(client)
    list_id, films, _ = _sync_review_fixture(client, monkeypatch)
    response = client.patch(f"/api/curated/lists/{list_id}/entries/2", json={"tmdb_id": 42})
    assert response.status_code == 200, response.text
    assert response.json()["match_tier"] == "manual" and response.json()["tmdb_id"] == 42
    with Session(client.db_engine) as session:
        badges = session.exec(
            select(CanonMovieBadge).where(CanonMovieBadge.curated_list_id == list_id)
        ).all()
        assert sorted((badge.movie_id, badge.rank) for badge in badges) == [(1, 1), (42, 2)]
    reordered = [films[1], films[0], films[2], films[3]]
    monkeypatch.setattr(letterboxd, "scrape_letterboxd_list", _fake_scrape(reordered))
    task = _run_task(client, f"/api/curated/sync/{list_id}")
    assert task["progress_data"]["result"]["matched"] == 2
    entries = client.get(f"/api/curated/lists/{list_id}/entries").json()
    assert entries[0]["slug"] == "missing" and entries[0]["match_tier"] == "manual"
    assert entries[0]["tmdb_id"] == 42
    # A changed slug at the same position must not inherit the previous manual match.
    reordered[0] = {**reordered[0], "slug": "different-film"}
    monkeypatch.setattr(letterboxd, "scrape_letterboxd_list", _fake_scrape(reordered))
    _run_task(client, f"/api/curated/sync/{list_id}")
    assert client.get(f"/api/curated/lists/{list_id}/entries").json()[0]["tmdb_id"] is None
    with Session(client.db_engine) as session:
        assert [badge.movie_id for badge in session.exec(select(CanonMovieBadge)).all()] == [1]


def test_review_endpoints_are_admin_only_and_validate_manual_movie(client, monkeypatch):
    _register_and_login(client)
    list_id, _, _ = _sync_review_fixture(client, monkeypatch)
    base = f"/api/curated/lists/{list_id}/entries"
    assert client.patch(f"{base}/99", json={"tmdb_id": 42}).status_code == 404
    assert client.patch(f"{base}/2", json={"tmdb_id": 0}).status_code == 422
    monkeypatch.setattr(
        TMDBClient, "get_movie", AsyncMock(side_effect=TMDBNotFoundError("missing"))
    )
    assert client.patch(f"{base}/2", json={"tmdb_id": 404}).status_code == 404
    assert client.get(base).json()[1]["status"] == "unmatched"
    _register_and_login(client, "bob")
    assert client.get(base).status_code == 403
    assert client.patch(f"{base}/2", json={"tmdb_id": 42}).status_code == 403
    client.post("/api/auth/logout")
    assert client.get(base).status_code == 401
    assert client.patch(f"{base}/2", json={"tmdb_id": 42}).status_code == 401


def test_commit_failure_rolls_back_entries_and_badges_together(client, monkeypatch):
    _register_and_login(client)
    list_id, _, _ = _sync_review_fixture(client, monkeypatch)
    with Session(client.db_engine) as session:
        row = session.get(CuratedList, list_id)
        monkeypatch.setattr(
            session, "commit", lambda: (_ for _ in ()).throw(RuntimeError("disk full"))
        )
        with pytest.raises(RuntimeError, match="disk full"):
            routes_curated._persist_sync_result(
                session,
                row,
                {
                    "is_ranked": False,
                    "films": [{"title": "New", "slug": "new", "tmdb_id": 55}],
                },
            )
        session.rollback()
    with Session(client.db_engine) as session:
        entries = session.exec(
            select(CuratedListEntry)
            .where(CuratedListEntry.list_id == list_id)
            .order_by(CuratedListEntry.position)
        ).all()
        assert [entry.slug for entry in entries] == ["matched", "missing", "tv", "ambiguous"]
        assert [badge.movie_id for badge in session.exec(select(CanonMovieBadge)).all()] == [1]
        assert session.get(CuratedList, list_id).film_count == 4


def test_entry_positions_are_unique_and_duplicate_movies_have_one_badge(client, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    _register_and_login(client)
    list_id, films, _ = _sync_review_fixture(client, monkeypatch)
    monkeypatch.setattr(letterboxd, "scrape_letterboxd_list", _fake_scrape([films[0], films[0]]))
    task = _run_task(client, f"/api/curated/sync/{list_id}")
    assert task["progress_data"]["result"]["matched"] == 2
    with Session(client.db_engine) as session:
        assert len(session.exec(select(CanonMovieBadge)).all()) == 1
        assert len(session.exec(select(CuratedListEntry)).all()) == 2
        session.add(
            CuratedListEntry(list_id=list_id, position=1, slug="duplicate", title="Duplicate")
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_entry_migration_preserves_legacy_badges_and_has_one_head(config_dir):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import inspect, text

    from app.config import get_settings

    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    assert len(ScriptDirectory.from_config(config).get_heads()) == 1
    command.upgrade(config, "b3c4d5e6f7a8")
    db = create_engine(get_settings().database_url)
    with db.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO curated_lists (id, title, url, badge_prefix, badge_color, is_ranked, "
                "total_items, is_enabled, film_count, preview_posters, created_at) "
                "VALUES ('old', 'Old', 'https://letterboxd.com/test/list/old/', 'OLD', '#ffffff', "
                "0, 1, 1, 1, '[]', '2026-01-01')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO canon_movie_badges (id, curated_list_id, movie_id, badge_label) "
                "VALUES ('badge', 'old', 42, 'OLD')"
            )
        )
    command.upgrade(config, "head")
    with Session(db) as session:
        assert session.get(CanonMovieBadge, "badge").movie_id == 42
        assert not session.exec(select(CuratedListEntry)).all()
        session.add(
            CuratedListEntry(
                list_id="old",
                position=1,
                slug="film",
                title="Film",
                tmdb_id=42,
                status="matched",
                match_tier="manual",
            )
        )
        session.commit()
        assert session.exec(select(CuratedListEntry)).one().tmdb_id == 42
    command.downgrade(config, "b3c4d5e6f7a8")
    assert "curated_list_entries" not in inspect(db).get_table_names()
    with db.begin() as connection:
        assert connection.execute(text("SELECT movie_id FROM canon_movie_badges")).scalar() == 42
    db.dispose()
