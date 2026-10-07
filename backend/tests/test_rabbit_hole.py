"""Phase 26b: The Rabbit Hole - escalating tiers, a 3-life survival budget and tier-filtered Pick Next."""

import asyncio
import random
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import httpx
import pytest
import respx
from sqlmodel import Session, select

from app.engines import rabbit_hole
from app.engines.predicates import predicate
from app.engines.rabbit_hole import RabbitHoleEngine, tier_for_depth, tier_state
from app.models.cache import CachedActor, CachedMovie, CachedMovieCast, CachedMovieRating
from app.models.run import Run, RunStep
from app.services import feasibility
from app.services.movie_filters import is_reality_eligible
from app.services.tmdb import TMDBClient
from app.utils.ids import utcnow
from tests.test_graph_mutators import TMDB_BASE, client, create_run, db_engine, log

__all__ = ["client", "db_engine"]

ACTOR = 100
LONER_ACTOR = 200
ANCHOR = 1  # a film every run can already contain: modern, English, 120 min, well rated


def add_film(
    session,
    film_id,
    *,
    year=1990,
    lang="en",
    runtime=120,
    vote=7.0,
    vote_count=100,
    imdb=None,
    actor=ACTOR,
    title=None,
):
    session.add(
        CachedMovie(
            tmdb_id=film_id,
            title=title or f"Film {film_id}",
            release_date=f"{year}-06-01",
            status="Released",
            original_language=lang,
            runtime=runtime,
            vote_average=vote,
            vote_count=vote_count,
            overview="x",
            popularity=10.0,
            origin_country='["US"]',
            cast_fetched_at=utcnow(),
            directors_fetched_at=utcnow(),
        )
    )
    session.flush()
    session.add(CachedMovieCast(movie_id=film_id, actor_id=actor, cast_order=0, character_name="x"))
    if imdb is not None:
        session.add(CachedMovieRating(movie_id=film_id, imdb_rating=imdb))


@pytest.fixture()
def world(db_engine):
    with Session(db_engine) as session:
        for actor in (ACTOR, LONER_ACTOR):
            session.add(
                CachedActor(tmdb_id=actor, name=f"Actor {actor}", credits_fetched_at=utcnow())
            )
        add_film(session, ANCHOR, year=2010, runtime=120)
        session.commit()
    return db_engine


def put_at_depth(db_engine, run_id, depth):
    """Inserts `depth` steps, the last one being the anchor film (every film links to it)."""
    base = utcnow() - timedelta(seconds=depth + 1)
    with Session(db_engine) as session:
        for index in range(depth):
            session.add(
                RunStep(
                    run_id=run_id,
                    movie_id=ANCHOR,
                    movie_title="Film 1",
                    status="watched",
                    logged_at=base + timedelta(seconds=index),
                )
            )
        session.commit()


def rabbit_run(client, **rules):
    async def legacy_prepare(self, config, user_id):
        return config

    # These historical assertions intentionally exercise stored v1 rules, not a new deck.
    with patch.object(RabbitHoleEngine, "prepare_run", legacy_prepare):
        return create_run(client, "rabbit_hole", **rules)


def test_ironman_refuses_reroll_before_spending(client, world):
    run_id = rabbit_run(client, max_lives=1, allow_reroll=False)
    put_at_depth(world, run_id, 6)
    response = client.post(f"/api/runs/{run_id}/rabbit-hole/reroll")
    assert response.status_code == 409
    assert "disabled" in response.json()["detail"]
    rules = client.get(f"/api/runs/{run_id}").json()["rules_config"]
    assert rules["lives_remaining"] == 1 and "tier_override" not in rules


def run_detail(client, run_id):
    return client.get(f"/api/runs/{run_id}").json()


def lives(client, run_id):
    return run_detail(client, run_id)["rules_config"]["lives_remaining"]


def update_rules(db_engine, run_id, **updates):
    with Session(db_engine) as session:
        run = session.get(Run, run_id)
        run.rules_config = {**(run.rules_config or {}), **updates}
        session.add(run)
        session.commit()


def validate(client, run_id, film_id):
    resp = client.post(f"/api/runs/{run_id}/validate", json={"movie_id": film_id})
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- the state machine ---


@pytest.mark.parametrize(
    "depth,tier",
    [(0, 1), (4, 1), (5, 2), (9, 2), (10, 3), (14, 3), (15, 4), (19, 4), (20, 5), (99, 5)],
)
def test_the_tier_follows_the_depth(depth, tier):
    assert tier_for_depth(depth).number == tier


def test_tier_names_and_rules():
    assert [t.name for t in (tier_for_depth(d) for d in (0, 5, 10, 15, 20))] == [
        "Freefall",
        "The Retro Lock",
        "Tower of Babel",
        "The Micro-Clock",
        "The B-Movie Abyss",
    ]


@pytest.mark.parametrize(
    "depth,away,warned",
    [
        (0, 5, False),
        (2, 3, False),
        (3, 2, True),
        (4, 1, True),
        (5, 5, False),
        (13, 2, True),
        (14, 1, True),
        (19, 1, True),
        (20, None, False),
        (25, None, False),
    ],
)
def test_the_next_tier_is_announced_one_or_two_hops_ahead(depth, away, warned):
    state = tier_state(depth, None)
    assert state.steps_until_next == away
    assert (state.upcoming_tier_warning is not None) is warned


def test_the_warning_text():
    assert tier_state(14, None).upcoming_tier_warning == (
        "⚠️ Warning: Tier 4 (Under 100 mins) begins on the next hop!"
    )
    assert tier_state(13, None).upcoming_tier_warning == (
        "⚠️ Warning: Tier 4 (Under 100 mins) begins in 2 hops!"
    )


# --- creating a run ---


def test_the_engine_is_registered(client):
    engines = {e["game_type"]: e for e in client.get("/api/engines").json()}
    assert "rabbit_hole" in engines and engines["rabbit_hole"]["display_name"] == "The Rabbit Hole"
    assert "discover_candidates" in engines["rabbit_hole"]["capabilities"]
    assert RabbitHoleEngine.uses_lives


