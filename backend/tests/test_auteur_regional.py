"""Phase 27a: The Auteur Marathon (director filmography) and the Regional Deep Dive (canon slice)."""

from datetime import date

import httpx
import pytest
import respx
from sqlmodel import Session

from app.engines.auteur_marathon import build_filmography
from app.engines.base import RunSetupError
from app.models.curated import CanonMovieBadge, CuratedList
from tests.test_graph_mutators import TMDB_BASE, client, db_engine

__all__ = ["client", "db_engine"]

TODAY = date(2026, 10, 4)
DIRECTOR = 700


def crew(movie_id, year, *, job="Director", genres=(), votes=100, month="06-01", **extra):
    return {
        "id": movie_id,
        "title": f"Film {movie_id}",
        "release_date": f"{year}-{month}",
        "poster_path": f"/p{movie_id}.jpg",
        "job": job,
        "genre_ids": list(genres),
        "vote_count": votes,
        **extra,
    }


# Out of order on purpose: ids 1-5 are the features, oldest first.
CREDITS = [
    crew(3, 1980),
    crew(1, 1970),
    crew(5, 1995),
    crew(2, 1975),
    crew(4, 1988),
    crew(10, 1971, job="Producer"),  # not directed
    crew(11, 1972, genres=[99]),  # documentary
    crew(12, 1973, genres=[10770]),  # TV movie
    crew(13, 1974, video=True),  # music video
    crew(14, 1976),  # short (runtime below)
    crew(15, 2031),  # not released
    {**crew(16, 1977), "release_date": ""},  # undated
    crew(1, 1970),  # duplicate credit
]
RUNTIMES = {i: (110, []) for i in (1, 2, 3, 4, 5)} | {14: (12, [])}


def test_the_filmography_is_the_chronological_feature_films_only():
    films = build_filmography(CREDITS, RUNTIMES, TODAY)
    assert [f["movie_id"] for f in films] == [1, 2, 3, 4, 5]
    assert films[0] == {
        "movie_id": 1,
        "title": "Film 1",
        "release_date": "1970-06-01",
        "year": 1970,
        "poster_path": "/p1.jpg",
        "runtime": 110,
    }


def test_films_without_a_runtime_are_kept_only_when_somebody_rated_them():
    credits = [crew(1, 2000, votes=0), crew(2, 2001, votes=5)]
    assert [f["movie_id"] for f in build_filmography(credits, {}, TODAY)] == [2]


def test_a_director_with_no_features_is_a_setup_error():
    with pytest.raises(RunSetupError):
        build_filmography([crew(14, 1976)], {14: (12, [])}, TODAY)
    with pytest.raises(RunSetupError):
        build_filmography([], {}, TODAY)


# --- the Auteur Marathon API ---


def movie_json(movie_id, title=None, year=1990, countries=("US",), runtime=110, **extra):
    return {
        "id": movie_id,
        "title": title or f"Film {movie_id}",
        "release_date": f"{year}-06-01",
        "poster_path": f"/p{movie_id}.jpg",
        "overview": "",
        "origin_country": list(countries),
        "original_language": "en",
        "runtime": runtime,
        "genres": [],
        "popularity": 5.0,
        "status": "Released",
        **extra,
    }


def mock_director(credits=CREDITS):
    respx.get(f"{TMDB_BASE}/person/{DIRECTOR}").mock(
        return_value=httpx.Response(
            200, json={"id": DIRECTOR, "name": "Auteur Person", "known_for_department": "Directing"}
        )
    )
    respx.get(f"{TMDB_BASE}/person/{DIRECTOR}/movie_credits").mock(
        return_value=httpx.Response(200, json={"id": DIRECTOR, "cast": [], "crew": credits})
    )
    for entry in credits:
        runtime = RUNTIMES.get(entry["id"], (110, []))[0]
        respx.get(f"{TMDB_BASE}/movie/{entry['id']}").mock(
            return_value=httpx.Response(
                200,
                json=movie_json(
                    entry["id"], year=entry["release_date"][:4] or 1990, runtime=runtime
                ),
            )
        )
    respx.get(f"{TMDB_BASE}/movie/99").mock(
        return_value=httpx.Response(200, json=movie_json(99, "Off Filmography"))
    )


