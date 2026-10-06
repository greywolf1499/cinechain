"""S8: draws fit mode bounds, rewards are spendable, and expiry is reversible."""

import asyncio
import random

import httpx
import pytest
import respx
from sqlmodel import Session

from app.api.routes_runs import _bounty_context, _remove_step
from app.engines import chaos
from app.engines.predicates import predicate
from app.engines.registry import get_engine
from app.models.cache import CachedMovie, CachedMovieDirector
from app.models.run import Run, RunStep
from app.services import bounties, feasibility, llm
from app.services.tmdb import TMDBClient
from tests.test_bounty_split import mock_film
from tests.test_graph_mutators import client, db_engine
from tests.test_rabbit_hole import add_film, put_at_depth, rabbit_run, world

__all__ = ["client", "db_engine", "world"]


@pytest.fixture
def tmdb():
    transport = httpx.AsyncClient()
    yield TMDBClient(transport)
    asyncio.run(transport.aclose())


def cache_pool(session, *, year=2010, language="en", country="US"):
    for index, runtime in enumerate((80, 100, 160, 110, 95, 120), 1):
        session.add(CachedMovie(
            tmdb_id=index, title=f"Film {index}", release_date=f"{year + index % 3}-01-01",
            runtime=runtime, origin_country=f'["{country}"]', original_language=language,
            popularity=5 if index < 3 else 50, genre_ids=[18], overview="A family drama.",
        ))
        session.add(CachedMovieDirector(movie_id=index, person_id=index, name="Director",
                                        gender=1 if index == 1 else 2))
    session.commit()


@pytest.mark.parametrize("mode", ["decade_sieve", "regional_deep_dive"])
def test_200_seeded_boards_never_deal_impossible_or_trivial_quests(db_engine, tmdb, mode):
    with Session(db_engine) as session:
        regional = mode == "regional_deep_dive"
        cache_pool(session, year=1970 if regional else 2010,
                   language="ja" if regional else "en", country="JP" if regional else "US")
        rules = {"target_decade": 1970 if regional else 2010, "min_runtime": 0,
                 "expedition": {"movie_ids": list(range(1, 7)), "decade": 1970}}
        engine = get_engine(mode, session, tmdb)
        for seed in range(200):
            board = bounties.prepare_board(
                rules, random.Random(seed),
                feasible=lambda bounty_id: engine.bounty_feasible(rules, [], bounties.BOUNTIES[bounty_id]).drawable,
            )
            assert "time_capsule" not in board["active_bounties"]
            assert "foreign_horizon" not in board["active_bounties"]
            assert len(board["active_bounties"]) == 3
            for bounty_id in board["active_bounties"]:
                result = engine.bounty_feasible(rules, [], bounties.BOUNTIES[bounty_id])
                assert result.ok and result.pass_rate is not None
                assert 0 < result.pass_rate <= 0.8


def test_symbolic_bounds_work_even_without_cached_evidence(db_engine, tmdb):
    with Session(db_engine) as session:
        engine = get_engine("decade_sieve", session, tmdb)
        result = engine.bounty_feasible({"target_decade": 2010}, [], bounties.BOUNTIES["time_capsule"])
        assert not result.ok and result.pass_rate == 0
        assert engine.bounty_feasible({"target_decade": 2010}, [], bounties.BOUNTIES["short_king"]).ok


def test_unknowns_are_passable_but_do_not_prove_triviality(db_engine, tmdb):
    with Session(db_engine) as session:
        session.add(CachedMovie(tmdb_id=1, title="Unknown"))
        session.commit()
        test = predicate("runtime_lt", value=90)
        assert feasibility.pass_rate(session, test, [1, 999]) == 1
        assert feasibility.exists(session, test, [999])
        assert feasibility.cache_pass_rate(session, test) == 1
        assert not feasibility.exists(session, test, [])
        result = get_engine("cinechain", session, tmdb).bounty_feasible({}, [], bounties.BOUNTIES["short_king"])
        assert result.ok and result.drawable and result.pass_rate is None


def test_exact_empty_track_is_impossible_and_order_ignores_passed_films(db_engine, tmdb):
    with Session(db_engine) as session:
        cache_pool(session, year=1950)
        engine = get_engine("method_actor", session, tmdb)
        rules = {"filmography": [{"movie_id": 1}, {"movie_id": 2}], "order": "strict"}
        history = [RunStep(run_id="test", movie_id=2, movie_title="Film 2")]
        assert not engine.bounty_feasible(rules, history, bounties.BOUNTIES["time_capsule"]).ok
        rules["order"] = "free"
        assert engine.bounty_feasible(rules, history, bounties.BOUNTIES["time_capsule"]).ok
        regional = get_engine("regional_deep_dive", session, tmdb)
        assert not regional.bounty_feasible({"expedition": {"movie_ids": []}}, [], bounties.BOUNTIES["short_king"]).ok


