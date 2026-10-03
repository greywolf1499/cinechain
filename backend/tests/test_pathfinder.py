"""Bidirectional bridge pathfinder tests.

Uses small synthetic movie/actor universes against a raw SQLModel session +
TMDBClient (no FastAPI app needed), mirroring test_cache_repo.py's pattern.
"""

import httpx
import respx
from sqlmodel import Session, SQLModel

from app.services import pathfinder
from app.services.tmdb import TMDBClient

TMDB_BASE = "https://api.themoviedb.org/3"


def _session(config_dir) -> Session:
    from app.db import engine as app_engine

    SQLModel.metadata.create_all(app_engine)
    return Session(app_engine)


def _movie_response(tmdb_id: int, title: str, release_date: str = "2000-01-01"):
    return httpx.Response(
        200,
        json={
            "id": tmdb_id,
            "title": title,
            "release_date": release_date,
            "poster_path": None,
            "overview": "",
            "origin_country": ["US"],
            "original_language": "en",
            "runtime": 100,
            "genres": [],
        },
    )


def _credits_response(cast: list[dict]):
    return httpx.Response(200, json={"id": 0, "cast": cast})


def _cast_member(actor_id: int, name: str, order: int = 0, character: str = "Role"):
    return {"id": actor_id, "name": name, "profile_path": None, "character": character, "order": order}


def _person_credits_response(movies: list[dict]):
    return httpx.Response(200, json={"id": 0, "cast": movies})


def _person_credit(movie_id: int, title: str, character: str = "Role", release_date: str = "2000-01-01"):
    return {
        "id": movie_id,
        "title": title,
        "release_date": release_date,
        "poster_path": None,
        "character": character,
        "genre_ids": [],
        "original_language": "en",
    }


def _mock_movie_and_credits(movie_id: int, title: str, cast: list[dict]):
    respx.get(
        f"{TMDB_BASE}/movie/{movie_id}").mock(return_value=_movie_response(movie_id, title))
    respx.get(
        f"{TMDB_BASE}/movie/{movie_id}/credits").mock(return_value=_credits_response(cast))


async def _collect(agen):
    return [event async for event in agen]


async def test_1hop_pair_resolves_with_zero_deep_calls(config_dir):
    with _session(config_dir) as session, respx.mock:
        _mock_movie_and_credits(
            1, "Movie A", [_cast_member(100, "Actor Shared")])
        _mock_movie_and_credits(
            2, "Movie B", [_cast_member(100, "Actor Shared")])
        # No /person/100/movie_credits mock registered at all - if the search
        # ever needed it, respx would raise AllMockedAssertionError.

        async with httpx.AsyncClient() as client:
            tmdb = TMDBClient(client)
            events = await _collect(pathfinder.solve_bridge_bipartite(session, tmdb, 1, 2, max_depth=5))

    result = next(e for e in events if e["type"] == "result")
    assert result["hops"] == 1
    assert [n.movie_id for n in result["path"]] == [1, 2]
    assert result["connections"][0].actor_id == 100
    assert events[-1]["type"] == "done"


async def test_2hop_pair_resolves_via_shared_filmography(config_dir):
    with _session(config_dir) as session, respx.mock:
        _mock_movie_and_credits(3, "Movie C", [_cast_member(200, "Actor P")])
        _mock_movie_and_credits(4, "Movie D", [_cast_member(201, "Actor Q")])
        respx.get(f"{TMDB_BASE}/person/200/movie_credits").mock(
            return_value=_person_credits_response(
                [_person_credit(3, "Movie C"),
                 _person_credit(5, "Movie Bridge")]
            )
        )
        respx.get(f"{TMDB_BASE}/person/201/movie_credits").mock(
            return_value=_person_credits_response(
                [_person_credit(4, "Movie D"),
                 _person_credit(5, "Movie Bridge")]
            )
        )

        async with httpx.AsyncClient() as client:
            tmdb = TMDBClient(client)
            events = await _collect(pathfinder.solve_bridge_bipartite(session, tmdb, 3, 4, max_depth=5))

    result = next(e for e in events if e["type"] == "result")
    assert result["hops"] == 2
    assert [n.movie_id for n in result["path"]] == [3, 5, 4]


