"""Phase 23a Engine V3: composable modifiers (chrono_direction, runtime_staircase,
country_cooldown) layered on any compatible engine, and the Passport anti-yo-yo default."""

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app

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
        test_client.post("/api/auth/login", json={"username": "alice", "password": "password123"})
        yield test_client
    app.dependency_overrides.clear()


def film(title, year, country="US", runtime=100, cast=(100,)):
    return {
        "title": title,
        "year": year,
        "country": country,
        "runtime": runtime,
        "cast": list(cast),
    }


def mock_universe(universe):
    for movie_id, m in universe.items():
        respx.get(f"{TMDB_BASE}/movie/{movie_id}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": movie_id,
                    "title": m["title"],
                    "release_date": f"{m['year']}-06-01",
                    "poster_path": None,
                    "overview": "",
                    "origin_country": [m["country"]],
                    "original_language": "en",
                    "runtime": m["runtime"],
                    "genres": [],
                    "popularity": 100 - movie_id,
                    "status": "Released",
                },
            )
        )
        respx.get(f"{TMDB_BASE}/movie/{movie_id}/credits").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": movie_id,
                    "cast": [
                        {
                            "id": a,
                            "name": f"Person {a}",
                            "profile_path": None,
                            "character": f"Role {a}",
                            "order": i,
                        }
                        for i, a in enumerate(m["cast"])
                    ],
                    "crew": [],
                },
            )
        )
    for person in {a for m in universe.values() for a in m["cast"]}:
        respx.get(f"{TMDB_BASE}/person/{person}/movie_credits").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": person,
                    "cast": [
                        {
                            "id": i,
                            "title": m["title"],
                            "release_date": f"{m['year']}-06-01",
                            "poster_path": None,
                            "genre_ids": [],
                            "original_language": "en",
                            "popularity": 100 - i,
                            "character": f"Role {person}",
                        }
                        for i, m in universe.items()
                        if person in m["cast"]
                    ],
                    "crew": [],
                },
            )
        )


def create_run(client, game_type, expect=201, **rules):
    payload = {
        "allow_repeats": "strict",
        "no_consecutive_actor": False,
        "min_runtime": 0,
        "wildcards_budget": 2,
        **rules,
    }
    resp = client.post(
        "/api/runs", json={"name": "Run", "game_type": game_type, "rules_config": payload}
    )
    assert resp.status_code == expect, resp.text
    return resp.json()["id"] if expect == 201 else resp


def log(client, run_id, movie_id, **extra):
    return client.post(f"/api/runs/{run_id}/steps", json={"movie_id": movie_id, **extra})


def logged(client, run_id):
    return [s["movie_id"] for s in client.get(f"/api/runs/{run_id}").json()["steps"]]


COUNTRIES = {
    1: film("US One", 1990, "US"),
    2: film("UK One", 1991, "GB"),
    3: film("US Two", 1992, "US"),
    4: film("FR One", 1993, "FR"),
    5: film("JP One", 1994, "JP"),
    6: film("DE One", 1995, "DE"),
    7: film("UK Two", 1996, "GB"),
}


# --- World Passport anti yo-yo ---


def test_passport_blocks_toggling_between_two_countries(client):
    run_id = create_run(client, "world_passport")
    with respx.mock:
        mock_universe(COUNTRIES)
        assert log(client, run_id, 1).status_code == 201  # US
        assert log(client, run_id, 2).status_code == 201  # GB
        for force in (False, True):
            resp = log(client, run_id, 3, force=force)  # US again: still on cooldown
            assert resp.status_code == 409 and resp.json()["detail"]["blocked"] is True
        assert "Country Cooldown" in log(client, run_id, 3).json()["detail"]["reason"]
        assert log(client, run_id, 4).status_code == 201  # FR
        assert log(client, run_id, 3).status_code == 409  # US is still within the last 3
        assert log(client, run_id, 5).status_code == 201  # JP: US now falls out of the window
        assert log(client, run_id, 3).status_code == 201
    assert logged(client, run_id) == [1, 2, 4, 5, 3]


def test_passport_cooldown_can_be_tuned_or_disabled(client):
    run_id = create_run(client, "world_passport", country_cooldown=0)
    with respx.mock:
        mock_universe(COUNTRIES)
        for movie_id in (1, 2, 3, 7):  # US, GB, US, GB: only the previous country counts
            assert log(client, run_id, movie_id).status_code == 201
        assert log(client, run_id, 7).status_code == 409  # same country as the last film

    longer = create_run(client, "world_passport", country_cooldown=5)
    with respx.mock:
        mock_universe(COUNTRIES)
        for movie_id in (1, 2, 4, 5):
            assert log(client, longer, movie_id).status_code == 201
        assert log(client, longer, 3).status_code == 409  # US is 4 steps back, window is 5


def test_passport_constraint_lists_the_cooldown_countries(client):
    run_id = create_run(client, "world_passport")
    with respx.mock:
        mock_universe(COUNTRIES)
        for movie_id in (1, 2, 4, 5):
            log(client, run_id, movie_id)
        constraint = client.get(f"/api/runs/{run_id}/constraint").json()
    assert constraint["cooldown_countries"] == ["JP", "FR", "GB"]  # most recent first


