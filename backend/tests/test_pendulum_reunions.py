"""Phase 25b: the Genre Pendulum, Golden Reunions and Character Hops."""

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.engines.reunions import (
    CastCredit,
    Person,
    character_aliases,
    find_character_hop,
    find_golden_reunion,
)
from app.main import app

TMDB_BASE = "https://api.themoviedb.org/3"
HORROR, THRILLER, CRIME, COMEDY, DRAMA, ROMANCE = 27, 53, 80, 35, 18, 10749


@pytest.fixture()
def client(config_dir):
    engine = create_engine(
        f"sqlite:///{config_dir}/app_test.db", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)

    def override_get_session():
        with Session(engine) as session:
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


def cast(*members):
    """(actor id, character) pairs -> TMDB cast entries in billing order."""
    return [
        {"id": a, "name": f"Actor {a}", "profile_path": None, "character": c, "order": i}
        for i, (a, c) in enumerate(members)
    ]


def mock_movies(movies):
    """movies: id -> dict(title, genres, cast=[(actor, char)], directors=[ids])."""
    for movie_id, m in movies.items():
        respx.get(f"{TMDB_BASE}/movie/{movie_id}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": movie_id,
                    "title": m["title"],
                    "release_date": "2000-01-01",
                    "poster_path": None,
                    "overview": "",
                    "origin_country": ["US"],
                    "original_language": "en",
                    "runtime": 100,
                    "genres": [{"id": g, "name": str(g)} for g in m.get("genres", [])],
                    "popularity": m.get("popularity", 5.0),
                    "status": "Released",
                },
            )
        )
        respx.get(f"{TMDB_BASE}/movie/{movie_id}/credits").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": movie_id,
                    "cast": cast(*m.get("cast", [])),
                    "crew": [
                        {
                            "id": d,
                            "name": f"Director {d}",
                            "job": "Director",
                            "department": "Directing",
                            "profile_path": None,
                            "gender": 2,
                        }
                        for d in m.get("directors", [])
                    ],
                },
            )
        )
    actors = {a for m in movies.values() for a, _ in m.get("cast", [])}
    for actor in actors:
        respx.get(f"{TMDB_BASE}/person/{actor}/movie_credits").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": actor,
                    "crew": [],
                    "cast": [
                        {
                            "id": i,
                            "title": m["title"],
                            "release_date": "2000-01-01",
                            "poster_path": None,
                            "genre_ids": m.get("genres", []),
                            "original_language": "en",
                            "popularity": 5.0,
                            "character": "x",
                        }
                        for i, m in movies.items()
                        if actor in {a for a, _ in m.get("cast", [])}
                    ],
                },
            )
        )


def make_run(client, game_type, seed=None, expect=201, **rules):
    payload = {
        "name": "Run",
        "game_type": game_type,
        "seed_movie_id": seed,
        "rules_config": {
            "preset": "standard",
            "allow_repeats": "strict",
            "no_consecutive_actor": False,
            "max_cast_order": 15,
            "min_runtime": 40,
            "wildcards_budget": 2,
            **rules,
        },
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    resp = client.post("/api/runs", json=payload)
    assert resp.status_code == expect, resp.text
    return resp.json()


def log(client, run_id, movie_id, **extra):
    return client.post(f"/api/runs/{run_id}/steps", json={"movie_id": movie_id, **extra})


def test_search_further_continues_the_current_pendulum_swing(client):
    movies = {
        1: {"title": "First horror", "genres": [HORROR, THRILLER], "cast": [(100, "A")]},
        2: {"title": "Second horror", "genres": [HORROR, THRILLER], "cast": [(100, "B")]},
        3: {"title": "Wrong old swing", "genres": [HORROR], "cast": [(100, "C")]},
        4: {"title": "Next thriller", "genres": [THRILLER], "cast": [(100, "D")]},
    }
    with respx.mock:
        mock_movies(movies)
        run = make_run(client, "genre_pendulum", require_cast_link=True)
        assert log(client, run["id"], 1).status_code == 201
        assert log(client, run["id"], 2).status_code == 201
        response = client.get(f"/api/runs/{run['id']}/suggestions", params={"decade": 2000})
        assert response.status_code == 200, response.text
        assert [candidate["movie_id"] for candidate in response.json()] == [4]
        assert response.json()[0]["connections"][0]["actor_id"] == 100


# --- pure helpers ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "Self",
        "Himself",
        "Extra",
        "Police Officer",
        "Uncredited",
        "Narrator (voice)",
        "John",
        "Bob",
        "Waiter",
        "Man",
        "(uncredited)",
        "Self (archive footage)",
    ],
)
def test_generic_or_vague_characters_never_match(raw):
    assert character_aliases(raw) == set()


