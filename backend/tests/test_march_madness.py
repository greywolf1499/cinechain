"""Phase 26c: Watchlist March Madness - the 16-film bracket, advancing winners, partner votes."""

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.engines import march_madness
from app.main import app
from app.models.curated import LetterboxdWatchlist
from tests.test_graph_mutators import TMDB_BASE, client, db_engine

__all__ = ["client", "db_engine"]

IDS = list(range(1, 17))


@pytest.fixture()
def bob(client):
    client.post(
        "/api/auth/register",
        json={"username": "bob", "password": "password123", "display_name": "Bob"},
    )
    other = TestClient(app)
    other.post("/api/auth/login", json={"username": "bob", "password": "password123"})
    return other


def user_id(session_client):
    return session_client.get("/api/auth/me").json()["id"]


def mock_films(ids=IDS):
    for movie_id in ids:
        respx.get(f"{TMDB_BASE}/movie/{movie_id}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": movie_id,
                    "title": f"Film {movie_id}",
                    "release_date": "2000-06-01",
                    "poster_path": f"/p{movie_id}.jpg",
                    "overview": f"Plot {movie_id}",
                    "tagline": "",
                    "origin_country": ["US"],
                    "original_language": "en",
                    "runtime": 90 + movie_id,
                    "genres": [],
                    "popularity": 5.0,
                    "status": "Released",
                },
            )
        )


def create(client, partner=None, **rules):
    payload = {
        "name": "Bracket",
        "game_type": "march_madness",
        "participant_user_ids": [user_id(partner)] if partner else [],
        "rules_config": {"bracket_movie_ids": IDS, **rules},
    }
    return client.post("/api/runs", json=payload)


def make_run(client, partner=None):
    resp = create(client, partner)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def detail(client, run_id):
    return client.get(f"/api/runs/{run_id}").json()


def advance(client, run_id, matchup_id, winner):
    return client.post(
        f"/api/runs/{run_id}/bracket/advance",
        json={"matchup_id": matchup_id, "winning_movie_id": winner},
    )


def vote(client, run_id, matchup_id, movie_id):
    return client.post(
        f"/api/runs/{run_id}/bracket/vote", json={"matchup_id": matchup_id, "movie_id": movie_id}
    )


# --- pure bracket logic ---


def test_the_bracket_pairs_adjacent_films():
    bracket = march_madness.build_bracket(IDS)
    assert [len(bracket[r]) for r in march_madness.ROUNDS] == [8, 4, 2, 1]
    assert bracket["champion"] is None
    assert (bracket["round_of_16"][0]["a"], bracket["round_of_16"][0]["b"]) == (1, 2)
    assert (bracket["round_of_16"][7]["a"], bracket["round_of_16"][7]["b"]) == (15, 16)
    assert bracket["quarterfinals"][0]["a"] is None


def test_a_bracket_needs_sixteen_distinct_films():
    with pytest.raises(ValueError):
        march_madness.build_bracket(IDS[:15])
    with pytest.raises(ValueError):
        march_madness.build_bracket([1] * 16)


def test_winners_move_into_the_right_slot_without_mutating_the_input():
    bracket = march_madness.build_bracket(IDS)
    after, round_name, crowned = march_madness.advance_matchup(bracket, "round_of_16-4", 8)
    assert (round_name, crowned) == ("round_of_16", False)
    assert after["quarterfinals"][1]["b"] == 8  # index 3 -> matchup 1, second slot
    assert bracket["quarterfinals"][1]["b"] is None


def test_majority_needs_more_than_half_of_everyone():
    matchup = {"votes": {"a": 1, "b": 2}}
    assert march_madness.majority_winner(matchup, ["a", "b"]) is None
    assert march_madness.majority_winner({"votes": {"a": 1, "b": 1}}, ["a", "b"]) == 1
    assert march_madness.majority_winner({"votes": {"a": 1}}, ["a", "b", "c"]) is None
    assert march_madness.majority_winner({"votes": {"a": 1, "b": 1}}, ["a", "b", "c"]) == 1
    assert march_madness.majority_winner({"votes": {"x": 1}}, ["a"]) is None  # not a participant


# --- creating a run ---


def test_the_engine_is_registered(client):
    engines = {e["game_type"]: e for e in client.get("/api/engines").json()}
    assert engines["march_madness"]["display_name"] == "Watchlist March Madness"


