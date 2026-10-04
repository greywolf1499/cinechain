"""Phase 28a: the Perfect Marathon Router (whiplash cost, optimizer, API)."""

import itertools
import random
import time

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import get_session
from app.main import app
from app.models.cache import CachedGenre, CachedMovie, CachedMovieRating
from app.models.run import Run, RunStep
from app.services import marathon_router as mr


def film(movie_id, genres=(18,), year=2000, runtime=100, rating=7.0):
    return mr.RouterFilm(movie_id, f"F{movie_id}", tuple(genres), year, runtime, rating)


def random_films(n, seed):
    rng = random.Random(seed)
    return [
        film(i + 1, rng.sample(range(1, 15), rng.randint(1, 3)), rng.randint(1930, 2024),
             rng.randint(70, 190), rng.uniform(3, 9))
        for i in range(n)]


def held_karp(matrix):
    n = len(matrix)
    inf = float("inf")
    best = [[inf] * n for _ in range(1 << n)]
    for i in range(n):
        best[1 << i][i] = 0.0
    for mask in range(1 << n):
        for last in range(n):
            if best[mask][last] == inf:
                continue
            for nxt in range(n):
                if not mask >> nxt & 1:
                    cost = best[mask][last] + matrix[last][nxt]
                    best[mask | 1 << nxt][nxt] = min(best[mask | 1 << nxt][nxt], cost)
    return min(best[(1 << n) - 1])


# --- cost function ---------------------------------------------------------


def test_identical_films_cost_nothing():
    a = film(1, (18, 35), 1990, 100, 7.0)
    assert mr.whiplash_cost(a, film(2, (18, 35), 1990, 100, 7.0)) == pytest.approx(0.0)


def test_maximally_different_films_cost_one():
    a = film(1, (18,), 1920, 60, 2.0)
    b = film(2, (35,), 2020, 200, 9.0)
    assert mr.whiplash_cost(a, b) == pytest.approx(1.0)


def test_cost_blends_each_component_with_its_weight():
    a = film(1, (1, 2), 2000, 100, 6.0)
    b = film(2, (2, 3), 2025, 130, 8.5)
    expected = 0.40 * (1 - 1 / 3) + 0.25 * (25 / 50) + 0.15 * (30 / 60) + 0.20 * (2.5 / 5)
    assert mr.whiplash_cost(a, b) == pytest.approx(expected)


def test_component_deltas_are_capped_at_one():
    d = mr.component_deltas(film(1, (1,), 1900, 50, 0.0), film(2, (1,), 2024, 400, 10.0))
    assert (d.genre, d.year, d.runtime, d.rating) == (0.0, 1.0, 1.0, 1.0)


def test_cost_is_symmetric():
    a, b = film(1, (1,), 1980, 90, 6.5), film(2, (2, 1), 2015, 150, 8.0)
    assert mr.whiplash_cost(a, b) == pytest.approx(mr.whiplash_cost(b, a))


def test_missing_data_scores_neutral():
    d = mr.component_deltas(film(1, (), None, None, None), film(2, (1,), 2000, 100, 7.0))
    assert (d.genre, d.year, d.runtime, d.rating) == (0.5, 0.5, 0.5, 0.5)


def test_weights_are_normalised_and_validated():
    w = mr.Weights(2, 1, 1, 0).normalised()
    assert (w.genre, w.year, w.runtime, w.rating) == (0.5, 0.25, 0.25, 0.0)
    with pytest.raises(ValueError):
        mr.Weights(0, 0, 0, 0).normalised()
    with pytest.raises(ValueError):
        mr.Weights(-1, 1, 1, 1).normalised()


# --- optimizer -------------------------------------------------------------


def test_input_size_is_bounded():
    with pytest.raises(ValueError):
        mr.optimize(random_films(3, 0))
    with pytest.raises(ValueError):
        mr.optimize(random_films(26, 0))
    with pytest.raises(ValueError):
        mr.optimize([film(1), film(1), film(2), film(3)])


