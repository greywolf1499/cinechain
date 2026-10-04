"""Phase 24a: Meet in the Middle - the two-way tunnel with collision victory."""

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app

TMDB_BASE = "https://api.themoviedb.org/3"

# head side: 1 -(100)- 2 -(101)- 3 -(102)- 11 -(103)- 10 :tail side
UNIVERSE = {
    1: ("Head Seed", [100]),
    2: ("Head Two", [100, 101]),
    3: ("Head Three", [101, 102]),
    10: ("Tail Seed", [103]),
    11: ("Tail Two", [103, 102]),
    12: ("Tail Alt", [103, 104]),
    15: ("Bridge Film", [101, 103]),  # connects Head Two (101) and Tail Seed (103)
    20: ("Island", [200]),
}


@pytest.fixture()
def client(config_dir):
    engine = create_engine(
        f"sqlite:///{config_dir}/app_test.db", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        test_client.post("/api/auth/register", json={
            "username": "alice", "password": "password123", "display_name": "Alice"})
        test_client.post("/api/auth/login", json={"username": "alice", "password": "password123"})
        yield test_client
    app.dependency_overrides.clear()


def mock_universe():
    for movie_id, (title, cast) in UNIVERSE.items():
        respx.get(f"{TMDB_BASE}/movie/{movie_id}").mock(return_value=httpx.Response(200, json={
            "id": movie_id, "title": title, "release_date": "2000-01-01", "poster_path": None,
            "overview": "", "origin_country": ["US"], "original_language": "en", "runtime": 100,
            "genres": [], "popularity": 5.0, "status": "Released"}))
        respx.get(f"{TMDB_BASE}/movie/{movie_id}/credits").mock(return_value=httpx.Response(200, json={
            "id": movie_id, "crew": [],
            "cast": [{"id": a, "name": f"Actor {a}", "profile_path": None, "character": "r",
                      "order": i} for i, a in enumerate(cast)]}))
    for actor in {a for _, cast in UNIVERSE.values() for a in cast}:
        respx.get(f"{TMDB_BASE}/person/{actor}/movie_credits").mock(return_value=httpx.Response(200, json={
            "id": actor, "crew": [],
            "cast": [{"id": i, "title": t, "release_date": "2000-01-01", "poster_path": None,
                      "genre_ids": [], "original_language": "en", "popularity": 5.0,
                      "character": "r"} for i, (t, c) in UNIVERSE.items() if actor in c]}))


def create_run(client, head=1, tail=10, expect=201, game_type="meet_in_the_middle", **extra):
    payload = {"name": "Tunnel", "game_type": game_type, "seed_movie_id": head,
               "tail_seed_movie_id": tail, **extra}
    payload = {k: v for k, v in payload.items() if v is not None}
    resp = client.post("/api/runs", json=payload)
    assert resp.status_code == expect, resp.text
    return resp.json()


def log(client, run_id, movie_id, side="head", **extra):
    return client.post(f"/api/runs/{run_id}/steps", json={
        "movie_id": movie_id, "tunnel_side": side, **extra})


def run_detail(client, run_id):
    return client.get(f"/api/runs/{run_id}").json()


def test_engine_is_registered_as_a_tunnel(client):
    engines = {e["game_type"]: e for e in client.get("/api/engines").json()}
    meta = engines["meet_in_the_middle"]
    assert meta["display_name"] == "Meet in the Middle"
    assert {"tunnel", "discover_candidates"} <= set(meta["capabilities"])
    assert "modifiers" not in meta["capabilities"]


def test_creation_needs_two_distinct_seeds(client):
    with respx.mock:
        mock_universe()
        assert "two starting films" in create_run(client, tail=None, expect=422)["detail"]
        assert "different" in create_run(client, head=1, tail=1, expect=422)["detail"]
        assert "only for Meet in the Middle" in create_run(
            client, game_type="cinechain", expect=422)["detail"]


def test_seeds_become_the_head_and_the_tail(client):
    with respx.mock:
        mock_universe()
        run = create_run(client)
    steps = run["steps"]
    assert [(s["movie_id"], s["transition_metadata"]["tunnel_side"]) for s in steps] == [
        (1, "head"), (10, "tail")]


def test_a_side_is_required_and_validated(client):
    with respx.mock:
        mock_universe()
        run_id = create_run(client)["id"]
        missing = client.post(f"/api/runs/{run_id}/steps", json={"movie_id": 2})
        wrong = client.post(f"/api/runs/{run_id}/steps", json={"movie_id": 2, "tunnel_side": "middle"})
    assert missing.status_code == 422 and "head or tail" in missing.json()["detail"]
    assert wrong.status_code == 422


def test_each_end_links_from_its_own_frontier(client):
    with respx.mock:
        mock_universe()
        run_id = create_run(client)["id"]
        ok = log(client, run_id, 2, "head")  # 100 links Head Seed
        no_cast = log(client, run_id, 20, "head")
        # Head Three (101, 102) connects the head end but has nothing in common with the tail seed.
        wrong_end = log(client, run_id, 3, "tail")
        tail_ok = log(client, run_id, 12, "tail")  # 103 links Tail Seed
    assert ok.status_code == 201 and ok.json()["transition_metadata"]["tunnel_side"] == "head"
    assert no_cast.status_code == 409 and "shared credited cast" in no_cast.json()["detail"]["reason"]
    assert wrong_end.status_code == 409
    assert tail_ok.status_code == 201 and tail_ok.json()["transition_metadata"]["tunnel_side"] == "tail"
    assert run_detail(client, run_id)["status"] == "active"


def test_extending_one_end_that_also_reaches_the_other_is_a_collision(client):
    with respx.mock:
        mock_universe()
        run_id = create_run(client)["id"]
        log(client, run_id, 2, "head")
        log(client, run_id, 3, "head")  # head frontier: Head Three (101, 102)
        # Tail Two links Tail Seed via 103 AND shares 102 with Head Three: the chains collide.
        final = log(client, run_id, 11, "tail")
        detail = run_detail(client, run_id)
        late = log(client, run_id, 12, "tail")

    assert final.status_code == 201
    assert final.json()["transition_metadata"]["collision"] is True
    assert final.json()["transition_metadata"]["collision_with"] == 3
    assert detail["status"] == "completed"
    assert detail["status_reason"] == "Chains collided at Tail Two!"
    assert late.status_code == 409 and "can no longer be played" in late.json()["detail"]["reason"]


def test_the_head_can_collide_too(client):
    with respx.mock:
        mock_universe()
        run_id = create_run(client)["id"]
        log(client, run_id, 2, "head")
        # Bridge Film links Head Two (101) and the tail frontier Tail Seed (103).
        final = log(client, run_id, 15, "head")
    assert final.json()["transition_metadata"]["collision"] is True
    assert run_detail(client, run_id)["status_reason"] == "Chains collided at Bridge Film!"


def test_a_wildcard_cannot_fake_a_collision(client):
    with respx.mock:
        mock_universe()
        run_id = create_run(client)["id"]
        # Bridge Film has no cast in common with Head Seed, so on its own it breaks the link rule.
        forced = log(client, run_id, 15, "head", force=True)
    assert forced.status_code == 201  # wildcard spent on the broken head link
    assert forced.json()["transition_metadata"]["wildcard_used"] is True
    assert "collision" not in forced.json()["transition_metadata"]
    assert run_detail(client, run_id)["status"] == "active"


def test_clients_cannot_forge_a_collision(client):
    with respx.mock:
        mock_universe()
        run_id = create_run(client)["id"]
        step = log(client, run_id, 2, "head", transition_metadata={
            "collision": True, "tunnel_side": "tail", "actor_id": 100})
        assert step.status_code == 201
        meta = step.json()["transition_metadata"]
        patched = client.patch(
            f"/api/runs/{run_id}/steps/{step.json()['id']}",
            json={"transition_metadata": {"collision": True, "note": "hi", "tunnel_side": "tail"}})
    assert "collision" not in meta and meta["tunnel_side"] == "head"
    assert patched.status_code == 200
    assert "collision" not in patched.json()["transition_metadata"]
    assert patched.json()["transition_metadata"]["tunnel_side"] == "head"
    assert patched.json()["transition_metadata"]["note"] == "hi"
    assert run_detail(client, run_id)["status"] == "active"


def test_undoing_the_colliding_step_reopens_the_tunnel(client):
    with respx.mock:
        mock_universe()
        run_id = create_run(client)["id"]
        log(client, run_id, 2, "head")
        final = log(client, run_id, 15, "head").json()
        deleted = client.delete(f"/api/runs/{run_id}/steps/{final['id']}")
    assert deleted.status_code == 204
    detail = run_detail(client, run_id)
    assert detail["status"] == "active" and detail["status_reason"] is None


def test_validate_previews_the_collision(client):
    with respx.mock:
        mock_universe()
        run_id = create_run(client)["id"]
        log(client, run_id, 2, "head")
        collides = client.post(f"/api/runs/{run_id}/validate", json={"movie_id": 15, "tunnel_side": "head"})
        plain = client.post(f"/api/runs/{run_id}/validate", json={"movie_id": 3, "tunnel_side": "head"})
        invalid = client.post(f"/api/runs/{run_id}/validate", json={"movie_id": 20, "tunnel_side": "head"})
        no_side = client.post(f"/api/runs/{run_id}/validate", json={"movie_id": 3})
    assert collides.json()["valid"] is True and collides.json()["collision"] is True
    assert plain.json()["valid"] is True and plain.json()["collision"] is False
    assert invalid.json()["valid"] is False and invalid.json()["collision"] is False
    assert no_side.status_code == 422


def test_tunnel_state_measures_the_distance_between_frontiers(client):
    with respx.mock:
        mock_universe()
        run_id = create_run(client)["id"]
        far = client.get(f"/api/runs/{run_id}/tunnel").json()
        log(client, run_id, 2, "head")
        log(client, run_id, 3, "head")
        near = client.get(f"/api/runs/{run_id}/tunnel").json()

    assert far["head_frontier_movie_id"] == 1 and far["tail_frontier_movie_id"] == 10
    assert far["distance_hops"] == 3  # 1 - 2 - Bridge Film - 10
    assert near["head_frontier_movie_id"] == 3 and near["head_steps"] == 3 and near["tail_steps"] == 1
    assert near["distance_hops"] == 2  # 3 - 11 - 10 (the run's own films never reappear)
    assert near["collided"] is False


def test_tunnel_state_after_a_collision_is_zero(client):
    with respx.mock:
        mock_universe()
        run_id = create_run(client)["id"]
        log(client, run_id, 2, "head")
        log(client, run_id, 15, "head")
        state = client.get(f"/api/runs/{run_id}/tunnel").json()
    assert state["collided"] is True and state["distance_hops"] == 0


def test_tunnel_state_reports_no_route_gracefully(client):
    with respx.mock:
        mock_universe()
        run_id = create_run(client, head=1, tail=20)["id"]
        state = client.get(f"/api/runs/{run_id}/tunnel").json()
    assert state["distance_hops"] is None and state["message"]


def test_tunnel_endpoint_is_for_tunnel_runs_only(client):
    run = client.post("/api/runs", json={"name": "Plain", "game_type": "cinechain"}).json()
    assert client.get(f"/api/runs/{run['id']}/tunnel").status_code == 400


def test_pick_next_pool_works_from_either_frontier(client):
    with respx.mock:
        mock_universe()
        run_id = create_run(client)["id"]
        head_pool = client.get(f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 1}).json()
        tail_pool = client.get(f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 10}).json()
    assert {c["movie_id"] for c in head_pool} == {2}
    assert {c["movie_id"] for c in tail_pool} == {11, 12, 15}
