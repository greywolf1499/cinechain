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
        test_client.post(
            "/api/auth/login", json={"username": "alice", "password": "password123"})
        yield test_client
    app.dependency_overrides.clear()


def alice_id(db_engine) -> str:
    with Session(db_engine) as session:
        from sqlmodel import select
        return session.exec(select(User)).first().id


def seed(db_engine):
    uid = alice_id(db_engine)
    with Session(db_engine) as session:
        session.add(CuratedList(id="l", title="L", url="https://letterboxd.com/a/list/l/", badge_prefix="SS"))
        session.add(CachedMovie(
            tmdb_id=1, title="Cached Classic", release_date="1962-01-01", runtime=82,
            original_language="fr", origin_country='["FR"]', genre_ids=[18], popularity=3.0,
            poster_path="/p.jpg", directors_fetched_at=utcnow()))
        session.flush()
        session.add(CachedMovieDirector(movie_id=1, person_id=9, name="Agnes", gender=1))
        session.add(CachedMovieRating(movie_id=1, imdb_rating="8.1"))
        session.add(CanonMovieBadge(curated_list_id="l", movie_id=1, badge_label="SS22 #4"))
        session.add(LetterboxdWatchlist(user_id=uid, letterboxd_username="a", movie_id=1,
                                        title="Cached Classic", year=1962))
        session.add(LetterboxdWatchlist(user_id=uid, letterboxd_username="a", movie_id=2,
                                        title="Uncached", year=2020))
        # someone else's watchlist must never leak in
        session.add(User(id="bob", username="bob", display_name="Bob", password_hash="x"))
        session.flush()
        session.add(LetterboxdWatchlist(user_id="bob", letterboxd_username="b", movie_id=3,
                                        title="Not Mine", year=2001))
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
        respx.get(f"{TMDB_BASE}/movie/2").mock(return_value=httpx.Response(200, json={
            "id": 2, "title": "Uncached", "release_date": "2020-05-01", "poster_path": None,
            "overview": "", "origin_country": ["JP"], "original_language": "ja", "runtime": 120,
            "genres": [{"id": 27, "name": "Horror"}], "popularity": 4.0, "status": "Released"}))
        respx.get(f"{TMDB_BASE}/movie/2/credits").mock(return_value=httpx.Response(200, json={
            "id": 2, "cast": [], "crew": [
                {"id": 77, "name": "Sofia", "job": "Director", "gender": 1},
                {"id": 78, "name": "Writer", "job": "Writer", "gender": 2}]}))
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
            session.add(CachedMovie(tmdb_id=movie_id, title=f"M{movie_id}", runtime=100,
                                    directors_fetched_at=utcnow()))
            session.flush()
            session.add(CachedMovieDirector(movie_id=movie_id, person_id=movie_id, name="D", gender=gender))
            session.add(LetterboxdWatchlist(user_id=uid, letterboxd_username="a", movie_id=movie_id,
                                            title=f"M{movie_id}"))
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