def test_small_sets_are_solved_exactly():
    for seed in range(5):
        films = random_films(7, seed)
        result = mr.optimize(films)
        matrix = mr.cost_matrix(films, mr.Weights())
        assert result.method == mr.METHOD_EXACT
        assert result.optimized_whiplash_score == pytest.approx(held_karp(matrix), abs=1e-3)


def test_exact_search_finds_the_obvious_order():
    # years 1990, 2020, 1991, 2021 are only smooth when sorted
    films = [film(1, year=1990), film(2, year=2020), film(3, year=1991), film(4, year=2021)]
    result = mr.optimize(films, mr.Weights(0, 1, 0, 0))
    assert result.ordered_movie_ids in ([1, 3, 2, 4], [4, 2, 3, 1])
    assert result.improvement_percentage > 60  # the 1991 -> 2020 jump is unavoidable


def test_larger_sets_use_annealing_and_land_near_the_optimum():
    for seed in range(4):
        films = random_films(10, seed)
        result = mr.optimize(films)
        matrix = mr.cost_matrix(films, mr.Weights())
        assert result.method == mr.METHOD_ANNEALING
        assert result.optimized_whiplash_score <= held_karp(matrix) * 1.03 + 1e-3


def test_result_is_a_permutation_with_consistent_scores():
    films = random_films(15, 3)
    result = mr.optimize(films)
    assert sorted(result.ordered_movie_ids) == sorted(f.movie_id for f in films)
    assert result.optimized_whiplash_score <= result.initial_whiplash_score
    assert len(result.transitions) == 14
    assert sum(t.cost for t in result.transitions) == pytest.approx(
        result.optimized_whiplash_score, abs=0.01)
    expected = (result.initial_whiplash_score - result.optimized_whiplash_score)
    expected = expected / result.initial_whiplash_score * 100
    assert result.improvement_percentage == pytest.approx(expected, abs=0.1)
    assert [(t.from_movie_id, t.to_movie_id) for t in result.transitions] == list(
        itertools.pairwise(result.ordered_movie_ids))


def test_already_smooth_input_is_left_alone():
    films = [film(i + 1, year=1990 + i) for i in range(10)]
    result = mr.optimize(films, mr.Weights(0, 1, 0, 0))
    assert result.ordered_movie_ids == list(range(1, 11))
    assert result.improvement_percentage == 0.0


def test_identical_films_have_nothing_to_improve():
    result = mr.optimize([film(i + 1) for i in range(5)])
    assert result.initial_whiplash_score == 0 and result.improvement_percentage == 0.0
    assert result.ordered_movie_ids == [1, 2, 3, 4, 5]


def test_optimizer_is_deterministic():
    films = random_films(20, 9)
    assert mr.optimize(films).ordered_movie_ids == mr.optimize(films).ordered_movie_ids


def test_weights_steer_the_order():
    films = random_films(9, 5)
    by_year = mr.optimize(films, mr.Weights(0, 1, 0, 0))
    by_runtime = mr.optimize(films, mr.Weights(0, 0, 1, 0))
    year_of = {f.movie_id: f.year for f in films}
    runtime_of = {f.movie_id: f.runtime for f in films}

    def span(order, values):
        return sum(abs(values[a] - values[b]) for a, b in itertools.pairwise(order))

    assert span(by_year.ordered_movie_ids, year_of) < span(by_runtime.ordered_movie_ids, year_of)
    assert span(by_runtime.ordered_movie_ids, runtime_of) < span(by_year.ordered_movie_ids, runtime_of)


def test_worst_case_runs_inside_the_budget():
    films = random_films(25, 1)
    mr.optimize(films)  # warm up
    started = time.perf_counter()
    mr.optimize(films)
    assert time.perf_counter() - started < 0.25