def test_character_names_are_normalized():
    assert character_aliases("James Bond") == {"james bond"}
    assert character_aliases("JAMES  BOND (uncredited)") == {"james bond"}
    assert character_aliases("Peter Parker / Spider-Man") == {"peter parker", "spider-man"}
    assert character_aliases("Batman (voice)") == {"batman"}
    assert character_aliases("Dr. Jekyll") == {"dr jekyll"}


def test_a_character_hop_needs_different_actors():
    connery = CastCredit(1, "Sean Connery", "James Bond", 0)
    craig = CastCredit(2, "Daniel Craig", "James Bond", 0)
    hop = find_character_hop([connery], [craig])
    assert hop and hop.character == "James Bond"
    assert (hop.actor_from.name, hop.actor_to.name) == ("Sean Connery", "Daniel Craig")
    assert find_character_hop([connery], [CastCredit(1, "Sean Connery", "James Bond", 0)]) is None
    assert (
        find_character_hop(
            [CastCredit(1, "A", "Police Officer", 3)], [CastCredit(2, "B", "Police Officer", 4)]
        )
        is None
    )
    assert (
        find_character_hop(
            [CastCredit(1, "A", "Peter Parker / Spider-Man", 0)],
            [CastCredit(2, "B", "Spider-Man", 0)],
        )
        is not None
    )


def test_golden_reunion_needs_the_director_and_a_top5_actor():
    director = [Person(100, "Hamilton")]
    top = [CastCredit(i, f"Actor {i}", "x", i) for i in range(7)]
    got = find_golden_reunion(director, top, director, [CastCredit(2, "Actor 2", "y", 9)])
    assert got == {"director": "Hamilton", "actor": "Actor 2", "director_id": 100, "actor_id": 2}
    # billed 6th on the earlier film: not a top-5 actor
    assert find_golden_reunion(director, top, director, [CastCredit(5, "Actor 5", "y", 0)]) is None
    # a different director, or the director with nobody back
    assert (
        find_golden_reunion(director, top, [Person(7, "Other")], [CastCredit(2, "Actor 2", "y", 0)])
        is None
    )
    assert find_golden_reunion(director, top, director, [CastCredit(40, "New", "y", 0)]) is None


# --- Golden Reunion & Character Hop through the engines --------------------------------

BOND = {
    1: {
        "title": "Goldfinger",
        "genres": [28],
        "directors": [100],
        "cast": [
            (10, "James Bond"),
            (11, "Goldfinger"),
            (12, "Pussy Galore"),
            (13, "Oddjob"),
            (14, "Tilly"),
            (15, "Strap Man"),
        ],
    },
    2: {
        "title": "Thunderball",
        "genres": [28],
        "directors": [101],
        "cast": [(10, "James Bond"), (16, "Largo")],
    },  # same actor, same character
    3: {
        "title": "Casino Royale",
        "genres": [28],
        "directors": [102],
        "cast": [(20, "James Bond"), (21, "Le Chiffre")],
    },  # a different Bond: pure character hop
    4: {
        "title": "Goldfinger Returns",
        "genres": [28],
        "directors": [100],
        "cast": [(10, "Someone Else"), (13, "Oddjob")],
    },  # same director + Connery + Oddjob
    5: {
        "title": "Late Billed",
        "genres": [28],
        "directors": [100],
        "cast": [(15, "A Man")],
    },  # director back, actor was 6th billed
    6: {
        "title": "Cop Movie A",
        "genres": [28],
        "directors": [110],
        "cast": [(30, "Police Officer"), (31, "Hero A")],
    },
    7: {
        "title": "Cop Movie B",
        "genres": [28],
        "directors": [111],
        "cast": [(32, "Police Officer"), (33, "Hero B")],
    },
}