def test_creating_a_run_builds_the_bracket(client):
    with respx.mock:
        mock_films()
        run_id = make_run(client)
    rules = detail(client, run_id)["rules_config"]
    bracket = rules["bracket"]
    assert bracket["round_of_16"][0]["a"] == 1 and bracket["champion"] is None
    assert [len(bracket[r]) for r in ("round_of_16", "quarterfinals", "semifinals", "finals")] == [
        8,
        4,
        2,
        1,
    ]
    assert "bracket_movie_ids" not in rules
    card = rules["bracket_films"]["3"]
    assert (card["title"], card["runtime"], card["overview"]) == ("Film 3", 93, "Plot 3")
    assert detail(client, run_id)["steps"] == []


@pytest.mark.parametrize("ids", [IDS[:15], [*IDS[:15], 1], [*IDS[:15], "x"]])
def test_a_bracket_needs_exactly_sixteen_distinct_ids(client, ids):
    resp = client.post(
        "/api/runs",
        json={
            "name": "x",
            "game_type": "march_madness",
            "rules_config": {"bracket_movie_ids": ids},
        },
    )
    assert resp.status_code == 422


def test_a_client_cannot_supply_its_own_bracket(client):
    forged = {"champion": 1, "round_of_16": []}
    with respx.mock:
        mock_films()
        resp = create(client, bracket=forged)
    assert resp.status_code == 201
    assert detail(client, resp.json()["id"])["rules_config"]["bracket"]["champion"] is None


def test_an_unknown_film_is_a_setup_error(client):
    with respx.mock:
        mock_films(IDS[:15])
        respx.get(f"{TMDB_BASE}/movie/16").mock(return_value=httpx.Response(404, json={}))
        resp = create(client)
    assert resp.status_code == 422 and "16" in resp.json()["detail"]


def add_watchlist(db_engine, user, count):
    with Session(db_engine) as session:
        for movie_id in range(1, count + 1):
            session.add(
                LetterboxdWatchlist(
                    user_id=user,
                    letterboxd_username="alice",
                    movie_id=movie_id,
                    title=f"Film {movie_id}",
                    year=2000,
                )
            )
        session.commit()


def test_seeding_from_the_watchlist(client, db_engine):
    add_watchlist(db_engine, user_id(client), 30)
    seed = client.get("/api/tools/march-madness/seed")
    assert seed.status_code == 200
    assert len({film["tmdb_id"] for film in seed.json()}) == 16
    assert seed.json()[0]["title"].startswith("Film ")

    with respx.mock:
        mock_films(range(1, 31))
        resp = client.post(
            "/api/runs",
            json={
                "name": "x",
                "game_type": "march_madness",
                "rules_config": {"seed_from_watchlist": True},
            },
        )
    assert resp.status_code == 201, resp.text
    rules = detail(client, resp.json()["id"])["rules_config"]
    assert "seed_from_watchlist" not in rules
    seeded = {m[side] for m in rules["bracket"]["round_of_16"] for side in ("a", "b")}
    assert len(seeded) == 16


def test_a_thin_watchlist_cannot_seed(client, db_engine):
    add_watchlist(db_engine, user_id(client), 10)
    assert client.get("/api/tools/march-madness/seed").status_code == 409
    resp = client.post(
        "/api/runs",
        json={
            "name": "x",
            "game_type": "march_madness",
            "rules_config": {"seed_from_watchlist": True},
        },
    )
    assert resp.status_code == 422 and "10 films" in resp.json()["detail"]


# --- playing ---


def test_manual_logging_is_blocked(client):
    with respx.mock:
        mock_films()
        run_id = make_run(client)
        resp = client.post(f"/api/runs/{run_id}/steps", json={"movie_id": 1})
    assert resp.status_code == 409 and resp.json()["detail"]["blocked"] is True


def test_advancing_logs_the_winner_and_fills_the_next_round(client):
    with respx.mock:
        mock_films()
        run_id = make_run(client)
        resp = advance(client, run_id, "round_of_16-1", 2)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rules_config"]["bracket"]["round_of_16"][0]["winner"] == 2
    assert body["rules_config"]["bracket"]["quarterfinals"][0]["a"] == 2
    assert [(s["movie_id"], s["status"]) for s in body["steps"]] == [(2, "watched")]
    assert body["steps"][0]["transition_metadata"]["matchup_id"] == "round_of_16-1"
    assert body["status"] == "active"


