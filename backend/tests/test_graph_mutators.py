"""Phase 21b graph mutators: Chrono Climb, World Passport and Auteur Relay."""

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.engines import base
from app.engines.cinechain import CineChainEngine
from app.engines.mutators import AuteurRelayEngine, ChronoClimbEngine, WorldPassportEngine
from app.main import app
from app.services.tmdb import TMDBClient

TMDB_BASE = "https://api.themoviedb.org/3"


@pytest.fixture()
def db_engine(config_dir):
    engine = create_engine(
        f"sqlite:///{config_dir}/app_test.db", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture()
def client(db_engine):
    def override_get_session():
        with Session(db_engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        test_client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password123", "display_name": "Alice"},
        )
        test_client.post(
            "/api/auth/login", json={"username": "alice", "password": "password123"})
        yield test_client
    app.dependency_overrides.clear()


def movie(title, year, countries=("US",), cast=(), directors=(), popularity=1.0):
    return {"title": title, "year": year, "countries": list(countries), "cast": list(cast),
            "directors": list(directors), "popularity": popularity}


def mock_universe(universe: dict[int, dict]):
    """Registers every TMDB route a tiny fake world needs. Actors and directors
    share one id namespace (use disjoint ids)."""
    people = {}
    for movie_id, m in universe.items():
        for person in [*m["cast"], *m["directors"]]:
            people.setdefault(person, f"Person {person}")
        respx.get(f"{TMDB_BASE}/movie/{movie_id}").mock(return_value=httpx.Response(200, json={
            "id": movie_id, "title": m["title"], "release_date": f"{m['year']}-06-01",
            "poster_path": None, "overview": "", "origin_country": m["countries"],
            "original_language": "en", "runtime": 100, "genres": [], "popularity": m["popularity"],
            "status": "Released",
        }))
        respx.get(f"{TMDB_BASE}/movie/{movie_id}/credits").mock(return_value=httpx.Response(200, json={
            "id": movie_id,
            "cast": [{"id": a, "name": people[a], "profile_path": None, "character": f"Role {a}",
                      "order": i} for i, a in enumerate(m["cast"])],
            "crew": [{"id": d, "name": people[d], "job": "Director"} for d in m["directors"]],
        }))
    for person in people:
        def credit(movie_id, m, **extra):
            return {"id": movie_id, "title": m["title"], "release_date": f"{m['year']}-06-01",
                    "poster_path": None, "genre_ids": [], "original_language": "en",
                    "popularity": m["popularity"], **extra}
        respx.get(f"{TMDB_BASE}/person/{person}/movie_credits").mock(
            return_value=httpx.Response(200, json={
                "id": person,
                "cast": [credit(i, m, character=f"Role {person}")
                         for i, m in universe.items() if person in m["cast"]],
                "crew": [credit(i, m, job="Director")
                         for i, m in universe.items() if person in m["directors"]],
            }))
    return people


def create_run(client, game_type, **rules):
    base = {"allow_repeats": "strict", "no_consecutive_actor": False, "min_runtime": 0,
            "wildcards_budget": 2, **rules}
    resp = client.post("/api/runs", json={"name": "Run", "game_type": game_type, "rules_config": base})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def log(client, run_id, movie_id, **extra):
    return client.post(f"/api/runs/{run_id}/steps", json={"movie_id": movie_id, **extra})


def steps(client, run_id):
    return client.get(f"/api/runs/{run_id}").json()["steps"]


# --- registry ---


def test_mutators_are_registered(client):
    engines = {e["game_type"]: e for e in client.get("/api/engines").json()}
    for game_type in ("chrono_climb", "world_passport", "auteur_relay"):
        assert game_type in engines
        assert {"discover_candidates", "solve_bridge"} <= set(engines[game_type]["capabilities"])
        assert "bridge_swap" not in engines[game_type]["capabilities"]


# --- Chrono Climb ---

CHRONO = {
    1: movie("Start", 1990, cast=[100]),
    2: movie("Later", 1995, cast=[100]),
    3: movie("Earlier", 1985, cast=[100]),
    4: movie("Same Year", 1990, cast=[100]),
    5: movie("Unlinked", 2000, cast=[200]),
}


def test_chrono_requires_strictly_later_release_year(client):
    run_id = create_run(client, "chrono_climb")
    with respx.mock:
        mock_universe(CHRONO)
        assert log(client, run_id, 1).status_code == 201
        for movie_id in (3, 4):
            for force in (False, True):
                resp = log(client, run_id, movie_id, force=force)
                assert resp.status_code == 409
                assert resp.json()["detail"]["blocked"] is True
        assert "must be released after" in log(client, run_id, 3).json()["detail"]["reason"]
        assert log(client, run_id, 2).status_code == 201
    assert [s["movie_id"] for s in steps(client, run_id)] == [1, 2]


def test_chrono_hybrid_still_needs_a_cast_link_but_a_wildcard_can_skip_it(client):
    run_id = create_run(client, "chrono_climb", require_cast_link=True)
    with respx.mock:
        mock_universe(CHRONO)
        log(client, run_id, 1)
        unlinked = log(client, run_id, 5)
        assert unlinked.status_code == 409
        assert unlinked.json()["detail"]["blocked"] is False
        forced = log(client, run_id, 5, force=True)
    assert forced.status_code == 201
    assert forced.json()["transition_metadata"]["wildcard_used"] is True


def test_chrono_hybrid_constraint_and_candidate_pool(client):
    run_id = create_run(client, "chrono_climb", require_cast_link=True)
    with respx.mock:
        mock_universe(CHRONO)
        before = client.get(f"/api/runs/{run_id}/constraint").json()
        log(client, run_id, 1)
        after = client.get(f"/api/runs/{run_id}/constraint").json()
        pool = client.get(f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 1}).json()

    assert before["kind"] == "year" and "starts the climb" in before["title"]
    assert after["title"] == "Next film must be released after 1990"
    assert {c["movie_id"] for c in pool} == {2}  # 3 is earlier, 4 is the same year


