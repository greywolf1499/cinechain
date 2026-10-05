"""Phase 26c: The Method Actor Marathon - career track, milestones, near-chronological order."""

from datetime import date

import httpx
import pytest
import respx

from app.engines.base import RunSetupError
from app.engines.method_actor import age_at, build_career_track
from tests.test_graph_mutators import TMDB_BASE, client, db_engine

__all__ = ["client", "db_engine"]

TODAY = date(2026, 10, 4)
ACTOR = 500


def credit(
    movie_id,
    year,
    *,
    order=0,
    votes=1000,
    rating=7.0,
    character="Hero",
    genres=(),
    month="06-01",
    **extra,
):
    return {
        "id": movie_id,
        "title": f"Film {movie_id}",
        "release_date": f"{year}-{month}",
        "poster_path": f"/p{movie_id}.jpg",
        "character": character,
        "order": order,
        "vote_average": rating,
        "vote_count": votes,
        "genre_ids": list(genres),
        **extra,
    }


# A career in order: id 1 is the debut ... id 8 is the latest.
CAREER = [
    credit(1, 1985, order=9, votes=80, rating=5.5),  # debut
    credit(2, 1990, order=5, votes=300, rating=6.5),
    credit(3, 1994, order=1, votes=9000, rating=8.9),  # breakout + prestige peak
    credit(4, 1999, order=0, votes=6000, rating=8.1),
    credit(5, 2005, order=0, votes=1500, rating=7.0),
    credit(6, 2012, order=2, votes=900, rating=6.8),
    credit(7, 2018, order=6, votes=700, rating=7.2),
    credit(8, 2024, order=1, votes=2500, rating=7.9),  # modern resurgence
]


def milestones(track):
    return {f["movie_id"]: f["milestones"] for f in track if f["milestones"]}


# --- the career track ---


def test_the_track_is_chronological_and_flags_the_milestones():
    track = build_career_track(CAREER, "1960-03-10", TODAY)
    assert [f["movie_id"] for f in track] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert milestones(track) == {
        1: ["debut"],
        3: ["breakout", "prestige_peak"],
        8: ["modern_resurgence"],
    }


def test_age_is_the_actors_age_at_release():
    track = build_career_track(CAREER, "1960-07-01", TODAY)
    assert [f["age"] for f in track][:3] == [24, 29, 33]  # 1985-06 (before the July birthday)
    assert age_at(None, "2000-01-01") is None
    assert age_at("1960-03-10", "1950-01-01") is None  # before they were born


def test_non_roles_and_unreleased_films_never_make_the_track():
    noisy = [
        *CAREER,
        credit(20, 1980, character="Himself"),  # earlier than the debut
        credit(21, 1981, character="Cop (uncredited)"),
        credit(22, 1982, genres=[99]),  # documentary
        credit(23, 1983, genres=[10770]),  # TV movie
        credit(24, 2030),  # not released yet
        {**credit(25, 1984), "release_date": ""},  # no date
        {**credit(26, 1984), "adult": True},
    ]
    track = build_career_track(noisy, None, TODAY)
    assert [f["movie_id"] for f in track] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert track[0]["milestones"] == ["debut"]


def test_the_track_is_curated_to_well_known_leading_roles_but_keeps_milestones():
    extras = [credit(100 + i, 1995 + i % 20, order=40, votes=3) for i in range(30)]
    cameos = [credit(200, 2001, order=30, votes=10)]
    track = build_career_track([*CAREER, *extras, *cameos], None, TODAY)
    ids = [f["movie_id"] for f in track]
    assert ids == [1, 2, 3, 4, 5, 6, 7, 8]  # unknown cameos are curated away, the debut stays
    many = [credit(300 + i, 1980 + i, order=1, votes=500 + i) for i in range(40)]
    capped = build_career_track(many, None, TODAY)
    assert len(capped) == 25
    assert [f["release_date"] for f in capped] == sorted(f["release_date"] for f in capped)


def test_a_thin_career_still_gets_a_track():
    thin = [credit(i, 2000 + i, order=20, votes=5) for i in range(1, 8)]
    track = build_career_track(thin, None, TODAY)
    assert len(track) >= 5 and track[0]["milestones"] == ["debut"]


def test_milestone_thresholds_fall_back_for_small_careers():
    modest = [
        credit(1, 1990, order=0, votes=150, rating=6.0),
        credit(2, 2024, order=0, votes=120, rating=7.5),
    ]
    marks = milestones(build_career_track(modest, None, TODAY))
    assert marks[1] == ["debut", "breakout"] and marks[2] == ["prestige_peak", "modern_resurgence"]


def test_no_feature_credits_is_a_setup_error():
    with pytest.raises(RunSetupError):
        build_career_track([credit(1, 2030)], None, TODAY)


# --- the API ---


def mock_actor(credits=CAREER, birthday="1960-03-10"):
    respx.get(f"{TMDB_BASE}/person/{ACTOR}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": ACTOR,
                "name": "Method Man",
                "birthday": birthday,
                "profile_path": "/m.jpg",
            },
        )
    )
    respx.get(f"{TMDB_BASE}/person/{ACTOR}/movie_credits").mock(
        return_value=httpx.Response(200, json={"id": ACTOR, "cast": credits, "crew": []})
    )
    for entry in credits:
        respx.get(f"{TMDB_BASE}/movie/{entry['id']}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": entry["id"],
                    "title": entry["title"],
                    "release_date": entry["release_date"],
                    "poster_path": entry["poster_path"],
                    "overview": "",
                    "origin_country": ["US"],
                    "original_language": "en",
                    "runtime": 100,
                    "genres": [],
                    "popularity": 5.0,
                    "status": "Released",
                },
            )
        )
    respx.get(f"{TMDB_BASE}/movie/99").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 99,
                "title": "Off Track",
                "release_date": "2000-01-01",
                "poster_path": None,
                "overview": "",
                "origin_country": ["US"],
                "original_language": "en",
                "runtime": 100,
                "genres": [],
                "popularity": 5.0,
                "status": "Released",
            },
        )
    )