async def test_synthetic_3hop_chain_resolves(config_dir):
    with _session(config_dir) as session, respx.mock:
        _mock_movie_and_credits(
            20, "Movie 20", [_cast_member(1000, "Actor A")])
        _mock_movie_and_credits(21, "Movie 21", [_cast_member(
            1000, "Actor A"), _cast_member(1001, "Actor B")])
        _mock_movie_and_credits(22, "Movie 22", [_cast_member(
            1001, "Actor B"), _cast_member(1002, "Actor C")])
        _mock_movie_and_credits(
            23, "Movie 23", [_cast_member(1002, "Actor C")])
        respx.get(f"{TMDB_BASE}/person/1000/movie_credits").mock(
            return_value=_person_credits_response(
                [_person_credit(20, "Movie 20"),
                 _person_credit(21, "Movie 21")]
            )
        )
        respx.get(f"{TMDB_BASE}/person/1001/movie_credits").mock(
            return_value=_person_credits_response(
                [_person_credit(21, "Movie 21"),
                 _person_credit(22, "Movie 22")]
            )
        )
        respx.get(f"{TMDB_BASE}/person/1002/movie_credits").mock(
            return_value=_person_credits_response(
                [_person_credit(22, "Movie 22"),
                 _person_credit(23, "Movie 23")]
            )
        )

        async with httpx.AsyncClient() as client:
            tmdb = TMDBClient(client)
            events = await _collect(
                pathfinder.solve_bridge_bipartite(
                    session, tmdb, 20, 23, max_depth=5)
            )

    result = next(e for e in events if e["type"] == "result")
    assert result["hops"] == 3
    assert [n.movie_id for n in result["path"]] == [20, 21, 22, 23]


async def test_synthetic_4hop_chain_resolves(config_dir):
    with _session(config_dir) as session, respx.mock:
        _mock_movie_and_credits(
            30, "Movie 30", [_cast_member(2000, "Actor A")])
        _mock_movie_and_credits(31, "Movie 31", [_cast_member(
            2000, "Actor A"), _cast_member(2001, "Actor B")])
        _mock_movie_and_credits(32, "Movie 32", [_cast_member(
            2001, "Actor B"), _cast_member(2002, "Actor C")])
        _mock_movie_and_credits(33, "Movie 33", [_cast_member(
            2002, "Actor C"), _cast_member(2003, "Actor D")])
        _mock_movie_and_credits(
            34, "Movie 34", [_cast_member(2003, "Actor D")])
        respx.get(f"{TMDB_BASE}/person/2000/movie_credits").mock(
            return_value=_person_credits_response(
                [_person_credit(30, "Movie 30"),
                 _person_credit(31, "Movie 31")]
            )
        )
        respx.get(f"{TMDB_BASE}/person/2001/movie_credits").mock(
            return_value=_person_credits_response(
                [_person_credit(31, "Movie 31"),
                 _person_credit(32, "Movie 32")]
            )
        )
        respx.get(f"{TMDB_BASE}/person/2002/movie_credits").mock(
            return_value=_person_credits_response(
                [_person_credit(32, "Movie 32"),
                 _person_credit(33, "Movie 33")]
            )
        )
        respx.get(f"{TMDB_BASE}/person/2003/movie_credits").mock(
            return_value=_person_credits_response(
                [_person_credit(33, "Movie 33"),
                 _person_credit(34, "Movie 34")]
            )
        )

        async with httpx.AsyncClient() as client:
            tmdb = TMDBClient(client)
            events = await _collect(
                pathfinder.solve_bridge_bipartite(
                    session, tmdb, 30, 34, max_depth=5)
            )

    result = next(e for e in events if e["type"] == "result")
    assert result["hops"] == 4
    assert [n.movie_id for n in result["path"]] == [30, 31, 32, 33, 34]


async def test_no_bridge_within_budget_emits_exhausted(config_dir):
    with _session(config_dir) as session, respx.mock:
        _mock_movie_and_credits(40, "Island A", [_cast_member(900, "Actor X")])
        _mock_movie_and_credits(41, "Island B", [_cast_member(901, "Actor Y")])
        respx.get(f"{TMDB_BASE}/person/900/movie_credits").mock(
            return_value=_person_credits_response(
                [_person_credit(40, "Island A")])
        )
        respx.get(f"{TMDB_BASE}/person/901/movie_credits").mock(
            return_value=_person_credits_response(
                [_person_credit(41, "Island B")])
        )

        async with httpx.AsyncClient() as client:
            tmdb = TMDBClient(client)
            events = await _collect(
                pathfinder.solve_bridge_bipartite(
                    session, tmdb, 40, 41, max_depth=5)
            )

    assert not any(e["type"] == "result" for e in events)
    assert not any(e["type"] == "error" for e in events)
    exhausted = next(e for e in events if e["type"] == "exhausted")
    assert exhausted["reason"] == "max_depth_reached"
    assert events[-1]["type"] == "done"


