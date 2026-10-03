"""Curated canon API tests: admin gating, preset/custom list management, SSE
sync persistence (via a monkeypatched scraper - no real network), and the
bulk badges lookup used by movie cards across the app.
"""

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
        test_client.db_engine = engine
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


def _fake_scrape(films):
    def _scrape(url, tmdb_api_key=None, max_pages=None, no_cache=False, progress_callback=None):
        if progress_callback:
            progress_callback(
                {"stage": "fetch_page", "current": 1, "total": None, "message": "..."})
            progress_callback(
                {"stage": "page_done", "current": len(films), "total": None, "message": "..."})
        return {"is_ranked": True, "total_films": len(films), "films": films}
    return _scrape


def test_curated_lists_returns_all_four_presets_unsynced_by_default(client):
    _register_and_login(client)
    resp = client.get("/api/curated/lists")
    assert resp.status_code == 200
    body = resp.json()
    preset_keys = {row["preset_key"] for row in body}
    assert preset_keys == {
        "sight-and-sound-2022", "sight-and-sound-directors",
        "letterboxd-top-250", "letterboxd-docs-250",
    }
    assert all(row["total_items"] == 0 for row in body)
    assert all(row["last_synced_at"] is None for row in body)


def test_sync_and_custom_require_admin(client):
    _register_and_login(client, "alice")  # first user - admin
    client.post(
        "/api/auth/register",
        json={"username": "bob", "password": "password123", "display_name": "Bob"},
    )
    client.post("/api/auth/login",
                json={"username": "bob", "password": "password123"})

    assert client.post(
        "/api/curated/sync/sight-and-sound-2022").status_code == 403
    assert client.post(
        "/api/curated/custom",
        json={"url": "https://letterboxd.com/someone/list/my-list/"},
    ).status_code == 403


def test_create_custom_list_derives_badge_prefix(client):
    _register_and_login(client)
    resp = client.post(
        "/api/curated/custom",
        json={"url": "https://letterboxd.com/someone/list/cannes-palme-dor-winners/",
              "badge_color": "#ff0000"},
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
        json={"url": "https://letterboxd.com/someone/list/anything/",
              "badge_prefix": "palme"},
    )
    assert resp.json()["badge_prefix"] == "PALME"


def test_sync_preset_persists_badges_and_updates_list_metadata(client, monkeypatch):
    _register_and_login(client)
    films = [
        {"title": "Parasite", "year": 2019, "slug": "parasite-2019",
         "tmdb_id": 496243, "rank": 1},
        {"title": "Stalker", "year": 1979, "slug": "stalker-1979",
         "tmdb_id": 10543, "rank": 2},
    ]
    monkeypatch.setattr(
        letterboxd, "scrape_letterboxd_list", _fake_scrape(films))

    resp = client.post("/api/curated/sync/sight-and-sound-2022")
    assert resp.status_code == 200
    assert "event: result" in resp.text
    assert "event: done" in resp.text

    lists_resp = client.get("/api/curated/lists")
    ss22 = next(row for row in lists_resp.json()
                if row["preset_key"] == "sight-and-sound-2022")
    assert ss22["total_items"] == 2
    assert ss22["is_ranked"] is True
    assert ss22["last_synced_at"] is not None

    badges_resp = client.post(
        "/api/curated/badges/bulk", json={"movie_ids": [496243, 10543, 603]})
    body = badges_resp.json()
    assert body["496243"][0]["badge_label"] == "SS22 #1"
    assert body["10543"][0]["badge_label"] == "SS22 #2"
    assert "603" not in body


def test_resync_replaces_stale_badges(client, monkeypatch):
    _register_and_login(client)
    monkeypatch.setattr(
        letterboxd, "scrape_letterboxd_list",
        _fake_scrape([{"title": "Parasite", "year": 2019,
                      "slug": "parasite-2019", "tmdb_id": 496243, "rank": 1}]),
    )
    client.post("/api/curated/sync/sight-and-sound-2022")

    monkeypatch.setattr(
        letterboxd, "scrape_letterboxd_list",
        _fake_scrape([{"title": "Stalker", "year": 1979,
                      "slug": "stalker-1979", "tmdb_id": 10543, "rank": 1}]),
    )
    client.post("/api/curated/sync/sight-and-sound-2022")

    badges_resp = client.post(
        "/api/curated/badges/bulk", json={"movie_ids": [496243, 10543]})
    body = badges_resp.json()
    assert "496243" not in body
    assert body["10543"][0]["badge_label"] == "SS22 #1"


