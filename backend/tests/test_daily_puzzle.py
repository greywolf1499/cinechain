"""Phase 26a: The Daily Bridge - deterministic daily puzzle, anti-cheat lock, share grid, run conversion."""

import itertools
from datetime import date

import pytest
from sqlmodel import Session, select

from app.models.cache import CachedActor, CachedMovie, CachedMovieCast
from app.models.daily import DailyPuzzle
from app.models.run import Run, RunStep
from app.services import daily_puzzle
from tests.test_algorithm_sandbox import client, db_engine

__all__ = ["client", "db_engine"]

FILMS = 8  # a chain: film i shares actor 100+i with film i+1


@pytest.fixture(autouse=True)
def _reset_puzzle_state():
    daily_puzzle.reset_state()
    yield
    daily_puzzle.reset_state()


def seed_chain(db_engine, films: int = FILMS, popularity: float = 50.0) -> None:
    from app.utils.ids import utcnow

    with Session(db_engine) as session:
        for actor in range(101, 101 + films):
            session.add(CachedActor(tmdb_id=actor, name=f"Actor {actor}"))
        for film in range(1, films + 1):
            session.add(CachedMovie(
                tmdb_id=film, title=f"Film {film}", release_date="2000-01-01",
                status="Released", overview="x", popularity=popularity,
                cast_fetched_at=utcnow(), directors_fetched_at=utcnow()))
        session.commit()
        for film in range(1, films + 1):
            for order, actor in enumerate((100 + film - 1, 100 + film)):
                if 101 <= actor < 101 + films:
                    session.add(CachedMovieCast(
                        movie_id=film, actor_id=actor, cast_order=order,
                        character_name=f"Role {actor}"))
        session.commit()


def get_daily(client):
    resp = client.get("/api/puzzles/daily")
    assert resp.status_code == 200, resp.text
    return resp.json()


def walk(puzzle: dict) -> list[int]:
    """The chain's films from start to target (the fake world is a line)."""
    start, target = puzzle["start_movie"]["tmdb_id"], puzzle["target_movie"]["tmdb_id"]
    step = 1 if target > start else -1
    return list(range(start, target + step, step))


def hop(client, current, nxt):
    return client.post("/api/puzzles/daily/validate-hop", json={
        "current_movie_id": current, "next_movie_id": nxt})


def solve(client, puzzle):
    route = walk(puzzle)
    for current, nxt in itertools.pairwise(route):
        resp = hop(client, current, nxt)
        assert resp.status_code == 200 and resp.json()["recorded"], resp.text
    return resp.json()


# --- the deterministic pair ---


def test_seed_is_a_sha256_of_the_date_and_the_secret():
    day = date(2026, 10, 4)
    assert daily_puzzle.puzzle_seed(day, "s") == daily_puzzle.puzzle_seed(day, "s")
    assert daily_puzzle.puzzle_seed(day, "s") != daily_puzzle.puzzle_seed(date(2026, 10, 5), "s")
    assert daily_puzzle.puzzle_seed(day, "s") != daily_puzzle.puzzle_seed(day, "t")
    assert daily_puzzle.puzzle_number(date(2026, 10, 4)) == 1
    assert daily_puzzle.puzzle_number(date(2026, 10, 14)) == 11


def test_the_cached_graph_bfs_finds_shortest_routes(db_engine):
    seed_chain(db_engine)
    with Session(db_engine) as session:
        reached = daily_puzzle.explore(session, 1, 5, cast_limit=15)
    assert {m: r.hops for m, r in reached.items()} == {1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}
    assert daily_puzzle.route_to(reached, 5) == [1, 2, 3, 4, 5]


def test_the_daily_puzzle_is_a_verified_pair_stored_once(client, db_engine, monkeypatch):
    seed_chain(db_engine)
    puzzle = get_daily(client)
    assert 3 <= puzzle["par_hops"] <= 5
    assert puzzle["puzzle_number"] >= 1
    assert abs(puzzle["start_movie"]["tmdb_id"] - puzzle["target_movie"]["tmdb_id"]) == puzzle["par_hops"]
    assert puzzle["attempt"]["status"] == "not_started"
    assert puzzle["optimal_path"] is None  # the answer stays hidden until the attempt ends

    def boom(*args, **kwargs):
        raise AssertionError("the BFS must not run again once the day's pair is stored")

    monkeypatch.setattr(daily_puzzle, "explore", boom)
    again = get_daily(client)
    assert (again["start_movie"], again["target_movie"], again["date"]) == (
        puzzle["start_movie"], puzzle["target_movie"], puzzle["date"])
    with Session(db_engine) as session:
        assert len(session.exec(select(DailyPuzzle)).all()) == 1


