"""Movie/people API tests.

Reuses the dependency_overrides pattern from test_runs_api.py.
"""

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app

TMDB_BASE = "https://api.themoviedb.org/3"


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
        yield test_client
    app.dependency_overrides.clear()


def _register_and_login(client, username="alice"):
    client.post(
        "/api/auth/register",
        json={"username": username, "password": "password123", "display_name": username.title()},
    )
    client.post("/api/auth/login", json={"username": username, "password": "password123"})


def test_search_movies(client):
    _register_and_login(client)
    with respx.mock:
        respx.get(f"{TMDB_BASE}/search/movie").mock(
            return_value=httpx.Response(
                200,
                json={
                    "page": 1,
                    "total_pages": 3,
                    "total_results": 50,
                    "results": [
                        {
                            "id": 603,
                            "title": "The Matrix",
                            "release_date": "1999-03-30",
                            "poster_path": "/poster.jpg",
                            "genre_ids": [28],
                            "original_language": "en",
                        }
                    ],
                },
            )
        )
        resp = client.get("/api/movies/search", params={"q": "matrix"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["page"] == 1
    assert body["total_pages"] == 3
    assert body["results"][0]["tmdb_id"] == 603
    assert body["results"][0]["release_year"] == 1999
    assert body["results"][0]["origin_country"] is None
    assert body["results"][0]["origin_countries"] == []


def test_get_movie_detail(client):
    _register_and_login(client)
    with respx.mock:
        respx.get(f"{TMDB_BASE}/movie/603").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 603,
                    "title": "The Matrix",
                    "release_date": "1999-03-30",
                    "poster_path": "/poster.jpg",
                    "overview": "A hacker learns the truth.",
                    "origin_country": ["US"],
                    "original_language": "en",
                    "runtime": 136,
                    "genres": [{"id": 28, "name": "Action"}],
                },
            )
        )
        resp = client.get("/api/movies/603")

    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "The Matrix"
    assert body["runtime"] == 136
    assert body["genre_ids"] == [28]
    assert body["origin_countries"] == ["US"]


def test_country_list_contracts_preserve_legacy_strings():
    from app.schemas.discovery import DiscoveryCandidate
    from app.schemas.engine import RouletteMovie, Suggestion
    from app.schemas.movies import MovieDetail, MovieSummary
    from app.schemas.runs import RunStepPublic
    from app.utils.ids import utcnow

    base = {"title": "Legacy", "origin_country": "US, GB"}
    for movie in (
        MovieSummary(tmdb_id=1, **base),
        MovieDetail(tmdb_id=1, **base),
        DiscoveryCandidate(movie_id=1, **base),
        RouletteMovie(tmdb_id=1, **base),
        Suggestion(movie_id=1, connecting_actor_id=1, connecting_actor_name="A", **base),
    ):
        assert movie.model_dump()["origin_countries"] == ["US", "GB"]
        assert movie.model_dump()["origin_country"] == "US, GB"
    step = RunStepPublic(
        id="step",
        run_id="run",
        movie_id=1,
        step_number=1,
        logged_by_user_id="user",
        logged_at=utcnow(),
        movie_title="Legacy",
        movie_origin_country="US, GB",
        movie_poster_path=None,
        movie_release_year=None,
        transition_metadata=None,
        user_notes=None,
        status="watched",
        watched_at=None,
    )
    assert step.model_dump()["movie_origin_countries"] == ["US", "GB"]
    assert step.model_dump()["movie_origin_country"] == "US, GB"


def test_refresh_ratings_bypasses_fresh_negative_cache(client):
    from app.models.cache import CachedMovie, CachedMovieRating
    from app.models.system import SystemSetting

    _register_and_login(client)
    for session in app.dependency_overrides[get_session]():
        session.add(
            CachedMovie(
                tmdb_id=603,
                title="The Matrix",
                imdb_id="tt0133093",
                origin_country="US, GB",
                overview="",
                tagline="",
            )
        )
        session.add(CachedMovieRating(movie_id=603))
        session.add(SystemSetting(key="omdb_api_key", value="test"))
        session.commit()
    with respx.mock:
        route = respx.get("https://www.omdbapi.com/").mock(
            return_value=httpx.Response(200, json={"Response": "True", "imdbRating": "8.7"})
        )
        ordinary = client.get("/api/movies/603")
        assert ordinary.json()["ratings"]["imdb_rating"] is None and route.call_count == 0
        refreshed = client.get("/api/movies/603?refresh_ratings=true")
        assert refreshed.status_code == 200
        assert refreshed.json()["origin_countries"] == ["US", "GB"]
        assert refreshed.json()["origin_country"] == "US, GB"
        assert refreshed.json()["ratings"]["imdb_rating"] == "8.7"
        assert route.call_count == 1


def test_get_movie_cast(client):
    _register_and_login(client)
    with respx.mock:
        respx.get(f"{TMDB_BASE}/movie/603").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 603,
                    "title": "The Matrix",
                    "release_date": "1999-03-30",
                    "poster_path": None,
                    "overview": "",
                    "origin_country": ["US"],
                    "original_language": "en",
                    "runtime": 136,
                    "genres": [],
                },
            )
        )
        respx.get(f"{TMDB_BASE}/movie/603/credits").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 603,
                    "cast": [
                        {
                            "id": 6384,
                            "name": "Keanu Reeves",
                            "profile_path": "/kr.jpg",
                            "character": "Neo",
                            "order": 0,
                        }
                    ],
                },
            )
        )
        resp = client.get("/api/movies/603/cast", params={"limit": 15})

    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["actor_id"] == 6384
    assert body[0]["character_name"] == "Neo"