def test_same_actor_same_character_is_a_normal_link_without_a_hop(client):
    with respx.mock:
        mock_movies(BOND)
        run_id = make_run(client, "cinechain", seed=1)["id"]
        step = log(client, run_id, 2).json()
    meta = step["transition_metadata"] or {}
    assert "character_hop" not in meta and "golden_reunion" not in meta


def test_different_actors_playing_the_same_character_link_the_films(client):
    with respx.mock:
        mock_movies(BOND)
        run_id = make_run(client, "cinechain", seed=1)["id"]
        checked = client.post(f"/api/runs/{run_id}/validate", json={"movie_id": 3}).json()
        step = log(client, run_id, 3)
    assert checked["valid"] and checked["connections"][0]["kind"] == "character"
    assert step.status_code == 201, step.text
    meta = step.json()["transition_metadata"]
    assert meta["character_hop"] == "James Bond"
    assert "golden_reunion" not in meta
    # who played the character is recorded even though the client sent no link
    assert meta["actor_name"] == "Actor 10 \u2192 Actor 20"
    assert (meta["character_in_from"], meta["character_in_to"]) == ("James Bond", "James Bond")


def test_generic_roles_do_not_hop(client):
    with respx.mock:
        mock_movies(BOND)
        run_id = make_run(client, "cinechain", seed=6)["id"]
        resp = log(client, run_id, 7)
    assert resp.status_code == 409 and "shared credited cast" in resp.json()["detail"]["reason"]


def test_golden_reunion_is_stamped_on_the_step(client):
    with respx.mock:
        mock_movies(BOND)
        run_id = make_run(client, "cinechain", seed=1)["id"]
        step = log(client, run_id, 4)
    assert step.status_code == 201, step.text
    reunion = step.json()["transition_metadata"]["golden_reunion"]
    assert reunion["director"] == "Director 100"
    assert reunion["actor"] in ("Actor 10", "Actor 13")


def test_the_reunion_actor_must_be_top_5_billed_on_the_earlier_film(client):
    with respx.mock:
        mock_movies(BOND)
        run_id = make_run(client, "cinechain", seed=1)["id"]
        step = log(client, run_id, 5)  # Strap Man (15) was billed 6th in Goldfinger
    assert step.status_code == 201
    assert "golden_reunion" not in (step.json()["transition_metadata"] or {})


def test_clients_cannot_forge_link_bonuses(client):
    with respx.mock:
        mock_movies(BOND)
        run_id = make_run(client, "cinechain", seed=1)["id"]
        step = log(
            client,
            run_id,
            2,
            transition_metadata={
                "golden_reunion": {"director": "Fake", "actor": "Fake"},
                "character_hop": "Fake",
            },
        ).json()
        meta = step["transition_metadata"] or {}
        assert "golden_reunion" not in meta and "character_hop" not in meta
        patched = client.patch(
            f"/api/runs/{run_id}/steps/{step['id']}",
            json={"transition_metadata": {"character_hop": "Fake"}},
        ).json()
    assert "character_hop" not in (patched["transition_metadata"] or {})


def test_the_bonuses_survive_a_step_edit(client):
    with respx.mock:
        mock_movies(BOND)
        run_id = make_run(client, "cinechain", seed=1)["id"]
        step = log(client, run_id, 3).json()
        patched = client.patch(
            f"/api/runs/{run_id}/steps/{step['id']}", json={"transition_metadata": {"note": "x"}}
        ).json()
    assert patched["transition_metadata"]["character_hop"] == "James Bond"