def test_a_new_run_starts_on_full_lives_whatever_the_client_sends(client):
    rules = run_detail(client, rabbit_run(client))["rules_config"]
    assert (rules["lives_remaining"], rules["max_lives"]) == (3, 3)
    cheat = run_detail(client, rabbit_run(client, lives_remaining=99))["rules_config"]
    assert (cheat["lives_remaining"], cheat["max_lives"]) == (3, 3)
    five = run_detail(client, rabbit_run(client, max_lives=5))["rules_config"]
    assert (five["lives_remaining"], five["max_lives"]) == (5, 5)
    bad = client.post(
        "/api/runs",
        json={"name": "x", "game_type": "rabbit_hole", "rules_config": {"max_lives": 0}},
    )
    assert bad.status_code == 422


@pytest.mark.parametrize(
    "escape_depth,valid",
    [
        (None, True),
        (25, True),
        (60, True),
        (24, False),
        (61, False),
        (True, False),
        (25.0, False),
    ],
)
def test_escape_depth_is_optional_and_bounded(client, procedural_world, escape_depth, valid):
    rules = {"escape_depth": escape_depth} if escape_depth is not None else {}
    response = client.post(
        "/api/runs", json={"name": "Escape", "game_type": "rabbit_hole", "rules_config": rules}
    )
    assert (response.status_code == 201) is valid


def test_clients_cannot_forge_rabbit_hole_server_state(client):
    run = rabbit_run(
        client,
        lives_remaining=0,
        tier_override={"depth": 5, "tier": 5},
    )
    assert run_detail(client, run)["rules_config"]["lives_remaining"] == 3
    assert "tier_override" not in run_detail(client, run)["rules_config"]


# --- tier rules ---


def add_candidates(db_engine):
    with Session(db_engine) as session:
        add_film(session, 10, year=1985, title="Retro")
        add_film(session, 11, year=2005, title="Modern")
        add_film(session, 12, year=2015, lang="fr", title="Français")
        add_film(session, 13, year=2015, lang="en", title="English")
        add_film(session, 14, year=2015, runtime=99, title="Short")
        add_film(session, 15, year=2015, runtime=100, title="Exactly100")
        add_film(session, 16, year=2015, vote=5.9, title="Bad")
        add_film(session, 17, year=2015, vote=6.0, title="Mediocre")
        add_film(session, 18, year=2015, vote=8.0, imdb="5.5", title="ImdbBad")
        add_film(session, 19, year=2015, vote=3.0, imdb="7.5", title="ImdbGood")
        session.commit()


@pytest.mark.parametrize(
    "depth,film,ok",
    [
        (0, 11, True),
        (4, 13, True),  # Freefall: anything linked
        (5, 10, True),
        (5, 11, False),
        (5, 12, False),  # Retro Lock: before 2000
        (10, 12, True),
        (10, 13, False),  # Tower of Babel: not English
        (15, 14, True),
        (15, 15, False),
        (15, 13, False),  # Micro-Clock: under 100
        (20, 16, True),
        (20, 17, False),
        (20, 18, True),
        (20, 19, False),  # B-Movie Abyss
    ],
)
def test_each_tier_judges_the_next_film(client, world, depth, film, ok):
    add_candidates(world)
    run_id = rabbit_run(client)
    if depth:
        put_at_depth(world, run_id, depth)
    result = validate(client, run_id, film)
    assert result["valid"] is ok
    assert result["blocked"] is False  # soft: a life can buy it
    if not ok:
        assert "Tier " in result["reason"]


def test_an_unknown_value_never_costs_a_life(client, world):
    with Session(world) as session:
        add_film(session, 30, year=2015, lang=None)
        session.commit()
    run_id = rabbit_run(client)
    put_at_depth(world, run_id, 10)  # Tower of Babel needs the language
    with respx.mock:
        # TMDB can't say either: the film stays unverified, and unverified is allowed.
        respx.get(f"{TMDB_BASE}/movie/30").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 30,
                    "title": "Film 30",
                    "release_date": "2015-06-01",
                    "poster_path": None,
                    "overview": "x",
                    "origin_country": ["US"],
                    "original_language": "",
                    "runtime": 120,
                    "genres": [],
                    "popularity": 1.0,
                    "status": "Released",
                    "vote_average": 7.0,
                },
            )
        )
        assert validate(client, run_id, 30)["valid"] is True


# --- lives ---


def test_a_rule_breaking_step_needs_force_and_costs_a_life(client, world):
    add_candidates(world)
    run_id = rabbit_run(client)
    put_at_depth(world, run_id, 5)  # Tier 2: before 2000

    refused = log(client, run_id, 11)
    assert refused.status_code == 409 and refused.json()["detail"]["blocked"] is False
    assert lives(client, run_id) == 3

    forced = log(client, run_id, 11, force=True)
    assert forced.status_code == 201
    assert forced.json()["transition_metadata"]["life_lost"] is True
    rules = run_detail(client, run_id)["rules_config"]
    assert (
        rules["lives_remaining"] == 2 and rules["wildcards_budget"] == 2
    )  # a life, not a wildcard

    assert log(client, run_id, 10).status_code == 201  # a legal film costs nothing
    assert lives(client, run_id) == 2


def test_a_missing_cast_link_also_costs_a_life(client, world):
    with Session(world) as session:
        add_film(session, 40, year=1980, actor=LONER_ACTOR, title="Stranger")
        session.commit()
    run_id = rabbit_run(client)
    log(client, run_id, ANCHOR)
    assert log(client, run_id, 40).status_code == 409
    assert log(client, run_id, 40, force=True).status_code == 201
    assert lives(client, run_id) == 2


def test_a_double_violation_still_costs_one_life(client, world):
    with Session(world) as session:
        add_film(session, 41, year=2015, actor=LONER_ACTOR, title="Both")
        session.commit()
    run_id = rabbit_run(client)
    put_at_depth(world, run_id, 5)
    assert log(client, run_id, 41, force=True).status_code == 201
    assert lives(client, run_id) == 2


