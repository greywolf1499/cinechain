"""Phase 22b: the Watchlist Bingo data endpoint."""

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app
from app.models.cache import CachedMovie, CachedMovieDirector, CachedMovieRating
from app.models.curated import CanonMovieBadge, CuratedList, LetterboxdWatchlist
from app.models.user import User
from app.utils.ids import utcnow

TMDB_BASE = "https://api.themoviedb.org/3"


@pytest.fixture()
def db_engine(config_dir):
    engine = create_engine(
        f"sqlite:///{config_dir}/app_test.db", connect_args={"check_same_thread": False}
    )
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
            json={"username": "alice", "password": "password123", "display_name": "Alice"},
        )
        test_client.post("/api/auth/login", json={"username": "alice", "password": "password123"})
        yield test_client
    app.dependency_overrides.clear()


def alice_id(db_engine) -> str:
    with Session(db_engine) as session:
        from sqlmodel import select

        return session.exec(select(User)).first().id


def seed(db_engine):
    uid = alice_id(db_engine)
    with Session(db_engine) as session:
        session.add(
            CuratedList(
                id="l", title="L", url="https://letterboxd.com/a/list/l/", badge_prefix="SS"
            )
        )
        session.add(
            CachedMovie(
                tmdb_id=1,
                title="Cached Classic",
                release_date="1962-01-01",
                runtime=82,
                original_language="fr",
                origin_country='["FR"]',
                genre_ids=[18],
                popularity=3.0,
                poster_path="/p.jpg",
                directors_fetched_at=utcnow(),
            )
        )
        session.flush()
        session.add(CachedMovieDirector(movie_id=1, person_id=9, name="Agnes", gender=1))
        session.add(CachedMovieRating(movie_id=1, imdb_rating="8.1"))
        session.add(CanonMovieBadge(curated_list_id="l", movie_id=1, badge_label="SS22 #4"))
        session.add(
            LetterboxdWatchlist(
                user_id=uid, letterboxd_username="a", movie_id=1, title="Cached Classic", year=1962
            )
        )
        session.add(
            LetterboxdWatchlist(
                user_id=uid, letterboxd_username="a", movie_id=2, title="Uncached", year=2020
            )
        )
        # someone else's watchlist must never leak in
        session.add(User(id="bob", username="bob", display_name="Bob", password_hash="x"))
        session.flush()
        session.add(
            LetterboxdWatchlist(
                user_id="bob", letterboxd_username="b", movie_id=3, title="Not Mine", year=2001
            )
        )
        session.commit()


def test_empty_watchlist_returns_an_empty_pool(client):
    body = client.get("/api/tools/bingo/watchlist").json()
    assert body == {"films": [], "total": 0, "pending": 0}


def test_watchlist_is_joined_with_the_cache_and_scoped_to_the_user(client, db_engine):
    seed(db_engine)
    body = client.get("/api/tools/bingo/watchlist").json()

    assert body["total"] == 2 and body["pending"] == 1  # the uncached film still needs detail
    films = {f["movie_id"]: f for f in body["films"]}
    assert 3 not in films
    classic = films[1]
    assert classic["runtime"] == 82 and classic["original_language"] == "fr"
    assert classic["imdb_rating"] == 8.1 and classic["canon_badges"] == ["SS22 #4"]
    assert classic["directed_by_woman"] is True and classic["year"] == 1962
    uncached = films[2]
    assert uncached["title"] == "Uncached" and uncached["year"] == 2020
    assert uncached["runtime"] is None and uncached["directed_by_woman"] is None


def test_hydrate_fills_in_detail_directors_and_gender(client, db_engine):
    seed(db_engine)
    with respx.mock:
        respx.get(f"{TMDB_BASE}/movie/2").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 2,
                    "title": "Uncached",
                    "release_date": "2020-05-01",
                    "poster_path": None,
                    "overview": "",
                    "origin_country": ["JP"],
                    "original_language": "ja",
                    "runtime": 120,
                    "genres": [{"id": 27, "name": "Horror"}],
                    "popularity": 4.0,
                    "status": "Released",
                },
            )
        )
        respx.get(f"{TMDB_BASE}/movie/2/credits").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 2,
                    "cast": [],
                    "crew": [
                        {"id": 77, "name": "Sofia", "job": "Director", "gender": 1},
                        {"id": 78, "name": "Writer", "job": "Writer", "gender": 2},
                    ],
                },
            )
        )
        body = client.get("/api/tools/bingo/watchlist", params={"hydrate": 5}).json()

    films = {f["movie_id"]: f for f in body["films"]}
    assert films[2]["runtime"] == 120 and films[2]["genre_ids"] == [27]
    assert films[2]["original_language"] == "ja" and films[2]["directed_by_woman"] is True
    assert body["pending"] == 0


