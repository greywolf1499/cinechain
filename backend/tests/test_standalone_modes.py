"""Phase 22a: standalone modes - no forced cast link, Chrono direction, rule-shaped pools."""

import httpx
import numpy as np
import pytest
import respx
from sqlmodel import Session

from app.models.cache import CachedMovie
from app.services import embeddings
from tests.test_algorithm_sandbox import (
    PLOTS,
    VECTORS,
    fake_model,
)
from tests.test_algorithm_sandbox import mock_universe as mock_feature_universe
from tests.test_graph_mutators import (
    TMDB_BASE,
    client,
    create_run,
    db_engine,
    log,
    mock_universe,
    movie,
    steps,
)

# Disjoint casts: nothing here can be linked through an actor.
LONERS = {
    1: movie("Start", 1990, ["US"], cast=[101]),
    2: movie("Later FR", 1995, ["FR"], cast=[102], popularity=9),
    3: movie("Earlier JP", 1980, ["JP"], cast=[103], popularity=8),
    4: movie("Later US", 2005, ["US"], cast=[104], popularity=7),
    5: movie("Same Year", 1990, ["FR"], cast=[105], popularity=6),
}


def mock_discover(universe: dict[int, dict]):
    """A TMDB /discover/movie that honours release-date and origin-country filters."""
    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        lo = params.get("primary_release_date.gte", "0000-01-01")
        hi = params.get("primary_release_date.lte", "9999-12-31")
        country = params.get("with_origin_country")
        results = [
            {"id": i, "title": m["title"], "release_date": f"{m['year']}-06-01",
             "poster_path": None, "genre_ids": [], "original_language": "en",
             "popularity": m["popularity"]}
            for i, m in universe.items()
            if lo <= f"{m['year']}-06-01" <= hi and (country is None or country in m["countries"])
        ]
        return httpx.Response(200, json={"page": 1, "total_pages": 1, "results": results})

    respx.get(f"{TMDB_BASE}/discover/movie").mock(side_effect=handler)


# --- Chrono: no cast link, both directions ---


def test_chrono_climb_links_films_that_share_no_cast(client):
    run_id = create_run(client, "chrono_climb")
    with respx.mock:
        mock_universe(LONERS)
        assert log(client, run_id, 1).status_code == 201
        resp = log(client, run_id, 2)
        assert resp.status_code == 201
        assert resp.json()["transition_metadata"] == {"year_delta": 5, "direction": "climb"}
        assert log(client, run_id, 3).status_code == 409  # earlier
    assert [s["movie_id"] for s in steps(client, run_id)] == [1, 2]


def test_chrono_descent_goes_backwards(client):
    run_id = create_run(client, "chrono_climb", direction="descent")
    with respx.mock:
        mock_universe(LONERS)
        log(client, run_id, 1)
        blocked = log(client, run_id, 2, force=True)
        assert blocked.status_code == 409 and blocked.json()["detail"]["blocked"] is True
        assert "must be released before" in blocked.json()["detail"]["reason"]
        ok = log(client, run_id, 3)
        constraint = client.get(f"/api/runs/{run_id}/constraint").json()
    assert ok.status_code == 201
    assert ok.json()["transition_metadata"] == {"year_delta": -10, "direction": "descent"}
    assert constraint["title"] == "Next film must be released before 1980"


def test_require_cast_link_turns_the_modifier_on(client):
    run_id = create_run(client, "chrono_climb", require_cast_link=True)
    with respx.mock:
        mock_universe(LONERS)
        log(client, run_id, 1)
        resp = log(client, run_id, 2)
    assert resp.status_code == 409 and resp.json()["detail"]["blocked"] is False


def test_invalid_mode_options_are_rejected(client):
    for rules in ({"direction": "sideways"}, {"require_cast_link": "yes"}):
        resp = client.post("/api/runs", json={
            "name": "x", "game_type": "chrono_climb", "rules_config": rules})
        assert resp.status_code == 422, rules


def test_mode_options_can_be_edited_and_survive(client):
    run_id = create_run(client, "chrono_climb")
    patched = client.patch(f"/api/runs/{run_id}/rules", json={
        "allow_repeats": "strict", "no_consecutive_actor": False, "max_cast_order": 15,
        "min_runtime": 0, "wildcards_budget": 2, "direction": "descent", "require_cast_link": True})
    assert patched.status_code == 200
    rules = patched.json()["rules_config"]
    assert rules["direction"] == "descent" and rules["require_cast_link"] is True
    again = client.patch(f"/api/runs/{run_id}/rules", json={
        "allow_repeats": "strict", "no_consecutive_actor": False, "max_cast_order": 15,
        "min_runtime": 0, "wildcards_budget": 2})
    assert again.json()["rules_config"]["direction"] == "descent"  # untouched when omitted


