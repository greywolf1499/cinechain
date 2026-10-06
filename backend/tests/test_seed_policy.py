"""S1 setup policies and cache-only, rules-aware seed selection."""

import json
from unittest.mock import AsyncMock

import pytest
from sqlmodel import Session

from app.engines.regional_deep_dive import RegionalDeepDiveEngine
from app.engines.registry import ENGINE_REGISTRY
from app.models.cache import CachedMovie
from app.models.curated import CanonMovieBadge, CuratedList
from app.services.tmdb import TMDBClient
from tests.test_graph_mutators import client, db_engine

__all__ = ["client", "db_engine"]


@pytest.fixture()
def seed_cache(db_engine, monkeypatch):
    network = AsyncMock(side_effect=AssertionError("Seed setup must not call TMDB"))
    monkeypatch.setattr(TMDBClient, "get_movie", network)
    with Session(db_engine) as session:
        session.add(
            CuratedList(
                id="canon",
                title="Test canon",
                url="https://letterboxd.com/test/list/canon/",
                badge_prefix="TEST",
            )
        )
        session.flush()
        for movie_id, countries, year, runtime, poster in (
            (1, ["JP"], 1970, 100, "/1.jpg"),
            (2, ["JP", "AU"], 1979, 90, None),
            (3, ["AU"], 1980, None, None),
            (4, ["IN"], 1975, 100, "/4.jpg"),
            (5, ["US"], 2000, 100, "/5.jpg"),
            (6, ["AU"], 1975, 20, None),
            (7, ["JP"], 1975, 100, "/7.jpg"),
        ):
            session.add(
                CachedMovie(
                    tmdb_id=movie_id,
                    title=f"Film {movie_id}",
                    release_date=f"{year}-06-01",
                    origin_country=json.dumps(countries),
                    runtime=runtime,
                    poster_path=poster,
                    status="Released",
                    popularity=5,
                )
            )
            if movie_id != 7:
                session.add(
                    CanonMovieBadge(
                        curated_list_id="canon",
                        movie_id=movie_id,
                        badge_label=f"TEST #{movie_id}",
                        rank=movie_id,
                    )
                )
        session.commit()
    return network