async def test_second_solve_of_same_pair_is_zero_http(config_dir):
    with _session(config_dir) as session:
        with respx.mock:
            _mock_movie_and_credits(
                1, "Movie A", [_cast_member(100, "Actor Shared")])
            _mock_movie_and_credits(
                2, "Movie B", [_cast_member(100, "Actor Shared")])

            async with httpx.AsyncClient() as client:
                tmdb = TMDBClient(client)
                first_events = await _collect(
                    pathfinder.solve_bridge_bipartite(
                        session, tmdb, 1, 2, max_depth=5)
                )
            assert any(e["type"] == "result" for e in first_events)

        # Fresh respx.mock with zero routes registered: any HTTP call at all
        # would raise AllMockedAssertionError, proving a full cache hit.
        with respx.mock:
            async with httpx.AsyncClient() as client:
                tmdb = TMDBClient(client)
                second_events = await _collect(
                    pathfinder.solve_bridge_bipartite(
                        session, tmdb, 1, 2, max_depth=5)
                )
            result = next(e for e in second_events if e["type"] == "result")
            assert result["hops"] == 1


async def test_closing_generator_releases_search_semaphore(config_dir):
    with _session(config_dir) as session, respx.mock:
        _mock_movie_and_credits(40, "Island A", [_cast_member(900, "Actor X")])
        _mock_movie_and_credits(41, "Island B", [_cast_member(901, "Actor Y")])

        async with httpx.AsyncClient() as client:
            tmdb = TMDBClient(client)
            agen = pathfinder.solve_bridge_bipartite(
                session, tmdb, 40, 41, max_depth=5)
            await agen.__anext__()  # partially consume, then abandon
            await agen.aclose()

    assert not pathfinder._SEARCH_SEMAPHORE.locked()


async def test_second_search_proceeds_after_cancellation(config_dir):
    """A cancelled search must not permanently hold the process-wide semaphore."""
    with _session(config_dir) as session, respx.mock:
        _mock_movie_and_credits(40, "Island A", [_cast_member(900, "Actor X")])
        _mock_movie_and_credits(41, "Island B", [_cast_member(901, "Actor Y")])
        respx.get(f"{TMDB_BASE}/person/900/movie_credits").mock(
            return_value=_person_credits_response(
                [_person_credit(40, "Island A")])
        )
        respx.get(f"{TMDB_BASE}/person/901/movie_credits").mock(
            return_value=_person_credits_response(
                [_person_credit(41, "Island B")])
        )

        async with httpx.AsyncClient() as client:
            tmdb = TMDBClient(client)

            first_gen = pathfinder.solve_bridge_bipartite(
                session, tmdb, 40, 41, max_depth=5)
            await first_gen.__anext__()
            await first_gen.aclose()

            # Should complete promptly, not deadlock on an unreleased semaphore.
            events = await _collect(
                pathfinder.solve_bridge_bipartite(
                    session, tmdb, 40, 41, max_depth=5)
            )
            assert events[-1]["type"] == "done"


async def test_tmdb_failure_emits_error_event_not_a_raw_exception(config_dir):
    with _session(config_dir) as session, respx.mock:
        respx.get(
            f"{TMDB_BASE}/movie/50").mock(return_value=httpx.Response(401, json={}))

        async with httpx.AsyncClient() as client:
            tmdb = TMDBClient(client)
            events = await _collect(
                pathfinder.solve_bridge_bipartite(
                    session, tmdb, 50, 51, max_depth=5)
            )

    assert [e["type"] for e in events] == ["error", "done"]
    assert "50" in events[0]["message"] or "401" in events[0]["message"]
    assert not pathfinder._SEARCH_SEMAPHORE.locked()


