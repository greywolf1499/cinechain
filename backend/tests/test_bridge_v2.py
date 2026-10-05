"""Bridge Solver 2.0: dynamic path tags, same-actor node swaps, and "Search Deeper"."""

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app
from app.models.cache import CachedMovie
from app.models.curated import CanonMovieBadge, CuratedList
from app.schemas.engine import BridgeNode
from app.services import bridge_paths, pathfinder
from app.services.tmdb import TMDBClient

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


def _movie_json(
    tmdb_id: int,
    title: str,
    runtime: int = 100,
    countries=("US",),
    release_date: str = "2000-01-01",
):
    return {
        "id": tmdb_id,
        "title": title,
        "release_date": release_date,
        "poster_path": None,
        "overview": "",
        "origin_country": list(countries),
        "original_language": "en",
        "runtime": runtime,
        "genres": [],
    }


def _mock_movie(tmdb_id: int, title: str, cast: list[dict] | None = None, **kwargs):
    respx.get(f"{TMDB_BASE}/movie/{tmdb_id}").mock(
        return_value=httpx.Response(200, json=_movie_json(tmdb_id, title, **kwargs))
    )
    if cast is not None:
        respx.get(f"{TMDB_BASE}/movie/{tmdb_id}/credits").mock(
            return_value=httpx.Response(200, json={"id": tmdb_id, "cast": cast})
        )


def _cast(actor_id: int, name: str, order: int = 0):
    return {
        "id": actor_id,
        "name": name,
        "profile_path": None,
        "character": f"{name} role",
        "order": order,
    }


def _credit(movie_id: int, title: str, popularity: float = 1.0, release_date: str = "2000-01-01"):
    return {
        "id": movie_id,
        "title": title,
        "release_date": release_date,
        "poster_path": None,
        "character": f"Role in {title}",
        "genre_ids": [],
        "original_language": "en",
        "popularity": popularity,
    }


def _mock_person(actor_id: int, credits_: list[dict]):
    return respx.get(f"{TMDB_BASE}/person/{actor_id}/movie_credits").mock(
        return_value=httpx.Response(200, json={"id": actor_id, "cast": credits_})
    )


def _node(movie_id: int, runtime=None, countries=()):
    return BridgeNode(
        movie_id=movie_id, title=str(movie_id), runtime=runtime, origin_countries=list(countries)
    )


# --- tags ---


def test_canon_heavy_needs_two_badged_films(db_engine):
    with Session(db_engine) as session:
        curated = CuratedList(title="Canon", url="https://x", badge_prefix="C")
        session.add(curated)
        session.commit()
        session.refresh(curated)
        session.add(CanonMovieBadge(curated_list_id=curated.id, movie_id=1, badge_label="C"))
        session.commit()
        nodes = [_node(1), _node(2), _node(3)]
        assert bridge_paths.analyze_path_tags(session, nodes) == []

        session.add(CanonMovieBadge(curated_list_id=curated.id, movie_id=3, badge_label="C"))
        session.commit()
        tags = bridge_paths.analyze_path_tags(session, nodes)
        assert [t.key for t in tags] == ["canon_heavy"]
        assert "2 films" in tags[0].detail


def test_multi_country_needs_three_distinct_countries(db_engine):
    with Session(db_engine) as session:
        two = [_node(1, countries=["US"]), _node(2, countries=["FR", "US"])]
        assert bridge_paths.analyze_path_tags(session, two) == []
        three = [*two, _node(3, countries=["JP"])]
        tags = bridge_paths.analyze_path_tags(session, three)
        assert [t.key for t in tags] == ["multi_country"]
        assert "3 countries" in tags[0].detail


def test_epic_runtimes_needs_two_films_over_150_minutes(db_engine):
    with Session(db_engine) as session:
        one_epic = [_node(1, runtime=200), _node(2, runtime=150), _node(3, runtime=90)]
        assert bridge_paths.analyze_path_tags(session, one_epic) == []
        two_epics = [_node(1, runtime=200), _node(2, runtime=151), _node(3, runtime=None)]
        assert [t.key for t in bridge_paths.analyze_path_tags(session, two_epics)] == [
            "epic_runtimes"
        ]


