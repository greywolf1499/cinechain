"""Phase 21a game modes: Canon-Only Island, Decade Sieve and Movie Night Roulette."""

from typing import get_args

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app
from app.models.cache import CachedMovie, CachedMovieRating
from app.models.curated import CanonMovieBadge, CuratedList
from app.schemas.engine import FilterSource, FilterSpec

TMDB_BASE = "https://api.themoviedb.org/3"


def test_discovery_filters_are_safe_engine_metadata(client):
    engines = {engine["game_type"]: engine for engine in client.get("/api/engines").json()}
    for engine in engines.values():
        for spec in engine["discovery_filters"]:
            assert spec["source"] in get_args(FilterSource)
            assert spec["kind"] in {"select", "toggle", "range"}
            assert spec["server_param"] in {None, "include_off_tier"}
    for game_type in (
        "world_passport", "chrono_climb", "historical_time_travel",
        "genre_pendulum", "tug_of_war", "rabbit_hole",
    ):
        assert engines[game_type]["discovery_filters"]
    assert engines["cinechain"]["discovery_filters"] == []
    assert engines["historical_time_travel"]["discovery_filters"][0]["source"] == "narrative_year"
    assert engines["genre_pendulum"]["discovery_filters"][0]["default"] is True
    with pytest.raises(ValidationError):
        FilterSpec(key="unsafe", kind="toggle", label="Unsafe", source="candidate.eval()")
    with pytest.raises(ValidationError):
        FilterSpec(key="unsafe", kind="toggle", label="Unsafe",
                   source="new_country", server_param="arbitrary_query")


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


def _mock_movie(tmdb_id: int, title: str, year: int = 1975, cast: list[dict] | None = None):
    respx.get(f"{TMDB_BASE}/movie/{tmdb_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": tmdb_id,
                "title": title,
                "release_date": f"{year}-06-01",
                "poster_path": None,
                "overview": "",
                "origin_country": ["US"],
                "original_language": "en",
                "runtime": 100,
                "genres": [],
            },
        )
    )
    respx.get(f"{TMDB_BASE}/movie/{tmdb_id}/credits").mock(
        return_value=httpx.Response(200, json={"id": tmdb_id, "cast": cast or []})
    )


def _cast(actor_id: int, name: str = "Actor", order: int = 0):
    return {"id": actor_id, "name": name, "profile_path": None, "character": "Role", "order": order}


def _make_list(db_engine, title="Canon", movie_ids=()) -> str:
    with Session(db_engine) as session:
        curated = CuratedList(title=title, url="https://x", badge_prefix="C")
        session.add(curated)
        session.commit()
        session.refresh(curated)
        for movie_id in movie_ids:
            session.add(
                CanonMovieBadge(curated_list_id=curated.id, movie_id=movie_id, badge_label="C")
            )
        session.commit()
        return curated.id


def _create(client, game_type, rules, **extra):
    return client.post(
        "/api/runs", json={"name": "Run", "game_type": game_type, "rules_config": rules, **extra}
    )


def _log(client, run_id, movie_id, **extra):
    return client.post(f"/api/runs/{run_id}/steps", json={"movie_id": movie_id, **extra})


STANDARD = {
    "allow_repeats": "strict",
    "no_consecutive_actor": False,
    "min_runtime": 0,
    "wildcards_budget": 2,
}


# --- registry ---


def test_new_engines_are_registered_with_capabilities(client):
    engines = {e["game_type"]: e for e in client.get("/api/engines").json()}
    assert {"cinechain", "canon_island", "decade_sieve", "roulette"} <= set(engines)
    assert "discover_candidates" in engines["canon_island"]["capabilities"]
    assert "solve_bridge" not in engines["canon_island"]["capabilities"]
    assert "roulette_spin" in engines["roulette"]["capabilities"]
    assert "discover_candidates" not in engines["decade_sieve"]["capabilities"]


# --- Canon-Only Island ---


def test_island_requires_an_existing_list(client, db_engine):
    assert _create(client, "canon_island", STANDARD).status_code == 422
    resp = _create(client, "canon_island", {**STANDARD, "allowed_curated_list_id": "nope"})
    assert resp.status_code == 422
    list_id = _make_list(db_engine)
    assert (
        _create(
            client, "canon_island", {**STANDARD, "allowed_curated_list_id": list_id}
        ).status_code
        == 201
    )