def test_no_puzzle_without_enough_cached_films(client):
    resp = client.get("/api/puzzles/daily")
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "puzzle_unavailable"


def test_unpopular_films_are_never_picked(client, db_engine):
    seed_chain(db_engine, popularity=1.0)
    assert client.get("/api/puzzles/daily").status_code == 503


# --- playing ---


def test_validate_hop_checks_the_link_and_records_the_chain(client, db_engine):
    seed_chain(db_engine)
    puzzle = get_daily(client)
    route = walk(puzzle)

    far = next(film for film in range(1, FILMS + 1) if abs(film - route[0]) >= 2)
    miss = hop(client, route[0], far)
    assert miss.status_code == 200 and miss.json()["valid"] is False
    assert miss.json()["recorded"] is False and "shared" in miss.json()["reason"]

    ok = hop(client, route[0], route[1]).json()
    assert ok["valid"] and ok["recorded"] and not ok["solved"]
    assert ok["connections"][0]["actor_name"].startswith("Actor")
    assert ok["attempt"]["status"] == "in_progress" and ok["attempt"]["hops"] == 1
    # A valid link that doesn't continue from the chain's tip is checked but never recorded.
    off_tip = hop(client, route[1], route[2]).json() if len(route) > 3 else None
    assert off_tip is not None and off_tip["valid"] and off_tip["recorded"] is True
    back = hop(client, route[0], route[1]).json()
    assert back["valid"] and back["recorded"] is False
    assert get_daily(client)["attempt"]["hops"] == 2


def test_a_film_cannot_be_reused_in_the_chain(client, db_engine):
    seed_chain(db_engine)
    route = walk(get_daily(client))
    hop(client, route[0], route[1])
    again = hop(client, route[1], route[0]).json()
    assert again["valid"] is False and "already" in again["reason"]


def test_reaching_the_target_solves_and_builds_the_share_grid(client, db_engine):
    seed_chain(db_engine)
    puzzle = get_daily(client)
    final = solve(client, puzzle)

    assert final["solved"] is True
    attempt = final["attempt"]
    assert attempt["status"] == "solved" and attempt["hops"] == puzzle["par_hops"]
    assert attempt["grades"][-1] == "green" and len(attempt["grades"]) == puzzle["par_hops"]
    lines = attempt["share_text"].split("\n")
    assert lines[0] == f"CineChain Daily #{puzzle['puzzle_number']} 🎬"
    assert lines[1] == f"{puzzle['start_movie']['title']} ──► {puzzle['target_movie']['title']}"
    assert lines[2].endswith(f"🎯 ({puzzle['par_hops']} Hops)") and "🟩" in lines[2]
    assert lines[3] == "cinechain.local"
    revealed = get_daily(client)
    assert revealed["optimal_path"] is not None and len(revealed["optimal_path"]) == puzzle["par_hops"] + 1


def test_a_detour_is_graded_yellow(client, db_engine):
    """1 -> 2 -> 3 -> 4 is par; film 9 (shares 101 with film 1) and film 10 (109 with film 9,
    102 with films 2 and 3) offer a longer way round."""
    from app.utils.ids import utcnow

    seed_chain(db_engine, films=4)
    with Session(db_engine) as session:
        session.add(CachedActor(tmdb_id=109, name="Actor 109"))
        for film in (9, 10):
            session.add(CachedMovie(
                tmdb_id=film, title=f"Film {film}", release_date="2000-01-01", status="Released",
                overview="x", popularity=50.0, cast_fetched_at=utcnow(),
                directors_fetched_at=utcnow()))
        session.commit()
        for film, actor, order in ((9, 101, 0), (9, 109, 1), (10, 109, 0), (10, 102, 1)):
            session.add(CachedMovieCast(
                movie_id=film, actor_id=actor, cast_order=order, character_name="x"))
        session.add(DailyPuzzle(
            puzzle_date=daily_puzzle.today_utc().isoformat(), puzzle_number=1,
            start_movie_id=1, target_movie_id=4, par_hops=3,
            optimal_path=[{"movie_id": m, "link": None} for m in (1, 2, 3, 4)]))
        session.commit()

    for current, nxt in ((1, 9), (9, 10), (10, 3), (3, 4)):
        resp = hop(client, current, nxt)
        assert resp.json()["recorded"], resp.text
    attempt = resp.json()["attempt"]
    assert attempt["status"] == "solved" and attempt["hops"] == 4
    assert attempt["grades"] == ["yellow", "green", "green", "green"]
    assert attempt["share_text"].split("\n")[2] == "🟨 🟩 🟩 🎯 (4 Hops)"


