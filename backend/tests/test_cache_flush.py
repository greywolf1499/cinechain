"""Stale cache eviction: what is dropped, what is kept, and who may trigger it."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import get_session
from app.main import app
from app.models.cache import CachedActor, CachedMovie, CachedMovieCast, CachedMovieRating
from app.services import cache_flush
from app.utils.ids import utcnow

OLD = utcnow() - timedelta(days=30)
FRESH = utcnow() - timedelta(days=1)


@pytest.fixture()
def engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path}/flush.db", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _seed(session):
    # Movie 1: stale cast (actors 10, 11). Movie 2: fresh cast (actors 11, 12).
    session.add_all(
        [
            CachedMovie(tmdb_id=1, title="Stale", cast_fetched_at=OLD, overview="kept"),
            CachedMovie(tmdb_id=2, title="Fresh", cast_fetched_at=FRESH),
            CachedMovie(tmdb_id=3, title="Never fetched cast"),
            CachedActor(tmdb_id=10, name="Only in stale", credits_fetched_at=FRESH),
            CachedActor(tmdb_id=11, name="Shared", credits_fetched_at=FRESH),
            CachedActor(tmdb_id=12, name="Only in fresh", credits_fetched_at=OLD),
            CachedMovieRating(movie_id=1, imdb_rating="8", fetched_at=OLD),
            CachedMovieRating(movie_id=2, imdb_rating="7", fetched_at=FRESH),
        ]
    )
    session.flush()
    session.add_all(
        [
            CachedMovieCast(movie_id=1, actor_id=10, cast_order=0),
            CachedMovieCast(movie_id=1, actor_id=11, cast_order=1),
            CachedMovieCast(movie_id=2, actor_id=11, cast_order=0),
            CachedMovieCast(movie_id=2, actor_id=12, cast_order=1),
        ]
    )
    session.commit()


def test_flush_drops_only_stale_entries_and_keeps_fresh_data(engine):
    with Session(engine) as session:
        _seed(session)
        counts = cache_flush.flush_stale_cache(session, max_age_days=7)

        assert counts == {
            "ratings_removed": 1,
            "movies_cast_reset": 1,
            "cast_edges_removed": 2,
            "actors_marked_stale": 1,
            "actors_removed": 1,  # actor 10 only lived in the stale movie
        }
        movies = {m.tmdb_id: m for m in session.exec(select(CachedMovie)).all()}
        assert (
            movies[1].cast_fetched_at is None and movies[1].overview == "kept"
        )  # detail row survives
        assert movies[2].cast_fetched_at is not None
        assert {r.movie_id for r in session.exec(select(CachedMovieRating)).all()} == {2}
        actors = {a.tmdb_id: a for a in session.exec(select(CachedActor)).all()}
        assert set(actors) == {11, 12}
        # 11 lost an edge (credit list incomplete) and 12's credits were stale: both refetch.
        assert actors[11].credits_fetched_at is None and actors[12].credits_fetched_at is None
        edges = {(e.movie_id, e.actor_id) for e in session.exec(select(CachedMovieCast)).all()}
        assert edges == {(2, 11), (2, 12)}


def test_flush_on_an_empty_or_fresh_cache_is_a_noop(engine):
    with Session(engine) as session:
        assert set(cache_flush.flush_stale_cache(session).values()) == {0}
        session.add(CachedMovie(tmdb_id=5, title="x", cast_fetched_at=FRESH))
        session.add(CachedActor(tmdb_id=50, name="a", credits_fetched_at=FRESH))
        session.commit()
        assert set(cache_flush.flush_stale_cache(session).values()) == {0}


def test_flushed_cast_is_reported_as_uncached_so_it_refetches(engine):
    from app.services.cache_repo import CacheRepo

    with Session(engine) as session:
        _seed(session)
        repo = CacheRepo(session)
        assert repo.get_cached_cast(1, 15) is not None
        cache_flush.flush_stale_cache(session)
        assert repo.get_cached_cast(1, 15) is None
        assert repo.get_cached_actor_credits(11) is None


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
        test_client.db_engine = engine
        yield test_client
    app.dependency_overrides.clear()


def _login(client, username):
    client.post("/api/auth/login", json={"username": username, "password": "password123"})


def test_flush_endpoint_is_admin_only_and_reports_counts(client):
    assert client.post("/api/system/cache/flush").status_code == 401
    client.post(
        "/api/auth/register",
        json={"username": "alice", "password": "password123", "display_name": "A"},
    )
    _login(client, "alice")
    client.post(
        "/api/auth/register",
        json={"username": "bob", "password": "password123", "display_name": "B"},
    )
    with Session(client.db_engine) as session:
        _seed(session)

    with TestClient(app) as bob:
        _login(bob, "bob")
        assert bob.post("/api/system/cache/flush").status_code == 403

    resp = client.post("/api/system/cache/flush", params={"max_age_days": 7})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ratings_removed"] == 1 and body["cast_edges_removed"] == 2
    assert body["max_age_days"] == 7
    assert body["vacuumed"] is True
    assert client.post("/api/system/cache/flush", params={"max_age_days": 0}).status_code == 422