def test_hydrate_budget_and_tmdb_failures_do_not_break_the_board(client, db_engine):
    seed(db_engine)
    with respx.mock:
        respx.get(f"{TMDB_BASE}/movie/2").mock(return_value=httpx.Response(500))
        resp = client.get("/api/tools/bingo/watchlist", params={"hydrate": 5})
    assert resp.status_code == 200
    assert resp.json()["pending"] == 1  # still pending, but the pool is returned


def test_men_directing_is_false_and_unspecified_gender_is_unknown(client, db_engine):
    uid = alice_id(db_engine)
    with Session(db_engine) as session:
        for movie_id, gender in ((10, 2), (11, 0)):
            session.add(
                CachedMovie(
                    tmdb_id=movie_id,
                    title=f"M{movie_id}",
                    runtime=100,
                    directors_fetched_at=utcnow(),
                )
            )
            session.flush()
            session.add(
                CachedMovieDirector(movie_id=movie_id, person_id=movie_id, name="D", gender=gender)
            )
            session.add(
                LetterboxdWatchlist(
                    user_id=uid, letterboxd_username="a", movie_id=movie_id, title=f"M{movie_id}"
                )
            )
        session.commit()
    films = {f["movie_id"]: f for f in client.get("/api/tools/bingo/watchlist").json()["films"]}
    assert films[10]["directed_by_woman"] is False
    assert films[11]["directed_by_woman"] is None