def create(client, mode="decade_sieve", **rules):
    response = client.post("/api/runs", json={
        "name": "Fair draws", "game_type": mode,
        "rules_config": {"target_decade": 2010, "bounty_board": True, "min_runtime": 0, **rules},
    })
    assert response.status_code == 201, response.text
    return response.json()


def set_rules(db_engine, run_id, **updates):
    with Session(db_engine) as session:
        run = session.get(Run, run_id)
        assert run is not None
        run.rules_config = {**run.rules_config, **updates}
        session.add(run)
        session.commit()


def test_chrono_frontier_expires_time_capsule_and_undo_restores_it(client, db_engine):
    run = create(client, "chrono_climb", direction="climb")
    with respx.mock:
        mock_film(1, year=1950)
        mock_film(2, year=1980)
        first = client.post(f"/api/runs/{run['id']}/steps", json={"movie_id": 1})
        assert first.status_code == 201, first.text
        set_rules(db_engine, run["id"], active_bounties=["time_capsule"], completed_bounties=[])
        response = client.post(f"/api/runs/{run['id']}/steps", json={"movie_id": 2})
        assert response.status_code == 201, response.text
    step = response.json()
    assert step["transition_metadata"]["bounty_expired"] == ["time_capsule"]
    assert "bounds" in step["transition_metadata"]["bounty_expiry_reasons"]["time_capsule"]
    after = client.get(f"/api/runs/{run['id']}").json()["rules_config"]
    assert "time_capsule" not in after["active_bounties"]
    assert after["bounty_discards_left"] == 1
    assert client.delete(f"/api/runs/{run['id']}/steps/{step['id']}").status_code == 204
    assert client.get(f"/api/runs/{run['id']}").json()["rules_config"]["active_bounties"] == ["time_capsule"]


@pytest.mark.parametrize(("mode", "reward", "key", "before"), [
    ("regional_deep_dive", "star", "bounty_stars", 0),
    ("decade_sieve", "star", "bounty_stars", 0),
    ("rt_split", "star", "bounty_stars", 0),
    ("roulette", "star", "bounty_stars", 0),
    ("meet_in_the_middle", "hint", "tunnel_hints_remaining", 2),
    ("rabbit_hole", "life", "lives_remaining", 2),
    ("method_actor", "wildcard", "wildcards_budget", 0),
    ("auteur_marathon", "wildcard", "wildcards_budget", 0),
])
def test_rewards_and_actual_delete_path_are_inverses(db_engine, tmdb, mode, reward, key, before):
    with Session(db_engine) as session:
        engine = get_engine(mode, session, tmdb)
        assert engine.bounty_reward == reward
        rules = {"bounty_board": True, "active_bounties": ["short_king"],
                 "completed_bounties": [], "max_lives": 3, key: before, "wildcards_budget": 0}
        rules[key] = before
        awarded = engine.award_bounty(rules, "short_king", "time_capsule")
        assert awarded[key] == before + 1
        if reward != "wildcard":
            assert awarded["wildcards_budget"] == 0
        run = Run(name="Reward", game_type=mode, rules_config=awarded)
        session.add(run)
        session.flush()
        step = RunStep(run_id=run.id, movie_id=1, movie_title="Short",
                       transition_metadata={"completed_bounty": "short_king",
                                            "bounty_replacement": "time_capsule",
                                            "bounty_reward": reward, "bounty_life_awarded": reward == "life"})
        session.add(step)
        session.flush()
        _remove_step(session, tmdb, run, step)
        assert run.rules_config[key] == before
        assert run.rules_config["active_bounties"] == ["short_king"]
        assert run.rules_config["completed_bounties"] == []


def test_discard_is_once_per_run_and_validates_before_spending(client):
    run = create(client)
    board = run["rules_config"]["active_bounties"]
    assert client.post(f"/api/runs/{run['id']}/bounties/not-real/discard").status_code == 409
    response = client.post(f"/api/runs/{run['id']}/bounties/{board[0]}/discard")
    assert response.status_code == 200, response.text
    rules = response.json()["rules_config"]
    assert board[0] not in rules["active_bounties"] and rules["bounty_discards_left"] == 0
    assert client.post(f"/api/runs/{run['id']}/bounties/{rules['active_bounties'][0]}/discard").status_code == 409