def test_chrono_pool_ignores_cast_and_orders_by_year_distance(client, db_engine):
    run_id = create_run(client, "chrono_climb")
    with respx.mock:
        mock_universe(LONERS)
        mock_discover(LONERS)  # only offers films within ten years of the frontier
        with Session(db_engine) as session:  # ...while the cache can reach further ahead
            session.add(CachedMovie(
                tmdb_id=4, title="Later US", release_date="2005-06-01", popularity=7.0,
                status="Released"))
            session.commit()
        log(client, run_id, 1)
        pool = client.get(f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 1}).json()

    assert [c["movie_id"] for c in pool] == [2, 4]  # same year / earlier films are not offered
    assert [c["year_delta"] for c in pool] == [5, 15]
    assert all(c["connections"] == [] for c in pool)


def test_chrono_descent_pool_looks_backwards(client):
    run_id = create_run(client, "chrono_climb", direction="descent")
    with respx.mock:
        mock_universe(LONERS)
        mock_discover(LONERS)
        log(client, run_id, 1)
        pool = client.get(f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 1}).json()
    assert [(c["movie_id"], c["year_delta"]) for c in pool] == [(3, -10)]


# --- Passport: different country, no cast link ---


def test_passport_links_other_countries_without_shared_cast(client):
    run_id = create_run(client, "world_passport")
    with respx.mock:
        mock_universe(LONERS)
        log(client, run_id, 1)
        blocked = log(client, run_id, 4)  # US -> US
        ok = log(client, run_id, 2)  # US -> FR
    assert blocked.status_code == 409 and "other than US" in blocked.json()["detail"]["reason"]
    assert ok.status_code == 201
    assert ok.json()["transition_metadata"] == {"from_country": "US", "to_country": "FR"}


def test_passport_pool_is_films_from_other_countries(client):
    run_id = create_run(client, "world_passport")
    with respx.mock:
        mock_universe(LONERS)
        mock_discover(LONERS)
        log(client, run_id, 1)
        pool = {c["movie_id"]: c for c in client.get(
            f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 1}).json()}

    assert set(pool) == {2, 3, 5}  # 4 is American like the frontier
    assert pool[2]["origin_country"] == '["FR"]'
    assert all(c["connections"] == [] for c in pool.values())


def test_passport_pool_survives_tmdb_failing(client):
    run_id = create_run(client, "world_passport")
    with respx.mock:
        mock_universe(LONERS)
        respx.get(f"{TMDB_BASE}/discover/movie").mock(return_value=httpx.Response(500))
        log(client, run_id, 1)
        pool = client.get(f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 1})
    assert pool.status_code == 200  # falls back to whatever the cache holds


# --- Bridge needs a cast link ---


def test_bridge_is_refused_for_standalone_runs(client):
    run_id = create_run(client, "chrono_climb")
    with respx.mock:
        mock_universe(LONERS)
        resp = client.get("/api/engine/bridge/stream", params={
            "from_movie_id": 1, "to_movie_id": 2, "game_type": "chrono_climb", "run_id": run_id})
    assert "event: error" in resp.text and "nothing to bridge" in resp.text


# --- Semantic / Aesthetic pools ---

RELATED = {
    1: {"title": "Heist One", "overview": "heist", "cast": [101], "popularity": 9},
    2: {"title": "Heist Two", "overview": "heist-ish", "cast": [102], "popularity": 8},
    3: {"title": "Love Story", "overview": "romance", "cast": [103], "popularity": 7},
}


def test_semantic_pool_is_the_best_plot_matches_with_no_cast(client, fake_model):
    run_id = create_run(client, "semantic_trope")
    with respx.mock:
        mock_feature_universe(RELATED)
        related = [{"id": i, "title": m["title"], "release_date": "2000-06-01", "poster_path": None,
                    "genre_ids": [], "original_language": "en", "popularity": m["popularity"]}
                   for i, m in RELATED.items() if i != 1]
        respx.get(f"{TMDB_BASE}/movie/1/recommendations").mock(
            return_value=httpx.Response(200, json={"results": related}))
        respx.get(f"{TMDB_BASE}/movie/1/similar").mock(
            return_value=httpx.Response(200, json={"results": []}))
        log(client, run_id, 1)
        pool = client.get(f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 1}).json()

    assert [c["movie_id"] for c in pool] == [2]  # Love Story falls below the threshold
    assert pool[0]["semantic_score"] == pytest.approx(0.8944, abs=1e-3)
    assert pool[0]["connections"] == []