def suggest(client, game_type, rules=None, exclude=None):
    response = client.post(
        "/api/movies/seed-suggestion",
        json={
            "game_type": game_type,
            "rules_config": rules or {},
            "exclude": exclude or [],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_every_engine_exposes_a_valid_seed_policy(client):
    policies = {"none", "free", "derived", "pair"}
    assert all(engine.seed_policy in policies for engine in ENGINE_REGISTRY.values())
    modes = client.get("/api/engines").json()
    assert {mode["game_type"]: mode["seed_policy"] for mode in modes} == {
        name: engine.seed_policy for name, engine in ENGINE_REGISTRY.items()
    }


@pytest.mark.parametrize(("country", "allowed"), [("JP", {1, 2}), ("AU", {2, 3, 6})])
def test_regional_dice_stays_in_checklist_for_thirty_rolls(
    client, seed_cache, monkeypatch, country, allowed
):
    prepare = AsyncMock(side_effect=AssertionError("Dice must not prepare a run"))
    monkeypatch.setattr(RegionalDeepDiveEngine, "prepare_run", prepare)
    for _ in range(30):
        result = suggest(
            client,
            "regional_deep_dive",
            {
                "curated_list_id": "canon",
                "target_country": country,
            },
        )
        assert result["suggestion"]["tmdb_id"] in allowed
    prepare.assert_not_awaited()
    seed_cache.assert_not_awaited()


@pytest.mark.parametrize(
    ("mode", "rules", "allowed"),
    [
        ("decade_sieve", {"target_decade": 1970}, {1, 2, 4, 6, 7}),
        ("canon_island", {"allowed_curated_list_id": "canon"}, {1, 2, 3, 4, 5, 6}),
    ],
)
def test_derived_dice_and_options_respect_bounds(client, seed_cache, mode, rules, allowed):
    options = client.post(
        "/api/movies/seed-options",
        json={
            "game_type": mode,
            "rules_config": rules,
        },
    )
    assert options.status_code == 200, options.text
    assert set(options.json()["allowed_ids"]) == allowed
    for _ in range(15):
        assert suggest(client, mode, rules)["suggestion"]["tmdb_id"] in allowed
    seed_cache.assert_not_awaited()


def test_niche_slice_without_posters_still_has_runtime_eligible_seeds(client, seed_cache):
    rules = {"curated_list_id": "canon", "target_country": "AU", "min_runtime": 40}
    assert (
        suggest(client, "regional_deep_dive", rules, exclude=[1, 2, 3, 4, 5])["suggestion"] is None
    )
    result = suggest(client, "regional_deep_dive", rules, exclude=[1, 2, 4, 5, 6])
    assert result["suggestion"]["tmdb_id"] == 3  # unknown runtime, no poster
    assert result["reason"] == "On the TEST #3 list"
    seed_cache.assert_not_awaited()


@pytest.mark.parametrize(
    "mode",
    [
        "march_madness",
        "rt_split",
        "roulette",
        "method_actor",
        "auteur_marathon",
    ],
)
def test_board_modes_reject_seeds_before_preparation(client, seed_cache, monkeypatch, mode):
    engine = ENGINE_REGISTRY[mode]
    prepare = AsyncMock(side_effect=AssertionError("Illegal seed must fail before preparation"))
    monkeypatch.setattr(engine, "prepare_run", prepare)
    response = client.post(
        "/api/runs",
        json={
            "name": "Illegal seed",
            "game_type": mode,
            "seed_movie_id": 1,
        },
    )
    assert response.status_code == 422, response.text
    assert engine.display_name in response.json()["detail"]
    result = suggest(client, mode)
    assert result["suggestion"] is None
    assert engine.display_name in result["reason"]
    prepare.assert_not_awaited()
    seed_cache.assert_not_awaited()


@pytest.mark.parametrize(
    ("mode", "rules", "seed"),
    [
        ("regional_deep_dive", {"curated_list_id": "canon", "target_country": "AU"}, 4),
        ("canon_island", {"allowed_curated_list_id": "canon"}, 7),
        ("decade_sieve", {"target_decade": 1970}, 5),
    ],
)
def test_nonmember_seed_fails_before_hydration(client, seed_cache, monkeypatch, mode, rules, seed):
    prepare = AsyncMock(side_effect=AssertionError("Nonmember seed must not prepare"))
    monkeypatch.setattr(ENGINE_REGISTRY[mode], "prepare_run", prepare)
    response = client.post(
        "/api/runs",
        json={
            "name": "Illegal slice",
            "game_type": mode,
            "rules_config": rules,
            "seed_movie_id": seed,
        },
    )
    assert response.status_code == 422, response.text
    assert "slice" in response.json()["detail"]
    prepare.assert_not_awaited()
    seed_cache.assert_not_awaited()


def test_pair_dice_keeps_partner_exclusion_when_rotation_exhausts(client, seed_cache):
    rules = {"min_runtime": 40}
    remaining = {1, 4, 5, 7}
    seen = {4}
    while remaining - seen:
        pick = suggest(client, "meet_in_the_middle", rules, exclude=sorted(seen))["suggestion"]
        assert pick["tmdb_id"] != 4
        assert pick["tmdb_id"] not in seen
        seen.add(pick["tmdb_id"])
    assert suggest(client, "meet_in_the_middle", rules, exclude=sorted(seen))["suggestion"] is None
    assert suggest(client, "meet_in_the_middle", rules, exclude=[4])["suggestion"]["tmdb_id"] != 4
    seed_cache.assert_not_awaited()


def test_seed_api_validates_rules_and_strips_forged_expedition(client, seed_cache):
    invalid = client.post(
        "/api/movies/seed-suggestion",
        json={
            "game_type": "regional_deep_dive",
            "rules_config": {"expedition": {"movie_ids": [4]}},
        },
    )
    assert invalid.status_code == 422
    rules = {"curated_list_id": "canon", "target_country": "JP", "expedition": {"movie_ids": [4]}}
    assert suggest(client, "regional_deep_dive", rules)["suggestion"]["tmdb_id"] in {1, 2}
    for minimum in (-1, True, "40"):
        response = client.post(
            "/api/movies/seed-suggestion",
            json={
                "game_type": "cinechain",
                "rules_config": {"min_runtime": minimum},
            },
        )
        assert response.status_code == 422
    seed_cache.assert_not_awaited()


def test_legacy_get_keeps_movie_shape_and_accepts_derived_rules(client, seed_cache):
    response = client.get(
        "/api/movies/seed-suggestion",
        params={
            "game_type": "regional_deep_dive",
            "rules_config": json.dumps({"curated_list_id": "canon", "target_country": "AU"}),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["tmdb_id"] in {2, 3, 6}
    assert "suggestion" not in response.json()
    assert (
        client.get("/api/movies/seed-suggestion", params={"rules_config": "[]"}).status_code == 422
    )
    assert (
        client.get("/api/movies/seed-suggestion", params={"rules_config": "{"}).status_code == 422
    )
    seed_cache.assert_not_awaited()


def test_empty_slice_has_a_reason_and_no_unbounded_fallback(client, seed_cache):
    result = suggest(
        client, "regional_deep_dive", {"curated_list_id": "canon", "target_country": "FR"}
    )
    assert result == {"suggestion": None, "reason": "No indexed films match this slice yet."}
    seed_cache.assert_not_awaited()