def test_bingo_requires_login(db_engine):
    def override_get_session():
        with Session(db_engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(app) as anonymous:
            assert anonymous.get("/api/tools/bingo/watchlist").status_code == 401
    finally:
        app.dependency_overrides.clear()


# --- F5b: server-side squares and stamps -------------------------------------------------


def squares_by_id(client) -> dict:
    resp = client.get("/api/tools/bingo/squares")
    assert resp.status_code == 200
    return {s["id"]: s for s in resp.json()["squares"]}


def test_squares_keep_their_ids_and_read_named_variant_thresholds(client):
    from app.facets.registry import named_variants

    squares = squares_by_id(client)
    assert {"classic", "short", "epic", "canon", "woman-director", "scifi", "decade-1960"} <= set(
        squares
    )
    assert len(squares) == 34
    variants = named_variants()
    assert squares["short"]["query"] == variants["short"]["query"]
    assert squares["hidden-gem"]["query"] == variants["hidden_gem"]["query"]
    assert str(variants["short"]["query"]["value"]) in squares["short"]["hint"]
    assert squares["canon"]["query"] == {"facet": "canon", "op": "eq", "value": True}
    assert squares["scifi"]["query"] == {"facet": "genre", "op": "contains", "value": 878}


def test_square_matches_come_from_the_callers_watchlist_only(client, db_engine):
    seed(db_engine)
    with Session(db_engine) as session:
        # Bob's film fits everything Alice's classic does; it must never appear.
        session.add(
            CachedMovie(
                tmdb_id=3,
                title="Not Mine",
                release_date="1961-01-01",
                runtime=80,
                original_language="fr",
                origin_country='["FR"]',
                genre_ids=[18],
                popularity=1.0,
            )
        )
        session.add(CanonMovieBadge(curated_list_id="l", movie_id=3, badge_label="SS22 #9"))
        session.commit()
    squares = squares_by_id(client)

    for square_id in (
        "classic",
        "short",
        "non-english",
        "imdb-high",
        "canon",
        "woman-director",
        "hidden-gem",
        "european",
        "not-us-uk",
        "decade-1960",
    ):
        assert squares[square_id]["matches"] == [1], square_id
    assert squares["epic"]["matches"] == [] and squares["horror"]["matches"] == []
    assert squares["asian"]["matches"] == []
    # Film 2 isn't cached, so the server can't decide it yet; Bob's film is never counted.
    assert squares["epic"]["unknown"] == 1
    assert all(3 not in s["matches"] for s in squares.values())


def test_squares_for_an_empty_watchlist_have_no_matches(client):
    squares = squares_by_id(client)
    assert all(s["matches"] == [] and s["unknown"] == 0 for s in squares.values())


def test_grid_board_reuses_tool_generator_and_stamps_its_server_query(client, db_engine, monkeypatch):
    seed(db_engine)
    from app.api import routes_tools

    calls = []

    def generated_board(session, user_id, *, seed):
        calls.append((user_id, seed))
        return {
            "cells": [
                {
                    "id": "0:0",
                    "label": "Classic",
                    "query": {"facet": "release_year", "op": "lt", "value": 1970},
                }
            ]
        }

    monkeypatch.setattr(routes_tools, "tool_board", generated_board)
    board = client.get("/api/tools/bingo/grid")
    assert board.status_code == 200, board.text
    square = board.json()["squares"][0]
    assert square["id"] == "0:0" and square["matches"] == [1]
    stamp = client.post("/api/tools/bingo/stamp", json={"square_id": "0:0", "movie_id": 1})
    assert stamp.status_code == 200 and stamp.json()["valid"] is True
    assert len(calls) == 2
    assert calls[0] == calls[1]


def test_stamp_is_validated_against_the_server_square(client, db_engine):
    seed(db_engine)
    ok = client.post("/api/tools/bingo/stamp", json={"square_id": "classic", "movie_id": 1})
    assert ok.status_code == 200 and ok.json()["valid"] is True and ok.json()["verdict"] is True

    wrong = client.post("/api/tools/bingo/stamp", json={"square_id": "epic", "movie_id": 1}).json()
    assert wrong["valid"] is False and wrong["verdict"] is False

    unknown = client.post(
        "/api/tools/bingo/stamp", json={"square_id": "classic", "movie_id": 2}
    ).json()
    assert unknown["valid"] is False and unknown["verdict"] is None


def test_stamp_rejects_other_users_films_unknown_squares_and_client_queries(client, db_engine):
    seed(db_engine)
    with Session(db_engine) as session:
        session.add(CachedMovie(tmdb_id=3, title="Not Mine", release_date="1950-01-01"))
        session.commit()
    foreign = client.post("/api/tools/bingo/stamp", json={"square_id": "classic", "movie_id": 3})
    assert foreign.status_code == 200 and foreign.json()["valid"] is False

    missing = client.post("/api/tools/bingo/stamp", json={"square_id": "nope", "movie_id": 1})
    assert missing.status_code == 404

    forged = client.post(
        "/api/tools/bingo/stamp",
        json={
            "square_id": "epic",
            "movie_id": 1,
            "query": {"facet": "runtime", "op": "gt", "value": 0},
            "matches": [1],
        },
    )
    assert forged.status_code == 422


def test_outside_us_uk_needs_a_known_non_empty_country_list(client, db_engine):
    uid = alice_id(db_engine)
    with Session(db_engine) as session:
        for movie_id, countries in ((20, "[]"), (21, None), (22, '["US", "FR"]'), (23, '["JP"]')):
            session.add(
                CachedMovie(tmdb_id=movie_id, title=f"C{movie_id}", origin_country=countries)
            )
            session.add(
                LetterboxdWatchlist(
                    user_id=uid, letterboxd_username="a", movie_id=movie_id, title=f"C{movie_id}"
                )
            )
        session.commit()
    square = squares_by_id(client)["not-us-uk"]
    assert square["matches"] == [23]

    def stamp(movie_id):
        return client.post(
            "/api/tools/bingo/stamp", json={"square_id": "not-us-uk", "movie_id": movie_id}
        ).json()

    assert stamp(23)["valid"] is True
    assert stamp(20)["valid"] is False  # known empty: no evidence it's non-US/UK
    assert stamp(21)["valid"] is False and stamp(21)["verdict"] is None  # unknown countries
    assert stamp(22)["valid"] is False and stamp(22)["verdict"] is False


def test_squares_and_stamps_require_login(db_engine):
    def override_get_session():
        with Session(db_engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(app) as anonymous:
            assert anonymous.get("/api/tools/bingo/squares").status_code == 401
            assert (
                anonymous.post(
                    "/api/tools/bingo/stamp", json={"square_id": "classic", "movie_id": 1}
                ).status_code
                == 401
            )
    finally:
        app.dependency_overrides.clear()


def test_zero_or_missing_runtime_never_fills_short_or_epic(client, db_engine):
    uid = alice_id(db_engine)
    with Session(db_engine) as session:
        for movie_id, runtime in ((30, 0), (31, None), (32, 80), (33, 170)):
            session.add(CachedMovie(tmdb_id=movie_id, title=f"R{movie_id}", runtime=runtime))
            session.add(
                LetterboxdWatchlist(
                    user_id=uid, letterboxd_username="a", movie_id=movie_id, title=f"R{movie_id}"
                )
            )
        session.commit()
    squares = squares_by_id(client)
    assert squares["short"]["matches"] == [32]
    assert squares["epic"]["matches"] == [33]
    zero = client.post("/api/tools/bingo/stamp", json={"square_id": "short", "movie_id": 30}).json()
    assert zero["valid"] is False