def test_undo_takes_back_the_last_hop(client, db_engine):
    seed_chain(db_engine)
    route = walk(get_daily(client))
    hop(client, route[0], route[1])
    assert client.post("/api/puzzles/daily/undo").json()["attempt"]["hops"] == 0
    assert hop(client, route[0], route[1]).json()["recorded"] is True


# --- forfeit & conversion ---


def test_forfeit_reveals_the_optimal_path_and_stamps_the_attempt(client, db_engine):
    seed_chain(db_engine)
    puzzle = get_daily(client)
    resp = client.post("/api/puzzles/daily/forfeit")
    assert resp.status_code == 200
    body = resp.json()
    assert body["attempt"]["status"] == "forfeited"
    assert [h["movie"]["tmdb_id"] for h in body["optimal_path"]] == walk(puzzle)
    assert body["optimal_path"][0]["link"] is None and body["optimal_path"][1]["link"]["actor_name"]
    # Moves after a forfeit are checked but no longer recorded.
    route = walk(puzzle)
    assert hop(client, route[0], route[1]).json()["recorded"] is False


def test_a_solved_puzzle_cannot_be_forfeited(client, db_engine):
    seed_chain(db_engine)
    solve(client, get_daily(client))
    assert client.post("/api/puzzles/daily/forfeit").status_code == 409


def test_convert_needs_a_finished_attempt(client, db_engine):
    seed_chain(db_engine)
    get_daily(client)
    assert client.post("/api/puzzles/daily/convert-to-run").status_code == 409


def test_convert_to_run_seeds_planned_steps_once(client, db_engine):
    seed_chain(db_engine)
    puzzle = get_daily(client)
    solve(client, puzzle)
    first = client.post("/api/puzzles/daily/convert-to-run")
    assert first.status_code == 201
    run_id = first.json()["run_id"]
    assert first.json()["movies"] == puzzle["par_hops"] + 1
    assert client.post("/api/puzzles/daily/convert-to-run").json()["run_id"] == run_id

    run = client.get(f"/api/runs/{run_id}").json()
    assert run["name"] == f"Daily Bridge #{puzzle['puzzle_number']}"
    assert [s["movie_id"] for s in run["steps"]] == walk(puzzle)
    assert {s["status"] for s in run["steps"]} == {"planned"}
    assert run["steps"][1]["transition_metadata"]["actor_name"].startswith("Actor")
    assert get_daily(client)["attempt"]["run_id"] == run_id
    with Session(db_engine) as session:
        assert len(session.exec(select(Run)).all()) == 1
        assert len(session.exec(select(RunStep)).all()) == puzzle["par_hops"] + 1


def test_convert_after_a_forfeit_uses_the_optimal_route(client, db_engine):
    seed_chain(db_engine)
    puzzle = get_daily(client)
    client.post("/api/puzzles/daily/forfeit")
    resp = client.post("/api/puzzles/daily/convert-to-run")
    run = client.get(f"/api/runs/{resp.json()['run_id']}").json()
    assert [s["movie_id"] for s in run["steps"]] == walk(puzzle)


# --- anti-cheat ---


def bridge_stream(client, start, target):
    return client.get(
        "/api/engine/bridge/stream", params={"from_movie_id": start, "to_movie_id": target})


def test_the_solver_is_locked_for_todays_pair_until_solved_or_forfeited(client, db_engine):
    seed_chain(db_engine)
    puzzle = get_daily(client)
    start, target = puzzle["start_movie"]["tmdb_id"], puzzle["target_movie"]["tmdb_id"]

    locked = bridge_stream(client, start, target)
    assert locked.status_code == 403
    assert locked.json() == {
        "code": "anti_cheat_locked",
        "message": "Bridge Solver is locked for today's Daily Puzzle until solved or forfeited!"}
    assert bridge_stream(client, target, start).status_code == 403  # the reverse is the same puzzle
    fast = client.post("/api/engine/bridge", json={"from_movie_id": start, "to_movie_id": target})
    assert fast.status_code == 403

    middle = walk(puzzle)[1]
    assert bridge_stream(client, start, middle).status_code == 200  # any other pair is free

    client.post("/api/puzzles/daily/forfeit")
    assert bridge_stream(client, start, target).status_code == 200


def test_solving_also_unlocks_the_solver(client, db_engine):
    seed_chain(db_engine)
    puzzle = get_daily(client)
    solve(client, puzzle)
    assert bridge_stream(
        client, puzzle["start_movie"]["tmdb_id"], puzzle["target_movie"]["tmdb_id"]).status_code == 200


def test_the_solver_is_free_before_the_puzzle_exists(client, db_engine):
    seed_chain(db_engine)
    assert bridge_stream(client, 1, 4).status_code == 200