def test_crew_craft_also_detects_hops_and_reunions(client):
    with respx.mock:
        mock_movies(BOND)
        run_id = make_run(client, "crew_craft", seed=1)["id"]
        hop = client.post(f"/api/runs/{run_id}/validate", json={"movie_id": 3}).json()
        reunion = log(client, run_id, 4)
        run2 = make_run(client, "crew_craft", seed=1)["id"]
        hopped = log(client, run2, 3)
    assert hop["valid"] and hop["connections"][0]["role_in_to"] == "actor"
    assert reunion.json()["transition_metadata"]["golden_reunion"]["director"] == "Director 100"
    assert hopped.status_code == 201
    assert hopped.json()["transition_metadata"]["character_hop"] == "James Bond"


def test_auteur_relay_ignores_character_hops(client):
    with respx.mock:
        mock_movies(BOND)
        run_id = make_run(client, "auteur_relay", seed=1)["id"]
        resp = log(client, run_id, 3)
    assert resp.status_code == 409


# --- the Genre Pendulum ------------------------------------------------------------------

PENDULUM = {
    1: {"title": "Horror One", "genres": [HORROR, THRILLER]},
    2: {"title": "Horror Two", "genres": [HORROR, COMEDY]},
    3: {"title": "Thriller Crime", "genres": [THRILLER, CRIME]},
    4: {"title": "Pure Comedy", "genres": [COMEDY]},
    5: {"title": "Crime Drama", "genres": [CRIME, DRAMA]},
    6: {"title": "Thriller Only", "genres": [THRILLER]},
    7: {"title": "Comedy Crime", "genres": [COMEDY, CRIME]},
    8: {"title": "Romance", "genres": [ROMANCE]},
    9: {"title": "Horror Comedy", "genres": [HORROR, COMEDY, DRAMA]},
}


def pendulum_run(client, seed=1, **rules):
    return make_run(client, "genre_pendulum", seed=seed, **rules)["id"]


def test_engine_is_registered_without_a_bridge_solver(client):
    meta = {e["game_type"]: e for e in client.get("/api/engines").json()}["genre_pendulum"]
    assert meta["display_name"] == "The Genre Pendulum"
    assert (
        "discover_candidates" in meta["capabilities"] and "solve_bridge" not in meta["capabilities"]
    )


def test_defaults_are_filled_and_names_canonicalised(client):
    with respx.mock:
        mock_movies(PENDULUM)
        rules = make_run(client, "genre_pendulum", seed=1)["rules_config"]
        custom = make_run(
            client, "genre_pendulum", seed=1, genre_cycle=["horror", "sci-fi"], swing_frequency=3
        )["rules_config"]
    assert rules["genre_cycle"] == ["Horror", "Thriller", "Crime", "Comedy"]
    assert rules["swing_frequency"] == 2
    assert custom["genre_cycle"] == ["Horror", "Science Fiction"] and custom["swing_frequency"] == 3


def test_rules_are_validated(client):
    with respx.mock:
        mock_movies(PENDULUM)
        unknown = make_run(client, "genre_pendulum", expect=422, genre_cycle=["Horror", "Gloom"])
        empty = make_run(client, "genre_pendulum", expect=422, genre_cycle=[])
        freq = make_run(client, "genre_pendulum", expect=422, swing_frequency=0)
    assert "Gloom" in unknown["detail"] and "genre_cycle" in empty["detail"]
    assert "swing_frequency" in freq["detail"]


def test_the_first_film_must_carry_the_opening_genre(client):
    with respx.mock:
        mock_movies(PENDULUM)
        bad = make_run(client, "genre_pendulum", seed=4, expect=422)
        assert "needs a Horror film" in bad["detail"]
        run_id = make_run(client, "genre_pendulum")["id"]  # unseeded
        assert log(client, run_id, 4).status_code == 409
        assert log(client, run_id, 1).status_code == 201