def test_bounty_rewards_a_life_instead_of_a_wildcard_and_undo_restores_state(
    client, world, db_engine
):
    run_id = rabbit_run(client, bounty_board=True)
    add_film_in_run = 51
    with Session(world) as session:
        add_film(session, add_film_in_run, runtime=80)
        session.commit()
    update_rules(
        db_engine,
        run_id,
        active_bounties=["short_king", "time_capsule", "hidden_gem"],
        completed_bounties=[],
        lives_remaining=2,
        wildcards_budget=0,
    )

    step = log(client, run_id, add_film_in_run).json()
    rules = run_detail(client, run_id)["rules_config"]
    assert step["transition_metadata"]["completed_bounty"] == "short_king"
    assert step["transition_metadata"]["bounty_life_awarded"] is True
    assert rules["lives_remaining"] == 3 and rules["wildcards_budget"] == 0
    assert client.delete(f"/api/runs/{run_id}/steps/{step['id']}").status_code == 204
    rules = run_detail(client, run_id)["rules_config"]
    assert rules["lives_remaining"] == 2 and rules["wildcards_budget"] == 0
    assert "short_king" in rules["active_bounties"]


def test_bounty_life_reward_is_capped_and_its_marker_cannot_be_forged(client, world):
    run_id = rabbit_run(client, bounty_board=True)
    with Session(world) as session:
        add_film(session, 52, runtime=80)
        session.commit()
    update_rules(
        world,
        run_id,
        active_bounties=["short_king", "time_capsule", "hidden_gem"],
        completed_bounties=[],
        lives_remaining=3,
        wildcards_budget=0,
    )
    step = log(
        client,
        run_id,
        52,
        transition_metadata={"bounty_life_awarded": True, "life_lost": True},
    ).json()
    assert step["transition_metadata"]["bounty_life_awarded"] is False
    assert "life_lost" not in step["transition_metadata"]
    assert lives(client, run_id) == 3


def test_tier_reroll_spends_one_life_and_only_affects_one_depth(client, world):
    add_candidates(world)
    run_id = rabbit_run(client)
    put_at_depth(world, run_id, 5)
    response = client.post(f"/api/runs/{run_id}/rabbit-hole/reroll")
    assert response.status_code == 200, response.text
    rules = response.json()["rules_config"]
    override = rules["tier_override"]
    assert override["depth"] == 5 and override["tier"] in {3, 4, 5}
    assert rules["lives_remaining"] == 2
    state = client.get(f"/api/runs/{run_id}/constraint").json()["rabbit_hole"]
    assert state["tier"] == override["tier"] and state["tier_override"] == override["tier"]

    legal_film = {2: 10, 3: 12, 4: 14, 5: 16}[override["tier"]]
    assert log(client, run_id, legal_film).status_code == 201
    assert "tier_override" not in run_detail(client, run_id)["rules_config"]
    assert lives(client, run_id) == 2


def test_tier_reroll_refusals_do_not_spend_lives(client, world):
    tier_one = rabbit_run(client)
    assert client.post(f"/api/runs/{tier_one}/rabbit-hole/reroll").status_code == 409
    assert lives(client, tier_one) == 3

    last_life = rabbit_run(client, max_lives=2)
    put_at_depth(world, last_life, 5)
    update_rules(world, last_life, lives_remaining=1)
    assert client.post(f"/api/runs/{last_life}/rabbit-hole/reroll").status_code == 409
    assert lives(client, last_life) == 1

    inactive = rabbit_run(client)
    put_at_depth(world, inactive, 5)
    assert client.patch(f"/api/runs/{inactive}", json={"status": "forfeited"}).status_code == 200
    assert client.post(f"/api/runs/{inactive}/rabbit-hole/reroll").status_code == 409
    assert lives(client, inactive) == 3

    already_rerolled = rabbit_run(client)
    put_at_depth(world, already_rerolled, 5)
    update_rules(
        world,
        already_rerolled,
        lives_remaining=3,
        tier_override={"depth": 5, "tier": 3},
    )
    assert client.post(f"/api/runs/{already_rerolled}/rabbit-hole/reroll").status_code == 409
    assert lives(client, already_rerolled) == 3


def test_with_no_lives_left_only_a_legal_film_continues(client, world):
    add_candidates(world)
    run_id = rabbit_run(client, max_lives=1)
    put_at_depth(world, run_id, 5)
    assert log(client, run_id, 11, force=True).status_code == 201
    assert lives(client, run_id) == 0
    assert run_detail(client, run_id)["status"] == "active"  # zero lives isn't the end by itself

    blocked = log(client, run_id, 12, force=True)  # still Tier 2: modern/foreign
    assert blocked.status_code == 409 and "No lives remaining" in blocked.json()["detail"]["reason"]
    assert lives(client, run_id) == 0
    assert log(client, run_id, 10).status_code == 201


# --- game over ---


