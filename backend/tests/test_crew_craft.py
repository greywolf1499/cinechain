"""Phase 25a: key-crew ingestion (cached_crew_credits) and the Crew & Craft Trail engine."""

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import get_session
from app.main import app
from app.models.cache import CachedCrewCredit, CachedMovie

TMDB_BASE = "https://api.themoviedb.org/3"

ZIMMER, DEAKINS, PEELE, KAUFMAN, NOLAN, DICAPRIO, EDITOR = 10, 22, 40, 50, 30, 1, 60

# movie id -> (title, cast ids, crew [(person id, name, job, department)])
UNIVERSE = {
    1: ("Inception", [DICAPRIO], [
        (ZIMMER, "Hans Zimmer", "Original Music Composer", "Sound"),
        (20, "Wally Pfister", "Director of Photography", "Camera"),
        (NOLAN, "Christopher Nolan", "Director", "Directing"),
        (NOLAN, "Christopher Nolan", "Screenplay", "Writing"),
        (EDITOR, "Lee Smith", "Editor", "Editing"),
        (61, "Some Grip", "Key Grip", "Crew"),
    ]),
    2: ("Gladiator", [2], [
        (ZIMMER, "Hans Zimmer", "Original Music Composer", "Sound"),
        (21, "John Mathieson", "Director of Photography", "Camera"),
        (EDITOR, "Lee Smith", "Editor", "Editing"),
    ]),
    3: ("Sicario", [3], [
        (DEAKINS, "Roger Deakins", "Director of Photography", "Camera"),
        (11, "Johann Johannsson", "Original Music Composer", "Sound"),
    ]),
    4: ("Blade Runner 2049", [4], [
        (DEAKINS, "Roger Deakins", "Director of Photography", "Camera"),
        (12, "Benjamin Wallfisch", "Original Music Composer", "Sound"),
    ]),
    5: ("Get Out", [5], [(PEELE, "Jordan Peele", "Director", "Directing"),
                         (PEELE, "Jordan Peele", "Writer", "Writing")]),
    6: ("Keanu", [PEELE], []),
    7: ("Island", [7], [(70, "Nobody", "Director", "Directing")]),
    8: ("Eternal Sunshine", [8], [(KAUFMAN, "Charlie Kaufman", "Screenplay", "Writing")]),
    9: ("Anomalisa", [9], [(KAUFMAN, "Charlie Kaufman", "Writer", "Writing")]),
    10: ("Tenet", [DICAPRIO], [(NOLAN, "Christopher Nolan", "Director", "Directing")]),
    11: ("Just Editing", [11], [(EDITOR, "Lee Smith", "Editor", "Editing")]),
    12: ("Dunkirk", [12], [
        (NOLAN, "Christopher Nolan", "Director", "Directing"),
        (ZIMMER, "Hans Zimmer", "Original Music Composer", "Sound")]),
}