async def test_solved_result_carries_tags(config_dir, db_engine):
    with Session(db_engine) as session, respx.mock:
        _mock_movie(1, "Epic A", [_cast(100, "Shared")], runtime=190, countries=("US",))
        _mock_movie(2, "Epic B", [_cast(100, "Shared")], runtime=170, countries=("FR",))
        async with httpx.AsyncClient() as http:
            events = [
                e
                async for e in pathfinder.solve_bridge_bipartite(
                    session, TMDBClient(http), 1, 2, max_depth=3
                )
            ]

    result = next(e for e in events if e["type"] == "result")
    assert [t.key for t in result["tags"]] == ["epic_runtimes"]
    assert result["path"][0].runtime == 190
    assert result["path"][1].origin_countries == ["FR"]


async def test_tags_hydrate_stub_movies_for_runtime(config_dir, db_engine):
    with Session(db_engine) as session, respx.mock:
        for movie_id in (1, 2):
            session.add(CachedMovie(tmdb_id=movie_id, title=f"Stub {movie_id}"))
        session.commit()
        _mock_movie(1, "Stub 1", runtime=180)
        _mock_movie(2, "Stub 2", runtime=160)
        async with httpx.AsyncClient() as http:
            await bridge_paths.hydrate_movies(session, TMDBClient(http), [1, 2])
        nodes = bridge_paths.build_nodes(session, [1, 2])

    assert [n.runtime for n in nodes] == [180, 160]
    assert [t.key for t in bridge_paths.analyze_path_tags(session, nodes)] == ["epic_runtimes"]


def test_tags_endpoint_hydrates_and_analyzes(client, db_engine):
    with Session(db_engine) as session:
        for movie_id in (1, 2):
            session.add(CachedMovie(tmdb_id=movie_id, title=f"Stub {movie_id}"))
        session.commit()
    with respx.mock:
        _mock_movie(1, "Stub 1", runtime=180)
        _mock_movie(2, "Stub 2", runtime=160)
        resp = client.get("/api/engine/bridge/tags", params={"movie_ids": "1,2"})

    assert resp.status_code == 200
    body = resp.json()
    assert [t["key"] for t in body["tags"]] == ["epic_runtimes"]
    assert [n["runtime"] for n in body["nodes"]] == [180, 160]
    assert client.get("/api/engine/bridge/tags", params={"movie_ids": "1,x"}).status_code == 422


# --- same-actor swap ---


def _mock_swap_universe():
    # Actors X=100, Y=200. B=2 is the current node; 4 and 5 star both actors.
    _mock_person(
        100,
        [
            _credit(1, "A"),
            _credit(2, "B"),
            _credit(4, "Alt Four", popularity=5.0),
            _credit(5, "Alt Five", popularity=50.0),
            _credit(6, "Only X"),
            _credit(8, "Unreleased", release_date="2999-01-01"),
        ],
    )
    return _mock_person(
        200,
        [
            _credit(2, "B"),
            _credit(3, "C"),
            _credit(4, "Alt Four", popularity=5.0),
            _credit(5, "Alt Five", popularity=50.0),
            _credit(7, "Only Y"),
            _credit(8, "Unreleased", release_date="2999-01-01"),
        ],
    )


SWAP_PARAMS = {
    "movie_id": 2,
    "from_movie_id": 1,
    "to_movie_id": 3,
    "actor_in_id": 100,
    "actor_out_id": 200,
}


def test_same_actor_swap_returns_the_filmography_intersection(client):
    with respx.mock:
        _mock_swap_universe()
        resp = client.get("/api/engine/bridge/swap-node", params=SWAP_PARAMS)

    assert resp.status_code == 200
    body = resp.json()
    # Both actors, not the current film, not unreleased - most popular first.
    assert [c["node"]["movie_id"] for c in body["candidates"]] == [5, 4]
    assert body["total"] == 2
    first = body["candidates"][0]
    assert first["connection_in"]["actor_id"] == 100
    assert first["connection_in"]["character_in_to"] == "Role in Alt Five"
    assert first["connection_out"]["actor_id"] == 200
    assert first["connection_out"]["character_in_to"] == "Role in C"