async def test_excluded_movie_ids_prunes_the_only_bridge(config_dir):
    """Phase 15 run-scoped solver: a would-be bridge movie in `excluded_movie_ids`
    must never enter the graph, even though it's the only route between the pair."""
    with _session(config_dir) as session, respx.mock:
        _mock_movie_and_credits(3, "Movie C", [_cast_member(200, "Actor P")])
        _mock_movie_and_credits(4, "Movie D", [_cast_member(201, "Actor Q")])
        respx.get(f"{TMDB_BASE}/person/200/movie_credits").mock(
            return_value=_person_credits_response(
                [_person_credit(3, "Movie C"),
                 _person_credit(5, "Movie Bridge")]
            )
        )
        respx.get(f"{TMDB_BASE}/person/201/movie_credits").mock(
            return_value=_person_credits_response(
                [_person_credit(4, "Movie D"),
                 _person_credit(5, "Movie Bridge")]
            )
        )

        async with httpx.AsyncClient() as client:
            tmdb = TMDBClient(client)
            events = await _collect(
                pathfinder.solve_bridge_bipartite(
                    session, tmdb, 3, 4, max_depth=5, excluded_movie_ids={5}
                )
            )

    assert not any(e["type"] == "result" for e in events)
    exhausted = next(e for e in events if e["type"] == "exhausted")
    assert exhausted["reason"] == "max_depth_reached"


async def test_future_release_date_excludes_candidate_from_graph(config_dir):
    """Reality filter: an unreleased (future release_date) movie must never be
    used as a bridge, even though it's otherwise a perfectly good connector."""
    with _session(config_dir) as session, respx.mock:
        _mock_movie_and_credits(3, "Movie C", [_cast_member(200, "Actor P")])
        _mock_movie_and_credits(4, "Movie D", [_cast_member(201, "Actor Q")])
        respx.get(f"{TMDB_BASE}/person/200/movie_credits").mock(
            return_value=_person_credits_response(
                [
                    _person_credit(3, "Movie C"),
                    _person_credit(
                        5, "Unreleased Sequel", release_date="2099-01-01"),
                ]
            )
        )
        respx.get(f"{TMDB_BASE}/person/201/movie_credits").mock(
            return_value=_person_credits_response(
                [
                    _person_credit(4, "Movie D"),
                    _person_credit(
                        5, "Unreleased Sequel", release_date="2099-01-01"),
                ]
            )
        )

        async with httpx.AsyncClient() as client:
            tmdb = TMDBClient(client)
            events = await _collect(
                pathfinder.solve_bridge_bipartite(
                    session, tmdb, 3, 4, max_depth=5)
            )

    assert not any(e["type"] == "result" for e in events)
    exhausted = next(e for e in events if e["type"] == "exhausted")
    assert exhausted["reason"] == "max_depth_reached"


async def test_min_runtime_filters_a_known_short_bridge_movie(config_dir):
    """A bridge movie whose runtime is ALREADY cached (from an earlier full
    detail fetch) and falls below the run's min_runtime rule must be pruned.
    Unknown-runtime stubs (the common case) are never penalized - only
    checked when the data happens to already be known."""
    from app.models.cache import CachedMovie

    with _session(config_dir) as session, respx.mock:
        session.add(
            CachedMovie(
                tmdb_id=5, title="Short Film", release_date="2000-01-01", runtime=10,
            )
        )
        session.commit()

        _mock_movie_and_credits(3, "Movie C", [_cast_member(200, "Actor P")])
        _mock_movie_and_credits(4, "Movie D", [_cast_member(201, "Actor Q")])
        respx.get(f"{TMDB_BASE}/person/200/movie_credits").mock(
            return_value=_person_credits_response(
                [_person_credit(3, "Movie C"),
                 _person_credit(5, "Short Film")]
            )
        )
        respx.get(f"{TMDB_BASE}/person/201/movie_credits").mock(
            return_value=_person_credits_response(
                [_person_credit(4, "Movie D"),
                 _person_credit(5, "Short Film")]
            )
        )

        async with httpx.AsyncClient() as client:
            tmdb = TMDBClient(client)
            events = await _collect(
                pathfinder.solve_bridge_bipartite(
                    session, tmdb, 3, 4, max_depth=5, min_runtime=60)
            )

    assert not any(e["type"] == "result" for e in events)
    exhausted = next(e for e in events if e["type"] == "exhausted")
    assert exhausted["reason"] == "max_depth_reached"


async def test_multiple_direct_actor_links_produce_alternate_paths(config_dir):
    """Two different actors both directly connecting the same two films must
    surface as a primary path plus an 'Alternative Cast Link' alternate."""
    with _session(config_dir) as session, respx.mock:
        _mock_movie_and_credits(
            1,
            "Movie A",
            [_cast_member(100, "Actor Shared 1"),
             _cast_member(101, "Actor Shared 2")],
        )
        _mock_movie_and_credits(
            2,
            "Movie B",
            [_cast_member(100, "Actor Shared 1"),
             _cast_member(101, "Actor Shared 2")],
        )

        async with httpx.AsyncClient() as client:
            tmdb = TMDBClient(client)
            events = await _collect(
                pathfinder.solve_bridge_bipartite(
                    session, tmdb, 1, 2, max_depth=5)
            )

    result = next(e for e in events if e["type"] == "result")
    assert result["hops"] == 1
    assert result["label"] == "Shortest"
    assert len(result["alternate_paths"]) == 1
    alternate = result["alternate_paths"][0]
    assert alternate["label"] == "Alternative Cast Link"
    assert alternate["connections"][0].actor_id != result["connections"][0].actor_id


