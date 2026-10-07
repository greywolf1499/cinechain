"""Phase 27c: the Chaos Button, The Chaser (palate cleansers) and the Underdog B-Side flip."""

import random

import httpx
import pytest
import respx
from sqlmodel import Session

from app.engines import chaos
from app.models.cache import CachedMovie, CachedMovieRating
from app.services import pool_options
from tests.test_graph_mutators import TMDB_BASE, client, db_engine

__all__ = ["client", "db_engine"]

COMEDY, ANIMATION, DRAMA = 35, 16, 18
ACTOR = 100


def film(title, year=1990, runtime=100, language="en", popularity=50.0, genres=(), vote=7.0):
    return {
        "title": title,
        "year": year,
        "runtime": runtime,
        "language": language,
        "popularity": popularity,
        "genres": list(genres),
        "vote": vote,
    }


def mock_universe(universe):
    """Every film shares ACTOR, so any film can follow any other."""
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
                    "origin_country": ["US"],
                    "original_language": m["language"],
                    "runtime": m["runtime"],
                    "genres": [{"id": g, "name": str(g)} for g in m["genres"]],
                    "popularity": m["popularity"],
                    "vote_average": m["vote"],
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
                            "id": ACTOR,
                            "name": "Everyman",
                            "profile_path": None,
                            "character": "X",
                            "order": 0,
                        }
                    ],
                    "crew": [],
                },
            )
        )
    respx.get(f"{TMDB_BASE}/person/{ACTOR}/movie_credits").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": ACTOR,
                "crew": [],
                "cast": [
                    {
                        "id": i,
                        "title": m["title"],
                        "release_date": f"{m['year']}-06-01",
                        "poster_path": None,
                        "genre_ids": m["genres"],
                        "original_language": m["language"],
                        "popularity": m["popularity"],
                        "vote_average": m["vote"],
                        "character": "X",
                    }
                    for i, m in universe.items()
                ],
            },
        )
    )