async def _solve(session, engine_class, from_id, to_id, **kwargs):
    async with httpx.AsyncClient() as http:
        engine = engine_class(session, TMDBClient(http))
        return [e async for e in engine.solve_bridge(from_id, to_id, **kwargs)]


BRIDGE_CHRONO = {
    1: movie("A", 1990, cast=[100]),
    5: movie("Too Early", 1980, cast=[100, 200]),
    6: movie("Just Right", 2000, cast=[100, 200]),
    9: movie("C", 2010, cast=[200]),
}


async def test_chrono_bridge_skips_hops_that_go_backwards(config_dir, db_engine):
    with Session(db_engine) as session, respx.mock:
        mock_universe(BRIDGE_CHRONO)
        constrained = await _solve(session, ChronoClimbEngine, 1, 9, max_depth=3, rules={"require_cast_link": True})
        unconstrained = await _solve(session, CineChainEngine, 1, 9, max_depth=3)

    result = next(e for e in constrained if e["type"] == "result")
    assert [n.movie_id for n in result["path"]] == [1, 6, 9]
    assert result["alternate_paths"] == []
    plain = next(e for e in unconstrained if e["type"] == "result")
    assert {5, 6} <= {n.movie_id for alt in [plain, *plain["alternate_paths"]] for n in alt["path"]}


async def test_chrono_bridge_is_impossible_when_the_target_is_older(config_dir, db_engine):
    with Session(db_engine) as session, respx.mock:
        mock_universe(BRIDGE_CHRONO)
        events = await _solve(session, ChronoClimbEngine, 9, 1, max_depth=3, rules={"require_cast_link": True})

    exhausted = next(e for e in events if e["type"] == "exhausted")
    assert exhausted["reason"] == "constraint_impossible"
    assert "after" in exhausted["message"]


# --- World Passport ---

PASSPORT = {
    1: movie("American", 1990, ["US"], cast=[100]),
    2: movie("French", 1991, ["FR"], cast=[100], popularity=9),
    3: movie("Also American", 1992, ["US", "GB"], cast=[100], popularity=8),
    4: movie("Japanese", 1993, ["JP"], cast=[100], popularity=7),
    5: movie("No Country", 1994, [], cast=[100], popularity=6),
}


def test_passport_blocks_the_same_primary_country(client):
    run_id = create_run(client, "world_passport")
    with respx.mock:
        mock_universe(PASSPORT)
        assert log(client, run_id, 1).status_code == 201
        for force in (False, True):
            resp = log(client, run_id, 3, force=force)
            assert resp.status_code == 409 and resp.json()["detail"]["blocked"] is True
        assert "other than US" in log(client, run_id, 3).json()["detail"]["reason"]
        assert log(client, run_id, 2).status_code == 201  # FR
        # A film with no country on record is never blocked.
        assert log(client, run_id, 5).status_code == 201
    assert [s["movie_id"] for s in steps(client, run_id)] == [1, 2, 5]