def make_run(client, **rules):
    resp = client.post(
        "/api/runs",
        json={
            "name": "Marathon",
            "game_type": "method_actor",
            "rules_config": {"actor_id": ACTOR, "wildcards_budget": 1, **rules},
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def log(client, run_id, movie_id, **extra):
    return client.post(f"/api/runs/{run_id}/steps", json={"movie_id": movie_id, **extra})


def detail(client, run_id):
    return client.get(f"/api/runs/{run_id}").json()


def test_the_engine_is_registered(client):
    engines = {e["game_type"]: e for e in client.get("/api/engines").json()}
    assert engines["method_actor"]["display_name"] == "The Method Actor Marathon"


def test_creating_a_run_stores_the_actor_and_the_career_track(client):
    with respx.mock:
        mock_actor()
        run_id = make_run(client)
    rules = detail(client, run_id)["rules_config"]
    assert rules["actor"] == {"id": ACTOR, "name": "Method Man"}
    assert "actor_id" not in rules and rules["max_skip"] == 2
    track = rules["filmography"]
    assert [f["movie_id"] for f in track] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert track[0]["milestones"] == ["debut"] and track[0]["age"] == 25
    assert track[2]["milestones"] == ["breakout", "prestige_peak"]


def test_an_unknown_actor_or_a_missing_id_is_rejected(client):
    with respx.mock:
        respx.get(f"{TMDB_BASE}/person/{ACTOR}").mock(return_value=httpx.Response(404, json={}))
        respx.get(f"{TMDB_BASE}/person/{ACTOR}/movie_credits").mock(
            return_value=httpx.Response(404, json={})
        )
        missing = client.post(
            "/api/runs",
            json={"name": "x", "game_type": "method_actor", "rules_config": {"actor_id": ACTOR}},
        )
    assert missing.status_code == 422
    nothing = client.post("/api/runs", json={"name": "x", "game_type": "method_actor"})
    assert nothing.status_code == 422
    bad = client.post(
        "/api/runs",
        json={
            "name": "x",
            "game_type": "method_actor",
            "rules_config": {"actor_id": ACTOR, "max_skip": 99},
        },
    )
    assert bad.status_code == 422


def test_films_must_follow_the_career_order(client):
    with respx.mock:
        mock_actor()
        run_id = make_run(client)
        assert log(client, run_id, 1).status_code == 201  # the debut
        assert log(client, run_id, 2).status_code == 201  # next in line
        assert (
            log(client, run_id, 5).status_code == 201
        )  # skips films 3 and 4: still near-sequential
    assert [s["movie_id"] for s in detail(client, run_id)["steps"]] == [1, 2, 5]


def test_skipping_too_far_or_going_backwards_needs_a_wildcard(client):
    with respx.mock:
        mock_actor()
        run_id = make_run(client)
        log(client, run_id, 1)
        too_far = log(client, run_id, 6)  # skips four films
        assert too_far.status_code == 409 and not too_far.json()["detail"]["blocked"]
        assert "skips 4 films" in too_far.json()["detail"]["reason"]
        assert log(client, run_id, 3).status_code == 201
        back = log(client, run_id, 2)
        assert back.status_code == 409 and "comes before" in back.json()["detail"]["reason"]
        forced = log(client, run_id, 2, force=True)  # a wildcard buys it
        assert forced.status_code == 201 and forced.json()["transition_metadata"]["wildcard_used"]


def test_strict_mode_allows_no_skips(client):
    with respx.mock:
        mock_actor()
        run_id = make_run(client, max_skip=0)
        assert log(client, run_id, 2).status_code == 409  # the first film must be the debut
        assert log(client, run_id, 1).status_code == 201
        assert log(client, run_id, 3).status_code == 409
        assert log(client, run_id, 2).status_code == 201


def test_a_film_off_the_track_is_always_blocked(client):
    with respx.mock:
        mock_actor()
        run_id = make_run(client)
        for force in (False, True):
            resp = log(client, run_id, 99, force=force)
            assert resp.status_code == 409 and resp.json()["detail"]["blocked"] is True
        assert "career track" in resp.json()["detail"]["reason"]


def test_logging_the_final_film_completes_the_career(client):
    with respx.mock:
        mock_actor()
        run_id = make_run(client)
        for movie_id in range(1, 9):
            assert log(client, run_id, movie_id).status_code == 201
    done = detail(client, run_id)
    assert done["status"] == "completed" and done["status_reason"] == "Career complete: Method Man"


def test_person_search_lists_actors_first(client):
    with respx.mock:
        respx.get(f"{TMDB_BASE}/search/person").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": 1,
                            "name": "A Director",
                            "known_for_department": "Directing",
                            "profile_path": None,
                            "known_for": [{"title": "X"}],
                        },
                        {
                            "id": 2,
                            "name": "An Actor",
                            "known_for_department": "Acting",
                            "profile_path": "/a.jpg",
                            "known_for": [{"title": "Y"}, {"name": "Z"}],
                        },
                    ]
                },
            )
        )
        resp = client.get("/api/people/search", params={"q": "an"})
    assert resp.status_code == 200
    people = resp.json()
    assert [p["person_id"] for p in people] == [2, 1]
    assert people[0]["known_for"] == ["Y", "Z"] and people[0]["profile_path"] == "/a.jpg"