def test_giving_up_at_zero_lives_fails_the_run_with_a_flavoured_reason(client, world):
    add_candidates(world)
    run_id = rabbit_run(client, max_lives=1)
    put_at_depth(world, run_id, 10)  # Tower of Babel
    log(client, run_id, 13, force=True)  # English at Tier 3: the last life
    assert lives(client, run_id) == 0

    resp = client.patch(f"/api/runs/{run_id}", json={"status": "forfeited"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["status_reason"] == "Succumbed to the Rabbit Hole at Depth 11 (Tower of Babel)"
    assert body["completed_at"] is not None
    # A finished run is locked.
    assert log(client, run_id, 12).status_code == 409


def test_giving_up_with_lives_left_is_a_plain_forfeit(client, world):
    run_id = rabbit_run(client)
    put_at_depth(world, run_id, 3)
    body = client.patch(f"/api/runs/{run_id}", json={"status": "forfeited"}).json()
    assert body["status"] == "forfeited"
    assert body["status_reason"] == "Forfeited by a participant"


# --- Pick Next ---


def discover(client, run_id):
    resp = client.get(f"/api/runs/{run_id}/discover", params={"frontier_movie_id": ANCHOR})
    assert resp.status_code == 200, resp.text
    return {c["movie_id"]: c for c in resp.json()}


def test_pick_next_is_prefiltered_to_the_active_tier(client, world):
    add_candidates(world)
    run_id = rabbit_run(client)

    put_at_depth(world, run_id, 3)
    free = discover(client, run_id)
    assert {10, 11, 12, 13} <= set(free)  # Freefall: every linked film
    assert all(c["tier_compliant"] is True for c in free.values())

    run_id = rabbit_run(client)
    put_at_depth(world, run_id, 5)
    retro = discover(client, run_id)
    assert 10 in retro and not {11, 12, 13, 14} & set(retro)
    assert retro[10]["tier_compliant"] is True

    run_id = rabbit_run(client)
    put_at_depth(world, run_id, 10)
    babel = discover(client, run_id)
    assert 12 in babel and not {10, 11, 13} & set(babel)

    run_id = rabbit_run(client)
    put_at_depth(world, run_id, 15)
    micro = discover(client, run_id)
    assert 14 in micro and not {15, 12, 13} & set(micro)

    run_id = rabbit_run(client)
    put_at_depth(world, run_id, 20)
    abyss = discover(client, run_id)
    assert {16, 18} <= set(abyss) and not {17, 19} & set(abyss)


def test_pick_next_carries_the_upcoming_tier_warning(client, world):
    add_candidates(world)
    run_id = rabbit_run(client)
    put_at_depth(world, run_id, 13)
    pool = discover(client, run_id)
    assert pool and all(
        c["upcoming_tier_warning"] == "⚠️ Warning: Tier 4 (Under 100 mins) begins in 2 hops!"
        for c in pool.values()
    )

    run_id = rabbit_run(client)
    put_at_depth(world, run_id, 11)
    assert all(c["upcoming_tier_warning"] is None for c in discover(client, run_id).values())


def test_include_off_tier_is_read_only_and_logging_still_costs_a_life(client, world):
    add_candidates(world)
    run_id = rabbit_run(client)
    put_at_depth(world, run_id, 5)
    normal = discover(client, run_id)
    assert 11 not in normal
    resp = client.get(
        f"/api/runs/{run_id}/discover",
        params={"frontier_movie_id": ANCHOR, "include_off_tier": True},
    )
    assert resp.status_code == 200, resp.text
    pool = {c["movie_id"]: c for c in resp.json()}
    assert pool[11]["tier_compliant"] is False
    assert pool[10]["tier_compliant"] is True
    assert lives(client, run_id) == 3
    assert log(client, run_id, 11).status_code == 409
    assert lives(client, run_id) == 3
    forced = log(client, run_id, 11, force=True)
    assert forced.status_code == 201, forced.text
    assert forced.json()["transition_metadata"]["life_lost"] is True
    assert lives(client, run_id) == 2


def test_other_modes_cannot_request_off_tier_discovery(client, world):
    run_id = create_run(client, "cinechain")
    response = client.get(
        f"/api/runs/{run_id}/discover",
        params={"frontier_movie_id": ANCHOR, "include_off_tier": True},
    )
    assert response.status_code == 422


def test_the_constraint_endpoint_describes_the_tier(client, world):
    run_id = rabbit_run(client)
    put_at_depth(world, run_id, 14)
    info = client.get(f"/api/runs/{run_id}/constraint").json()
    assert info["title"] == "Tier 3: Tower of Babel"
    state = info["rabbit_hole"]
    assert (state["depth"], state["tier"], state["lives_remaining"], state["max_lives"]) == (
        14,
        3,
        3,
        3,
    )
    assert state["next_tier"] == 4 and state["steps_until_next"] == 1
    assert "begins on the next hop" in state["upcoming_tier_warning"]


def test_constraint_marks_a_zero_life_empty_pool_as_dead_end(client, world, monkeypatch):
    run_id = rabbit_run(client, max_lives=1)
    put_at_depth(world, run_id, 10)
    update_rules(world, run_id, lives_remaining=0)

    async def no_candidates(self, **kwargs):
        return []

    monkeypatch.setattr(RabbitHoleEngine, "discover_with_modifiers", no_candidates)
    state = client.get(f"/api/runs/{run_id}/constraint").json()["rabbit_hole"]
    assert state["dead_end"] is True


def test_escape_depth_completes_the_run_with_remaining_lives(client, world):
    run_id = rabbit_run(client, max_lives=4, escape_depth=25)
    put_at_depth(world, run_id, 24)
    with Session(world) as session:
        add_film(session, 80, year=2015, vote=5.0)
        session.commit()
    response = log(client, run_id, 80)
    assert response.status_code == 201
    run = run_detail(client, run_id)
    assert run["status"] == "completed"
    assert run["status_reason"] == "Escaped the Rabbit Hole at Depth 25 with ❤️×4"
    last_step_id = run["steps"][-1]["id"]
    assert client.delete(f"/api/runs/{run_id}/steps/{last_step_id}").status_code == 204
    assert run_detail(client, run_id)["status"] == "active"


@pytest.mark.parametrize("vote_count", [None, 0, 9])
def test_tier_five_tmdb_score_with_too_few_votes_is_unverified(client, world, vote_count):
    with Session(world) as session:
        add_film(session, 60 + (vote_count or 0), vote=4.2, vote_count=vote_count)
        session.commit()
    film_id = 60 + (vote_count or 0)
    with respx.mock:
        respx.get(f"{TMDB_BASE}/movie/{film_id}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": film_id,
                    "title": f"Film {film_id}",
                    "release_date": "2015-06-01",
                    "poster_path": None,
                    "overview": "x",
                    "origin_country": ["US"],
                    "original_language": "en",
                    "runtime": 120,
                    "genres": [],
                    "popularity": 1.0,
                    "status": "Released",
                    "vote_average": 4.2,
                    "vote_count": vote_count,
                },
            )
        )
        run_id = rabbit_run(client)
        put_at_depth(world, run_id, 20)
        result = validate(client, run_id, film_id)
    assert result["valid"] is True