def test_new_server_state_cannot_be_forged_or_edited(client):
    run = create(client, bounty_stars=900, bounty_discards_left=900, bounty_roll_note="forged")
    assert run["rules_config"]["bounty_stars"] == 0
    assert run["rules_config"]["bounty_discards_left"] == 1
    assert "bounty_roll_note" not in run["rules_config"]
    with respx.mock:
        mock_film(1, year=2010)
        forged = {"bounty_expired": ["short_king"], "bounty_expiry_reasons": {"x": "forged"},
                  "bounty_expiry_changes": [{"id": "forged"}], "bounty_reward": "hint"}
        step = client.post(f"/api/runs/{run['id']}/steps",
                           json={"movie_id": 1, "transition_metadata": forged}).json()
    metadata = step["transition_metadata"] or {}
    assert not any(key in metadata for key in forged)
    updated = client.patch(f"/api/runs/{run['id']}/steps/{step['id']}",
                           json={"transition_metadata": forged})
    assert updated.status_code == 200, updated.text
    assert not any(key in (updated.json()["transition_metadata"] or {}) for key in forged)


@pytest.mark.asyncio
async def test_ai_retries_infeasible_rules_with_mode_context(db_engine, tmdb, monkeypatch):
    prompts = []

    async def generate(config, system, prompt, **kwargs):
        prompts.append(prompt)
        return '{"title":"Impossible Past","rule":[{"type":"year","max":1959}]}'

    monkeypatch.setattr(llm, "generate", generate)
    with Session(db_engine) as session:
        engine = get_engine("decade_sieve", session, tmdb)
        rules = {"target_decade": 2010}
        context = _bounty_context(engine, rules, [])
        assert "mode=decade_sieve" in context and "2019" in context
        rabbit = get_engine("rabbit_hole", session, tmdb)
        history = [RunStep(run_id="test", movie_id=1, movie_title="Film")] * 15
        assert "tier=Under 100 mins" in _bounty_context(rabbit, {}, history)
        with pytest.raises(llm.LlmUnavailable):
            await bounties.generate_custom_bounty(
                llm.LlmConfig(), feasible=lambda quest: engine.bounty_feasible(rules, [], quest).drawable,
                context=context,
            )
    assert len(prompts) == 2 and all("2010" in prompt and "2019" in prompt for prompt in prompts)


def test_star_reward_appears_in_victory_text(client, db_engine):
    run = create(client)
    set_rules(db_engine, run["id"], active_bounties=["short_king"])
    with respx.mock:
        mock_film(1, year=2010, runtime=80)
        response = client.post(f"/api/runs/{run['id']}/steps", json={"movie_id": 1})
    assert response.status_code == 201, response.text
    completed = client.patch(f"/api/runs/{run['id']}", json={"status": "completed"}).json()
    assert "1 bounty star" in completed["status_reason"]


def test_all_trivial_pool_keeps_a_smaller_board(db_engine, tmdb):
    with Session(db_engine) as session:
        session.add(CachedMovie(tmdb_id=1, title="Short", runtime=80, release_date="2010-01-01",
                                original_language="en", origin_country='["US"]', popularity=2))
        session.add(CachedMovieDirector(movie_id=1, person_id=1, name="Director", gender=2))
        session.commit()
        engine = get_engine("decade_sieve", session, tmdb)
        rules = {"target_decade": 2010}
        board = bounties.prepare_board(
            rules, feasible=lambda bounty_id: engine.bounty_feasible(rules, [], bounties.BOUNTIES[bounty_id]).drawable,
        )
        assert board["active_bounties"] == []


def test_rabbit_tier_four_never_rolls_long_haul(client, world):
    with Session(world) as session:
        add_film(session, 2, year=1950, runtime=80, lang="fr", vote=5)
        add_film(session, 3, year=2010, runtime=95, lang="en", vote=8)
        add_film(session, 4, year=1960, runtime=180, lang="fr", vote=5)
        session.commit()
    run_id = rabbit_run(client)
    put_at_depth(world, run_id, 15)
    response = client.post(f"/api/runs/{run_id}/chaos")
    assert response.status_code == 200, response.text
    active = response.json()["rules_config"]["active_chaos"]
    assert active["id"] != "epic_length"
    assert chaos.HANDICAPS["epic_length"].label in active["skipped"]
    with Session(world) as session:
        evidence = feasibility.Evidence(session)
        choices = [h.id for h in chaos.HANDICAPS.values() if evidence.check(h.predicate, [2, 3]).drawable]
        assert "epic_length" not in choices
        for seed in range(200):
            assert chaos.roll(random.Random(seed), choices)["id"] != "epic_length"


def test_impossible_or_trivial_reroll_does_not_spend_a_life(client, world):
    with Session(world) as session:
        add_film(session, 2, year=1950, runtime=120, lang="en", vote=8)
        session.commit()
    run_id = rabbit_run(client)
    put_at_depth(world, run_id, 5)
    response = client.post(f"/api/runs/{run_id}/rabbit-hole/reroll")
    assert response.status_code == 409, response.text
    rules = client.get(f"/api/runs/{run_id}").json()["rules_config"]
    assert rules["lives_remaining"] == 3 and "tier_override" not in rules