def test_swap_skips_films_already_on_the_path_and_uses_the_cache(client):
    with respx.mock:
        x_route = _mock_swap_universe()
        client.get("/api/engine/bridge/swap-node", params=SWAP_PARAMS)
        calls_after_first = x_route.call_count
        resp = client.get(
            "/api/engine/bridge/swap-node", params={**SWAP_PARAMS, "exclude_movie_ids": "5"}
        )

    assert [c["node"]["movie_id"] for c in resp.json()["candidates"]] == [4]
    assert x_route.call_count == calls_after_first  # second call was pure SQLite


def test_swap_with_no_alternatives_returns_empty_list(client):
    with respx.mock:
        _mock_person(100, [_credit(1, "A"), _credit(2, "B")])
        _mock_person(200, [_credit(2, "B"), _credit(3, "C")])
        resp = client.get("/api/engine/bridge/swap-node", params=SWAP_PARAMS)

    assert resp.status_code == 200
    assert resp.json() == {"candidates": [], "total": 0}


def test_swap_surfaces_tmdb_failure_as_502(client):
    with respx.mock:
        respx.get(f"{TMDB_BASE}/person/100/movie_credits").mock(return_value=httpx.Response(500))
        resp = client.get("/api/engine/bridge/swap-node", params=SWAP_PARAMS)

    assert resp.status_code == 502


# --- Search Deeper ---


def _mock_deeper_universe():
    # Shortest route: A -X-> C (1 hop). A longer one: A -Z-> M -W-> C (2 hops).
    _mock_movie(1, "Movie A", [_cast(100, "X"), _cast(101, "Z")])
    _mock_movie(3, "Movie C", [_cast(100, "X"), _cast(102, "W")])
    _mock_movie(2, "Movie M", [])
    _mock_person(100, [_credit(1, "Movie A"), _credit(3, "Movie C")])
    _mock_person(101, [_credit(1, "Movie A"), _credit(2, "Movie M")])
    _mock_person(102, [_credit(3, "Movie C"), _credit(2, "Movie M")])


async def _solve(session, **kwargs):
    async with httpx.AsyncClient() as http:
        return [
            e
            async for e in pathfinder.solve_bridge_bipartite(
                session, TMDBClient(http), 1, 3, **kwargs
            )
        ]


async def test_search_deeper_skips_the_shallow_route(config_dir, db_engine):
    with Session(db_engine) as session, respx.mock:
        _mock_deeper_universe()
        shallow = next(e for e in await _solve(session, max_depth=3) if e["type"] == "result")
        deeper = next(
            e for e in await _solve(session, max_depth=3, min_hops=2) if e["type"] == "result"
        )

    assert shallow["hops"] == 1
    assert deeper["hops"] == 2
    assert [n.movie_id for n in deeper["path"]] == [1, 2, 3]
    assert [c.actor_id for c in deeper["connections"]] == [101, 102]


async def test_search_deeper_raises_the_depth_cap_to_reach_min_hops(config_dir, db_engine):
    with Session(db_engine) as session, respx.mock:
        _mock_deeper_universe()
        events = await _solve(session, max_depth=1, min_hops=2)

    assert any(e["type"] == "result" for e in events)


async def test_search_deeper_with_nothing_deeper_is_exhausted(config_dir, db_engine):
    with Session(db_engine) as session, respx.mock:
        _mock_deeper_universe()
        events = await _solve(session, max_depth=3, min_hops=4)

    assert not any(e["type"] == "result" for e in events)
    assert events[-2]["type"] == "exhausted"


async def test_search_deeper_still_honours_the_solver_timeout(config_dir, db_engine):
    with Session(db_engine) as session, respx.mock:
        _mock_deeper_universe()
        events = await _solve(session, max_depth=5, min_hops=3, max_duration_seconds=0)

    assert any(e["type"] == "timeout" for e in events)
    assert not any(e["type"] == "result" for e in events)