def make_run(client, **rules):
    resp = client.post(
        "/api/runs",
        json={
            "name": "Run",
            "game_type": "cinechain",
            "rules_config": {
                "allow_repeats": "strict",
                "no_consecutive_actor": False,
                "min_runtime": 0,
                "wildcards_budget": 2,
                **rules,
            },
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def log(client, run_id, movie_id, **extra):
    return client.post(f"/api/runs/{run_id}/steps", json={"movie_id": movie_id, **extra})


def rules_of(client, run_id):
    return client.get(f"/api/runs/{run_id}").json()["rules_config"]


def force_chaos(monkeypatch, handicap_id):
    handicap = chaos.HANDICAPS[handicap_id]
    monkeypatch.setattr(
        chaos, "roll", lambda rng=None, feasible=None: {"id": handicap.id, "label": handicap.label}
    )


def test_modern_only_can_be_dealt_and_enforced(client, monkeypatch):
    original_roll = chaos.roll

    def deal_modern(rng=None, feasible=None):
        assert "modern_only" in feasible
        for seed in range(200):
            dealt = original_roll(random.Random(seed), feasible)
            if dealt["id"] == "modern_only":
                return dealt
        pytest.fail("Modern only was never selected from the measured eligible pool")

    monkeypatch.setattr(chaos, "roll", deal_modern)
    run_id = make_run(client)
    with respx.mock:
        mock_universe(
            {1: film("Anchor"), 2: film("Classic", year=1955), 3: film("Modern", year=1995)}
        )
        assert log(client, run_id, 1).status_code == 201
        response = client.post(f"/api/runs/{run_id}/chaos")
        assert response.status_code == 200, response.text
        assert response.json()["rules_config"]["active_chaos"]["label"] == "Modern only"
        assert log(client, run_id, 2).status_code == 409
        assert log(client, run_id, 3).status_code == 201


# --- the handicap rules ---


@pytest.mark.parametrize(
    ("handicap", "good", "bad"),
    [
        ("pre_1970", {"release_date": "1969-12-31"}, {"release_date": "1970-01-01"}),
        ("epic_length", {"runtime": 151}, {"runtime": 150}),
        ("short_flick", {"runtime": 89}, {"runtime": 90}),
        ("foreign_tongue", {"original_language": "fr"}, {"original_language": "en"}),
        ("b_movie", {"vote_average": 5.9}, {"vote_average": 6.0}),
        ("modern_only", {"release_date": "1970-01-01"}, {"release_date": "1969-12-31"}),
        ("english_only", {"original_language": "en"}, {"original_language": "fr"}),
        ("crowd_pleaser", {"vote_average": 6.0}, {"vote_average": 5.9}),
        ("one_word_titles", {"title": "Alien"}, {"title": "Two Words"}),
    ],
)
def test_each_handicap_checks_its_criterion(db_engine, handicap, good, bad):
    rules = {"active_chaos": {"id": handicap, "label": "x"}}
    base = {
        "tmdb_id": 1,
        "title": "T",
        "release_date": "1990-01-01",
        "runtime": 100,
        "original_language": "en",
        "vote_average": 7.0,
        "vote_count": 100,
    }
    with Session(db_engine) as session:
        assert chaos.violation(session, CachedMovie(**{**base, **good}), rules) is None
        assert chaos.violation(session, CachedMovie(**{**base, **bad}), rules) is not None


def test_unknown_data_never_blocks_and_imdb_beats_the_tmdb_score(db_engine):
    rules = {"active_chaos": {"id": "short_flick", "label": "x"}}
    with Session(db_engine) as session:
        assert (
            chaos.violation(session, CachedMovie(tmdb_id=1, title="T", runtime=None), rules) is None
        )
        assert chaos.violation(session, CachedMovie(tmdb_id=1, title="T", runtime=0), rules) is None
        assert chaos.violation(session, CachedMovie(tmdb_id=1, title="T"), {}) is None
        session.add(CachedMovie(tmdb_id=2, title="Rated", vote_average=8.0))
        session.commit()
        session.add(CachedMovieRating(movie_id=2, imdb_rating="4.5"))
        session.commit()
        b_movie = {"active_chaos": {"id": "b_movie", "label": "x"}}
        assert (
            chaos.violation(session, session.get(CachedMovie, 2), b_movie) is None
        )  # IMDb 4.5 beats TMDB 8.0
    assert set(chaos.HANDICAPS) == {
        "pre_1970",
        "b_movie",
        "epic_length",
        "short_flick",
        "foreign_tongue",
        "modern_only",
        "crowd_pleaser",
        "english_only",
        "one_word_titles",
        "cult_classics",
    }


# --- the Chaos Button ---


def test_rolling_stores_one_handicap_and_it_cannot_be_rerolled(client, db_engine):
    with Session(db_engine) as session:
        session.add(
            CachedMovie(
                tmdb_id=1,
                title="Old",
                release_date="1950-01-01",
                runtime=70,
                original_language="fr",
                vote_average=5,
                vote_count=100,
            )
        )
        session.add(
            CachedMovie(
                tmdb_id=2,
                title="New",
                release_date="2000-01-01",
                runtime=160,
                original_language="en",
                vote_average=7,
                vote_count=100,
            )
        )
        session.commit()
    run_id = make_run(client)
    rolled = client.post(f"/api/runs/{run_id}/chaos")
    assert rolled.status_code == 200, rolled.text
    active = rolled.json()["rules_config"]["active_chaos"]
    assert (
        active["id"] in chaos.HANDICAPS and active["label"] == chaos.HANDICAPS[active["id"]].label
    )
    assert client.post(f"/api/runs/{run_id}/chaos").status_code == 409
    cancelled = client.delete(f"/api/runs/{run_id}/chaos")
    assert cancelled.status_code == 200 and cancelled.json()["rules_config"]["active_chaos"] is None
    assert client.post(f"/api/runs/{run_id}/chaos").status_code == 200


def test_empty_pool_cannot_roll_a_handicap(client):
    run_id = make_run(client)
    response = client.post(f"/api/runs/{run_id}/chaos")
    assert response.status_code == 409
    assert "no roll was applied" in response.json()["detail"]
    assert rules_of(client, run_id).get("active_chaos") is None


def test_chaos_needs_a_mode_with_a_pick_next_pool(client):
    run = client.post(
        "/api/runs",
        json={"name": "x", "game_type": "decade_sieve", "rules_config": {"target_decade": 1970}},
    ).json()
    assert client.post(f"/api/runs/{run['id']}/chaos").status_code == 400


def test_a_client_cannot_forge_a_handicap(client):
    forged = {"id": "pre_1970", "label": "x"}
    run_id = make_run(client, active_chaos=forged)
    assert "active_chaos" not in rules_of(client, run_id)


def test_the_handicap_blocks_the_next_film_and_expires_after_one_step(client, monkeypatch):
    universe = {
        1: film("Anchor"),
        2: film("Modern", year=1995),
        3: film("Old", year=1955),
        4: film("Also Modern", year=2001),
    }
    run_id = make_run(client)
    force_chaos(monkeypatch, "pre_1970")
    with respx.mock:
        mock_universe(universe)
        assert log(client, run_id, 1).status_code == 201
        client.post(f"/api/runs/{run_id}/chaos")
        check = client.post(f"/api/runs/{run_id}/validate", json={"movie_id": 2}).json()
        assert (
            check["valid"] is False
            and check["blocked"] is True
            and "Time Machine" in check["reason"]
        )
        for force in (False, True):  # a wildcard can't buy a handicap
            refused = log(client, run_id, 2, force=force)
            assert refused.status_code == 409 and refused.json()["detail"]["blocked"] is True
        assert rules_of(client, run_id)["active_chaos"]["id"] == "pre_1970"
        assert log(client, run_id, 3).status_code == 201  # a pre-1970 film
        assert rules_of(client, run_id)["active_chaos"] is None
        assert log(client, run_id, 4).status_code == 201  # unconstrained again


def test_the_pool_only_offers_films_that_satisfy_the_handicap(client, monkeypatch):
    universe = {
        1: film("Anchor"),
        2: film("Modern", year=1995),
        3: film("Old", year=1955),
        4: film("Older", year=1940),
    }
    run_id = make_run(client)
    force_chaos(monkeypatch, "pre_1970")
    with respx.mock:
        mock_universe(universe)
        log(client, run_id, 1)
        before = {
            c["movie_id"]
            for c in client.get(
                f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 1}
            ).json()
        }
        client.post(f"/api/runs/{run_id}/chaos")
        during = {
            c["movie_id"]
            for c in client.get(
                f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 1}
            ).json()
        }
    assert {2, 3, 4} <= before and during == {3, 4}


