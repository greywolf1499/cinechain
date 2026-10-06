"""Phase 27b: the Bounty Board (wildcard quests) and the Rotten Tomatoes Split engine."""

import random

import httpx
import pytest
import respx
from sqlmodel import Session

from app.engines.rt_split import compute_scores
from app.models.cache import CachedMovie, CachedMovieRating
from app.models.run import Run, RunStep
from app.models.system import SystemSetting
from app.services import bounties
from app.services.bounties import BOUNTIES, MovieFacts
from tests.test_graph_mutators import TMDB_BASE, client, db_engine

__all__ = ["client", "db_engine"]


def facts(**overrides):
    base = {
        "runtime": 100,
        "year": 1999,
        "popularity": 50.0,
        "language": "en",
        "countries": ["US"],
        "director_genders": [2],
    }
    return MovieFacts(**{**base, **overrides})


# --- the bounty catalogue ---


@pytest.mark.parametrize(
    ("bounty", "yes", "no"),
    [
        ("short_king", {"runtime": 89}, {"runtime": 90}),
        ("short_king", {"runtime": 60}, {"runtime": None}),
        ("time_capsule", {"year": 1959}, {"year": 1960}),
        ("hidden_gem", {"popularity": 11.9}, {"popularity": 12.0}),
        ("hidden_gem", {"popularity": 0.5}, {"popularity": None}),
        (
            "foreign_horizon",
            {"language": "ja", "countries": ["JP"]},
            {"language": "en", "countries": ["FR"]},
        ),
        (
            "foreign_horizon",
            {"language": "fr", "countries": ["FR", "GB"]},
            {"language": "fr", "countries": ["US", "FR"]},
        ),
        ("female_gaze", {"director_genders": [2, 1]}, {"director_genders": [2, None, 0]}),
        ("epic_odyssey", {"runtime": 151}, {"runtime": 150}),
    ],
)
def test_each_bounty_checks_its_criterion(bounty, yes, no):
    assert BOUNTIES[bounty].check(facts(**yes)) is True
    assert BOUNTIES[bounty].check(facts(**no)) is False


def test_the_catalogue_holds_the_six_bounties():
    assert set(BOUNTIES) == {
        "short_king",
        "time_capsule",
        "hidden_gem",
        "foreign_horizon",
        "female_gaze",
        "epic_odyssey",
    }


def test_a_replacement_comes_from_the_uncompleted_pool():
    rng = random.Random(1)
    active = ["short_king", "time_capsule", "hidden_gem"]
    for _ in range(20):
        pick = bounties.draw_replacement(active, ["foreign_horizon"], rng)
        assert pick in {"female_gaze", "epic_odyssey"}
    # Everything done once: any bounty not on the board may come round again.
    assert bounties.draw_replacement(active, list(BOUNTIES), rng) in {
        "foreign_horizon",
        "female_gaze",
        "epic_odyssey",
    }
    assert bounties.draw_replacement(list(BOUNTIES), [], rng) is None


def test_award_and_revoke_are_inverses():
    rules = {
        "wildcards_budget": 0,
        "active_bounties": ["short_king", "time_capsule", "hidden_gem"],
        "completed_bounties": [],
    }
    won = bounties.award(rules, "short_king", "epic_odyssey")
    assert won["wildcards_budget"] == 1 and won["completed_bounties"] == ["short_king"]
    assert won["active_bounties"] == ["time_capsule", "hidden_gem", "epic_odyssey"]
    back = bounties.revoke(won, "short_king", "epic_odyssey")
    assert back == rules | {"active_bounties": ["short_king", "time_capsule", "hidden_gem"]}


# --- the Bounty Board in a run ---


def movie_json(
    movie_id, *, runtime=100, year=1999, popularity=50.0, language="en", countries=("US",)
):
    return {
        "id": movie_id,
        "title": f"Film {movie_id}",
        "release_date": f"{year}-06-01",
        "poster_path": None,
        "overview": "",
        "origin_country": list(countries),
        "original_language": language,
        "runtime": runtime,
        "genres": [],
        "popularity": popularity,
        "status": "Released",
    }