def test_tier_five_tmdb_score_requires_ten_votes_and_imdb_still_takes_precedence(client, world):
    with Session(world) as session:
        add_film(session, 70, vote=6.0, vote_count=10)
        add_film(session, 71, vote=4.2, vote_count=2, imdb="7.5")
        session.commit()
    run_id = rabbit_run(client)
    put_at_depth(world, run_id, 20)
    assert validate(client, run_id, 70)["valid"] is False
    with respx.mock:
        respx.get(f"{TMDB_BASE}/movie/71").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 71,
                    "title": "Film 71",
                    "release_date": "2015-06-01",
                    "poster_path": None,
                    "overview": "x",
                    "origin_country": ["US"],
                    "original_language": "en",
                    "runtime": 120,
                    "genres": [],
                    "popularity": 1.0,
                    "status": "Released",
                    "vote_average": 4.2,
                    "vote_count": 2,
                },
            )
        )
        assert validate(client, run_id, 71)["valid"] is False


def test_tmdb_user_score_is_cached_with_the_movie(client, db_engine):
    with respx.mock:
        respx.get(f"{TMDB_BASE}/movie/50").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 50,
                    "title": "Dud",
                    "release_date": "2015-06-01",
                    "poster_path": None,
                    "overview": "x",
                    "origin_country": ["US"],
                    "original_language": "en",
                    "runtime": 90,
                    "genres": [],
                    "popularity": 1.0,
                    "status": "Released",
                    "vote_average": 4.2,
                    "vote_count": 13,
                },
            )
        )
        assert client.get("/api/movies/50").status_code == 200
    with Session(db_engine) as session:
        assert session.get(CachedMovie, 50).vote_average == 4.2
        assert session.get(CachedMovie, 50).vote_count == 13


# --- procedural v2 ---


def procedural_run(client, **rules):
    return create_run(client, "rabbit_hole", **rules)


@pytest.fixture()
def procedural_world(world):
    with Session(world) as session:
        for index in range(40):
            add_film(
                session,
                1000 + index,
                year=1960 + index,
                lang="fr" if index % 2 else "en",
                runtime=75 + index * 3,
                vote=4 + index % 5,
            )
        session.commit()
    return world


def test_empty_cache_never_deals_unproven_rules(client):
    response = client.post("/api/runs", json={"name": "Empty", "game_type": "rabbit_hole"})
    assert response.status_code == 422
    assert "cached movie evidence" in response.json()["detail"]


def test_seed_hydrates_evidence_before_procedural_preparation(client, db_engine):
    with respx.mock:
        respx.get(f"{TMDB_BASE}/movie/90").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 90,
                    "title": "Cold-cache seed",
                    "release_date": "1990-01-01",
                    "status": "Released",
                    "runtime": 120,
                    "original_language": "fr",
                    "origin_country": ["FR"],
                    "overview": "Seed evidence",
                    "genres": [],
                    "popularity": 10,
                    "vote_average": 5,
                    "vote_count": 100,
                },
            )
        )
        respx.get(f"{TMDB_BASE}/movie/90/credits").mock(
            return_value=httpx.Response(200, json={"id": 90, "cast": [], "crew": []})
        )
        response = client.post(
            "/api/runs",
            json={
                "name": "Seed evidence",
                "game_type": "rabbit_hole",
                "seed_movie_id": 90,
            },
        )
    assert response.status_code == 422, response.text
    assert "pass-rate band" in response.json()["detail"]
    with Session(db_engine) as session:
        assert session.get(CachedMovie, 90).runtime == 120


def test_new_runs_deal_versioned_feasible_decks(client, procedural_world, monkeypatch):
    monkeypatch.setattr(rabbit_hole.secrets, "randbits", lambda bits: 12345)
    first = run_detail(client, procedural_run(client))["rules_config"]
    second = run_detail(client, procedural_run(client))["rules_config"]
    assert first["rh_rules_version"] == 3 and first["rh_seed"] == 12345
    assert first["tier_deck"] == second["tier_deck"]
    assert first["tier_deck"][0]["name"] == "Freefall"
    assert 5 <= len(first["tier_deck"]) <= 7
    assert [entry["start_depth"] for entry in first["tier_deck"]] == list(
        range(0, len(first["tier_deck"]) * 5, 5)
    )
    difficulties = [entry["difficulty"] for entry in first["tier_deck"][1:]]
    assert difficulties == sorted(difficulties)
    with Session(procedural_world) as session:
        movies = feasibility.movies(session)
        from app.services.movie_filters import is_reality_eligible

        eligible = [movie_id for movie_id, row in movies.items() if is_reality_eligible(row)]
        leaf_counts = []

        def leaves(query):
            return 1 if query.facet else sum(leaves(child) for child in query.children())

        for entry, (low, high) in zip(
            first["tier_deck"][1:], rabbit_hole.TIER_PASS_BANDS, strict=True
        ):
            test = rabbit_hole.test_from_data(entry)
            assert low <= feasibility.pass_rate(session, test, movies) <= high
            assert low <= feasibility.pass_rate(session, test, eligible) <= high
            leaf_counts.append(leaves(test.query))
        assert all(1 <= count <= 3 for count in leaf_counts)
        assert any(count > 1 for count in leaf_counts)
        assert len({str(rabbit_hole.draw_deck(session, seed)) for seed in range(20)}) > 10


def test_procedural_deck_respects_mode_runtime_bounds(client, procedural_world):
    rules = run_detail(client, procedural_run(client, min_runtime=150, curses=True))["rules_config"]
    with Session(procedural_world) as session:
        ids = [
            movie_id for movie_id, row in feasibility.movies(session).items() if row.runtime >= 150
        ]
        for tier in rabbit_hole.tiers_of(rules)[1:]:
            assert feasibility.pass_rate(session, rabbit_hole.tier_tests(tier), ids) >= 0.01
            assert not feasibility.contradicts(
                rabbit_hole.tier_tests(tier), {"runtime": (150, None)}
            )