def make_run(client, **rules):
    resp = client.post(
        "/api/runs",
        json={
            "name": "Marathon",
            "game_type": "auteur_marathon",
            "rules_config": {"director_id": DIRECTOR, "wildcards_budget": 1, **rules},
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def log(client, run_id, movie_id, **extra):
    return client.post(f"/api/runs/{run_id}/steps", json={"movie_id": movie_id, **extra})


def detail(client, run_id):
    return client.get(f"/api/runs/{run_id}").json()


def test_both_engines_are_registered(client):
    engines = {e["game_type"]: e for e in client.get("/api/engines").json()}
    assert engines["auteur_marathon"]["display_name"] == "The Auteur Marathon"
    assert engines["regional_deep_dive"]["display_name"] == "Regional Deep Dive"


def test_creating_a_run_stores_the_director_and_the_filmography(client):
    with respx.mock:
        mock_director()
        run_id = make_run(client)
    rules = detail(client, run_id)["rules_config"]
    assert rules["director"] == {"id": DIRECTOR, "name": "Auteur Person"}
    assert "director_id" not in rules and rules["max_skip"] == 1
    assert [f["movie_id"] for f in rules["filmography"]] == [1, 2, 3, 4, 5]
    assert rules["filmography"][0]["runtime"] == 110


def test_a_forged_filmography_is_discarded(client):
    forged = [{"movie_id": 99, "title": "Forged", "year": 1990}]
    with respx.mock:
        mock_director()
        run_id = make_run(client, filmography=forged, director={"id": 1, "name": "Fake"})
    rules = detail(client, run_id)["rules_config"]
    assert rules["director"]["name"] == "Auteur Person"
    assert [f["movie_id"] for f in rules["filmography"]] == [1, 2, 3, 4, 5]


def test_an_unknown_director_or_a_missing_id_is_rejected(client):
    with respx.mock:
        respx.get(f"{TMDB_BASE}/person/{DIRECTOR}").mock(return_value=httpx.Response(404, json={}))
        respx.get(f"{TMDB_BASE}/person/{DIRECTOR}/movie_credits").mock(
            return_value=httpx.Response(404, json={})
        )
        missing = client.post(
            "/api/runs",
            json={
                "name": "x",
                "game_type": "auteur_marathon",
                "rules_config": {"director_id": DIRECTOR},
            },
        )
    assert missing.status_code == 422
    assert (
        client.post("/api/runs", json={"name": "x", "game_type": "auteur_marathon"}).status_code
        == 422
    )
    bad = client.post(
        "/api/runs",
        json={
            "name": "x",
            "game_type": "auteur_marathon",
            "rules_config": {"director_id": DIRECTOR, "max_skip": 99},
        },
    )
    assert bad.status_code == 422


def test_films_must_follow_the_release_order_within_one_skip(client):
    with respx.mock:
        mock_director()
        run_id = make_run(client)
        assert log(client, run_id, 1).status_code == 201
        assert log(client, run_id, 3).status_code == 201  # skips film 2: allowed
        assert log(client, run_id, 5).status_code == 201  # skips film 4: allowed
    assert [s["movie_id"] for s in detail(client, run_id)["steps"]] == [1, 3, 5]


def test_skipping_too_far_or_going_back_needs_a_wildcard(client):
    with respx.mock:
        mock_director()
        run_id = make_run(client)
        too_far = log(client, run_id, 3)  # skips films 1 and 2
        assert too_far.status_code == 409 and not too_far.json()["detail"]["blocked"]
        assert "skips 2 films" in too_far.json()["detail"]["reason"]
        assert log(client, run_id, 1).status_code == 201
        assert log(client, run_id, 3).status_code == 201
        back = log(client, run_id, 2)
        assert back.status_code == 409 and "came before" in back.json()["detail"]["reason"]
        forced = log(client, run_id, 2, force=True)
        assert forced.status_code == 201 and forced.json()["transition_metadata"]["wildcard_used"]


def test_strict_mode_allows_no_skips(client):
    with respx.mock:
        mock_director()
        run_id = make_run(client, max_skip=0)
        assert log(client, run_id, 2).status_code == 409
        assert log(client, run_id, 1).status_code == 201
        assert log(client, run_id, 3).status_code == 409
        assert log(client, run_id, 2).status_code == 201


def test_a_film_off_the_filmography_is_always_blocked(client):
    with respx.mock:
        mock_director()
        run_id = make_run(client)
        for force in (False, True):
            resp = log(client, run_id, 99, force=force)
            assert resp.status_code == 409 and resp.json()["detail"]["blocked"] is True
        assert "Off the filmography" in resp.json()["detail"]["reason"]


def test_logging_the_final_film_completes_the_run(client):
    with respx.mock:
        mock_director()
        run_id = make_run(client)
        for movie_id in range(1, 6):
            assert log(client, run_id, movie_id).status_code == 201
    done = detail(client, run_id)
    assert done["status"] == "completed"
    assert done["status_reason"] == "Completed the works of Auteur Person!"


def test_person_search_can_be_limited_to_directors(client):
    results = [
        {"id": 1, "name": "An Actor", "known_for_department": "Acting", "known_for": []},
        {
            "id": 2,
            "name": "A Director",
            "known_for_department": "Directing",
            "profile_path": "/d.jpg",
            "known_for": [{"title": "X"}],
        },
    ]
    with respx.mock:
        respx.get(f"{TMDB_BASE}/search/person").mock(
            return_value=httpx.Response(200, json={"results": results})
        )
        everyone = client.get("/api/people/search", params={"q": "a"}).json()
        directors = client.get(
            "/api/people/search", params={"q": "a", "department": "Directing"}
        ).json()
    assert [p["person_id"] for p in everyone] == [1, 2]
    assert [p["person_id"] for p in directors] == [2]


# --- the Regional Deep Dive ---

# id -> (title, year, countries); list ranks follow the id.
CANON = {
    1: ("Tokyo Story", 1953, ["JP"]),
    2: ("Seven Samurai", 1954, ["JP"]),
    3: ("Cries and Whispers", 1972, ["SE"]),
    4: ("Sansho the Bailiff", 1954, ["JP"]),
    5: ("The Godfather", 1972, ["US"]),
    6: ("Ran", 1985, ["JP", "FR"]),
    7: ("Out of the List", 1953, ["JP"]),
}
LIST_ID = "list-ss22"


def seed_list(db_engine, *, enabled=True, movie_ids=(1, 2, 3, 4, 5, 6)):
    with Session(db_engine) as session:
        session.add(
            CuratedList(
                id=LIST_ID,
                title="Sight & Sound 2022",
                url="https://x",
                badge_prefix="SS22",
                badge_color="#112233",
                is_ranked=True,
                is_enabled=enabled,
            )
        )
        session.commit()
        for movie_id in movie_ids:
            session.add(
                CanonMovieBadge(
                    curated_list_id=LIST_ID,
                    movie_id=movie_id,
                    badge_label=f"SS22 #{movie_id}",
                    rank=movie_id,
                )
            )
        session.commit()


def mock_canon():
    for movie_id, (title, year, countries) in CANON.items():
        respx.get(f"{TMDB_BASE}/movie/{movie_id}").mock(
            return_value=httpx.Response(200, json=movie_json(movie_id, title, year, countries))
        )


def make_dive(client, **rules):
    resp = client.post(
        "/api/runs",
        json={
            "name": "Dive",
            "game_type": "regional_deep_dive",
            "rules_config": {"curated_list_id": LIST_ID, "wildcards_budget": 1, **rules},
        },
    )
    return resp


def test_a_country_slice_builds_the_expedition_checklist(client, db_engine):
    seed_list(db_engine)
    with respx.mock:
        mock_canon()
        resp = make_dive(client, target_country="jp")
    assert resp.status_code == 201, resp.text
    rules = detail(client, resp.json()["id"])["rules_config"]
    expedition = rules["expedition"]
    assert expedition["movie_ids"] == [1, 2, 4, 6]  # by rank
    assert expedition["country"] == "JP" and expedition["country_name"] == "Japan"
    assert expedition["decade"] is None and expedition["list_title"] == "Sight & Sound 2022"
    assert [f["badge_label"] for f in expedition["films"]] == [
        "SS22 #1",
        "SS22 #2",
        "SS22 #4",
        "SS22 #6",
    ]
    assert expedition["films"][0]["year"] == 1953 and expedition["films"][0]["rank"] == 1
    assert not {"curated_list_id", "target_country", "target_decade"} & set(rules)


def test_a_decade_slice_and_a_combined_slice(client, db_engine):
    seed_list(db_engine)
    with respx.mock:
        mock_canon()
        decade = make_dive(client, target_decade=1950)
        both = make_dive(client, target_country="JP", target_decade=1950)
        none = make_dive(client, target_country="IT")
    assert detail(client, decade.json()["id"])["rules_config"]["expedition"]["movie_ids"] == [
        1,
        2,
        4,
    ]
    combined = detail(client, both.json()["id"])["rules_config"]["expedition"]
    assert combined["movie_ids"] == [1, 2, 4] and combined["decade"] == 1950
    assert none.status_code == 422 and "No films" in none.json()["detail"]


def test_the_expedition_reuses_cached_films_instead_of_refetching(client, db_engine):
    seed_list(db_engine)
    with respx.mock:
        mock_canon()
        make_dive(client, target_country="JP")
        calls = sum(r.calls.call_count for r in respx.routes)
        make_dive(client, target_country="JP")
        assert sum(r.calls.call_count for r in respx.routes) == calls


def test_dive_setup_errors(client, db_engine):
    seed_list(db_engine, enabled=False)
    with respx.mock:
        mock_canon()
        assert make_dive(client, target_country="JP").status_code == 422  # list not enabled
        assert (
            client.post(
                "/api/runs",
                json={
                    "name": "x",
                    "game_type": "regional_deep_dive",
                    "rules_config": {"curated_list_id": "nope", "target_country": "JP"},
                },
            ).status_code
            == 422
        )
        assert make_dive(client).status_code == 422  # nothing to slice by
        assert make_dive(client, target_country="Japan").status_code == 422
        assert make_dive(client, target_decade=1955).status_code == 422
        assert (
            client.post(
                "/api/runs", json={"name": "x", "game_type": "regional_deep_dive"}
            ).status_code
            == 422
        )


def test_only_checklist_films_can_be_logged_and_completing_it_wins(client, db_engine):
    seed_list(db_engine)
    with respx.mock:
        mock_canon()
        run_id = make_dive(client, target_country="JP").json()["id"]
        for force in (False, True):
            off = log(client, run_id, 3, force=force)  # Swedish: off the slice
            assert off.status_code == 409 and off.json()["detail"]["blocked"] is True
        assert "Off the expedition" in off.json()["detail"]["reason"]
        assert log(client, run_id, 7).status_code == 409  # not on the list
        for movie_id in (6, 1, 4):  # any order
            assert log(client, run_id, movie_id).status_code == 201
            assert detail(client, run_id)["status"] == "active"
        assert log(client, run_id, 2).status_code == 201
    done = detail(client, run_id)
    assert done["status"] == "completed"
    assert (
        done["status_reason"]
        == "Expedition Complete: Conquered Japan cinema on Sight & Sound 2022!"
    )