def test_island_blocks_non_canon_films_even_with_a_wildcard(client, db_engine):
    list_id = _make_list(db_engine, "Sight & Sound", movie_ids=(1, 2))
    run_id = _create(
        client, "canon_island", {**STANDARD, "allowed_curated_list_id": list_id}
    ).json()["id"]
    with respx.mock:
        _mock_movie(1, "Canon One", cast=[_cast(100)])
        _mock_movie(2, "Canon Two", cast=[_cast(100)])
        _mock_movie(9, "Not Canon", cast=[_cast(100)])
        assert _log(client, run_id, 1).status_code == 201
        assert _log(client, run_id, 2).status_code == 201

        rejected = _log(client, run_id, 9)
        assert rejected.status_code == 409
        detail = rejected.json()["detail"]
        assert detail["blocked"] is True and "Sight & Sound" in detail["reason"]
        # Shares an actor, but a wildcard can't buy back a canon violation.
        assert _log(client, run_id, 9, force=True).status_code == 409

    run = client.get(f"/api/runs/{run_id}").json()
    assert run["rules_config"]["wildcards_budget"] == 2
    assert [s["movie_id"] for s in run["steps"]] == [1, 2]


def test_island_checks_the_first_film_and_the_seed(client, db_engine):
    list_id = _make_list(db_engine, movie_ids=(1,))
    rules = {**STANDARD, "allowed_curated_list_id": list_id}
    with respx.mock:
        _mock_movie(1, "Canon One")
        _mock_movie(9, "Not Canon")
        assert _create(client, "canon_island", rules, seed_movie_id=9).status_code == 422
        seeded = _create(client, "canon_island", rules, seed_movie_id=1)
        assert seeded.status_code == 201

        empty_run = _create(client, "canon_island", rules).json()["id"]
        assert _log(client, empty_run, 9).status_code == 409
        assert _log(client, empty_run, 1).status_code == 201


def test_island_still_requires_a_shared_actor(client, db_engine):
    list_id = _make_list(db_engine, movie_ids=(1, 2))
    run_id = _create(
        client, "canon_island", {**STANDARD, "allowed_curated_list_id": list_id}
    ).json()["id"]
    with respx.mock:
        _mock_movie(1, "One", cast=[_cast(100)])
        _mock_movie(2, "Two", cast=[_cast(200)])
        _log(client, run_id, 1)
        unlinked = _log(client, run_id, 2)

    assert unlinked.status_code == 409
    assert unlinked.json()["detail"]["blocked"] is False  # a wildcard could still allow it


def test_island_discovery_only_offers_canon_films(client, db_engine):
    list_id = _make_list(db_engine, movie_ids=(2,))
    run_id = _create(
        client, "canon_island", {**STANDARD, "allowed_curated_list_id": list_id}
    ).json()["id"]
    credit = {
        "release_date": "2000-01-01",
        "poster_path": None,
        "character": "R",
        "genre_ids": [],
        "original_language": "en",
    }
    with respx.mock:
        _mock_movie(1, "Frontier", cast=[_cast(100)])
        respx.get(f"{TMDB_BASE}/person/100/movie_credits").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 100,
                    "cast": [
                        {"id": 1, "title": "Frontier", **credit},
                        {"id": 2, "title": "Canon Two", **credit},
                        {"id": 3, "title": "Not Canon", **credit},
                    ],
                },
            )
        )
        resp = client.get(f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 1})

    assert [c["movie_id"] for c in resp.json()] == [2]


# --- Decade Sieve ---


@pytest.mark.parametrize("decade", [None, "1970", 1975, 1850, 2990])
def test_sieve_requires_a_valid_target_decade(client, decade):
    rules = dict(STANDARD)
    if decade is not None:
        rules["target_decade"] = decade
    assert _create(client, "decade_sieve", rules).status_code == 422