def test_exact_three_percent_draw_and_one_percent_curse_thresholds(db_engine, monkeypatch):
    tests = [
        predicate("year_lt", value=2000),
        predicate("runtime_lt", value=100),
        predicate("non_english"),
        predicate("rating_lt", value=6),
    ]
    monkeypatch.setattr(rabbit_hole, "tier_options", lambda: tests)
    with Session(db_engine) as session:
        for index in range(100):
            session.add(
                CachedMovie(
                    tmdb_id=index + 1,
                    title=str(index),
                    release_date="1990-01-01" if index < 3 else "2020-01-01",
                    runtime=80 if index in (0, 3, 4) else 120,
                    original_language="fr" if index in (0, 5, 6) else "en",
                    vote_average=5 if index in (0, 7, 8) else 8,
                    vote_count=100,
                    popularity=10,
                )
            )
        session.commit()
        for test in tests:
            assert feasibility.cache_pass_rate(session, test) == 0.03
        deck = rabbit_hole.draw_deck(session, 1, curses=True)
        assert len(deck) == 5
        assert deck[3]["curses"] and deck[4]["curses"]
        for entry in deck[3:]:
            combined = rabbit_hole.TierPredicates(
                (
                    rabbit_hole.test_from_data(entry),
                    *(rabbit_hole.test_from_data(curse) for curse in entry["curses"]),
                )
            )
            assert feasibility.cache_pass_rate(session, combined) == 0.01
        row = session.get(CachedMovie, 1)
        row.runtime = 120
        session.add(row)
        session.commit()
        feasibility.invalidate(session)
        from app.facets.store import invalidate

        invalidate(session, [1], ["production"])
        with pytest.raises(rabbit_hole.RunSetupError, match="four feasible"):
            rabbit_hole.draw_deck(session, 1)


def test_curses_stack_only_when_combined_rules_are_feasible(procedural_world):
    with Session(procedural_world) as session:
        stacked = False
        dropped = False
        for seed in range(40):
            deck = rabbit_hole.draw_deck(session, seed, curses=True)
            for entry in deck[1:]:
                assert not entry["curses"] if entry["number"] < 4 else True
                combined = rabbit_hole.TierPredicates(
                    (
                        rabbit_hole.test_from_data(entry),
                        *(rabbit_hole.test_from_data(curse) for curse in entry["curses"]),
                    )
                )
                assert feasibility.cache_pass_rate(session, combined) >= 0.01
                stacked |= len(entry["curses"]) > 1
                dropped |= entry["number"] >= 4 and not entry["curses"]
        assert stacked and dropped


@pytest.mark.parametrize("kind", ["life", "reroll", "skip_curse"])
def test_relic_boundary_award_and_delete_restore_exact_resources(client, procedural_world, kind):
    seed = next(
        seed
        for seed in range(100)
        if random.Random(f"{seed}:relic:5").choice(["life", "reroll", "skip_curse"]) == kind
    )
    run_id = procedural_run(client, curses=True)
    update_rules(procedural_world, run_id, rh_seed=seed, lives_remaining=2)
    put_at_depth(procedural_world, run_id, 4)
    before = run_detail(client, run_id)["rules_config"]
    response = log(
        client,
        run_id,
        1001,
        transition_metadata={
            "relic_awarded": {"kind": "life", "amount": 99},
            "rh_resources_before": {"lives_remaining": 99},
            "life_lost": True,
        },
    )
    assert response.status_code == 201, response.text
    step = response.json()
    assert step["transition_metadata"]["relic_awarded"] == {"kind": kind, "amount": 1, "depth": 5}
    after = run_detail(client, run_id)["rules_config"]
    key = {"life": "lives_remaining", "reroll": "reroll_tokens", "skip_curse": "relics"}[kind]
    assert after[key] != before[key]
    assert client.delete(f"/api/runs/{run_id}/steps/{step['id']}").status_code == 204
    restored = run_detail(client, run_id)["rules_config"]
    assert {k: restored[k] for k in ("lives_remaining", "relics", "reroll_tokens")} == {
        k: before[k] for k in ("lives_remaining", "relics", "reroll_tokens")
    }
    again = log(client, run_id, 1001).json()
    assert (
        again["transition_metadata"]["relic_awarded"]
        == step["transition_metadata"]["relic_awarded"]
    )


def test_capped_life_relic_and_forced_step_undo(client, procedural_world):
    seed = next(
        seed
        for seed in range(100)
        if random.Random(f"{seed}:relic:5").choice(["life", "reroll"]) == "life"
    )
    run_id = procedural_run(client)
    update_rules(procedural_world, run_id, rh_seed=seed)
    put_at_depth(procedural_world, run_id, 4)
    step = log(client, run_id, 1001).json()
    assert step["transition_metadata"]["relic_awarded"]["amount"] == 0
    assert lives(client, run_id) == 3
    assert client.delete(f"/api/runs/{run_id}/steps/{step['id']}").status_code == 204
    with Session(procedural_world) as session:
        add_film(session, 2000, actor=LONER_ACTOR)
        session.commit()
    step = log(client, run_id, 2000, force=True).json()
    assert step["transition_metadata"]["life_lost"] is True
    assert step["transition_metadata"]["relic_awarded"]["amount"] == 1
    assert lives(client, run_id) == 3
    assert client.delete(f"/api/runs/{run_id}/steps/{step['id']}").status_code == 204
    assert lives(client, run_id) == 3


def test_ironman_never_awards_an_unspendable_reroll(client, procedural_world):
    run_id = procedural_run(client, allow_reroll=False, curses=True)
    for depth in (4, 9, 14):
        # Direct history fixtures do not themselves earn rewards.
        with Session(procedural_world) as session:
            for step in session.exec(select(RunStep).where(RunStep.run_id == run_id)).all():
                session.delete(step)
            session.commit()
        put_at_depth(procedural_world, run_id, depth)
        update_rules(procedural_world, run_id, lives_remaining=3)
        step = log(client, run_id, 1001, force=True).json()
        assert step["transition_metadata"]["relic_awarded"]["kind"] in {"life", "skip_curse"}
        assert run_detail(client, run_id)["rules_config"]["reroll_tokens"] == 0


