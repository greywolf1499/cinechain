"""Phase 26b: The Rabbit Hole - escalating tiers, a 3-life survival budget and tier-filtered Pick Next."""

from datetime import timedelta

import httpx
import pytest
import respx
from sqlmodel import Session

from app.engines.rabbit_hole import RabbitHoleEngine, tier_for_depth, tier_state
from app.models.cache import CachedActor, CachedMovie, CachedMovieCast, CachedMovieRating
from app.models.run import RunStep
from app.utils.ids import utcnow
from tests.test_graph_mutators import TMDB_BASE, client, create_run, db_engine, log

__all__ = ["client", "db_engine"]

ACTOR = 100
LONER_ACTOR = 200
ANCHOR = 1  # a film every run can already contain: modern, English, 120 min, well rated


def add_film(
    session, film_id, *, year=1990, lang="en", runtime=120, vote=7.0, imdb=None, actor=ACTOR,
    title=None,
):
    session.add(CachedMovie(
        tmdb_id=film_id, title=title or f"Film {film_id}", release_date=f"{year}-06-01",
        status="Released", original_language=lang, runtime=runtime, vote_average=vote,
        overview="x", popularity=10.0, origin_country='["US"]',
        cast_fetched_at=utcnow(), directors_fetched_at=utcnow()))
    session.flush()
    session.add(CachedMovieCast(movie_id=film_id, actor_id=actor, cast_order=0, character_name="x"))
    if imdb is not None:
        session.add(CachedMovieRating(movie_id=film_id, imdb_rating=imdb))


@pytest.fixture()
def world(db_engine):
    with Session(db_engine) as session:
        for actor in (ACTOR, LONER_ACTOR):
            session.add(CachedActor(
                tmdb_id=actor, name=f"Actor {actor}", credits_fetched_at=utcnow()))
        add_film(session, ANCHOR, year=2010, runtime=120)
        session.commit()
    return db_engine


def put_at_depth(db_engine, run_id, depth):
    """Inserts `depth` steps, the last one being the anchor film (every film links to it)."""
    base = utcnow()
    with Session(db_engine) as session:
        for index in range(depth):
            session.add(RunStep(
                run_id=run_id, movie_id=ANCHOR, movie_title="Film 1", status="watched",
                logged_at=base + timedelta(seconds=index)))
        session.commit()


def rabbit_run(client, **rules):
    return create_run(client, "rabbit_hole", **rules)


def run_detail(client, run_id):
    return client.get(f"/api/runs/{run_id}").json()


def lives(client, run_id):
    return run_detail(client, run_id)["rules_config"]["lives_remaining"]


def validate(client, run_id, film_id):
    resp = client.post(f"/api/runs/{run_id}/validate", json={"movie_id": film_id})
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- the state machine ---


@pytest.mark.parametrize("depth,tier", [
    (0, 1), (4, 1), (5, 2), (9, 2), (10, 3), (14, 3), (15, 4), (19, 4), (20, 5), (99, 5)])
def test_the_tier_follows_the_depth(depth, tier):
    assert tier_for_depth(depth).number == tier


def test_tier_names_and_rules():
    assert [t.name for t in (tier_for_depth(d) for d in (0, 5, 10, 15, 20))] == [
        "Freefall", "The Retro Lock", "Tower of Babel", "The Micro-Clock", "The B-Movie Abyss"]


@pytest.mark.parametrize("depth,away,warned", [
    (0, 5, False), (2, 3, False), (3, 2, True), (4, 1, True), (5, 5, False),
    (13, 2, True), (14, 1, True), (19, 1, True), (20, None, False), (25, None, False)])
def test_the_next_tier_is_announced_one_or_two_hops_ahead(depth, away, warned):
    state = tier_state(depth, None)
    assert state.steps_until_next == away
    assert (state.upcoming_tier_warning is not None) is warned


def test_the_warning_text():
    assert tier_state(14, None).upcoming_tier_warning == (
        "⚠️ Warning: Tier 4 (Under 100 mins) begins on the next hop!")
    assert tier_state(13, None).upcoming_tier_warning == (
        "⚠️ Warning: Tier 4 (Under 100 mins) begins in 2 hops!")


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
    bad = client.post("/api/runs", json={
        "name": "x", "game_type": "rabbit_hole", "rules_config": {"max_lives": 0}})
    assert bad.status_code == 422


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


@pytest.mark.parametrize("depth,film,ok", [
    (0, 11, True), (4, 13, True),  # Freefall: anything linked
    (5, 10, True), (5, 11, False), (5, 12, False),  # Retro Lock: before 2000
    (10, 12, True), (10, 13, False),  # Tower of Babel: not English
    (15, 14, True), (15, 15, False), (15, 13, False),  # Micro-Clock: under 100
    (20, 16, True), (20, 17, False), (20, 18, True), (20, 19, False),  # B-Movie Abyss
])
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
        respx.get(f"{TMDB_BASE}/movie/30").mock(return_value=httpx.Response(200, json={
            "id": 30, "title": "Film 30", "release_date": "2015-06-01", "poster_path": None,
            "overview": "x", "origin_country": ["US"], "original_language": "", "runtime": 120,
            "genres": [], "popularity": 1.0, "status": "Released", "vote_average": 7.0}))
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
    assert rules["lives_remaining"] == 2 and rules["wildcards_budget"] == 2  # a life, not a wildcard

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
        for c in pool.values())

    run_id = rabbit_run(client)
    put_at_depth(world, run_id, 11)
    assert all(c["upcoming_tier_warning"] is None for c in discover(client, run_id).values())


def test_the_constraint_endpoint_describes_the_tier(client, world):
    run_id = rabbit_run(client)
    put_at_depth(world, run_id, 14)
    info = client.get(f"/api/runs/{run_id}/constraint").json()
    assert info["title"] == "Tier 3: Tower of Babel"
    state = info["rabbit_hole"]
    assert (state["depth"], state["tier"], state["lives_remaining"], state["max_lives"]) == (14, 3, 3, 3)
    assert state["next_tier"] == 4 and state["steps_until_next"] == 1
    assert "begins on the next hop" in state["upcoming_tier_warning"]


def test_tmdb_user_score_is_cached_with_the_movie(client, db_engine):
    with respx.mock:
        respx.get(f"{TMDB_BASE}/movie/50").mock(return_value=httpx.Response(200, json={
            "id": 50, "title": "Dud", "release_date": "2015-06-01", "poster_path": None,
            "overview": "x", "origin_country": ["US"], "original_language": "en", "runtime": 90,
            "genres": [], "popularity": 1.0, "status": "Released", "vote_average": 4.2}))
        assert client.get("/api/movies/50").status_code == 200
    with Session(db_engine) as session:
        assert session.get(CachedMovie, 50).vote_average == 4.2