def test_passport_hybrid_pool_drops_same_country_films(client):
    run_id = create_run(client, "world_passport", require_cast_link=True)
    with respx.mock:
        mock_universe(PASSPORT)
        log(client, run_id, 1)
        pool = client.get(f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 1}).json()
        constraint = client.get(f"/api/runs/{run_id}/constraint").json()

    assert {c["movie_id"] for c in pool} == {2, 4, 5}  # 3 shares the primary country (US)
    assert not any(c["constraint_unverified"] for c in pool if c["movie_id"] in (2, 4))
    assert constraint["title"] == "Next film must be from a country other than US"


def test_passport_hybrid_pool_flags_films_it_could_not_verify(client, monkeypatch):
    monkeypatch.setattr(base, "HYDRATE_BUDGET", 1)
    run_id = create_run(client, "world_passport", require_cast_link=True)
    with respx.mock:
        mock_universe(PASSPORT)
        log(client, run_id, 1)
        pool = {c["movie_id"]: c for c in client.get(
            f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 1}).json()}

    assert pool[2]["constraint_unverified"] is False  # most popular: hydrated (FR)
    assert pool[4]["constraint_unverified"] is True  # over budget: kept but flagged
    assert 3 in pool and pool[3]["constraint_unverified"] is True  # can't be ruled out yet


BRIDGE_PASSPORT = {
    1: movie("A", 1990, ["US"], cast=[100]),
    5: movie("French Link", 1995, ["FR"], cast=[100, 200]),
    6: movie("American Link", 1996, ["US"], cast=[100, 200]),
    9: movie("C", 2010, ["US"], cast=[200]),
}


async def test_passport_bridge_verifies_lazily_discovered_countries(config_dir, db_engine):
    with Session(db_engine) as session, respx.mock:
        mock_universe(BRIDGE_PASSPORT)
        events = await _solve(session, WorldPassportEngine, 1, 9, max_depth=3, rules={"require_cast_link": True})

    result = next(e for e in events if e["type"] == "result")
    assert [n.movie_id for n in result["path"]] == [1, 5, 9]  # 6 is US -> US: rejected
    assert result["alternate_paths"] == []


# --- Auteur Relay ---

AUTEUR = {
    1: movie("Start", 1990, cast=[100], directors=[500]),
    2: movie("Actor And Director Link", 1991, cast=[100], directors=[500]),
    3: movie("Director Only", 1992, cast=[300], directors=[500]),
    4: movie("Actor Only", 1993, cast=[300], directors=[600]),
    5: movie("Another Director Film", 1994, cast=[400], directors=[500]),
}


def test_auteur_alternates_actor_and_director_hops(client):
    run_id = create_run(client, "auteur_relay")
    with respx.mock:
        mock_universe(AUTEUR)
        assert log(client, run_id, 1).status_code == 201

        free = client.get(f"/api/runs/{run_id}/constraint").json()
        assert free["kind"] == "free"

        # First hop is free: films 1 and 2 share an actor AND a director -> actor by default.
        second = log(client, run_id, 2)
        assert second.status_code == 201
        assert second.json()["transition_metadata"]["connection_type"] == "actor"
        assert second.json()["transition_metadata"]["actor_id"] == 100

        need_director = client.get(f"/api/runs/{run_id}/constraint").json()
        assert need_director["kind"] == "director"
        assert need_director["title"] == "Next hop must be a Director"

        # 2 -> 3 shares only the director: allowed, and recorded as a director hop.
        third = log(client, run_id, 3)
        assert third.status_code == 201
        meta = third.json()["transition_metadata"]
        assert meta["connection_type"] == "director"
        assert meta["director_id"] == 500 and "actor_id" not in meta

        assert client.get(f"/api/runs/{run_id}/constraint").json()["title"] == "Next hop must be an Actor"
        # 3 -> 5 shares only the director again: a hard block, even with a wildcard.
        for force in (False, True):
            resp = log(client, run_id, 5, force=force)
            assert resp.status_code == 409 and resp.json()["detail"]["blocked"] is True
        assert "must connect through a actor" in log(client, run_id, 5).json()["detail"]["reason"]
        assert log(client, run_id, 4).status_code == 201  # shares actor 300