async def test_solve_duration_timeout_emits_clean_timeout_event(config_dir):
    """max_duration_seconds=0 must stop the search cleanly (not an error) as
    soon as a round completes without a meeting, explaining depth reached."""
    with _session(config_dir) as session, respx.mock:
        _mock_movie_and_credits(3, "Movie C", [_cast_member(200, "Actor P")])
        _mock_movie_and_credits(4, "Movie D", [_cast_member(201, "Actor Q")])
        respx.get(f"{TMDB_BASE}/person/200/movie_credits").mock(
            return_value=_person_credits_response(
                [_person_credit(3, "Movie C"),
                 _person_credit(5, "Movie Bridge")]
            )
        )
        respx.get(f"{TMDB_BASE}/person/201/movie_credits").mock(
            return_value=_person_credits_response(
                [_person_credit(4, "Movie D"),
                 _person_credit(5, "Movie Bridge")]
            )
        )

        async with httpx.AsyncClient() as client:
            tmdb = TMDBClient(client)
            events = await _collect(
                pathfinder.solve_bridge_bipartite(
                    session, tmdb, 3, 4, max_depth=5, max_duration_seconds=0)
            )

    assert not any(e["type"] == "result" for e in events)
    assert not any(e["type"] == "error" for e in events)
    timeout_event = next(e for e in events if e["type"] == "timeout")
    assert timeout_event["depth_reached"] == 0
    assert "timeout" in timeout_event["message"].lower()
    assert events[-1]["type"] == "done"


async def test_canon_badged_alternate_is_labeled_cinephile_route(config_dir):
    """An alternate bridge path whose intermediate film carries a Curated
    Canon badge must be labeled 'The Cinephile Route', regardless of hop
    count/popularity-based labeling."""
    from app.models.curated import CanonMovieBadge, CuratedList

    with _session(config_dir) as session, respx.mock:
        curated_list = CuratedList(
            title="Sight & Sound Top 100 (2022)",
            url="https://letterboxd.com/sightsoundmag/list/x/",
            badge_prefix="SS22",
        )
        session.add(curated_list)
        session.commit()
        session.refresh(curated_list)
        for movie_id in (5, 6):
            session.add(CanonMovieBadge(
                curated_list_id=curated_list.id, movie_id=movie_id, badge_label="SS22"))
        session.commit()

        _mock_movie_and_credits(
            3, "Movie C", [_cast_member(200, "Actor P"), _cast_member(202, "Actor P2")])
        _mock_movie_and_credits(
            4, "Movie D", [_cast_member(201, "Actor Q"), _cast_member(203, "Actor Q2")])
        respx.get(f"{TMDB_BASE}/person/200/movie_credits").mock(
            return_value=_person_credits_response(
                [_person_credit(3, "Movie C"), _person_credit(5, "Movie Bridge A")])
        )
        respx.get(f"{TMDB_BASE}/person/202/movie_credits").mock(
            return_value=_person_credits_response(
                [_person_credit(3, "Movie C"), _person_credit(6, "Movie Bridge B")])
        )
        respx.get(f"{TMDB_BASE}/person/201/movie_credits").mock(
            return_value=_person_credits_response(
                [_person_credit(4, "Movie D"), _person_credit(5, "Movie Bridge A")])
        )
        respx.get(f"{TMDB_BASE}/person/203/movie_credits").mock(
            return_value=_person_credits_response(
                [_person_credit(4, "Movie D"), _person_credit(6, "Movie Bridge B")])
        )

        async with httpx.AsyncClient() as client:
            tmdb = TMDBClient(client)
            events = await _collect(
                pathfinder.solve_bridge_bipartite(
                    session, tmdb, 3, 4, max_depth=5)
            )

    result = next(e for e in events if e["type"] == "result")
    assert result["label"] == "Shortest"
    assert len(result["alternate_paths"]) == 1
    assert result["alternate_paths"][0]["label"] == "The Cinephile Route"