def mock_film(movie_id, *, cast=(), directors=(), **fields):
    respx.get(f"{TMDB_BASE}/movie/{movie_id}").mock(
        return_value=httpx.Response(200, json=movie_json(movie_id, **fields))
    )
    respx.get(f"{TMDB_BASE}/movie/{movie_id}/credits").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": movie_id,
                "cast": [
                    {
                        "id": a,
                        "name": f"Actor {a}",
                        "profile_path": None,
                        "character": "X",
                        "order": i,
                    }
                    for i, a in enumerate(cast)
                ],
                "crew": [
                    {"id": d, "name": f"Director {d}", "job": "Director", "gender": g}
                    for d, g in directors
                ],
            },
        )
    )


def make_run(client, game_type="cinechain", **rules):
    resp = client.post(
        "/api/runs",
        json={
            "name": "Run",
            "game_type": game_type,
            "rules_config": {
                "allow_repeats": "strict",
                "no_consecutive_actor": False,
                "min_runtime": 0,
                "wildcards_budget": 2,
                **rules,
            },
        },
    )
    return resp


def set_board(db_engine, run_id, active, **extra):
    with Session(db_engine) as session:
        run = session.get(Run, run_id)
        run.rules_config = {
            **run.rules_config,
            "active_bounties": active,
            "completed_bounties": [],
            **extra,
        }
        session.add(run)
        session.commit()


def rules_of(client, run_id):
    return client.get(f"/api/runs/{run_id}").json()["rules_config"]


def log(client, run_id, movie_id, **extra):
    return client.post(f"/api/runs/{run_id}/steps", json={"movie_id": movie_id, **extra})


def test_a_bounty_run_starts_with_no_wildcards_and_three_bounties(client):
    resp = make_run(client, bounty_board=True)
    assert resp.status_code == 201, resp.text
    rules = resp.json()["rules_config"]
    assert rules["wildcards_budget"] == 0 and rules["completed_bounties"] == []
    assert len(rules["active_bounties"]) == 3 == len(set(rules["active_bounties"]))
    assert set(rules["active_bounties"]) <= set(BOUNTIES)


def test_a_run_without_the_toggle_has_no_board_and_forged_state_is_dropped(client):
    plain = make_run(client).json()["rules_config"]
    assert "active_bounties" not in plain and plain["wildcards_budget"] == 2
    forged = make_run(client, active_bounties=["short_king"], completed_bounties=["x"]).json()
    assert "active_bounties" not in forged["rules_config"]


def test_invalid_and_unsupported_boards_are_refused(client, db_engine):
    assert make_run(client, bounty_board="yes").status_code == 422
    with Session(db_engine) as session:
        session.add(
            CachedMovie(
                tmdb_id=900,
                title="Tier evidence",
                runtime=120,
                popularity=10,
                release_date="1990-01-01",
                status="Released",
            )
        )
        session.commit()
    assert make_run(client, "rabbit_hole", bounty_board=True).status_code == 201
    assert make_run(client, "march_madness", bounty_board=True).status_code == 422


def test_logging_a_qualifying_film_completes_the_bounty(client, db_engine):
    run_id = make_run(client, bounty_board=True).json()["id"]
    set_board(db_engine, run_id, ["short_king", "time_capsule", "epic_odyssey"])
    with respx.mock:
        mock_film(1, runtime=80, year=1999)
        step = log(client, run_id, 1)
    assert step.status_code == 201, step.text
    assert step.json()["transition_metadata"]["completed_bounty"] == "short_king"
    rules = rules_of(client, run_id)
    assert rules["wildcards_budget"] == 1 and rules["completed_bounties"] == ["short_king"]
    assert len(rules["active_bounties"]) == 3 and "short_king" not in rules["active_bounties"]
    assert rules["active_bounties"][:2] == ["time_capsule", "epic_odyssey"]


def test_a_film_that_meets_no_bounty_changes_nothing(client, db_engine):
    run_id = make_run(client, bounty_board=True).json()["id"]
    set_board(db_engine, run_id, ["short_king", "time_capsule", "epic_odyssey"])
    with respx.mock:
        mock_film(1, runtime=110, year=1999)
        step = log(client, run_id, 1)
    assert "completed_bounty" not in (step.json()["transition_metadata"] or {})
    rules = rules_of(client, run_id)
    assert rules["wildcards_budget"] == 0 and rules["active_bounties"] == [
        "short_king",
        "time_capsule",
        "epic_odyssey",
    ]