def test_transition_labels_and_summaries():
    names = {878: "Science Fiction", 35: "Comedy", 18: "Drama"}
    a = film(1, (878,), 1984, 110, 7.0)
    smooth = film(2, (878, 18), 1986, 112, 7.2)
    jarring = film(3, (35,), 1984, 110, 7.0)
    t1, t2 = mr.build_transitions([a, smooth, jarring], mr.Weights(), names)
    assert t1.label == mr.LABEL_SMOOTH and t1.summary == "1980s Science Fiction Harmony"
    assert t2.label in (mr.LABEL_GENTLE, mr.LABEL_WHIPLASH)
    assert "Comedy" in t2.summary
    assert mr.label_for(0.1) == mr.LABEL_SMOOTH
    assert mr.label_for(0.4) == mr.LABEL_GENTLE
    assert mr.label_for(0.9) == mr.LABEL_WHIPLASH


def test_summary_names_the_biggest_gap():
    a = film(1, (18,), 1950, 90, 7.0)
    b = film(2, (18,), 2020, 90, 7.0)
    (t,) = mr.build_transitions([a, b], mr.Weights(0, 1, 0, 0), {})
    assert t.summary.startswith("1950s") and "2020s" in t.summary


# --- API -------------------------------------------------------------------


@pytest.fixture()
def db_engine(config_dir):
    engine = create_engine(
        f"sqlite:///{config_dir}/app_test.db", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture()
def client(db_engine):
    def override_get_session():
        with Session(db_engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        test_client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password123", "display_name": "Alice"})
        test_client.post("/api/auth/login", json={"username": "alice", "password": "password123"})
        yield test_client
    app.dependency_overrides.clear()


def seed_movies(db_engine, n=10):
    with Session(db_engine) as session:
        session.add(CachedGenre(id=18, name="Drama"))
        session.add(CachedGenre(id=35, name="Comedy"))
        session.add(CachedGenre(id=878, name="Science Fiction"))
        for i in range(1, n + 1):
            session.add(CachedMovie(
                tmdb_id=i, title=f"Movie {i}", release_date=f"{1950 + i * 7}-01-01",
                runtime=80 + (i * 37) % 90, genre_ids=[[18, 35, 878][i % 3]],
                vote_average=3.0 + i % 6, poster_path=f"/p{i}.jpg", overview=f"Plot {i}"))
        session.commit()
        session.add(CachedMovieRating(movie_id=1, imdb_rating="8.0"))
        session.commit()


def test_optimize_requires_login(db_engine):
    def override_get_session():
        with Session(db_engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(app) as anon:
            response = anon.post("/api/tools/router/optimize", json={"movie_ids": [1, 2, 3, 4]})
        assert response.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_optimize_returns_the_sequence_and_metrics(client, db_engine):
    seed_movies(db_engine, 6)
    response = client.post("/api/tools/router/optimize", json={"movie_ids": [1, 2, 3, 4, 5, 6]})
    assert response.status_code == 200
    body = response.json()
    assert sorted(body["ordered_movie_ids"]) == [1, 2, 3, 4, 5, 6]
    assert body["optimized_whiplash_score"] <= body["initial_whiplash_score"]
    assert body["method"] == "exact"
    assert len(body["transitions"]) == 5
    assert {t["label"] for t in body["transitions"]} <= {
        "Smooth Transition", "Gentle Shift", "Tonal Whiplash"}
    assert [f["movie_id"] for f in body["films"]] == body["ordered_movie_ids"]
    assert body["films"][0]["genres"] and body["films"][0]["poster_path"]
    assert sum(body["weights"].values()) == pytest.approx(1.0, abs=1e-3)
    assert body["calculation_ms"] < 250


def test_optimize_uses_annealing_above_eight_films(client, db_engine):
    seed_movies(db_engine, 12)
    body = client.post(
        "/api/tools/router/optimize", json={"movie_ids": list(range(1, 13))}).json()
    assert body["method"] == "simulated_annealing" and len(body["ordered_movie_ids"]) == 12


def test_optimize_prefers_imdb_rating_over_tmdb_score(client, db_engine):
    seed_movies(db_engine, 4)
    films = client.post(
        "/api/tools/router/optimize", json={"movie_ids": [1, 2, 3, 4]}).json()["films"]
    assert next(f for f in films if f["movie_id"] == 1)["rating"] == 8.0
    assert next(f for f in films if f["movie_id"] == 2)["rating"] == 5.0  # TMDB vote_average


def test_weight_overrides_are_normalised(client, db_engine):
    seed_movies(db_engine, 5)
    body = client.post("/api/tools/router/optimize", json={
        "movie_ids": [1, 2, 3, 4, 5], "weight_genre": 1, "weight_year": 0,
        "weight_runtime": 0, "weight_rating": 0}).json()
    assert body["weights"] == {
        "weight_genre": 1.0, "weight_year": 0.0, "weight_runtime": 0.0, "weight_rating": 0.0}


def test_optimize_validation(client, db_engine):
    seed_movies(db_engine, 6)
    post = client.post
    assert post("/api/tools/router/optimize", json={"movie_ids": [1, 2, 3]}).status_code == 422
    assert post(
        "/api/tools/router/optimize", json={"movie_ids": list(range(1, 27))}).status_code == 422
    assert post("/api/tools/router/optimize", json={"movie_ids": [1, 1, 2, 3]}).status_code == 422
    assert post("/api/tools/router/optimize", json={
        "movie_ids": [1, 2, 3, 4], "weight_genre": -1}).status_code == 422
    assert post("/api/tools/router/optimize", json={
        "movie_ids": [1, 2, 3, 4], "weight_genre": 0, "weight_year": 0, "weight_runtime": 0,
        "weight_rating": 0}).status_code == 422


def test_convert_to_run_creates_planned_steps_in_order(client, db_engine):
    seed_movies(db_engine, 6)
    order = [4, 2, 6, 1, 3]
    response = client.post(
        "/api/tools/router/convert-to-run", json={"run_name": " Smooth Night ", "movie_ids": order})
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Smooth Night" and body["status"] == "active"
    assert [s["movie_id"] for s in body["steps"]] == order
    assert all(s["status"] == "planned" and s["watched_at"] is None for s in body["steps"])
    assert body["steps"][0]["transition_metadata"] is None
    assert body["steps"][1]["transition_metadata"]["marathon_router"]["label"]

    with Session(db_engine) as session:
        run = session.get(Run, body["id"])
        assert run.game_type == "roulette"
        assert len(session.exec(select(RunStep).where(RunStep.run_id == run.id)).all()) == 5

    listed = client.get("/api/runs").json()
    assert any(r["id"] == body["id"] for r in listed)


def test_converted_steps_can_be_marked_watched(client, db_engine):
    seed_movies(db_engine, 4)
    run = client.post(
        "/api/tools/router/convert-to-run", json={"run_name": "M", "movie_ids": [3, 1, 2]}).json()
    step = run["steps"][1]
    response = client.patch(f"/api/runs/{run['id']}/steps/{step['id']}/mark-watched", json={})
    assert response.status_code == 200 and response.json()["status"] == "watched"


def test_convert_to_run_validation(client, db_engine):
    seed_movies(db_engine, 3)
    post = client.post
    assert post("/api/tools/router/convert-to-run", json={
        "run_name": "", "movie_ids": [1, 2]}).status_code == 422
    assert post("/api/tools/router/convert-to-run", json={
        "run_name": "   ", "movie_ids": [1, 2]}).status_code == 422
    assert post("/api/tools/router/convert-to-run", json={
        "run_name": "x", "movie_ids": [1]}).status_code == 422
    assert post("/api/tools/router/convert-to-run", json={
        "run_name": "x", "movie_ids": [1, 1]}).status_code == 422


def test_optimize_with_fewer_than_two_movies_is_a_friendly_400(client, db_engine):
    for ids in ([], [1]):
        resp = client.post("/api/tools/router/optimize", json={"movie_ids": ids})
        assert resp.status_code == 400
        assert "at least 2" in resp.json()["detail"]