def test_stream_validates_min_hops(client):
    resp = client.get(
        "/api/engine/bridge/stream", params={"from_movie_id": 1, "to_movie_id": 3, "min_hops": 99}
    )
    assert resp.status_code == 422


# --- Find Alternative Routes (disjoint corridors) ---


def _mock_corridors_universe():
    # Two 2-hop corridors A -> M1 -> C and A -> M2 -> C; M1 is reachable through TWO actors, so
    # the same middle film would otherwise fill two of the three offered routes.
    _mock_movie(1, "Movie A", [_cast(100, "P"), _cast(101, "Q"), _cast(104, "S")])
    _mock_movie(4, "Movie C", [_cast(102, "R"), _cast(103, "T")])
    _mock_movie(2, "Movie M1", [_cast(100, "P"), _cast(101, "Q"), _cast(102, "R")])
    _mock_movie(3, "Movie M2", [_cast(104, "S"), _cast(103, "T")])
    _mock_person(100, [_credit(1, "Movie A"), _credit(2, "Movie M1")])
    _mock_person(101, [_credit(1, "Movie A"), _credit(2, "Movie M1")])
    _mock_person(104, [_credit(1, "Movie A"), _credit(3, "Movie M2")])
    _mock_person(102, [_credit(4, "Movie C"), _credit(2, "Movie M1")])
    _mock_person(103, [_credit(4, "Movie C"), _credit(3, "Movie M2")])


async def _solve_corridors(session, **kwargs):
    async with httpx.AsyncClient() as http:
        return [
            e
            async for e in pathfinder.solve_bridge_bipartite(
                session, TMDBClient(http), 1, 4, **kwargs
            )
        ]


def test_prefer_disjoint_paths_puts_distinct_corridors_first():
    def path(*movies):
        return [node for movie in movies for node in (("movie", movie), ("actor", 0))][:-1]

    same_middle_a, same_middle_b, other_middle = path(1, 2, 4), path(1, 2, 4), path(1, 3, 4)
    ordered = pathfinder.prefer_disjoint_paths([same_middle_a, same_middle_b, other_middle])
    assert ordered == [same_middle_a, other_middle, same_middle_b]


async def test_alternative_routes_are_distinct_corridors(config_dir, db_engine):
    with Session(db_engine) as session, respx.mock:
        _mock_corridors_universe()
        events = await _solve_corridors(session, max_depth=4)

    result = next(e for e in events if e["type"] == "result")
    corridors = [[n.movie_id for n in p["path"]] for p in [result, *result["alternate_paths"]]]
    assert [1, 2, 4] in corridors and [1, 3, 4] in corridors
    assert (
        corridors[1][1] != corridors[0][1]
    )  # the first alternative avoids the primary's middle film


async def test_excluding_the_intermediates_forces_a_different_corridor(config_dir, db_engine):
    with Session(db_engine) as session, respx.mock:
        _mock_corridors_universe()
        events = await _solve_corridors(session, max_depth=4, excluded_movie_ids={2})

    result = next(e for e in events if e["type"] == "result")
    assert [n.movie_id for n in result["path"]] == [1, 3, 4]
    assert not result["alternate_paths"]


async def test_excluding_every_corridor_is_exhausted(config_dir, db_engine):
    with Session(db_engine) as session, respx.mock:
        _mock_corridors_universe()
        events = await _solve_corridors(session, max_depth=4, excluded_movie_ids={2, 3})

    assert not any(e["type"] == "result" for e in events)
    assert events[-2]["type"] == "exhausted"


def _stream_result(client, **params):
    import json

    resp = client.get(
        "/api/engine/bridge/stream",
        params={"from_movie_id": 1, "to_movie_id": 4, "max_depth": 4, **params},
    )
    assert resp.status_code == 200
    for block in resp.text.split("\n\n"):
        if block.startswith("event: result"):
            return json.loads(block.split("data: ", 1)[1])
    return None


def test_stream_accepts_exclude_movie_ids(client):
    with respx.mock:
        _mock_corridors_universe()
        result = _stream_result(client, exclude_movie_ids=[2])
    assert [n["movie_id"] for n in result["path"]] == [1, 3, 4]