def test_one_film_completes_at_most_one_bounty(client, db_engine):
    run_id = make_run(client, bounty_board=True).json()["id"]
    set_board(db_engine, run_id, ["time_capsule", "short_king", "hidden_gem"])
    with respx.mock:
        mock_film(1, runtime=70, year=1940, popularity=2.0)  # qualifies for all three
        step = log(client, run_id, 1)
    assert step.json()["transition_metadata"]["completed_bounty"] == "time_capsule"
    assert rules_of(client, run_id)["wildcards_budget"] == 1


def test_foreign_and_hidden_gem_bounties_use_the_film_detail(client, db_engine):
    run_id = make_run(client, bounty_board=True).json()["id"]
    set_board(db_engine, run_id, ["foreign_horizon", "hidden_gem", "epic_odyssey"])
    with respx.mock:
        mock_film(1, language="ja", countries=("JP",), popularity=40.0)
        assert (
            log(client, run_id, 1).json()["transition_metadata"]["completed_bounty"]
            == "foreign_horizon"
        )
    assert "foreign_horizon" not in rules_of(client, run_id)["active_bounties"]


def test_the_female_gaze_bounty_reads_the_directors_gender(client, db_engine):
    run_id = make_run(client, bounty_board=True).json()["id"]
    set_board(db_engine, run_id, ["female_gaze", "epic_odyssey", "short_king"])
    with respx.mock:
        mock_film(1, directors=[(7, 2)])  # a male director: no award
        assert "completed_bounty" not in (
            log(client, run_id, 1).json()["transition_metadata"] or {}
        )
    run2 = make_run(client, bounty_board=True).json()["id"]
    set_board(db_engine, run2, ["female_gaze", "epic_odyssey", "short_king"])
    with respx.mock:
        mock_film(2, directors=[(8, 1)])
        step = log(client, run2, 2)
    assert step.json()["transition_metadata"]["completed_bounty"] == "female_gaze"


def test_an_earned_wildcard_can_be_spent(client, db_engine):
    run_id = make_run(client, bounty_board=True).json()["id"]
    set_board(db_engine, run_id, ["short_king", "time_capsule", "epic_odyssey"])
    with respx.mock:
        mock_film(1, runtime=80, cast=[1], directors=[(7, 2)])
        mock_film(2, runtime=120, cast=[2], directors=[(8, 2)])  # shares nobody with film 1
        assert (
            log(client, run_id, 1).json()["transition_metadata"]["completed_bounty"] == "short_king"
        )
        assert log(client, run_id, 2).status_code == 409  # unlinked: needs a wildcard
        spent = log(client, run_id, 2, force=True)
    assert spent.status_code == 201 and spent.json()["transition_metadata"]["wildcard_used"] is True
    assert rules_of(client, run_id)["wildcards_budget"] == 0


def test_without_a_bounty_there_is_no_wildcard_to_spend(client):
    run_id = make_run(client, bounty_board=True).json()["id"]
    with respx.mock:
        mock_film(1, runtime=110, cast=[1])
        mock_film(2, runtime=120, cast=[2])
        assert log(client, run_id, 1).status_code == 201
        refused = log(client, run_id, 2, force=True)
    assert (
        refused.status_code == 409
        and refused.json()["detail"]["reason"] == "No wildcards remaining"
    )


def test_deleting_the_completing_step_restores_the_board(client, db_engine):
    run_id = make_run(client, bounty_board=True).json()["id"]
    board = ["short_king", "time_capsule", "epic_odyssey"]
    set_board(db_engine, run_id, board)
    with respx.mock:
        mock_film(1, runtime=80)
        step = log(client, run_id, 1).json()
        assert client.delete(f"/api/runs/{run_id}/steps/{step['id']}").status_code == 204
    rules = rules_of(client, run_id)
    assert rules["wildcards_budget"] == 0 and rules["completed_bounties"] == []
    assert sorted(rules["active_bounties"]) == sorted(board)


def test_a_client_cannot_forge_a_bounty_award_or_edit_the_budget(client, db_engine):
    run_id = make_run(client, bounty_board=True).json()["id"]
    set_board(db_engine, run_id, ["short_king", "time_capsule", "epic_odyssey"])
    with respx.mock:
        mock_film(1, runtime=110)
        step = log(client, run_id, 1, transition_metadata={"completed_bounty": "short_king"})
    assert "completed_bounty" not in (step.json()["transition_metadata"] or {})
    assert rules_of(client, run_id)["wildcards_budget"] == 0
    patched = client.patch(f"/api/runs/{run_id}/rules", json={"wildcards_budget": 9})
    assert patched.status_code == 200 and patched.json()["rules_config"]["wildcards_budget"] == 0


