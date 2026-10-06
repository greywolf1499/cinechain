"""Registry aliases, scope enforcement and title-overlay integration."""
# ruff: noqa: F811

import httpx
import pytest
import respx
from sqlmodel import Session

from app.engines import modifiers
from app.engines.base import RunSetupError
from app.engines.modifier_registry import (
    AlphabetParams,
    AscendingParams,
    ModCtx,
    contexts,
    registry,
)
from app.engines.registry import ENGINE_REGISTRY
from app.models.cache import CachedMovie, CachedMovieRating
from app.models.run import RunStep
from tests.test_composable_modifiers import (  # noqa: F401
    client,
    create_run,
    db_engine,
    film,
    log,
    mock_universe,
)


def overlay(key, **params):
    return [{"key": key, "params": params}]


def step(title, *, seed=False, status="watched", **metadata):
    return RunStep(run_id="test", movie_id=1, movie_title=title, status=status,
                   transition_metadata={"seed": seed, **metadata})


@pytest.mark.parametrize(("key", "params", "flat"), [
    ("chrono_direction", {"direction": "descent"}, "descent"),
    ("runtime_staircase", {"direction": "ascending"}, "ascending"),
    ("country_cooldown", {"steps": 4}, 4),
    ("require_cast_link", {"enabled": True}, True),
])
def test_registry_aliases_override_flat_keys(key, params, flat):
    assert modifiers.merge_modifiers({}, {"modifiers": [{"key": key, "params": params}]}) == {key: flat}
    assert not modifiers.modifier_problems({"modifiers": [{"key": key, "params": params}]})


def test_registry_checks_keep_legacy_semantics():
    active = modifiers.merge_modifiers({}, {
        "modifiers": [{"key": "runtime_staircase", "params": {"direction": "ascending"}}],
    })
    a = CachedMovie(tmdb_id=1, title="A", runtime=100)
    b = CachedMovie(tmdb_id=2, title="B", runtime=80)
    spec, ctx = contexts(active, earlier=a)[0]
    assert spec.check(ctx, b).ok is False
    b.runtime = None
    assert spec.check(ctx, b).ok is None


def test_registry_param_validation_and_scope():
    for entries in [
        [{"key": "unknown"}],
        [{"key": "country_cooldown", "params": {"steps": True}}],
        [{"key": "chrono_direction", "params": {"direction": "up"}}],
        [{"key": "country_cooldown"}, {"key": "country_cooldown"}],
    ]:
        assert modifiers.modifier_problems({"modifiers": entries})
    assert registry()["runtime_staircase"].compatible(ENGINE_REGISTRY["rt_split"])
    assert registry()["country_cooldown"].compatible(ENGINE_REGISTRY["roulette"])
    assert registry()["chrono_direction"].compatible(ENGINE_REGISTRY["cinechain"]) is None


def test_alphabet_fold_wild_seed_and_reverse():
    spec = registry()["alphabet_run"]
    ctx = ModCtx(AlphabetParams(wild_letters=[]), [step("The Alien")])
    assert spec.check(ctx, CachedMovie(tmdb_id=2, title="Brazil")).ok
    assert not spec.check(ctx, CachedMovie(tmdb_id=3, title="Crash")).ok
    assert spec.progress(ctx)["next"] == "B"
    assert spec.progress(ModCtx(AlphabetParams(), [step("Alien", seed=True)]))["next"] == "A"
    seeded = ModCtx(AlphabetParams(seed_sets_start=True), [step("Yojimbo", seed=True), step("Zodiac")])
    assert spec.outcome(seeded).status == "completed"
    assert spec.progress(ModCtx(AlphabetParams(), [step(letter + "movie") for letter in "ABCDEFGHIJKLMNOP"]))["next"] == "Q"
    assert spec.check(ModCtx(AlphabetParams(), [step(letter + "movie") for letter in "ABCDEFGHIJKLMNOP"]),
                      CachedMovie(tmdb_id=3, title="Anything")).ok
    assert spec.check(ModCtx(AlphabetParams(), []), CachedMovie(tmdb_id=3, title="2001")).ok
    reverse = ModCtx(AlphabetParams(direction="za", seed_sets_start=True, wild_letters=[]),
                     [step("Brazil", seed=True), step("Alien")])
    assert spec.outcome(reverse).status == "completed"
    assert spec.progress(ModCtx(AlphabetParams(), [step("Alien", status="planned")]))["next"] == "A"