def test_a_handicap_fetches_the_detail_it_needs(client, monkeypatch):
    universe = {1: film("Anchor"), 2: film("Short", runtime=70), 3: film("Long", runtime=160)}
    run_id = make_run(client)
    force_chaos(monkeypatch, "epic_length")
    with respx.mock:
        mock_universe(universe)
        log(client, run_id, 1)
        client.get(f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 1})  # caches stubs
        client.post(f"/api/runs/{run_id}/chaos")
        assert log(client, run_id, 2).status_code == 409  # runtime only known from the detail fetch
        assert log(client, run_id, 3, status="planned").status_code == 201


# --- The Chaser ---


def test_the_chaser_trigger_and_qualification():
    assert pool_options.needs_chaser(135, []) and pool_options.needs_chaser(90, [DRAMA])
    assert not pool_options.needs_chaser(134, [COMEDY]) and not pool_options.needs_chaser(
        None, None
    )
    assert pool_options.is_chaser(95, [COMEDY]) and pool_options.is_chaser(60, [ANIMATION, DRAMA])
    assert not pool_options.is_chaser(96, [COMEDY]) and not pool_options.is_chaser(90, [DRAMA])
    assert not pool_options.is_chaser(None, [COMEDY]) and not pool_options.is_chaser(0, [COMEDY])