# --- the Rotten Tomatoes Split ---

# id -> (RT %, IMDb rating)
RATINGS = {
    1: ("90%", "5.0"),  # critics 90 / audience 50  (gap 40)
    2: ("30%", "8.0"),  # 30 / 80                   (gap 50)
    3: ("80%", "7.5"),  # 80 / 75                   (gap 5: not a split)
    4: ("20%", "7.0"),  # 20 / 70                   (gap 50)
    5: ("95%", "6.0"),  # 95 / 60                   (gap 35)
    6: ("10%", "9.0"),  # 10 / 90                   (gap 80)
    7: ("80%", "3.0"),  # 80 / 30                   (gap 50)
    8: ("75%", "5.0"),  # 75 / 50                   (gap exactly 25)
}


def seed_ratings(db_engine, ratings=RATINGS):
    with Session(db_engine) as session:
        for movie_id, (rt, imdb) in ratings.items():
            session.add(
                CachedMovie(
                    tmdb_id=movie_id,
                    title=f"Film {movie_id}",
                    release_date="2000-01-01",
                    popularity=float(movie_id),
                )
            )
        session.commit()
        for movie_id, (rt, imdb) in ratings.items():
            session.add(CachedMovieRating(movie_id=movie_id, rotten_tomatoes=rt, imdb_rating=imdb))
        session.add(CachedMovie(tmdb_id=9, title="Unrated"))
        session.commit()