@pytest.fixture()
def db_engine(config_dir):
    engine = create_engine(
        f"sqlite:///{config_dir}/app_test.db", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture()
def client(db_engine):
    def override_get_session():
        with Session(db_engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        test_client.post("/api/auth/register", json={
            "username": "alice", "password": "password123", "display_name": "Alice"})
        test_client.post("/api/auth/login", json={"username": "alice", "password": "password123"})
        yield test_client
    app.dependency_overrides.clear()


def mock_universe():
    credits = {}
    for movie_id, (title, cast, crew) in UNIVERSE.items():
        respx.get(f"{TMDB_BASE}/movie/{movie_id}").mock(return_value=httpx.Response(200, json={
            "id": movie_id, "title": title, "release_date": "2010-01-01", "poster_path": None,
            "overview": "", "origin_country": ["US"], "original_language": "en", "runtime": 100,
            "genres": [], "popularity": 5.0, "status": "Released"}))
        credits[movie_id] = respx.get(f"{TMDB_BASE}/movie/{movie_id}/credits").mock(
            return_value=httpx.Response(200, json={
                "id": movie_id,
                "cast": [{"id": a, "name": f"Actor {a}" if a != PEELE else "Jordan Peele",
                          "profile_path": None, "character": f"Role {a}", "order": i}
                         for i, a in enumerate(cast)],
                "crew": [{"id": p, "name": n, "job": j, "department": d, "profile_path": None}
                         for p, n, j, d in crew]}))
    people = {a for _, cast, _ in UNIVERSE.values() for a in cast}
    people |= {p for _, _, crew in UNIVERSE.values() for p, *_ in crew}
    for person in people:
        respx.get(f"{TMDB_BASE}/person/{person}/movie_credits").mock(
            return_value=httpx.Response(200, json={
                "id": person,
                "cast": [{"id": m, "title": t, "release_date": "2010-01-01", "poster_path": None,
                          "genre_ids": [], "original_language": "en", "popularity": 5.0,
                          "character": f"Role {person}"}
                         for m, (t, cast, _) in UNIVERSE.items() if person in cast],
                "crew": [{"id": m, "title": t, "release_date": "2010-01-01", "poster_path": None,
                          "genre_ids": [], "original_language": "en", "popularity": 5.0,
                          "job": j, "department": d}
                         for m, (t, _, crew) in UNIVERSE.items() for p, _, j, d in crew
                         if p == person]}))
    return credits


def create_run(client, seed=1, **rules):
    payload = {"name": "Craft", "game_type": "crew_craft", "seed_movie_id": seed,
               "rules_config": {"preset": "standard", "allow_repeats": "strict",
                                "no_consecutive_actor": True, "max_cast_order": 15,
                                "min_runtime": 40, "wildcards_budget": 2, **rules}}
    resp = client.post("/api/runs", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def validate(client, run_id, movie_id):
    return client.post(f"/api/runs/{run_id}/validate", json={"movie_id": movie_id}).json()


def roles(result):
    return {(c["actor_name"], c["role_in_from"], c["role_in_to"]) for c in result["connections"]}


# --- ingestion / storage --------------------------------------------------------------


def test_only_the_key_crafts_are_stored(client, db_engine):
    with respx.mock:
        mock_universe()
        crew = client.get("/api/movies/1/crew").json()
    assert {(c["name"], c["role"], c["job"], c["department"]) for c in crew} == {
        ("Hans Zimmer", "composer", "Original Music Composer", "Sound"),
        ("Wally Pfister", "cinematographer", "Director of Photography", "Camera"),
        ("Christopher Nolan", "director", "Director", "Directing"),
        ("Christopher Nolan", "writer", "Screenplay", "Writing"),
    }
    with Session(db_engine) as session:
        stored = session.exec(select(CachedCrewCredit)).all()
        assert {row.job for row in stored} == {
            "Original Music Composer", "Director of Photography", "Director", "Screenplay"}
        assert not [r for r in stored if r.person_id in (EDITOR, 61)]  # editors, grips: never stored
        assert session.get(CachedMovie, 1).crew_fetched_at is not None


def test_crew_is_fetched_just_in_time_then_served_from_the_cache(client):
    with respx.mock:
        credits = mock_universe()
        first = client.get("/api/movies/1/crew").json()
        second = client.get("/api/movies/1/crew").json()
    assert first == second and credits[1].call_count == 1


def test_a_film_with_no_key_crew_is_cached_as_empty(client):
    with respx.mock:
        credits = mock_universe()
        assert client.get("/api/movies/6/crew").json() == []
        assert client.get("/api/movies/6/crew").json() == []
    assert credits[6].call_count == 1


# --- validation -----------------------------------------------------------------------


def test_engine_is_registered(client):
    meta = {e["game_type"]: e for e in client.get("/api/engines").json()}["crew_craft"]
    assert meta["display_name"] == "Crew & Craft Trail"
    assert {"crew_craft", "discover_candidates", "modifiers"} <= set(meta["capabilities"])


def test_composer_to_composer(client):
    with respx.mock:
        mock_universe()
        result = validate(client, create_run(client, seed=1), 2)
    assert result["valid"]
    assert roles(result) == {("Hans Zimmer", "composer", "composer")}


def test_cinematographer_to_cinematographer(client):
    with respx.mock:
        mock_universe()
        result = validate(client, create_run(client, seed=3), 4)
    assert result["valid"]
    assert roles(result) == {("Roger Deakins", "cinematographer", "cinematographer")}


def test_screenplay_and_writer_are_the_same_craft(client):
    with respx.mock:
        mock_universe()
        result = validate(client, create_run(client, seed=8), 9)
    assert result["valid"]
    assert roles(result) == {("Charlie Kaufman", "writer", "writer")}


def test_director_to_director_and_actor_to_actor(client):
    with respx.mock:
        mock_universe()
        run_id = create_run(client, seed=1)
        director = validate(client, run_id, 10)  # Nolan directs both; DiCaprio also acts in both
    assert director["valid"]
    assert ("Christopher Nolan", "director", "director") in roles(director)
    assert ("Actor 1", "actor", "actor") in roles(director)


def test_cross_role_director_to_actor(client):
    with respx.mock:
        mock_universe()
        result = validate(client, create_run(client, seed=5), 6)
    assert result["valid"]
    assert roles(result) == {("Jordan Peele", "director", "actor")}
    link = result["connections"][0]
    assert link["character_in_to"] == f"Role {PEELE}" and link["character_in_from"] is None


def test_unlisted_crew_jobs_are_not_links(client):
    with respx.mock:
        mock_universe()
        # both films credit the same Editor, but editors don't count
        result = validate(client, create_run(client, seed=2), 11)
    assert not result["valid"] and "No shared cast or crew" in result["reason"]


def test_no_shared_person_is_invalid(client):
    with respx.mock:
        mock_universe()
        assert not validate(client, create_run(client, seed=1), 7)["valid"]


# --- step metadata ----------------------------------------------------------------------


def test_the_connecting_person_and_role_are_recorded_on_the_step(client):
    with respx.mock:
        mock_universe()
        run_id = create_run(client, seed=5)
        step = client.post(f"/api/runs/{run_id}/steps", json={"movie_id": 6})
    assert step.status_code == 201, step.text
    meta = step.json()["transition_metadata"]
    assert (meta["person_id"], meta["person_name"], meta["role"], meta["from_role"]) == (
        PEELE, "Jordan Peele", "actor", "director")


def test_the_client_cannot_forge_the_link(client):
    with respx.mock:
        mock_universe()
        run_id = create_run(client, seed=1)
        step = client.post(f"/api/runs/{run_id}/steps", json={
            "movie_id": 2, "transition_metadata": {
                "person_id": 999, "person_name": "Nobody", "role": "director", "from_role": "actor"}})
    meta = step.json()["transition_metadata"]
    assert (meta["person_id"], meta["person_name"], meta["role"]) == (ZIMMER, "Hans Zimmer", "composer")


def test_the_client_can_pick_which_shared_person_to_record(client):
    with respx.mock:
        mock_universe()
        run_id = create_run(client, seed=1)
        step = client.post(f"/api/runs/{run_id}/steps", json={
            "movie_id": 10, "transition_metadata": {
                "person_id": DICAPRIO, "person_name": "Actor 1", "role": "actor"}})
    meta = step.json()["transition_metadata"]
    assert (meta["person_id"], meta["role"]) == (DICAPRIO, "actor")


def test_a_person_cannot_link_two_hops_in_a_row(client):
    nolan = {"transition_metadata": {"person_id": NOLAN, "role": "director"}}
    with respx.mock:
        mock_universe()
        run_id = create_run(client, seed=1)
        first = client.post(f"/api/runs/{run_id}/steps", json={"movie_id": 10, **nolan})
        again = client.post(f"/api/runs/{run_id}/steps", json={"movie_id": 12, **nolan})
        other = client.post(f"/api/runs/{run_id}/steps", json={
            "movie_id": 12, "transition_metadata": {"person_id": ZIMMER, "role": "composer"}})
    assert first.status_code == 201
    assert again.status_code == 409
    assert "same actor" in again.json()["detail"]["reason"]
    # Tenet -> Dunkirk only shares Nolan: claiming Zimmer can't dodge the rule
    assert other.status_code == 409


def test_unrelated_films_are_refused(client):
    with respx.mock:
        mock_universe()
        run_id = create_run(client, seed=1)
        resp = client.post(f"/api/runs/{run_id}/steps", json={"movie_id": 7})
    assert resp.status_code == 409


# --- Pick Next ---------------------------------------------------------------------------


def test_pick_next_surfaces_candidates_with_role_links(client):
    with respx.mock:
        mock_universe()
        run_id = create_run(client, seed=5)
        pool = client.get(f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 5}).json()
    by_id = {c["movie_id"]: c for c in pool}
    assert set(by_id) == {6}  # Keanu, via Peele (director -> actor)
    link = by_id[6]["connections"][0]
    assert link["kind"] == "craft" and link["actor_name"] == "Jordan Peele"
    assert (link["role_in_frontier"], link["role_in_candidate"]) == ("director", "actor")


def test_pick_next_pools_every_craft_of_the_frontier(client):
    with respx.mock:
        mock_universe()
        run_id = create_run(client, seed=1)
        pool = client.get(f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 1}).json()
        both = client.get(
            f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 1, "mode": "and"}).json()
    by_id = {c["movie_id"]: c for c in pool}
    assert set(by_id) == {2, 10, 12}  # Gladiator (Zimmer), Tenet (Nolan + DiCaprio), Dunkirk
    assert {(c["actor_name"], c["role_in_candidate"]) for c in by_id[2]["connections"]} == {
        ("Hans Zimmer", "composer")}
    assert {(c["actor_name"], c["role_in_candidate"]) for c in by_id[10]["connections"]} == {
        ("Christopher Nolan", "director"), ("Actor 1", "actor")}
    assert sorted(c["movie_id"] for c in both) == [10, 12]  # 2+ shared people


def test_the_bridge_swap_capability_is_not_offered(client):
    meta = {e["game_type"]: e for e in client.get("/api/engines").json()}["crew_craft"]
    assert "bridge_swap" not in meta["capabilities"]


def test_keystone_stats_count_the_connecting_people(client):
    with respx.mock:
        mock_universe()
        run_id = create_run(client, seed=1)
        client.post(f"/api/runs/{run_id}/steps", json={"movie_id": 2})
        stats = client.get(f"/api/runs/{run_id}/stats").json()
    assert [(k["actor_name"], k["appearances"]) for k in stats["keystone_actors"]] == [
        ("Hans Zimmer", 1)]