def test_watchlist_sync_persists_for_current_user(client, monkeypatch):
    _register_and_login(client)
    films = [{"title": "Amelie", "year": 2001,
             "slug": "amelie-2001", "tmdb_id": 194, "rank": 1}]
    monkeypatch.setattr(
        letterboxd, "scrape_letterboxd_watchlist", _fake_scrape(films))

    resp = client.post("/api/curated/watchlist/sync",
                       json={"letterboxd_username": "alice_lb"})
    assert resp.status_code == 200
    assert "event: result" in resp.text
    assert "event: done" in resp.text


# ---------------------------------------------------------
# 3-tier architecture: curator accounts -> published lists -> enabled canons
# ---------------------------------------------------------
def _discovered_lists():
    return {
        "username": "sightsoundmag", "pages": 1, "partial": False, "error": None,
        "lists": [
            {"title": "Sight & Sound Greatest Films", "slug": "x",
             "url": letterboxd.PRESETS["sight-and-sound-2022"]["url"],
             "total_films": 100, "description": "The poll.",
             "preview_posters": ["https://letterboxd.com/p/1.jpg"], "preview_slugs": []},
            {"title": "Essential Women Directors", "slug": "essential-women-directors",
             "url": "https://letterboxd.com/sightsoundmag/list/essential-women-directors/",
             "total_films": 40, "description": None, "preview_posters": [], "preview_slugs": []},
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
        "criterion", "sightsoundmag", "bfi", "mubi", "a24"}
    assert all(a["is_hq"] for a in resp.json())
    assert len(client.get("/api/curated/accounts").json()) == 5  # seeding is idempotent


def test_discover_hq_persists_accounts_and_requires_admin(client, monkeypatch):
    _register_and_login(client)

    def fake_discover(target=None, max_pages=None, no_cache=False, include_all=False,
                      progress_callback=None):
        return {"source": "letterboxd_hq_directory", "total_hqs": 2, "partial": False, "error": None,
                "accounts": [
                    {"username": "Criterion", "display_name": "Criterion", "avatar_url": None,
                     "bio": None, "is_hq": True},
                    {"username": "neon", "display_name": "NEON", "avatar_url": None,
                     "bio": "Distributor", "is_hq": True},
                ]}

    monkeypatch.setattr(letterboxd, "discover_hq_accounts", fake_discover)

    resp = client.post("/api/curated/accounts/discover-hq")
    assert resp.status_code == 200
    assert "event: result" in resp.text
    assert '"new": 1' in resp.text  # criterion was already seeded, neon is new
    usernames = {a["username"] for a in client.get("/api/curated/accounts").json()}
    assert "neon" in usernames

    _bob(client)
    assert client.post("/api/curated/accounts/discover-hq").status_code == 403


def test_inspect_account_persists_profile(client, monkeypatch):
    _register_and_login(client)
    monkeypatch.setattr(
        letterboxd, "inspect_account",
        lambda username, no_cache=False, progress_callback=None: {
            "username": username, "display_name": "BFI", "avatar_url": "https://x/a.jpg",
            "bio": "British Film Institute", "account_tier": "HQ", "total_public_lists": 17},
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
        letterboxd, "discover_user_lists",
        lambda username, max_pages=None, no_cache=False, progress_callback=None: _discovered_lists())

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
    with_disabled = [row["title"] for row in
                     client.get("/api/curated/lists?include_disabled=true").json()]
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
        letterboxd, "discover_user_lists",
        lambda username, max_pages=None, no_cache=False, progress_callback=None: _discovered_lists())
    lists = client.get("/api/curated/accounts/sightsoundmag/lists").json()["lists"]
    women = next(row for row in lists if row["title"] == "Essential Women Directors")

    monkeypatch.setattr(
        letterboxd, "scrape_letterboxd_list",
        _fake_scrape([{"title": "Cleo from 5 to 7", "year": 1962, "slug": "cleo-1962",
                       "tmdb_id": 4325, "rank": 1}]))
    assert client.post(f"/api/curated/sync/{women['id']}").status_code == 200

    enabled = next(row for row in client.get("/api/curated/lists").json() if row["id"] == women["id"])
    assert enabled["is_enabled"] is True
    assert enabled["total_items"] == 1
    assert client.post("/api/curated/badges/bulk", json={"movie_ids": [4325]}).json()["4325"]

    patched = client.patch(f"/api/curated/lists/{women['id']}", json={"is_enabled": False})
    assert patched.status_code == 200
    assert patched.json()["is_enabled"] is False
    assert patched.json()["total_items"] == 0
    assert client.post("/api/curated/badges/bulk", json={"movie_ids": [4325]}).json() == {}

    assert client.patch(f"/api/curated/lists/{women['id']}",
                        json={"badge_color": "red"}).status_code == 422
    _bob(client)
    assert client.patch(f"/api/curated/lists/{women['id']}", json={"is_enabled": True}).status_code == 403