def test_semantic_pool_also_uses_already_embedded_cache_rows(client, db_engine, fake_model):
    run_id = create_run(client, "semantic_trope")
    with respx.mock:
        mock_feature_universe(PLOTS)
        respx.get(f"{TMDB_BASE}/movie/1/recommendations").mock(
            return_value=httpx.Response(200, json={"results": []}))
        respx.get(f"{TMDB_BASE}/movie/1/similar").mock(
            return_value=httpx.Response(200, json={"results": []}))
        with Session(db_engine) as session:
            session.add(CachedMovie(
                tmdb_id=77, title="Cached Heist", release_date="2001-01-01", popularity=3.0,
                overview="heist-ish", overview_embedding=embeddings.encode_embedding(
                    VECTORS["heist-ish"]), status="Released"))
            session.commit()
        log(client, run_id, 1)
        pool = client.get(f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 1}).json()
    assert [c["movie_id"] for c in pool] == [77]
    assert np.isclose(pool[0]["semantic_score"], 0.8944, atol=1e-3)


def test_aesthetic_pool_is_the_closest_colours_with_no_cast(client):
    colors = {
        1: {"title": "Crimson", "color": (200, 30, 40), "cast": [101], "popularity": 1},
        2: {"title": "Rose", "color": (210, 60, 70), "cast": [102], "popularity": 5},
        3: {"title": "Azure", "color": (30, 60, 220), "cast": [103], "popularity": 4},
        4: {"title": "Brick", "color": (190, 40, 30), "cast": [104], "popularity": 3},
    }
    run_id = create_run(client, "aesthetic_gradient")
    with respx.mock:
        mock_feature_universe(colors)
        respx.get(f"{TMDB_BASE}/discover/movie").mock(return_value=httpx.Response(200, json={
            "page": 1, "total_pages": 1, "results": [
                {"id": i, "title": m["title"], "release_date": "2000-06-01",
                 "poster_path": f"/p{i}.png", "genre_ids": [], "original_language": "en",
                 "popularity": m["popularity"]} for i, m in colors.items()]}))
        log(client, run_id, 1)
        pool = client.get(f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 1}).json()

    assert [c["movie_id"] for c in pool] == [4, 2]  # closest first; Azure is too far
    assert all(c["dominant_color"] and c["connections"] == [] for c in pool)


# --- Recommend Seed Movie ---


def seed_cache(db_engine):
    from app.models.cache import CachedMovieRating
    from app.models.curated import CanonMovieBadge, CuratedList

    with Session(db_engine) as session:
        for movie_id, title, popularity in ((1, "Acclaimed", 1.0), (2, "Popular", 50.0),
                                            (3, "Rated", 2.0), (4, "No Poster", 3.0)):
            session.add(CachedMovie(
                tmdb_id=movie_id, title=title, release_date="1999-01-01", popularity=popularity,
                poster_path=None if movie_id == 4 else f"/p{movie_id}.jpg", status="Released"))
        session.flush()
        session.add(CuratedList(id="l", title="L", url="https://letterboxd.com/a/list/l/", badge_prefix="L"))
        session.flush()
        session.add(CanonMovieBadge(curated_list_id="l", movie_id=1, badge_label="SS22 #4"))
        session.add(CachedMovieRating(movie_id=3, imdb_rating="8.4"))
        session.add(CachedMovieRating(movie_id=2, imdb_rating="N/A"))
        session.commit()


def test_seed_suggestion_prefers_acclaimed_films_and_honours_rerolls(client, db_engine):
    seed_cache(db_engine)
    seen: dict[int, str] = {}
    exclude = ""
    for _ in range(2):
        resp = client.get("/api/movies/seed-suggestion", params={"exclude": exclude})
        assert resp.status_code == 200
        body = resp.json()
        seen[body["tmdb_id"]] = body["reason"]
        exclude = ",".join(str(i) for i in seen)
    assert seen.keys() == {1, 3}  # canon + rated, never the unacclaimed or poster-less ones
    assert seen[1] == "On the SS22 #4 list" and seen[3] == "IMDb 8.4"

    # Everything acclaimed has been shown: it falls back to popular films.
    fallback = client.get("/api/movies/seed-suggestion", params={"exclude": exclude}).json()
    assert fallback["tmdb_id"] == 2 and fallback["reason"] == "Popular pick"


def test_seed_suggestion_is_null_for_an_empty_cache(client):
    resp = client.get("/api/movies/seed-suggestion")
    assert resp.status_code == 200 and resp.json() is None


def test_seed_suggestion_prefers_films_the_mode_can_judge(client, db_engine):
    seed_cache(db_engine)
    with Session(db_engine) as session:
        session.get(CachedMovie, 3).origin_country = '["FR"]'
        session.commit()
    for _ in range(5):
        body = client.get("/api/movies/seed-suggestion", params={"game_type": "world_passport"}).json()
        assert body["tmdb_id"] == 3  # the only acclaimed film with a known country