def test_auteur_free_first_hop_honours_the_clients_choice(client):
    run_id = create_run(client, "auteur_relay")
    with respx.mock:
        mock_universe(AUTEUR)
        log(client, run_id, 1)
        second = log(client, run_id, 2, transition_metadata={
            "connection_type": "director", "actor_id": 100, "actor_name": "ignored"})

    meta = second.json()["transition_metadata"]
    assert meta["connection_type"] == "director"
    assert meta["director_id"] == 500 and "actor_id" not in meta


def test_auteur_ignores_a_claimed_connection_on_the_first_film(client):
    run_id = create_run(client, "auteur_relay")
    with respx.mock:
        mock_universe(AUTEUR)
        first = log(client, run_id, 1, transition_metadata={"connection_type": "actor", "note": "x"})
        assert client.get(f"/api/runs/{run_id}/constraint").json()["kind"] == "free"
    assert first.json()["transition_metadata"] == {"note": "x"}


def test_auteur_validate_endpoint_and_pool_follow_the_required_kind(client):
    run_id = create_run(client, "auteur_relay")
    with respx.mock:
        mock_universe(AUTEUR)
        log(client, run_id, 1)
        log(client, run_id, 2)  # actor hop -> next must be a director
        blocked = client.post(f"/api/runs/{run_id}/validate", json={"movie_id": 4}).json()
        pool = client.get(f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 2}).json()

    assert blocked["valid"] is False  # 2 and 4 share nothing at all (forceable miss)
    assert blocked["blocked"] is False
    assert {c["movie_id"] for c in pool} == {1, 3, 5}  # films 2's director (500) also directed
    assert all(conn["kind"] == "director" for c in pool for conn in c["connections"])


BRIDGE_AUTEUR = {
    1: movie("A", 1990, cast=[100], directors=[500]),
    3: movie("B", 1992, cast=[300], directors=[500]),
    4: movie("C", 1993, cast=[300], directors=[600]),
}


async def test_auteur_bridge_alternates_link_kinds(config_dir, db_engine):
    with Session(db_engine) as session, respx.mock:
        mock_universe(BRIDGE_AUTEUR)
        events = await _solve(session, AuteurRelayEngine, 1, 4, max_depth=3)

    result = next(e for e in events if e["type"] == "result")
    assert [n.movie_id for n in result["path"]] == [1, 3, 4]
    assert [c.kind for c in result["connections"]] == ["director", "actor"]
    assert result["connections"][0].actor_id == 500


async def test_auteur_bridge_continues_the_runs_alternation(config_dir, db_engine):
    with Session(db_engine) as session, respx.mock:
        mock_universe(BRIDGE_AUTEUR)
        # The run's last hop was a director link, so hop one must be an actor...
        events = await _solve(session, AuteurRelayEngine, 1, 4, max_depth=3,
                              start_connection_type="director")

    # ...but 1 and 3 share no actor, so the director-first route is unavailable.
    assert not any(e["type"] == "result" for e in events)
    assert any(e["type"] == "exhausted" for e in events)


BRIDGE_ACTOR_ACTOR = {
    1: movie("P", 1990, cast=[100], directors=[501]),
    2: movie("Q", 1991, cast=[100, 200], directors=[502]),
    3: movie("R", 1992, cast=[200], directors=[503]),
}


async def test_auteur_bridge_rejects_two_actor_hops_in_a_row(config_dir, db_engine):
    with Session(db_engine) as session, respx.mock:
        mock_universe(BRIDGE_ACTOR_ACTOR)
        relay = await _solve(session, AuteurRelayEngine, 1, 3, max_depth=3)
        plain = await _solve(session, CineChainEngine, 1, 3, max_depth=3)

    assert not any(e["type"] == "result" for e in relay)
    assert any(e["type"] == "result" for e in plain)


def test_bridge_stream_passes_the_runs_last_connection_type(client):
    run_id = create_run(client, "auteur_relay")
    with respx.mock:
        mock_universe(BRIDGE_AUTEUR)
        log(client, run_id, 1)
        resp = client.get("/api/engine/bridge/stream", params={
            "from_movie_id": 1, "to_movie_id": 4, "game_type": "auteur_relay",
            "run_id": run_id, "max_depth": 3})
    assert resp.status_code == 200
    assert "event: result" in resp.text