def test_custom_import_of_discovered_url_enables_existing_row(client, monkeypatch):
    _register_and_login(client)
    monkeypatch.setattr(
        letterboxd, "discover_user_lists",
        lambda username, max_pages=None, no_cache=False, progress_callback=None: _discovered_lists())
    client.get("/api/curated/accounts/sightsoundmag/lists")

    resp = client.post("/api/curated/custom", json={
        "url": "https://letterboxd.com/sightsoundmag/list/essential-women-directors",
        "badge_prefix": "ewd"})

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
    monkeypatch.setattr(
        letterboxd, "scrape_letterboxd_watchlist", _fake_scrape(films))

    resp = client.post("/api/curated/watchlist/sync",
                       json={"letterboxd_username": "alice_lb"})
    assert resp.status_code == 200
    assert "event: error" not in resp.text
    assert '"matched": 1' in resp.text


def test_watchlist_sync_404_emits_structured_error(client, monkeypatch):
    import types

    from curl_cffi import requests as curl_requests

    _register_and_login(client)

    def not_found(*args, **kwargs):
        raise curl_requests.exceptions.HTTPError(
            "404", response=types.SimpleNamespace(status_code=404))

    monkeypatch.setattr(letterboxd, "fetch_html", not_found)

    resp = client.post("/api/curated/watchlist/sync",
                       json={"letterboxd_username": "ghost_user"})

    assert resp.status_code == 200
    error_line = next(line for line in resp.text.splitlines()
                      if line.startswith("data: ") and "watchlist_not_found" in line)
    body = json.loads(error_line.removeprefix("data: "))
    assert body["code"] == "watchlist_not_found"
    assert body["status"] == 404
    assert body["username"] == "ghost_user"
    assert "private" in body["message"]
    assert "event: result" not in resp.text


def test_image_proxy_requires_login_and_blocks_foreign_hosts(client):
    assert client.get("/api/images/proxy", params={"url": "https://a.ltrbxd.com/x.jpg"}).status_code == 401
    _register_and_login(client)
    blocked = client.get("/api/images/proxy", params={"url": "https://169.254.169.254/latest/meta-data"})
    assert blocked.status_code == 400


def test_image_proxy_fetches_once_and_returns_file(client, config_dir):
    import httpx
    import respx

    _register_and_login(client)
    url = "https://a.ltrbxd.com/resized/poster.jpg"
    with respx.mock:
        route = respx.get(url).mock(return_value=httpx.Response(
            200, content=b"\xff\xd8\xff\xe0jpegbytes", headers={"content-type": "image/jpeg"}))
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
        session.add(CuratedSourceAccount(
            username=username, display_name=username.title(), is_hq=True))
        session.commit()


def test_custom_url_from_known_curator_is_linked_and_gets_slug(client, monkeypatch):
    monkeypatch.setattr(
        letterboxd, "discover_user_lists",
        lambda username, max_pages=None, no_cache=False, progress_callback=None: {
            "lists": [], "partial": False, "error": None})
    _register_and_login(client)
    _seed_curator(client, "criterion")

    resp = client.post("/api/curated/custom", json={
        "url": "https://letterboxd.com/criterion/list/the-collection/", "title": "The Collection"})

    assert resp.status_code == 201
    body = resp.json()
    assert body["account_username"] == "criterion"
    assert body["slug"] == "the-collection"
    profile = client.get("/api/curated/accounts/criterion/lists").json()
    assert [row["title"] for row in profile["lists"]] == ["The Collection"]

    other = client.post("/api/curated/custom", json={
        "url": "https://letterboxd.com/nobody_known/list/the-collection/"}).json()
    assert other["account_username"] is None
    assert other["slug"] == "the-collection-2"