def test_relaxed_alphabet_is_non_decreasing():
    spec = registry()["alphabet_run"]
    ctx = ModCtx(AlphabetParams(strict=False, wild_letters=[]), [step("Crash"), step("Casablanca")])
    assert spec.check(ctx, CachedMovie(tmdb_id=2, title="Crash")).ok
    assert spec.check(ctx, CachedMovie(tmdb_id=2, title="Fight Club")).ok
    assert not spec.check(ctx, CachedMovie(tmdb_id=2, title="Brazil")).ok


def test_count_up_and_increasing_choose_smallest_eligible_token():
    spec = registry()["ascending_numbers"]
    ctx = ModCtx(AscendingParams(target=5), [step(f"Film {n}") for n in range(1, 6)])
    assert spec.outcome(ctx).reason == "Count to 5 conquered in 5 films"
    assert not spec.check(ModCtx(AscendingParams(), [step("One")]), CachedMovie(tmdb_id=2, title="Three")).ok
    inc = ModCtx(AscendingParams(mode="increasing"), [step("2 Fast 4 Furious")])
    assert spec.progress(inc)["next"] == "3"


@pytest.mark.parametrize("mode", ["meet_in_the_middle", "method_actor", "rt_split"])
def test_incompatible_sequence_returns_reason(client, mode):
    response = client.post("/api/runs", json={"name": "Bad", "game_type": mode,
                                             "rules_config": {"modifiers": overlay("alphabet_run")}})
    assert response.status_code == 422
    assert "isn't supported" in response.json()["detail"]


def test_checklist_and_graph_creation_feasibility(db_engine):
    from app.services.tmdb import TMDBClient
    with Session(db_engine) as session:
        tmdb = TMDBClient(httpx.AsyncClient())
        engine = ENGINE_REGISTRY["regional_deep_dive"](session, tmdb)
        rules = {"modifiers": overlay("alphabet_run", wild_letters=[]),
                 "expedition": {"films": [{"movie_id": i, "title": letter} for i, letter in enumerate("ABCDEFGHIJKL", 1)]}}
        with pytest.raises(RunSetupError, match="infeasible.*checklist"):
            engine.prepare_overlays(rules)
        session.add(CachedMovie(tmdb_id=1, title="Brazil", release_date="2000-01-01", status="Released"))
        session.commit()
        graph = ENGINE_REGISTRY["cinechain"](session, tmdb)
        with pytest.raises(RunSetupError, match="cached mode pool"):
            graph.prepare_overlays({"modifiers": overlay("alphabet_run", wild_letters=[])})