def test_chaser_mode_keeps_only_short_lighthearted_films(client):
    universe = {
        1: film("Heavy Drama", runtime=170, genres=[DRAMA]),
        2: film("Quick Comedy", runtime=90, genres=[COMEDY]),
        3: film("Short Cartoon", runtime=80, genres=[ANIMATION, 10751]),
        4: film("Long Comedy", runtime=120, genres=[COMEDY]),
        5: film("Short Drama", runtime=85, genres=[DRAMA]),
        6: film("Exactly 95", runtime=95, genres=[COMEDY]),
        7: film("Action Short", runtime=80, genres=[28]),
    }
    run_id = make_run(client)
    with respx.mock:
        mock_universe(universe)
        log(client, run_id, 1)
        plain = {
            c["movie_id"]
            for c in client.get(
                f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 1}
            ).json()
        }
        chaser = client.get(
            f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 1, "chaser": "true"}
        ).json()
    assert {2, 3, 4, 5, 6, 7} <= plain
    assert {c["movie_id"] for c in chaser} == {2, 3, 6}
    assert all(0 < c["runtime"] <= 95 for c in chaser)  # the runtime came from the detail fetch


def test_the_chaser_also_applies_to_suggestions(client):
    universe = {
        1: film("Heavy Drama", runtime=170, genres=[DRAMA]),
        2: film("Quick Comedy", runtime=90, genres=[COMEDY]),
        3: film("Long Comedy", runtime=120, genres=[COMEDY]),
    }
    run_id = make_run(client)
    with respx.mock:
        mock_universe(universe)
        log(client, run_id, 1)
        suggestions = client.get(
            f"/api/runs/{run_id}/suggestions", params={"chaser": "true"}
        ).json()
    assert [s["movie_id"] for s in suggestions] == [2]


# --- The Underdog B-Side flip ---


def test_underdog_sorts_by_ascending_popularity_and_drops_dead_entries(client):
    universe = {
        1: film("Anchor", popularity=80.0),
        2: film("Blockbuster", popularity=300.0),
        3: film("Cult Film", popularity=3.5),
        4: film("Mid", popularity=40.0),
        5: film("Barely Alive", popularity=1.0),
        6: film("Dead Entry", popularity=0.4),
    }
    run_id = make_run(client)
    with respx.mock:
        mock_universe(universe)
        log(client, run_id, 1)
        pool = client.get(
            f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 1, "sort_by": "underdog"}
        ).json()
    assert [c["movie_id"] for c in pool] == [
        5,
        3,
        4,
        2,
    ]  # 1.0 stays, 0.4 is dropped (the frontier isn't a candidate)
    assert (
        client.get(
            f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 1, "sort_by": "bogus"}
        ).status_code
        == 422
    )


def test_underdog_and_chaser_combine(client):
    universe = {
        1: film("Anchor", runtime=150, genres=[DRAMA]),
        2: film("Popular Comedy", runtime=90, genres=[COMEDY], popularity=90.0),
        3: film("Obscure Comedy", runtime=88, genres=[COMEDY], popularity=2.0),
        4: film("Obscure Drama", runtime=88, genres=[DRAMA], popularity=1.5),
    }
    run_id = make_run(client)
    with respx.mock:
        mock_universe(universe)
        log(client, run_id, 1)
        pool = client.get(
            f"/api/runs/{run_id}/discover",
            params={"frontier_movie_id": 1, "sort_by": "underdog", "chaser": "true"},
        ).json()
    assert [c["movie_id"] for c in pool] == [3, 2]


def test_search_can_flip_to_underdog_b_sides(client):
    results = [
        {"id": 1, "title": "Big", "release_date": "2010-01-01", "popularity": 200.0},
        {"id": 2, "title": "Small", "release_date": "2010-01-01", "popularity": 4.0},
        {"id": 3, "title": "Dead", "release_date": "2010-01-01", "popularity": 0.2},
        {"id": 4, "title": "Medium", "release_date": "2010-01-01", "popularity": 30.0},
    ]
    with respx.mock:
        respx.get(f"{TMDB_BASE}/search/movie").mock(
            return_value=httpx.Response(200, json={"results": results, "page": 1, "total_pages": 1})
        )
        plain = client.get("/api/movies/search", params={"q": "x"}).json()
        flipped = client.get("/api/movies/search", params={"q": "x", "sort_by": "underdog"}).json()
    assert [r["tmdb_id"] for r in plain["results"]] == [1, 2, 3, 4]  # untouched by default
    assert [r["tmdb_id"] for r in flipped["results"]] == [2, 4, 1]
    assert flipped["results"][0]["popularity"] == 4.0