def test_daily_seed_is_utc_stable_and_creation_only(client, procedural_world, monkeypatch):
    monkeypatch.setattr(rabbit_hole, "utcnow", lambda: datetime(2026, 10, 6, 23, 59, tzinfo=UTC))
    first = run_detail(client, procedural_run(client, daily=True))["rules_config"]
    second_id = procedural_run(client, daily=True)
    second = run_detail(client, second_id)["rules_config"]
    assert first["rh_seed"] == second["rh_seed"] == rabbit_hole.daily_seed(date(2026, 10, 6))
    assert first["tier_deck"] == second["tier_deck"]
    assert first["rh_seed"] != rabbit_hole.daily_seed(date(2026, 10, 7))
    assert client.patch(f"/api/runs/{second_id}/rules", json={"daily": False}).status_code == 422
    assert client.patch(f"/api/runs/{second_id}/rules", json={"curses": True}).status_code == 422


def test_clients_cannot_forge_or_patch_procedural_state(client, procedural_world):
    fake = {
        "rh_seed": 1,
        "rh_rules_version": 1,
        "tier_deck": [{"number": 99}],
        "relics": {"skip_curse": 999},
        "reroll_tokens": 999,
        "curse_skip": 0,
    }
    run_id = procedural_run(client, **fake)
    before = run_detail(client, run_id)["rules_config"]
    assert before["rh_rules_version"] == 3 and before["tier_deck"][0]["number"] == 1
    assert before["relics"] == {"skip_curse": 0} and before["reroll_tokens"] == 0
    assert before["rh_seed"] != 1 and "curse_skip" not in before
    response = client.patch(f"/api/runs/{run_id}/rules", json=fake)
    assert response.status_code == 200
    assert response.json()["rules_config"] == before


def test_seeded_reroll_prefers_tokens_and_is_reversible_with_the_hop(
    client, procedural_world, monkeypatch
):
    monkeypatch.setattr(rabbit_hole.secrets, "randbits", lambda bits: 987)
    runs = [procedural_run(client) for _ in range(2)]
    for run_id in runs:
        put_at_depth(procedural_world, run_id, 5)
        update_rules(procedural_world, run_id, lives_remaining=1, reroll_tokens=1)
    results = [client.post(f"/api/runs/{run_id}/rabbit-hole/reroll") for run_id in runs]
    assert all(response.status_code == 200 for response in results), [
        response.text for response in results
    ]
    rules = results[0].json()["rules_config"]
    assert rules["tier_override"] == results[1].json()["rules_config"]["tier_override"]
    assert rules["lives_remaining"] == 1 and rules["reroll_tokens"] == 0
    tier = tier_for_depth(5, rules)
    with Session(procedural_world) as session:
        valid_id = next(
            movie_id
            for movie_id, row in feasibility.movies(session).items()
            if movie_id != ANCHOR and rabbit_hole.compliance(session, tier, row) is True
        )
    step = log(client, runs[0], valid_id).json()
    assert "tier_override" not in run_detail(client, runs[0])["rules_config"]
    assert client.delete(f"/api/runs/{runs[0]}/steps/{step['id']}").status_code == 204
    restored = run_detail(client, runs[0])["rules_config"]
    assert restored["tier_override"] == rules["tier_override"]
    assert restored["reroll_tokens"] == 0
    assert client.post(f"/api/runs/{runs[0]}/rabbit-hole/reroll").status_code == 409


def test_empty_frontier_reroll_spends_nothing(client, procedural_world, monkeypatch):
    run_id = procedural_run(client)
    put_at_depth(procedural_world, run_id, 5)
    update_rules(procedural_world, run_id, reroll_tokens=1)

    async def no_candidates(self, *args, **kwargs):
        return []

    monkeypatch.setattr(RabbitHoleEngine, "discover_with_modifiers", no_candidates)
    before = deepcopy(run_detail(client, run_id)["rules_config"])
    response = client.post(f"/api/runs/{run_id}/rabbit-hole/reroll")
    assert response.status_code == 409
    assert "no resource was spent" in response.json()["detail"]
    assert run_detail(client, run_id)["rules_config"] == before


@pytest.mark.parametrize("version", [2, 3])
def test_reroll_keeps_curses_and_excludes_a_globally_feasible_frontier_dead_end(
    client,
    procedural_world,
    monkeypatch,
    version,
):
    run_id = procedural_run(client, curses=True)
    rules = run_detail(client, run_id)["rules_config"]
    rules["tier_deck"][3] = {
        **rabbit_hole.predicate_data(predicate("runtime_lt", value=100)),
        "number": 4,
        "start_depth": 15,
        "curses": [rabbit_hole.predicate_data(predicate("non_english"))],
    }
    update_rules(
        procedural_world,
        run_id,
        tier_deck=rules["tier_deck"],
        rh_seed=222,
        rh_rules_version=version,
    )
    put_at_depth(procedural_world, run_id, 15)

    # The lone reachable film is French, 78 minutes and from 1961.
    async def reachable(self, *args, **kwargs):
        from app.schemas.discovery import DiscoveryCandidate

        return [DiscoveryCandidate(movie_id=1001, title="Film 1001")]

    monkeypatch.setattr(RabbitHoleEngine, "discover_with_modifiers", reachable)
    monkeypatch.setattr(
        rabbit_hole,
        "tier_options",
        lambda: [
            predicate("runtime_ge", value=150),  # Many cached films pass, but not this frontier.
            predicate("year_lt", value=1980),
        ],
    )
    response = client.post(f"/api/runs/{run_id}/rabbit-hole/reroll")
    if version == 3:
        assert response.status_code == 409
        assert "no resource was spent" in response.json()["detail"]
        assert "tier_override" not in run_detail(client, run_id)["rules_config"]
        return
    assert response.status_code == 200, response.text
    override = response.json()["rules_config"]["tier_override"]
    assert override["predicate"]["predicate_id"] == "year_lt"
    tier = tier_for_depth(15, response.json()["rules_config"])
    assert [test.id for test in tier.curses] == ["non_english"]