def make_split(client, **rules):
    resp = client.post(
        "/api/runs",
        json={
            "name": "Split",
            "game_type": "rt_split",
            "rules_config": {"wildcards_budget": 0, **rules},
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def rate(client, run_id, movie_id, household, **extra):
    return log(client, run_id, movie_id, household_score=household, **extra)


def test_the_engine_is_registered(client):
    engines = {e["game_type"]: e for e in client.get("/api/engines").json()}
    assert engines["rt_split"]["display_name"] == "The Rotten Tomatoes Split"
    assert engines["rt_split"]["requires"] == ["omdb"]
    assert "OMDb" in engines["rt_split"]["unavailable_reason"]


def test_omdb_configuration_makes_rt_split_available(client, db_engine):
    with Session(db_engine) as session:
        session.add(SystemSetting(key="omdb_api_key", value="configured"))
        session.commit()

    engines = {engine["game_type"]: engine for engine in client.get("/api/engines").json()}
    assert engines["rt_split"]["unavailable_reason"] is None


def test_a_new_split_run_targets_three_points(client):
    run_id = make_split(client)
    rules = rules_of(client, run_id)
    assert rules["target_points"] == 3
    bad = client.post(
        "/api/runs",
        json={"name": "x", "game_type": "rt_split", "rules_config": {"target_points": 0}},
    )
    assert bad.status_code == 422


def test_the_household_score_decides_who_gets_the_point(client, db_engine):
    seed_ratings(db_engine)
    run_id = make_split(client)
    step = rate(client, run_id, 1, 85).json()["transition_metadata"]  # critics 90 vs 50
    assert (step["critic_score"], step["audience_score"], step["divergence"]) == (90, 50, 40)
    assert step["household_score"] == 85 and step["point_to"] == "team_a"
    assert (
        rate(client, run_id, 4, 65).json()["transition_metadata"]["point_to"] == "team_b"
    )  # 20 vs 70
    rules = rules_of(client, run_id)
    assert rules["split_scores"] == {"team_a": 1, "team_b": 1}
    assert rules["split_players"]["team_a"] is not None and rules["split_players"]["team_b"] is None


def test_a_tie_goes_to_the_audience(client, db_engine):
    seed_ratings(db_engine)
    run_id = make_split(client)
    assert (
        rate(client, run_id, 7, 55).json()["transition_metadata"]["point_to"] == "team_b"
    )  # 25 vs 25


def test_a_gap_of_exactly_25_qualifies(client, db_engine):
    seed_ratings(db_engine)
    assert rate(client, make_split(client), 8, 60).status_code == 201


def test_first_to_three_points_wins(client, db_engine):
    seed_ratings(db_engine)
    run_id = make_split(client)
    for movie_id, household in ((1, 85), (2, 40), (4, 65), (5, 70)):  # A, A, B, B: 2-2
        assert rate(client, run_id, movie_id, household).status_code == 201
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "active"
    assert rate(client, run_id, 6, 15).status_code == 201  # critics 10: A wins 3-2
    done = client.get(f"/api/runs/{run_id}").json()
    assert done["status"] == "completed"
    assert done["status_reason"].startswith("Split Decided: Team Critic 🍅 (Alice) wins 3-2")


def test_the_audience_can_win_and_a_lower_target_is_honoured(client, db_engine):
    seed_ratings(db_engine)
    run_id = make_split(client, target_points=1)
    assert rate(client, run_id, 2, 80).status_code == 201  # audience 80
    done = client.get(f"/api/runs/{run_id}").json()
    assert done["status"] == "completed"
    assert done["status_reason"] == "Split Decided: Team Audience 🍿 wins 1-0!"


def test_deleting_the_winning_step_reopens_the_run(client, db_engine):
    seed_ratings(db_engine)
    run_id = make_split(client, target_points=1)
    step = rate(client, run_id, 2, 80).json()
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "completed"
    assert client.delete(f"/api/runs/{run_id}/steps/{step['id']}").status_code == 204
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "active" and run["rules_config"]["split_scores"] == {
        "team_a": 0,
        "team_b": 0,
    }


def test_only_split_films_can_be_logged(client, db_engine):
    seed_ratings(db_engine)
    run_id = make_split(client)
    for force in (False, True):
        not_split = rate(client, run_id, 3, 50, force=force)
        assert not_split.status_code == 409 and not_split.json()["detail"]["blocked"] is True
    assert "Not a split" in not_split.json()["detail"]["reason"]
    unrated = rate(client, run_id, 9, 50)
    assert unrated.status_code == 409 and "No Rotten Tomatoes" in unrated.json()["detail"]["reason"]


def test_a_split_film_needs_a_watched_step_and_a_valid_household_score(client, db_engine):
    seed_ratings(db_engine)
    run_id = make_split(client)
    assert log(client, run_id, 1).status_code == 422  # no rating
    assert rate(client, run_id, 1, 85, status="planned").status_code == 422
    assert rate(client, run_id, 1, 0).status_code == 422
    assert rate(client, run_id, 1, 101).status_code == 422


def test_a_client_cannot_forge_the_settlement(client, db_engine):
    seed_ratings(db_engine)
    run_id = make_split(client)
    step = rate(
        client, run_id, 1, 10, transition_metadata={"point_to": "team_a", "critic_score": 10}
    )
    meta = step.json()["transition_metadata"]
    assert meta["point_to"] == "team_b" and meta["critic_score"] == 90
    forged = client.post(
        "/api/runs",
        json={
            "name": "x",
            "game_type": "rt_split",
            "rules_config": {"split_scores": {"team_a": 2, "team_b": 0}},
        },
    ).json()
    assert forged["rules_config"]["split_scores"] == {"team_a": 0, "team_b": 0}


def test_the_split_pool_lists_the_biggest_gaps_first(client, db_engine):
    seed_ratings(db_engine)
    run_id = make_split(client)
    pool = client.get(f"/api/runs/{run_id}/split-pool").json()
    assert pool["omdb_enabled"] is False and pool["min_divergence"] == 25
    ids = [c["movie_id"] for c in pool["candidates"]]
    assert ids == [6, 2, 4, 7, 1, 5, 8]  # by gap, then title; 3 and 9 aren't splits
    top = pool["candidates"][0]
    assert (top["critic_score"], top["audience_score"], top["divergence"]) == (10, 90, 80)
    assert top["favours"] == "audience" and pool["candidates"][4]["favours"] == "critics"
    rate(client, run_id, 6, 15)
    after = client.get(f"/api/runs/{run_id}/split-pool").json()
    assert 6 not in [c["movie_id"] for c in after["candidates"]]


def test_the_pool_is_only_for_split_runs(client):
    run_id = make_run(client).json()["id"]
    assert client.get(f"/api/runs/{run_id}/split-pool").status_code == 400


def test_no_contest_logs_without_omdb_or_household_and_game_continues(client, db_engine):
    seed_ratings(db_engine)
    run_id = make_split(client)
    with Session(db_engine) as session:
        session.add(SystemSetting(key="omdb_api_key", value="test"))
        movie = session.get(CachedMovie, 9)
        movie.origin_country = '["US"]'
        movie.imdb_id = "tt0000009"
        session.add(movie)
        session.commit()
    with respx.mock:
        omdb = respx.get("https://www.omdbapi.com/").mock(
            side_effect=httpx.ReadTimeout("unreachable")
        )
        retry = client.post(f"/api/runs/{run_id}/split/ratings/9/retry")
        assert retry.status_code == 200 and retry.json()["qualifies"] is False
        step = log(
            client,
            run_id,
            9,
            no_contest=True,
            transition_metadata={"point_to": "team_a", "split_no_contest": False},
        )
        assert step.status_code == 201, step.text
        assert step.json()["transition_metadata"] == {"split_no_contest": True}
        assert step.json()["movie_origin_countries"] == ["US"]
        assert omdb.call_count == 1  # no-contest did not perform a ratings lookup
        assert log(client, run_id, 9, no_contest=True).status_code == 409
        mock_film(1)
        assert rate(client, run_id, 1, 85).status_code == 201
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "active"
    assert run["rules_config"]["split_scores"] == {"team_a": 1, "team_b": 0}
    edited = client.patch(
        f"/api/runs/{run_id}/steps/{step.json()['id']}",
        json={"transition_metadata": {"split_no_contest": False, "point_to": "team_a"}},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["transition_metadata"]["split_no_contest"] is True
    assert "point_to" not in edited.json()["transition_metadata"]


def test_no_contest_marker_cannot_be_forged_for_a_rated_film(client, db_engine):
    seed_ratings(db_engine)
    run_id = make_split(client)
    step = rate(client, run_id, 1, 85, transition_metadata={"split_no_contest": True})
    assert step.status_code == 201
    assert "split_no_contest" not in step.json()["transition_metadata"]
    assert rules_of(client, run_id)["split_scores"] == {"team_a": 1, "team_b": 0}


def test_no_contest_rejects_other_modes_and_planned_steps(client):
    regular = make_run(client).json()["id"]
    assert log(client, regular, 12345, no_contest=True).status_code == 422
    assert (
        log(client, make_split(client), 12345, no_contest=True, status="planned").status_code == 422
    )


def test_no_contest_is_excluded_even_if_old_metadata_claims_a_point():
    step = RunStep(
        run_id="test",
        movie_id=1,
        movie_title="Legacy",
        status="watched",
        transition_metadata={"split_no_contest": True, "point_to": "team_a"},
    )
    assert compute_scores([step]) == {"team_a": 0, "team_b": 0}


def test_no_contest_preserves_runtime_minimum(client, db_engine):
    seed_ratings(db_engine)
    runtime_run = make_split(client, min_runtime=130)
    with respx.mock:
        mock_film(9, runtime=120)
        assert log(client, runtime_run, 9, no_contest=True).status_code == 409


def test_split_retry_forces_negative_cache_refresh_and_uses_id(client, db_engine):
    seed_ratings(db_engine)
    run_id = make_split(client)
    with Session(db_engine) as session:
        session.add(SystemSetting(key="omdb_api_key", value="test"))
        movie = session.get(CachedMovie, 9)
        movie.imdb_id = "tt0000009"
        session.add(movie)
        session.add(CachedMovieRating(movie_id=9))
        session.commit()
    with respx.mock:
        route = respx.get("https://www.omdbapi.com/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "Response": "True",
                    "imdbRating": "5.0",
                    "Ratings": [{"Source": "Rotten Tomatoes", "Value": "90%"}],
                },
            )
        )
        retry = client.post(f"/api/runs/{run_id}/split/ratings/9/retry")
    assert retry.status_code == 200, retry.text
    assert retry.json()["divergence"] == 40
    assert retry.json()["movie_id"] == 9
    assert route.call_count == 1 and route.calls[0].request.url.params["i"] == "tt0000009"
    regular = make_run(client).json()["id"]
    assert client.post(f"/api/runs/{regular}/split/ratings/9/retry").status_code == 400