def test_get_person_credits_with_filters(client):
    _register_and_login(client)
    with respx.mock:
        respx.get(f"{TMDB_BASE}/person/6384/movie_credits").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 6384,
                    "cast": [
                        {
                            "id": 603,
                            "title": "The Matrix",
                            "release_date": "1999-03-30",
                            "poster_path": None,
                            "character": "Neo",
                            "genre_ids": [28],
                            "original_language": "en",
                        },
                        {
                            "id": 700,
                            "title": "Some 2010s Film",
                            "release_date": "2015-06-01",
                            "poster_path": None,
                            "character": "Other",
                            "genre_ids": [18],
                            "original_language": "en",
                        },
                    ],
                },
            )
        )
        resp = client.get("/api/people/6384/credits", params={"decade": 1990})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["tmdb_id"] == 603


def test_movies_routes_require_auth(client):
    resp = client.get("/api/movies/search", params={"q": "matrix"})
    assert resp.status_code == 401


def test_list_genres(client):
    _register_and_login(client)
    with respx.mock:
        respx.get(f"{TMDB_BASE}/genre/movie/list").mock(
            return_value=httpx.Response(
                200,
                json={"genres": [{"id": 28, "name": "Action"}, {"id": 18, "name": "Drama"}]},
            )
        )
        resp = client.get("/api/movies/genres")

    assert resp.status_code == 200
    body = resp.json()
    assert {"id": 28, "name": "Action"} in body
    assert {"id": 18, "name": "Drama"} in body

    # Second call is served from the cache - zero further HTTP calls.
    with respx.mock:
        resp = client.get("/api/movies/genres")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_movie_ratings_null_when_omdb_not_configured(client):
    """No OMDb key -> ratings is null, and no OMDb call is even attempted."""
    _register_and_login(client)
    with respx.mock:
        respx.get(f"{TMDB_BASE}/movie/603").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 603,
                    "title": "The Matrix",
                    "release_date": "1999-03-30",
                    "poster_path": None,
                    "overview": "",
                    "origin_country": ["US"],
                    "original_language": "en",
                    "runtime": 136,
                    "genres": [],
                },
            )
        )
        resp = client.get("/api/movies/603")

    assert resp.status_code == 200
    assert resp.json()["ratings"] is None


def test_movie_ratings_bulk_endpoint(client):
    _register_and_login(client)
    client.patch("/api/settings/integrations", json={"omdb_api_key": "test-key"})

    with respx.mock:
        respx.get(f"{TMDB_BASE}/movie/603").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 603,
                    "title": "The Matrix",
                    "release_date": "1999-03-30",
                    "poster_path": None,
                    "overview": "",
                    "origin_country": ["US"],
                    "original_language": "en",
                    "runtime": 136,
                    "genres": [],
                },
            )
        )
        respx.get(f"{TMDB_BASE}/movie/604").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 604,
                    "title": "Some Other Film",
                    "release_date": "2001-01-01",
                    "poster_path": None,
                    "overview": "",
                    "origin_country": ["US"],
                    "original_language": "en",
                    "runtime": 100,
                    "genres": [],
                },
            )
        )
        respx.get("https://www.omdbapi.com/").mock(
            return_value=httpx.Response(200, json={"Response": "True", "imdbRating": "8.7"})
        )
        resp = client.post("/api/movies/ratings/bulk", json={"tmdb_ids": [603, 604]})

    assert resp.status_code == 200
    body = resp.json()
    assert body["603"]["imdb_rating"] == "8.7"
    assert body["604"]["imdb_rating"] == "8.7"


def test_get_movie_hydrates_stub_overview_and_supports_refresh(client):
    from app.models.cache import CachedMovie

    _register_and_login(client)
    session = next(app.dependency_overrides[get_session]())
    session.add(CachedMovie(tmdb_id=603, title="The Matrix"))  # stub: overview NULL
    session.commit()

    with respx.mock:
        route = respx.get(f"{TMDB_BASE}/movie/603").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 603,
                    "title": "The Matrix",
                    "overview": "A hacker learns the truth.",
                    "runtime": 136,
                    "genres": [],
                },
            )
        )
        first = client.get("/api/movies/603")
        second = client.get("/api/movies/603")
        refreshed = client.get("/api/movies/603", params={"refresh": "true"})

    assert first.json()["overview"] == "A hacker learns the truth."
    assert first.json()["runtime"] == 136
    assert second.json()["overview"] == "A hacker learns the truth."
    assert refreshed.status_code == 200
    assert route.call_count == 2  # stub hydration + explicit refresh; middle call was cached


def test_cold_movie_detail_returns_friendly_503_when_tmdb_is_unavailable(client):
    _register_and_login(client)
    client.app.state.tmdb._max_retries = 1

    with respx.mock:
        respx.get(f"{TMDB_BASE}/movie/603").mock(return_value=httpx.Response(500))
        response = client.get("/api/movies/603")

    assert response.status_code == 503
    assert response.json() == {"detail": "TMDB is unreachable right now - try again shortly"}


def test_cold_movie_detail_returns_friendly_429_when_tmdb_is_rate_limited(client):
    _register_and_login(client)
    client.app.state.tmdb._max_retries = 1

    with respx.mock:
        respx.get(f"{TMDB_BASE}/movie/603").mock(return_value=httpx.Response(429))
        response = client.get("/api/movies/603")

    assert response.status_code == 429
    assert response.json() == {"detail": "TMDB is rate-limiting us - try again in a moment"}