def test_passport_hybrid_pool_excludes_cooled_down_countries(client):
    run_id = create_run(client, "world_passport", require_cast_link=True)
    with respx.mock:
        mock_universe(COUNTRIES)
        for movie_id in (1, 2):
            log(client, run_id, movie_id)
        pool = client.get(f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 2}).json()
    ids = {c["movie_id"] for c in pool}
    assert 3 not in ids and 7 not in ids  # US and GB are locked out
    assert {4, 5, 6} <= ids


# --- modifiers on other engines ---

RUNTIMES = {
    1: film("Medium", 1990, runtime=100),
    2: film("Long", 1995, runtime=130),
    3: film("Short", 1985, runtime=80),
    4: film("Longer", 2000, runtime=150),
}


def test_runtime_staircase_on_cinechain(client):
    run_id = create_run(client, "cinechain", runtime_staircase="ascending")
    with respx.mock:
        mock_universe(RUNTIMES)
        log(client, run_id, 1)
        short = log(client, run_id, 3)
        assert short.status_code == 409 and short.json()["detail"]["blocked"] is True
        assert "Runtime Staircase" in short.json()["detail"]["reason"]
        assert log(client, run_id, 3, force=True).status_code == 409  # a wildcard can't skip it
        ok = log(client, run_id, 2)
        assert ok.status_code == 201
        assert ok.json()["transition_metadata"]["runtime_delta"] == 30
        assert log(client, run_id, 4).status_code == 201


def test_runtime_staircase_descending(client):
    run_id = create_run(client, "cinechain", runtime_staircase="descending")
    with respx.mock:
        mock_universe(RUNTIMES)
        log(client, run_id, 2)
        assert log(client, run_id, 4).status_code == 409
        assert log(client, run_id, 1).status_code == 201


def test_chrono_direction_on_cinechain_keeps_the_cast_link(client):
    universe = {**RUNTIMES, 5: film("Unlinked", 2005, cast=(200,))}
    run_id = create_run(client, "cinechain", chrono_direction="climb")
    with respx.mock:
        mock_universe(universe)
        log(client, run_id, 1)
        assert log(client, run_id, 3).status_code == 409  # 1985 < 1990
        unlinked = log(client, run_id, 5)  # newer, but shares no cast
        assert unlinked.status_code == 409 and unlinked.json()["detail"]["blocked"] is False
        assert log(client, run_id, 2).status_code == 201


def test_modifiers_stack(client):
    run_id = create_run(client, "chrono_climb", runtime_staircase="ascending", country_cooldown=2)
    universe = {
        1: film("A", 1990, "US", 100),
        2: film("B", 1991, "US", 90),  # newer but shorter
        3: film("C", 1992, "GB", 120),
        4: film("D", 1993, "US", 130),  # US is within the last two steps? (US, GB)
        5: film("E", 1994, "FR", 140),
    }
    with respx.mock:
        mock_universe(universe)
        log(client, run_id, 1)
        assert log(client, run_id, 2).status_code == 409
        assert log(client, run_id, 3).status_code == 201
        assert log(client, run_id, 4).status_code == 409
        assert log(client, run_id, 5).status_code == 201


def test_chrono_climb_keeps_its_legacy_direction_key(client):
    run_id = create_run(client, "chrono_climb", direction="descent")
    with respx.mock:
        mock_universe(RUNTIMES)
        log(client, run_id, 1)
        assert log(client, run_id, 2).status_code == 409
        assert log(client, run_id, 3).status_code == 201

    override = create_run(client, "chrono_climb", direction="descent", chrono_direction="climb")
    with respx.mock:
        mock_universe(RUNTIMES)
        log(client, override, 1)
        assert log(client, override, 2).status_code == 201


# --- validation ---


@pytest.mark.parametrize(
    "rules",
    [
        {"chrono_direction": "sideways"},
        {"runtime_staircase": "up"},
        {"country_cooldown": -1},
        {"country_cooldown": 99},
        {"country_cooldown": "3"},
        {"country_cooldown": True},
    ],
)
def test_invalid_modifier_values_are_rejected(client, rules):
    resp = create_run(client, "cinechain", expect=422, **rules)
    assert any(key in resp.json()["detail"] for key in rules)


def test_null_modifiers_mean_unset(client):
    run_id = create_run(
        client,
        "world_passport",
        chrono_direction=None,
        runtime_staircase=None,
        country_cooldown=None,
    )
    with respx.mock:
        mock_universe(COUNTRIES)
        log(client, run_id, 1)
        log(client, run_id, 2)
        assert log(client, run_id, 3).status_code == 409  # default cooldown still applies


def test_trackers_reject_modifiers(client):
    resp = create_run(client, "roulette", expect=422, runtime_staircase="ascending")
    assert "isn't supported" in resp.json()["detail"]


def test_rules_patch_can_toggle_modifiers(client):
    run_id = create_run(client, "cinechain")
    patch = client.patch(f"/api/runs/{run_id}/rules", json={"runtime_staircase": "ascending"})
    assert patch.status_code == 200
    assert patch.json()["rules_config"]["runtime_staircase"] == "ascending"
    cleared = client.patch(f"/api/runs/{run_id}/rules", json={"runtime_staircase": None})
    assert cleared.json()["rules_config"]["runtime_staircase"] is None
    assert (
        client.patch(f"/api/runs/{run_id}/rules", json={"country_cooldown": 50}).status_code == 422
    )