@pytest.mark.parametrize("depth", [5, 10, 15, 20, 25, 30])
def test_v2_pool_and_validation_use_the_dealt_predicates(client, procedural_world, depth):
    run_id = procedural_run(client, curses=True)
    put_at_depth(procedural_world, run_id, depth)
    rules = run_detail(client, run_id)["rules_config"]
    tier = tier_for_depth(depth, rules)
    pool = discover(client, run_id)
    with Session(procedural_world) as session:
        expected = {
            movie_id
            for movie_id, row in feasibility.movies(session).items()
            if movie_id != ANCHOR
            and is_reality_eligible(row)
            and rabbit_hole.compliance(session, tier, row) is True
        }
        assert set(pool) == expected
        for film_id in (1000, 1001, 1034):
            assert validate(client, run_id, film_id)["valid"] is (
                rabbit_hole.compliance(session, tier, session.get(CachedMovie, film_id)) is True
            )
    state = client.get(f"/api/runs/{run_id}/constraint").json()["rabbit_hole"]
    assert state["tier_name"] == tier.name
    assert state["curses"] == [rabbit_hole.predicate_data(test) for test in tier.curses]


def test_skip_curse_is_explicit_one_hop_and_keeps_other_rules(client, procedural_world):
    run_id = procedural_run(client, curses=True)
    rules = run_detail(client, run_id)["rules_config"]
    # Two compatible constraints, but this film breaks only the newest curse.
    rules["tier_deck"][3] = {
        **rabbit_hole.predicate_data(predicate("runtime_lt", value=100)),
        "number": 4,
        "start_depth": 15,
        "curses": [rabbit_hole.predicate_data(predicate("non_english"))],
    }
    update_rules(procedural_world, run_id, tier_deck=rules["tier_deck"], relics={"skip_curse": 1})
    put_at_depth(procedural_world, run_id, 15)
    assert validate(client, run_id, 1000)["valid"] is False
    assert client.post(f"/api/runs/{run_id}/rabbit-hole/skip-curse").status_code == 200
    after = run_detail(client, run_id)["rules_config"]
    assert after["relics"] == {"skip_curse": 0} and after["curse_skip"] == 15
    assert client.post(f"/api/runs/{run_id}/rabbit-hole/skip-curse").status_code == 409
    assert validate(client, run_id, 1000)["valid"] is True
    assert (
        validate(client, run_id, 1010)["valid"] is False
    )  # The primary runtime rule still applies.
    step = log(client, run_id, 1000).json()
    assert "curse_skip" not in run_detail(client, run_id)["rules_config"]
    assert client.delete(f"/api/runs/{run_id}/steps/{step['id']}").status_code == 204
    assert run_detail(client, run_id)["rules_config"]["curse_skip"] == 15
    assert tier_state(16, run_detail(client, run_id)["rules_config"]).curses


def test_deleting_boundary_after_spending_its_reward_cannot_keep_the_token(
    client, procedural_world
):
    seed = next(
        seed
        for seed in range(100)
        if random.Random(f"{seed}:relic:5").choice(["life", "reroll"]) == "reroll"
    )
    run_id = procedural_run(client)
    update_rules(procedural_world, run_id, rh_seed=seed)
    put_at_depth(procedural_world, run_id, 4)
    step = log(client, run_id, 1001).json()
    assert client.post(f"/api/runs/{run_id}/rabbit-hole/reroll").status_code == 200
    assert client.delete(f"/api/runs/{run_id}/steps/{step['id']}").status_code == 204
    rules = run_detail(client, run_id)["rules_config"]
    assert rules["reroll_tokens"] == 0 and "tier_override" not in rules
    assert rules["lives_remaining"] == 3


# --- Phase F0: the tier verdict is the last pass ---


def _engine_pool(session, run_id, rules):
    history = session.exec(select(RunStep).where(RunStep.run_id == run_id)).all()

    async def run():
        async with httpx.AsyncClient() as http:
            engine = RabbitHoleEngine(session, TMDBClient(http))
            pool = await engine.discover_candidates(ANCHOR, rules=rules, history=history)
            return engine, pool

    engine, pool = asyncio.run(run())
    return engine, history, pool


def test_the_tier_verdict_is_stamped_after_the_pool_is_final(client, world):
    """A film whose runtime only arrives in a later hydration pass is judged on that runtime."""
    add_candidates(world)
    run_id = rabbit_run(client)
    put_at_depth(world, run_id, 15)  # Micro-Clock: under 100 minutes
    with Session(world) as session:
        session.get(CachedMovie, 14).runtime = None
        session.commit()
        rules = session.get(Run, run_id).rules_config
        engine, history, pool = _engine_pool(session, run_id, rules)
        candidate = next(c for c in pool if c.movie_id == 14)
        assert candidate.tier_compliant is None  # discovery keeps it, but judges nothing

        session.get(CachedMovie, 14).runtime = 90  # a later pass hydrates the film
        from app.facets.store import invalidate

        invalidate(session, [14], ["production"])
        session.commit()
        engine.annotate_candidates(pool, rules, history)

    assert candidate.tier_compliant is True
    assert candidate.constraint_unverified is False


def test_a_film_hydrated_by_pool_shaping_is_never_both_timed_and_unverified(client, world):
    """The chaser pass fetches missing runtimes; the card must not say "rule unverified" after."""
    add_candidates(world)
    run_id = rabbit_run(client)
    put_at_depth(world, run_id, 15)
    with Session(world) as session:
        film = session.get(CachedMovie, 14)
        film.runtime = None
        film.genre_ids = None
        session.commit()

    with respx.mock:
        respx.get(f"{TMDB_BASE}/movie/14").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 14,
                    "title": "Short",
                    "release_date": "2015-06-01",
                    "runtime": 90,
                    "original_language": "en",
                    "vote_average": 7.0,
                    "vote_count": 100,
                    "genres": [{"id": 35, "name": "Comedy"}],
                    "origin_country": ["US"],
                    "status": "Released",
                },
            )
        )
        respx.route(host="api.themoviedb.org").mock(return_value=httpx.Response(200, json={}))
        resp = client.get(
            f"/api/runs/{run_id}/discover",
            params={"frontier_movie_id": ANCHOR, "chaser": True},
        )
    assert resp.status_code == 200, resp.text
    card = next(c for c in resp.json() if c["movie_id"] == 14)
    assert card["runtime"] == 90
    assert card["constraint_unverified"] is False
    assert card["tier_compliant"] is True