def test_sieve_accepts_only_the_target_decade_without_any_cast_link(client):
    run_id = _create(client, "decade_sieve", {**STANDARD, "target_decade": 1970}).json()["id"]
    with respx.mock:
        _mock_movie(1, "Seventies A", year=1970, cast=[_cast(100)])
        _mock_movie(2, "Seventies B", year=1979, cast=[_cast(200)])  # no shared cast
        _mock_movie(3, "Eighties", year=1980)
        _mock_movie(4, "Sixties", year=1969)
        assert _log(client, run_id, 1).status_code == 201
        assert _log(client, run_id, 2).status_code == 201

        for movie_id in (3, 4):
            for force in (False, True):
                resp = _log(client, run_id, movie_id, force=force)
                assert resp.status_code == 409
                assert resp.json()["detail"]["blocked"] is True
        assert "1970s" in _log(client, run_id, 3).json()["detail"]["reason"]

    steps = client.get(f"/api/runs/{run_id}").json()["steps"]
    assert [s["movie_id"] for s in steps] == [1, 2]
    stats = client.get(f"/api/runs/{run_id}/stats").json()
    assert stats["decades"] == [1970]


def test_run_validate_endpoint_mirrors_step_enforcement(client):
    run_id = _create(client, "decade_sieve", {**STANDARD, "target_decade": 1970}).json()["id"]
    with respx.mock:
        _mock_movie(1, "Seventies", year=1972)
        _mock_movie(2, "Nineties", year=1995)
        ok = client.post(f"/api/runs/{run_id}/validate", json={"movie_id": 1}).json()
        bad = client.post(f"/api/runs/{run_id}/validate", json={"movie_id": 2}).json()
        _log(client, run_id, 1)
        after = client.post(f"/api/runs/{run_id}/validate", json={"movie_id": 2}).json()

    assert ok["valid"] is True
    assert bad["valid"] is False and bad["blocked"] is True
    assert after["blocked"] is True


# --- Movie Night Roulette ---


def _seed_cache(db_engine):
    rows = [
        # id, title, runtime, genres, release_date, status
        (1, "Short Comedy", 90, [35], "2001-01-01", "Released"),
        (2, "Long Drama", 170, [18], "1999-01-01", "Released"),
        (3, "Short Drama", 95, [18], "2010-01-01", "Released"),
        (4, "Unreleased", 80, [35], "2999-01-01", "Planned"),
        (5, "Cancelled", 80, [35], "2005-01-01", "Canceled"),
        (6, "Stub", None, [35], "2003-01-01", None),
    ]
    with Session(db_engine) as session:
        for tmdb_id, title, runtime, genres, release_date, status in rows:
            session.add(
                CachedMovie(
                    tmdb_id=tmdb_id,
                    title=title,
                    runtime=runtime,
                    genre_ids=genres,
                    release_date=release_date,
                    status=status,
                )
            )
        session.commit()
        session.add(CachedMovieRating(movie_id=1, imdb_rating="7.8"))
        session.add(CachedMovieRating(movie_id=3, imdb_rating="6.1"))
        session.commit()


def _spin_ids(client, **params):
    seen = set()
    for _ in range(25):
        resp = client.get("/api/engine/roulette/spin", params=params)
        assert resp.status_code == 200
        seen.add(resp.json()["movie"]["tmdb_id"])
    return seen


def test_spin_filters_by_runtime_genre_and_rating(client, db_engine):
    _seed_cache(db_engine)
    # Unfiltered: only released films (unreleased/cancelled are never offered).
    assert _spin_ids(client) <= {1, 2, 3, 6}
    assert _spin_ids(client, max_runtime=100) <= {1, 3}
    assert _spin_ids(client, max_runtime=100, genre=18) == {3}
    assert _spin_ids(client, genre=35, min_rating=7) == {1}
    body = client.get("/api/engine/roulette/spin", params={"genre": 35, "min_rating": 7}).json()
    assert body["pool_size"] == 1
    assert body["movie"]["imdb_rating"] == "7.8"


def test_spin_is_random_across_the_matching_pool(client, db_engine):
    _seed_cache(db_engine)
    assert len(_spin_ids(client, max_runtime=100)) == 2


def test_blind_draft_serves_distinct_candidates(client, db_engine):
    _seed_cache(db_engine)
    body = client.get("/api/engine/roulette/spin", params={"count": 3}).json()
    ids = [m["tmdb_id"] for m in body["movies"]]
    assert len(ids) == 3 and len(set(ids)) == 3 and body["movie"]["tmdb_id"] == ids[0]
    assert set(ids) <= {1, 2, 3, 6}
    # Fewer matches than requested: serve what exists.
    short = client.get("/api/engine/roulette/spin", params={"count": 3, "max_runtime": 100}).json()
    assert {m["tmdb_id"] for m in short["movies"]} == {1, 3}
    assert client.get("/api/engine/roulette/spin", params={"count": 9}).status_code == 422