@pytest.mark.parametrize(("budget", "reachable_b", "expected"), [(1, False, 201), (2, False, 201), (1, True, 409), (0, False, 409), (-1, False, 201)])
def test_unreachable_skip_is_prechecked_spent_and_reversible(client, budget, reachable_b, expected):
    run = create_run(client, "cinechain", modifiers=overlay("alphabet_run", wild_letters=[]),
                     wildcards_budget=budget, no_consecutive_actor=budget == 2)
    with respx.mock:
        movies = {1: film("Alien", 2000), 2: film("Dune", 2001), 4: film("Eraser", 2003)}
        if reachable_b:
            movies[3] = film("Brazil", 2002)
        mock_universe(movies)
        assert log(client, run, 1).status_code == 201
        flags = client.get(f"/api/runs/{run}/discover?frontier_movie_id=1").json()
        assert next(candidate for candidate in flags if candidate["movie_id"] == 2)["overlay_ok"] == {"alphabet_run": False}
        preflight = client.post(f"/api/runs/{run}/validate", json={"movie_id": 2}).json()
        assert preflight["overlay_skippable"] == (["alphabet_run"] if not reachable_b and budget else [])
        if preflight["overlay_skippable"]:
            assert preflight["connections"]
        assert log(client, run, 2, force=True, skip_overlays=["alphabet_run"], status="planned").status_code == 422
        assert log(client, run, 2, force=True).status_code == 409
        response = log(client, run, 2, force=True, skip_overlays=["alphabet_run"])
        assert response.status_code == expected, response.text
        current = client.get(f"/api/runs/{run}").json()["rules_config"]["wildcards_budget"]
        assert current == (budget - 1 if expected == 201 and budget != -1 else budget)
        if expected == 201:
            progress = client.get(f"/api/runs/{run}/constraint").json()["overlay_progress"][0]
            assert progress["next"] == "C"
            assert response.json()["transition_metadata"]["overlay_skips"] == ["alphabet_run"]
            assert response.json()["transition_metadata"]["actor_id"] == preflight["connections"][0]["actor_id"]
            from app.engines.conditions import _wildcards_used
            assert _wildcards_used([RunStep(movie_id=2, run_id=run, transition_metadata=response.json()["transition_metadata"])]) == (0 if budget == -1 else 1)
            if budget == 2:
                consecutive = log(client, run, 4, force=True, skip_overlays=["alphabet_run"])
                assert consecutive.status_code == 409
                assert "every other run rule" in consecutive.json()["detail"]
                assert client.get(f"/api/runs/{run}").json()["rules_config"]["wildcards_budget"] == 1
            assert client.delete(f"/api/runs/{run}/steps/{response.json()['id']}").status_code == 204
            assert client.get(f"/api/runs/{run}").json()["rules_config"]["wildcards_budget"] == budget
            assert client.get(f"/api/runs/{run}/constraint").json()["overlay_progress"][0]["next"] == "B"


def test_queued_films_are_rechecked_on_watch_and_notes_update(client):
    run = create_run(client, "chrono_climb", modifiers=overlay("alphabet_run", wild_letters=[]))
    with respx.mock:
        mock_universe({1: film("Alien", 2000), 2: film("Arrival", 2001)})
        first = log(client, run, 1, status="planned").json()
        second = log(client, run, 2, status="planned").json()
        assert client.patch(f"/api/runs/{run}/steps/{first['id']}/mark-watched", json={}).status_code == 200
        assert client.patch(f"/api/runs/{run}/steps/{first['id']}/mark-watched", json={}).status_code == 409
        assert client.patch(f"/api/runs/{run}/steps/{second['id']}/mark-watched", json={}).status_code == 409
        assert client.patch(f"/api/runs/{run}/steps/{second['id']}", json={"watched_at": "2026-01-01T00:00:00Z"}).status_code == 409


def test_number_filter_applies_to_split_pool_and_no_contest(client, db_engine):
    run = create_run(client, "rt_split", modifiers=overlay("number_in_title"))
    with Session(db_engine) as session:
        for movie_id, title in [(1, "Rocky II"), (2, "Alien"), (3, "1917")]:
            session.add(CachedMovie(tmdb_id=movie_id, title=title, release_date="2000-01-01",
                                    runtime=100, status="Released"))
        session.commit()
        for movie_id in (1, 2, 3):
            session.add(CachedMovieRating(movie_id=movie_id, rotten_tomatoes="90%", imdb_rating="5.0"))
        session.commit()
    pool = client.get(f"/api/runs/{run}/split-pool").json()
    assert [movie["movie_id"] for movie in pool["candidates"]] == [1]
    assert pool["candidates"][0]["overlay_ok"] == {"number_in_title": True}
    with respx.mock:
        mock_universe({1: film("Rocky II", 2000), 2: film("Alien", 2000)})
        assert log(client, run, 2, no_contest=True, force=True).status_code == 409
        assert log(client, run, 1, no_contest=True).status_code == 201