def test_patch_customizes_preset_and_custom_lists(client):
    _register_and_login(client)
    preset = next(r for r in client.get("/api/curated/lists").json() if r["preset_key"] == "sight-and-sound-2022")

    resp = client.patch(f"/api/curated/lists/{preset['id']}", json={
        "badge_emoji": "🏆", "slug": "ss-greatest", "badge_color": "#112233",
        "image_url": "/api/images/proxy?url=https%3A%2F%2Fa.ltrbxd.com%2Fp%2F1.jpg"})

    assert resp.status_code == 200
    body = resp.json()
    assert (body["badge_emoji"], body["slug"], body["badge_color"]) == ("🏆", "ss-greatest", "#112233")
    assert body["image_url"] == "https://a.ltrbxd.com/p/1.jpg"  # proxy wrapper unwrapped

    # Addressable by slug, and slugs are unique.
    again = client.patch("/api/curated/lists/ss-greatest", json={"badge_emoji": ""})
    assert again.status_code == 200 and again.json()["badge_emoji"] is None
    custom = client.post("/api/curated/custom", json={"url": "https://letterboxd.com/x/list/y/"}).json()
    clash = client.patch(f"/api/curated/lists/{custom['id']}", json={"slug": "ss-greatest"})
    assert clash.status_code == 409
    assert client.patch(f"/api/curated/lists/{custom['id']}", json={"slug": "Bad Slug!"}).status_code == 422
    assert client.patch(f"/api/curated/lists/{custom['id']}",
                        json={"image_url": "https://evil.example.com/x.jpg"}).status_code == 422


def test_list_image_upload_serve_and_clear(client, config_dir):
    _register_and_login(client)
    custom = client.post("/api/curated/custom", json={"url": "https://letterboxd.com/x/list/y/"}).json()
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32

    assert client.put(f"/api/curated/lists/{custom['id']}/image", content=b"not an image").status_code == 415
    too_big = png + b"0" * (2 * 1024 * 1024)
    assert client.put(f"/api/curated/lists/{custom['id']}/image", content=too_big).status_code == 413

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
        created = client.post("/api/curated/custom", json={
            "url": f"https://letterboxd.com/u/list/{slug}/", "title": title}).json()
        client.patch(f"/api/curated/lists/{created['id']}", json={"is_enabled": slug != "c"})

    everything = client.get("/api/curated/lists/browse", params={"page_size": 100}).json()
    assert everything["total"] == 7  # 4 presets + 3 custom
    assert everything["pages"] == 1

    noir = client.get("/api/curated/lists/browse", params={"q": "noir", "sort": "name"}).json()
    assert [r["title"] for r in noir["items"]] == ["Alpha Noir", "Beta Noir"]
    assert client.get("/api/curated/lists/browse", params={"q": "100%"}).json()["total"] == 0

    disabled = client.get("/api/curated/lists/browse", params={"state": "disabled"}).json()
    assert [r["title"] for r in disabled["items"]] == ["Gamma Westerns"]
    enabled = client.get("/api/curated/lists/browse", params={"state": "enabled", "page_size": 100}).json()
    assert enabled["total"] == 6

    page_two = client.get("/api/curated/lists/browse",
                          params={"sort": "name", "page_size": 3, "page": 2}).json()
    assert page_two["pages"] == 3 and len(page_two["items"]) == 3
    assert client.get("/api/curated/lists/browse", params={"sort": "bogus"}).status_code == 422


def test_browse_lists_popularity_counts_watched_films(client):
    from app.models.curated import CanonMovieBadge
    from app.models.run import Run, RunStep

    _register_and_login(client)
    first = client.post("/api/curated/custom", json={"url": "https://letterboxd.com/u/list/one/", "title": "One"}).json()
    second = client.post("/api/curated/custom", json={"url": "https://letterboxd.com/u/list/two/", "title": "Two"}).json()
    with Session(client.db_engine) as session:
        run = Run(name="Watch")
        session.add(run)
        session.commit()
        for movie_id, status_ in ((1, "watched"), (2, "watched"), (4, "planned")):
            session.add(RunStep(run_id=run.id, movie_id=movie_id, movie_title=f"M{movie_id}", status=status_))
        for list_id, movies in ((first["id"], (1, 2, 3)), (second["id"], (3, 4))):
            for movie_id in movies:
                session.add(CanonMovieBadge(curated_list_id=list_id, movie_id=movie_id, badge_label="X"))
        session.commit()

    items = client.get("/api/curated/lists/browse", params={"sort": "popularity", "page_size": 2}).json()["items"]
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
            __import__("sqlmodel").select(CuratedSourceAccount).where(CuratedSourceAccount.username == "countme")
        ).one()
        for index in range(3):
            session.add(CuratedList(title=f"L{index}", url=f"https://letterboxd.com/countme/list/l{index}/",
                                    badge_prefix="L", is_enabled=index != 2, source_account_id=account.id,
                                    slug=f"countme-{index}"))
        session.commit()

    browsed = client.get("/api/curated/accounts/browse", params={"q": "countme"}).json()["items"][0]
    listed = next(a for a in client.get("/api/curated/accounts").json() if a["username"] == "countme")
    assert (browsed["discovered_lists"], browsed["enabled_lists"]) == (3, 2)
    assert (listed["discovered_lists"], listed["enabled_lists"]) == (3, 2)