def test_spin_with_no_match_is_a_helpful_404(client, db_engine):
    _seed_cache(db_engine)
    resp = client.get("/api/engine/roulette/spin", params={"max_runtime": 10})
    assert resp.status_code == 404
    assert "cache" in resp.json()["detail"]


def test_spin_skips_films_already_in_the_run(client, db_engine):
    _seed_cache(db_engine)
    run_id = _create(client, "roulette", STANDARD).json()["id"]
    with respx.mock:
        _mock_movie(1, "Short Comedy", year=2001)
        assert _log(client, run_id, 1).status_code == 201
    assert 1 not in _spin_ids(client, run_id=run_id, max_runtime=100)


def test_spin_rejects_other_game_types(client):
    resp = client.get("/api/engine/roulette/spin", params={"game_type": "cinechain"})
    assert resp.status_code == 400


def test_roulette_run_logs_any_film_without_a_link(client):
    run_id = _create(client, "roulette", STANDARD).json()["id"]
    with respx.mock:
        _mock_movie(1, "One", cast=[_cast(100)])
        _mock_movie(2, "Two", cast=[_cast(200)])
        assert _log(client, run_id, 1).status_code == 201
        assert _log(client, run_id, 2).status_code == 201


def test_win_conditions_work_in_tracker_modes(client):
    run_id = _create(
        client, "roulette", {**STANDARD, "win_condition": {"type": "movies_watched", "count": 2}}
    ).json()["id"]
    with respx.mock:
        _mock_movie(1, "One")
        _mock_movie(2, "Two")
        _log(client, run_id, 1)
        _log(client, run_id, 2)
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "completed"


# --- Roulette filter matrix (Phase 22b) ---


def _seed_matrix(db_engine):
    rows = [
        # id, runtime, genres, imdb
        (11, 120, [35, 10749], "4.8"),  # bad rom-com
        (12, 105, [28, 12], "5.4"),  # bad action-adventure
        (13, 160, [18], "8.4"),  # epic drama
        (14, 85, [35, 16], "7.2"),  # animated comedy
    ]
    with Session(db_engine) as session:
        for tmdb_id, runtime, genres, _ in rows:
            session.add(
                CachedMovie(
                    tmdb_id=tmdb_id,
                    title=f"M{tmdb_id}",
                    runtime=runtime,
                    genre_ids=genres,
                    release_date="2000-01-01",
                    status="Released",
                )
            )
        session.commit()
        for tmdb_id, _, _, imdb in rows:
            session.add(CachedMovieRating(movie_id=tmdb_id, imdb_rating=imdb))
        session.commit()


def test_spin_rating_and_runtime_ranges(client, db_engine):
    _seed_matrix(db_engine)
    assert _spin_ids(client, min_rating=1.0, max_rating=5.5) == {11, 12}  # a "bad movie night"
    assert _spin_ids(client, min_runtime=100, max_runtime=130, max_rating=5.5) == {11, 12}
    assert _spin_ids(client, min_runtime=150, min_rating=8) == {13}
    assert _spin_ids(client, max_rating=5.0) == {11}


def test_spin_genre_and_requires_every_genre_or_any(client, db_engine):
    _seed_matrix(db_engine)
    assert _spin_ids(client, genre_ids=[35, 16], genre_operator="AND") == {14}
    assert _spin_ids(client, genre_ids=[35, 18], genre_operator="OR") == {11, 13, 14}


def test_spin_genre_and_with_no_film_having_all_is_a_404(client, db_engine):
    _seed_matrix(db_engine)
    resp = client.get(
        "/api/engine/roulette/spin", params={"genre_ids": [35, 18], "genre_operator": "AND"}
    )
    assert resp.status_code == 404


def test_spin_rejects_inverted_ranges_and_bad_operators(client, db_engine):
    _seed_matrix(db_engine)
    assert (
        client.get(
            "/api/engine/roulette/spin", params={"min_runtime": 200, "max_runtime": 100}
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/engine/roulette/spin", params={"min_rating": 8, "max_rating": 3}
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/engine/roulette/spin", params={"genre_ids": [35], "genre_operator": "XOR"}
        ).status_code
        == 422
    )


def test_spin_on_a_cold_cache_is_a_helpful_404(client):
    resp = client.get("/api/engine/roulette/spin")
    assert resp.status_code == 404
    assert "No movies found in cache matching criteria" in resp.json()["detail"]