def test_the_pendulum_swings_every_two_steps(client):
    """steps: 1-2 Horror, 3-4 Thriller, 5-6 Crime, 7-8 Comedy, then Horror again."""
    with respx.mock:
        mock_movies(PENDULUM)
        run_id = pendulum_run(client)  # step 1: Horror One [27, 53]
        too_early = log(client, run_id, 3)  # overlaps via Thriller, but isn't Horror
        assert too_early.status_code == 409
        assert "needs a Horror film (step 2 of 2)" in too_early.json()["detail"]["reason"]
        assert log(client, run_id, 2).status_code == 201  # step 2: Horror Two [27, 35]
        # step 3 is a Thriller swing
        assert log(client, run_id, 9).status_code == 409  # Horror Comedy has no Thriller
        no_overlap = log(client, run_id, 6)  # Thriller Only [53] vs Horror Two [27, 35]
        assert no_overlap.status_code == 409
        assert "shares no genre" in no_overlap.json()["detail"]["reason"]


def test_both_rules_are_enforced_with_clear_409s(client):
    with respx.mock:
        mock_movies(PENDULUM)
        run_id = pendulum_run(client, genre_cycle=["Horror", "Crime"], swing_frequency=1)
        # step 2 target: Crime; Thriller Crime shares THRILLER with Horror One
        wrong_genre = log(client, run_id, 2)
        no_overlap = log(client, run_id, 5)  # Crime but no overlap with [27,53]
        ok = log(client, run_id, 3)
    assert wrong_genre.status_code == 409
    assert "needs a Crime film (step 1 of 1)" in wrong_genre.json()["detail"]["reason"]
    assert (
        no_overlap.status_code == 409 and "shares no genre" in no_overlap.json()["detail"]["reason"]
    )
    assert ok.status_code == 201
    assert ok.json()["transition_metadata"]["pendulum_genre"] == "Crime"


def test_the_violations_are_hard_blocks_a_wildcard_cannot_skip(client):
    with respx.mock:
        mock_movies(PENDULUM)
        run_id = pendulum_run(client)
        forced = log(client, run_id, 3, force=True)
    assert forced.status_code == 409


def test_the_cycle_wraps_around(client):
    with respx.mock:
        mock_movies(PENDULUM)
        run_id = pendulum_run(client, genre_cycle=["Horror", "Comedy"], swing_frequency=1)
        assert log(client, run_id, 9).status_code == 201  # step 2: Comedy (+ Horror overlap)
        assert log(client, run_id, 2).status_code == 201  # step 3: wraps back to Horror
        fourth = log(client, run_id, 4)  # step 4: Comedy again
    assert fourth.status_code == 201
    assert fourth.json()["transition_metadata"]["pendulum_genre"] == "Comedy"


def test_constraint_describes_the_current_swing(client):
    with respx.mock:
        mock_movies(PENDULUM)
        run_id = pendulum_run(client)
        first = client.get(f"/api/runs/{run_id}/constraint").json()
        log(client, run_id, 2)
        second = client.get(f"/api/runs/{run_id}/constraint").json()
    assert first["kind"] == "genre" and "Horror (step 2 of 2)" in first["title"]
    assert "Next swing: Thriller" in first["detail"]
    assert "Thriller (step 1 of 2)" in second["title"] and "share a genre" in second["detail"]


def test_pick_next_only_offers_films_that_fit_the_swing(client):
    with respx.mock:
        mock_movies(PENDULUM)
        respx.get(f"{TMDB_BASE}/discover/movie").mock(
            return_value=httpx.Response(
                200,
                json={
                    "page": 1,
                    "total_pages": 1,
                    "results": [
                        {
                            "id": m,
                            "title": PENDULUM[m]["title"],
                            "release_date": "2000-01-01",
                            "poster_path": None,
                            "genre_ids": PENDULUM[m]["genres"],
                            "original_language": "en",
                            "popularity": 5.0,
                        }
                        for m in PENDULUM
                    ],
                },
            )
        )
        run_id = pendulum_run(client)  # step 1 done; step 2 is still Horror
        pool = client.get(f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 1}).json()
    assert sorted(c["movie_id"] for c in pool) == [2, 9]  # the Horror films that overlap [27, 53]