def test_finite_number_filter_keeps_order_and_final_qualifying_film(db_engine):
    from app.services.tmdb import TMDBClient
    with Session(db_engine) as session:
        engine = ENGINE_REGISTRY["method_actor"](session, TMDBClient(httpx.AsyncClient()))
        films = [{"movie_id": movie_id, "title": title} for movie_id, title in enumerate(
            ["Alien", "Rocky II", "Eleven", "Three", "Zodiac"], 1)]
        rules = engine.prepare_overlays({"modifiers": overlay("number_in_title"), "filmography": films})
        assert [film["title"] for film in rules["filmography"]] == ["Rocky II", "Eleven", "Three"]
        with pytest.raises(RunSetupError, match="at least 3"):
            engine.prepare_overlays({"modifiers": overlay("number_in_title"), "filmography": films[:3]})


def test_number_filter_is_applied_before_sequence_feasibility(db_engine):
    from app.services.tmdb import TMDBClient
    with Session(db_engine) as session:
        engine = ENGINE_REGISTRY["regional_deep_dive"](session, TMDBClient(httpx.AsyncClient()))
        rules = {"modifiers": overlay("alphabet_run", strict=False, wild_letters=[]) + overlay("number_in_title"),
                 "expedition": {"films": [{"movie_id": i, "title": title} for i, title in enumerate(
                     ["One", "Two", "Three", "Zodiac"], 1)]}}
        with pytest.raises(RunSetupError, match="infeasible"):
            engine.prepare_overlays(rules)


def test_log_enforcement_progress_victory_and_undo(client):
    run = create_run(client, "chrono_climb", modifiers=overlay("ascending_numbers", target=2))
    with respx.mock:
        mock_universe({1: film("One", 2000), 2: film("Two", 2001), 3: film("Three", 2002)})
        assert log(client, run, 3, force=True).status_code == 409
        assert log(client, run, 1).status_code == 201
        last = log(client, run, 2)
        assert last.status_code == 201
        detail = client.get(f"/api/runs/{run}").json()
        assert detail["status"] == "completed"
        assert "Count to 2" in detail["status_reason"]
        assert client.delete(f"/api/runs/{run}/steps/{last.json()['id']}").status_code == 204
        assert client.get(f"/api/runs/{run}").json()["status"] == "active"


def test_clients_cannot_forge_overlay_progress(client):
    run = create_run(client, "cinechain", modifiers=overlay("alphabet_run"),
                     alphabet_next="Z", ascending_next=99, overlay_progress=[{"next": "Z"}])
    rules = client.get(f"/api/runs/{run}").json()["rules_config"]
    assert not {"alphabet_next", "ascending_next", "overlay_progress"} & rules.keys()
    with respx.mock:
        mock_universe({1: film("Alien", 2000)})
        response = log(client, run, 1, transition_metadata={"overlay_skips": ["alphabet_run"],
                                                         "overlay_wildcard_spent": -99, "alphabet_next": "Z"})
    assert response.status_code == 201
    assert not {"overlay_skips", "overlay_wildcard_spent", "alphabet_next"} & (response.json()["transition_metadata"] or {}).keys()


def test_engine_metadata_and_all_new_rulebook_fragments(client):
    metadata = client.get("/api/engines").json()
    for mode in metadata:
        assert {spec["key"] for spec in mode["modifiers"]} == set(registry())
        assert all(spec["params_schema"]["type"] == "object" for spec in mode["modifiers"])
        alphabet = next(spec for spec in mode["modifiers"] if spec["key"] == "alphabet_run")
        assert alphabet["params_schema"]["properties"]["wild_letters"]["default"] == ["Q", "X", "Z"]
    run = create_run(client, "cinechain", modifiers=overlay("alphabet_run"))
    response = client.get(f"/api/runs/{run}/rulebook")
    assert response.status_code == 200
    assert response.json()["overlays"][0]["key"] == "alphabet_run"