def test_stream_never_excludes_the_endpoints(client):
    with respx.mock:
        _mock_corridors_universe()
        result = _stream_result(client, exclude_movie_ids=[1, 4, 2])
    assert [n["movie_id"] for n in result["path"]] == [1, 3, 4]


def test_stream_rejects_an_oversized_exclusion_list(client):
    resp = client.get(
        "/api/engine/bridge/stream",
        params={"from_movie_id": 1, "to_movie_id": 4, "exclude_movie_ids": list(range(500))},
    )
    assert resp.status_code == 422


# --- broad detour ---


def _mock_broad_universe():
    # A=1 stars X=100 and P=300; C=3 stars Y=200 and Q=400; B=2 is the current node (X, Y).
    _mock_movie(1, "A", cast=[_cast(100, "X", 0), _cast(300, "P", 1)])
    _mock_movie(3, "C", cast=[_cast(200, "Y", 0), _cast(400, "Q", 1)])
    _mock_person(
        100,
        [
            _credit(1, "A"),
            _credit(2, "B"),
            _credit(4, "Same Cast", popularity=9.0),
            _credit(7, "X And Q", popularity=3.0),
        ],
    )
    _mock_person(200, [_credit(2, "B"), _credit(3, "C"), _credit(4, "Same Cast", popularity=9.0)])
    _mock_person(
        300,
        [
            _credit(1, "A"),
            _credit(6, "P And Q", popularity=7.0),
            _credit(8, "Only P", popularity=50.0),
        ],
    )
    _mock_person(
        400,
        [
            _credit(3, "C"),
            _credit(6, "P And Q", popularity=7.0),
            _credit(7, "X And Q", popularity=3.0),
        ],
    )


def test_broad_detour_links_a_and_c_through_different_cast(client):
    with respx.mock:
        _mock_broad_universe()
        resp = client.get("/api/engine/bridge/swap-node", params={**SWAP_PARAMS, "mode": "broad"})

    assert resp.status_code == 200
    body = resp.json()
    # The exact-same-actors film (4) belongs to the other tab; a film with only one side (8) never fits.
    assert [c["node"]["movie_id"] for c in body["candidates"]] == [6, 7]
    assert body["total"] == 2
    entirely_different = body["candidates"][0]
    assert entirely_different["connection_in"]["actor_id"] == 300
    assert entirely_different["connection_out"]["actor_id"] == 400
    assert entirely_different["connection_in"]["character_in_from"] == "P role"
    mixed = body["candidates"][1]
    assert (mixed["connection_in"]["actor_id"], mixed["connection_out"]["actor_id"]) == (100, 400)


def test_broad_detour_does_not_need_the_path_actors(client):
    with respx.mock:
        _mock_broad_universe()
        resp = client.get(
            "/api/engine/bridge/swap-node",
            params={"movie_id": 2, "from_movie_id": 1, "to_movie_id": 3, "mode": "broad"},
        )

    assert resp.status_code == 200
    assert [c["node"]["movie_id"] for c in resp.json()["candidates"]] == [4, 6, 7]


def test_broad_detour_tolerates_a_partial_filmography_pool(client):
    with respx.mock:
        _mock_broad_universe()
        respx.get(f"{TMDB_BASE}/person/400/movie_credits").mock(
            return_value=httpx.Response(200, json={"id": 400, "cast": []})
        )
        resp = client.get("/api/engine/bridge/swap-node", params={**SWAP_PARAMS, "mode": "broad"})
    assert resp.status_code == 200
    assert resp.json()["candidates"] == []


def test_same_mode_still_requires_both_actors_and_rejects_unknown_modes(client):
    params = {"movie_id": 2, "from_movie_id": 1, "to_movie_id": 3}
    assert client.get("/api/engine/bridge/swap-node", params=params).status_code == 422
    assert (
        client.get(
            "/api/engine/bridge/swap-node", params={**SWAP_PARAMS, "mode": "wild"}
        ).status_code
        == 422
    )