def test_invalid_advances_are_rejected(client):
    with respx.mock:
        mock_films()
        run_id = make_run(client)
        assert advance(client, run_id, "round_of_16-1", 5).status_code == 422  # not in the matchup
        assert advance(client, run_id, "quarterfinals-1", 1).status_code == 409  # not ready
        assert advance(client, run_id, "nope-9", 1).status_code == 404
        assert advance(client, run_id, "round_of_16-1", 1).status_code == 200
        assert advance(client, run_id, "round_of_16-1", 2).status_code == 409  # already decided
    assert len(detail(client, run_id)["steps"]) == 1


def play_out(client, run_id, pick=lambda a, b: a):
    """Decides every matchup, always taking `pick(a, b)`. Returns the last response."""
    resp = None
    for round_name in march_madness.ROUNDS:
        count = len(detail(client, run_id)["rules_config"]["bracket"][round_name])
        for index in range(count):
            matchup = detail(client, run_id)["rules_config"]["bracket"][round_name][index]
            resp = advance(client, run_id, matchup["id"], pick(matchup["a"], matchup["b"]))
            assert resp.status_code == 200, resp.text
    return resp


def test_the_final_crowns_the_champion_and_completes_the_run(client):
    with respx.mock:
        mock_films()
        run_id = make_run(client)
        final = play_out(client, run_id).json()

    assert final["rules_config"]["bracket"]["champion"] == 1
    assert final["status"] == "completed"
    assert final["status_reason"] == "Champion Crowned: Film 1!"
    assert final["completed_at"] is not None
    # Film 1 won every round but is logged once; the other round winners follow it.
    assert final["steps"][0]["movie_id"] == 1
    assert len({s["movie_id"] for s in final["steps"]}) == len(final["steps"])
    with respx.mock:
        again = advance(client, run_id, "finals-1", 1)
    assert again.status_code == 409


def test_an_upset_can_win_it_all(client):
    with respx.mock:
        mock_films()
        run_id = make_run(client)
        final = play_out(client, run_id, pick=lambda a, b: b).json()
    assert final["rules_config"]["bracket"]["champion"] == 16
    assert final["status_reason"] == "Champion Crowned: Film 16!"


# --- partners voting ---


def test_a_solo_vote_resolves_the_matchup(client):
    with respx.mock:
        mock_films()
        run_id = make_run(client)
        resp = vote(client, run_id, "round_of_16-2", 4)
    assert resp.status_code == 200
    assert resp.json()["rules_config"]["bracket"]["round_of_16"][1]["winner"] == 4


def test_partners_resolve_by_majority_and_ties_stay_open(client, bob):
    with respx.mock:
        mock_films()
        run_id = make_run(client, partner=bob)

        first = vote(client, run_id, "round_of_16-1", 1).json()
        matchup = first["rules_config"]["bracket"]["round_of_16"][0]
        assert matchup["winner"] is None and matchup["votes"] == {user_id(client): 1}

        split = vote(bob, run_id, "round_of_16-1", 2).json()
        assert split["rules_config"]["bracket"]["round_of_16"][0]["winner"] is None  # a tie

        agree = vote(bob, run_id, "round_of_16-1", 1).json()  # Bob changes his mind
        assert agree["rules_config"]["bracket"]["round_of_16"][0]["winner"] == 1
        assert agree["rules_config"]["bracket"]["round_of_16"][0]["votes"] == {}

        # Anyone can settle a tie with Advance Winner.
        vote(client, run_id, "round_of_16-2", 3)
        vote(bob, run_id, "round_of_16-2", 4)
        settled = advance(bob, run_id, "round_of_16-2", 4)
    assert settled.status_code == 200
    assert settled.json()["rules_config"]["bracket"]["round_of_16"][1]["winner"] == 4


def test_voting_for_a_stranger_is_rejected(client):
    with respx.mock:
        mock_films()
        run_id = make_run(client)
        assert vote(client, run_id, "round_of_16-1", 9).status_code == 422
        assert vote(client, run_id, "quarterfinals-1", 1).status_code == 409


def test_a_non_bracket_run_has_no_bracket(client):
    run_id = client.post("/api/runs", json={"name": "x"}).json()["id"]
    assert advance(client, run_id, "round_of_16-1", 1).status_code == 400
