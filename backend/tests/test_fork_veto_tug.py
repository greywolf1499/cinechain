"""Phase 24b: Golden Veto tokens, the Blind Fork offer/veto workflow and Tug of War."""

from datetime import timedelta

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.engines.tug_of_war import DEFAULT_TARGET_LEAD
from app.main import app
from app.models.user import User
from app.services.veto import refresh_veto_tokens
from app.utils.ids import utcnow

TMDB_BASE = "https://api.themoviedb.org/3"

# id -> (title, release year, origin countries); every film shares actor 1, so every link is legal
FILMS = {
    1: ("Seed", 1990, ["US"]),
    2: ("Old One", 1950, ["US"]),
    3: ("Old Two", 1960, ["JP"]),
    4: ("Old Three", 1970, ["FR"]),
    5: ("Old Four", 1930, ["IN"]),
    6: ("Old Five", 1940, ["US"]),
    7: ("New One", 2010, ["JP"]),
    8: ("New Two", 2015, ["US"]),
    9: ("New Three", 2020, ["KR"]),
    10: ("New Four", 2022, ["IN"]),
    11: ("Middle One", 1990, ["US"]),
    12: ("Middle Two", 2000, ["DE"]),
    13: ("No Country", 1995, []),
}


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
    with TestClient(app) as alice:
        alice.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password123", "display_name": "Alice"},
        )
        alice.post("/api/auth/login", json={"username": "alice", "password": "password123"})
        yield alice
    app.dependency_overrides.clear()


@pytest.fixture()
def bob(client):
    client.post(
        "/api/auth/register",
        json={"username": "bob", "password": "password123", "display_name": "Bob"},
    )
    other = TestClient(app)
    other.post("/api/auth/login", json={"username": "bob", "password": "password123"})
    return other


def mock_films():
    for movie_id, (title, year, countries) in FILMS.items():
        respx.get(f"{TMDB_BASE}/movie/{movie_id}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": movie_id,
                    "title": title,
                    "release_date": f"{year}-06-01",
                    "poster_path": None,
                    "overview": "",
                    "origin_country": countries,
                    "original_language": "en",
                    "runtime": 100,
                    "genres": [],
                    "popularity": 5.0,
                    "status": "Released",
                },
            )
        )
        respx.get(f"{TMDB_BASE}/movie/{movie_id}/credits").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": movie_id,
                    "crew": [],
                    "cast": [
                        {
                            "id": 1,
                            "name": "Everyone",
                            "profile_path": None,
                            "character": "r",
                            "order": 0,
                        },
                        {
                            "id": 100 + movie_id,
                            "name": "Solo",
                            "profile_path": None,
                            "character": "r",
                            "order": 1,
                        },
                    ],
                },
            )
        )


def user_id(session_client):
    return session_client.get("/api/auth/me").json()["id"]


def make_run(client, partner=None, game_type="cinechain", seed=1, **rules):
    payload = {
        "name": "Run",
        "game_type": game_type,
        "seed_movie_id": seed,
        "participant_user_ids": [user_id(partner)] if partner else [],
        "rules_config": {
            "preset": "standard",
            "allow_repeats": "strict",
            "no_consecutive_actor": False,
            "max_cast_order": 15,
            "min_runtime": 40,
            "wildcards_budget": 2,
            **rules,
        },
    }
    resp = client.post("/api/runs", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def detail(client, run_id):
    return client.get(f"/api/runs/{run_id}").json()


def log(client, run_id, movie_id, **extra):
    return client.post(f"/api/runs/{run_id}/steps", json={"movie_id": movie_id, **extra})


# --- Golden Veto tokens ------------------------------------------------------------------


def test_new_users_start_with_one_token(client):
    me = client.get("/api/auth/me").json()
    assert me["veto_tokens"] == 1 and me["last_veto_reset_at"]


def test_tokens_refill_just_in_time_after_30_days(client, db_engine):
    with Session(db_engine) as session:
        user = session.get(User, user_id(client))
        user.veto_tokens = 0
        user.last_veto_reset_at = utcnow() - timedelta(days=29)
        session.add(user)
        session.commit()
    assert client.get("/api/auth/me").json()["veto_tokens"] == 0

    with Session(db_engine) as session:
        user = session.get(User, user_id(client))
        user.last_veto_reset_at = utcnow() - timedelta(days=31)
        session.add(user)
        session.commit()
    before = utcnow()
    me = client.get("/api/auth/me").json()
    assert me["veto_tokens"] == 1
    assert me["last_veto_reset_at"] >= before.replace(tzinfo=None).isoformat()
    # the window restarted, so the next request doesn't "refill" again
    assert client.get("/api/auth/me").json()["last_veto_reset_at"] == me["last_veto_reset_at"]


def test_refresh_handles_naive_sqlite_timestamps(db_engine):
    with Session(db_engine) as session:
        user = User(username="x", display_name="X", password_hash="-", veto_tokens=0)
        user.last_veto_reset_at = (utcnow() - timedelta(days=45)).replace(tzinfo=None)
        session.add(user)
        session.commit()
        assert refresh_veto_tokens(session, user) is True
        assert user.veto_tokens == 1
        assert refresh_veto_tokens(session, user) is False


# --- Blind Fork --------------------------------------------------------------------------


def enable_fork(client, run_id):
    resp = client.patch(f"/api/runs/{run_id}/rules", json={"blind_fork": True})
    assert resp.status_code == 200, resp.text


def offer(client, run_id, ids=(2, 3, 7), **extra):
    return client.post(f"/api/runs/{run_id}/fork", json={"movie_ids": list(ids), **extra})


def test_fork_needs_the_toggle_a_partner_and_a_valid_offer(client, bob):
    with respx.mock:
        mock_films()
        solo = make_run(client)
        enable_fork(client, solo["id"])
        assert "partner" in offer(client, solo["id"]).json()["detail"]

        run = make_run(client, partner=bob)
        assert "off" in offer(client, run["id"]).json()["detail"]
        enable_fork(client, run["id"])
        assert offer(client, run["id"], ids=(2, 3)).status_code == 422
        assert offer(client, run["id"], ids=(2, 2, 3)).status_code == 422
        repeat = offer(client, run["id"], ids=(2, 3, 1))
        assert repeat.status_code == 409 and "Seed" in repeat.json()["detail"]


def test_fork_toggle_is_refused_for_tunnel_runs(client):
    with respx.mock:
        mock_films()
        resp = client.post(
            "/api/runs",
            json={
                "name": "T",
                "game_type": "meet_in_the_middle",
                "seed_movie_id": 1,
                "tail_seed_movie_id": 2,
                "rules_config": {"blind_fork": True},
            },
        )
    assert resp.status_code == 422 and "Blind Fork" in resp.json()["detail"]


def test_offer_veto_and_accept_flow(client, bob):
    with respx.mock:
        mock_films()
        run = make_run(client, partner=bob)
        run_id = run["id"]
        enable_fork(client, run_id)
        created = offer(client, run_id, links={"2": {"actor_id": 1, "actor_name": "Everyone"}})
        assert created.status_code == 201, created.text
        fork = created.json()["rules_config"]["pending_fork"]
        assert fork["offered_by_id"] == user_id(client)
        assert fork["movie_ids"] == [2, 3, 7] and fork["offered_at"]

        # the partner sees the offer, and nobody can sidestep it by logging directly
        assert bob.get(f"/api/runs/{run_id}").json()["rules_config"]["pending_fork"][
            "movie_ids"
        ] == [2, 3, 7]
        assert log(client, run_id, 4).status_code == 409
        assert offer(client, run_id, ids=(4, 5, 6)).status_code == 409

        # the offerer can neither veto nor pick from their own offer
        assert client.post(f"/api/runs/{run_id}/fork/veto", json={"movie_id": 2}).status_code == 403
        assert (
            client.post(f"/api/runs/{run_id}/fork/accept", json={"movie_id": 2}).status_code == 403
        )

        # a pick before the veto is refused
        assert bob.post(f"/api/runs/{run_id}/fork/accept", json={"movie_id": 2}).status_code == 409
        assert bob.post(f"/api/runs/{run_id}/fork/veto", json={"movie_id": 99}).status_code == 404
        vetoed = bob.post(f"/api/runs/{run_id}/fork/veto", json={"movie_id": 7})
        assert vetoed.status_code == 200
        assert vetoed.json()["rules_config"]["pending_fork"]["movie_ids"] == [2, 3]
        assert bob.post(f"/api/runs/{run_id}/fork/veto", json={"movie_id": 2}).status_code == 409
        # the vetoed film is gone
        assert bob.post(f"/api/runs/{run_id}/fork/accept", json={"movie_id": 7}).status_code == 404

        accepted = bob.post(f"/api/runs/{run_id}/fork/accept", json={"movie_id": 2})
    assert accepted.status_code == 201, accepted.text
    step = accepted.json()
    assert step["movie_id"] == 2 and step["status"] == "watched"
    assert step["logged_by_user_id"] == user_id(bob)
    assert step["transition_metadata"]["actor_name"] == "Everyone"
    after = detail(client, run_id)
    assert "pending_fork" not in after["rules_config"]
    assert [s["movie_id"] for s in after["steps"]] == [1, 2]


def test_the_offerer_can_withdraw_but_the_partner_cannot(client, bob):
    with respx.mock:
        mock_films()
        run_id = make_run(client, partner=bob)["id"]
        enable_fork(client, run_id)
        offer(client, run_id)
        assert bob.delete(f"/api/runs/{run_id}/fork").status_code == 403
        assert client.delete(f"/api/runs/{run_id}/fork").status_code == 200
        assert "pending_fork" not in detail(client, run_id)["rules_config"]
        assert client.delete(f"/api/runs/{run_id}/fork").status_code == 409


def test_turning_the_toggle_off_drops_a_pending_offer(client, bob):
    with respx.mock:
        mock_films()
        run_id = make_run(client, partner=bob)["id"]
        enable_fork(client, run_id)
        offer(client, run_id)
        off = client.patch(f"/api/runs/{run_id}/rules", json={"blind_fork": False})
    assert off.status_code == 200
    assert "pending_fork" not in off.json()["rules_config"]


def test_clients_cannot_forge_a_pending_fork_or_scores(client, bob):
    with respx.mock:
        mock_films()
        run = client.post(
            "/api/runs",
            json={
                "name": "Forged",
                "seed_movie_id": 1,
                "participant_user_ids": [user_id(bob)],
                "rules_config": {
                    "pending_fork": {"offered_by_id": user_id(bob), "movie_ids": [2, 3]},
                    "tug_scores": {"team_a": 9, "team_b": 0},
                },
            },
        ).json()
    assert "pending_fork" not in run["rules_config"] and "tug_scores" not in run["rules_config"]


# --- Golden Veto endpoint ----------------------------------------------------------------


def test_golden_veto_tears_up_a_fork_offer_and_spends_the_token(client, bob):
    with respx.mock:
        mock_films()
        run_id = make_run(client, partner=bob)["id"]
        enable_fork(client, run_id)
        offer(client, run_id)
        assert client.post(f"/api/runs/{run_id}/veto", json={"target": "fork"}).status_code == 403
        spent = bob.post(f"/api/runs/{run_id}/veto", json={"target": "fork"})
    assert spent.status_code == 200, spent.text
    body = spent.json()
    assert body["veto_tokens"] == 0 and body["target"] == "fork"
    assert "pending_fork" not in body["run"]["rules_config"]
    assert bob.get("/api/auth/me").json()["veto_tokens"] == 0


def test_golden_veto_needs_a_token(client, bob):
    with respx.mock:
        mock_films()
        run_id = make_run(client, partner=bob)["id"]
        enable_fork(client, run_id)
        offer(client, run_id)
        assert bob.post(f"/api/runs/{run_id}/veto", json={"target": "fork"}).status_code == 200
        offer(client, run_id, ids=(4, 5, 6))
        again = bob.post(f"/api/runs/{run_id}/veto", json={"target": "fork"})
    assert again.status_code == 409 and "No Golden Veto" in again.json()["detail"]
    # the refused veto left the offer in place
    assert detail(client, run_id)["rules_config"]["pending_fork"]["movie_ids"] == [4, 5, 6]


def test_golden_veto_removes_the_partners_latest_step(client, bob):
    with respx.mock:
        mock_films()
        run_id = make_run(client, partner=bob)["id"]
        assert log(bob, run_id, 2).status_code == 201
        assert bob.post(f"/api/runs/{run_id}/veto", json={"target": "step"}).status_code == 409
        assert bob.get("/api/auth/me").json()["veto_tokens"] == 1  # a refused veto costs nothing
        vetoed = client.post(f"/api/runs/{run_id}/veto", json={"target": "step"})
    assert vetoed.status_code == 200, vetoed.text
    assert [s["movie_id"] for s in vetoed.json()["run"]["steps"]] == [1]
    assert vetoed.json()["veto_tokens"] == 0


def test_golden_veto_cannot_remove_the_seed(client, bob):
    with respx.mock:
        mock_films()
        run_id = make_run(client, partner=bob)["id"]
        resp = bob.post(f"/api/runs/{run_id}/veto", json={"target": "step"})
    assert resp.status_code == 409 and bob.get("/api/auth/me").json()["veto_tokens"] == 1


def test_the_token_cannot_be_spent_twice_concurrently(client, db_engine):
    from app.services.veto import consume_veto_token

    with Session(db_engine) as first, Session(db_engine) as second:
        a = first.get(User, user_id(client))
        b = second.get(User, user_id(client))
        assert consume_veto_token(first, a) is True
        first.commit()
        assert consume_veto_token(second, b) is False


# --- Tug of War --------------------------------------------------------------------------


def tug(client, partner=None, **rules):
    return make_run(client, partner=partner, game_type="tug_of_war", seed=11, **rules)


def scores(client, run_id):
    return detail(client, run_id)["rules_config"]["tug_scores"]


def test_tug_of_war_is_registered_without_modifiers(client):
    meta = {e["game_type"]: e for e in client.get("/api/engines").json()}["tug_of_war"]
    assert meta["display_name"] == "Tug of War"
    assert {"tug_of_war", "discover_candidates"} <= set(meta["capabilities"])
    assert "modifiers" not in meta["capabilities"]


def test_tug_rules_are_validated_and_defaulted(client):
    with respx.mock:
        mock_films()
        bad = client.post(
            "/api/runs",
            json={"name": "x", "game_type": "tug_of_war", "rules_config": {"dimension": "mood"}},
        )
        low = client.post(
            "/api/runs",
            json={"name": "x", "game_type": "tug_of_war", "rules_config": {"target_lead": 1}},
        )
        bare = client.post(
            "/api/runs",
            json={
                "name": "x",
                "game_type": "tug_of_war",
                "rules_config": {
                    "tug_rules_version": 1,
                    "tug_momentum": {"effective_target": 1, "next_team": "team_b"},
                },
            },
        )
    assert bad.status_code == 422 and "dimension" in bad.json()["detail"]
    assert low.status_code == 422 and "target_lead" in low.json()["detail"]
    rules = bare.json()["rules_config"]
    assert rules["dimension"] == "era" and rules["target_lead"] == DEFAULT_TARGET_LEAD
    assert rules["tug_rules_version"] == 3
    assert rules["tug_momentum"]["effective_target"] == DEFAULT_TARGET_LEAD
    assert rules["tug_momentum"]["next_team"] == "team_a"
    assert rules["steal_enabled"] is True
    assert rules["momentum_cap"] == 3
    assert rules["sudden_death_after"] == 12
    assert rules["sudden_death_every"] == 2


def test_tug_turn_order_and_shared_device_team_attribution(client, bob):
    with respx.mock:
        mock_films()
        run_id = tug(
            client,
            partner=bob,
            tug_rules_version=1,
            tug_momentum={"effective_target": 1, "next_team": "team_b"},
        )["id"]
        run = detail(client, run_id)
        assert run["rules_config"]["tug_scores"] == {"team_a": 0, "team_b": 0}
        assert run["rules_config"]["tug_momentum"]["next_team"] == "team_a"

        first = log(
            client,
            run_id,
            2,
            transition_metadata={"tug_team": "team_b"},
        )
        assert first.status_code == 201, first.text
        assert first.json()["transition_metadata"]["tug_team"] == "team_a"
        assert first.json()["logged_by_user_id"] == user_id(client)

        wrong_turn = log(client, run_id, 3)
        assert wrong_turn.status_code == 409
        assert "Bob's pull" in wrong_turn.json()["detail"]

        shared_device = log(
            client,
            run_id,
            7,
            tug_team="team_b",
            transition_metadata={"tug_team": "team_a"},
        )
        assert shared_device.status_code == 201, shared_device.text
        assert shared_device.json()["transition_metadata"]["tug_team"] == "team_b"
        assert shared_device.json()["logged_by_user_id"] == user_id(client)
        assert scores(client, run_id) == {"team_a": 1, "team_b": 1}


def test_tug_blind_fork_acceptance_is_attributed_to_offerer(client, bob):
    with respx.mock:
        mock_films()
        run_id = tug(client, partner=bob)["id"]
        enable_fork(client, run_id)
        offered = offer(client, run_id)
        assert offered.status_code == 201, offered.text
        assert bob.post(f"/api/runs/{run_id}/fork/veto", json={"movie_id": 2}).status_code == 200
        accepted = bob.post(f"/api/runs/{run_id}/fork/accept", json={"movie_id": 3})
        assert accepted.status_code == 201, accepted.text

    step = accepted.json()
    assert step["movie_id"] == 3
    assert step["logged_by_user_id"] == user_id(bob)
    assert step["transition_metadata"]["tug_team"] == "team_a"


def test_tug_veto_uses_team_attribution_on_shared_device(client, bob):
    with respx.mock:
        mock_films()
        run_id = tug(client, partner=bob)["id"]
        assert log(client, run_id, 12).status_code == 201
        contested = log(client, run_id, 7, tug_team="team_b")
        assert contested.status_code == 201, contested.text
        assert contested.json()["logged_by_user_id"] == user_id(client)
        assert contested.json()["transition_metadata"]["tug_team"] == "team_b"

        veto = client.post(f"/api/runs/{run_id}/veto", json={"target": "step"})
        assert veto.status_code == 200, veto.text

    assert all(step["id"] != contested.json()["id"] for step in detail(client, run_id)["steps"])


def test_era_dimension_scores_each_film_for_a_team(client, bob):
    with respx.mock:
        mock_films()
        run_id = tug(client, partner=bob, dimension="era")["id"]
        assert scores(client, run_id) == {"team_a": 0, "team_b": 0}  # Seed steps never score.
        log(client, run_id, 2)  # 1950 -> A
        log(bob, run_id, 7)  # 2010 -> B
        log(client, run_id, 12)  # 2000: neutral anchor
        log(bob, run_id, 3)  # B invades A territory.
        got = detail(client, run_id)
    assert got["rules_config"]["tug_scores"] == {"team_a": 1, "team_b": 3}
    assert got["status"] == "active"
    players = got["rules_config"]["tug_players"]
    assert players == {"team_a": user_id(client), "team_b": user_id(bob)}


def test_geography_dimension_uses_first_country_and_skips_the_seed(client):
    with respx.mock:
        mock_films()
        run_id = tug(client, dimension="geography", target_lead=10)["id"]
        assert scores(client, run_id) == {"team_a": 0, "team_b": 0}  # Seed film: US, skipped
        log(client, run_id, 3)  # JP: invasion for solo Team A.
        log(client, run_id, 4)  # FR -> A
        log(client, run_id, 12)  # DE -> A
        log(client, run_id, 13)  # no country -> neutral anchor.
        got = scores(client, run_id)
    assert got == {"team_a": 5, "team_b": 0}


def test_a_lead_of_the_target_wins_for_the_leading_partner(client, bob):
    with respx.mock:
        mock_films()
        run_id = tug(client, partner=bob, dimension="era", target_lead=3)["id"]
        for picker, movie_id in (
            (client, 2),
            (bob, 12),
            (client, 3),
            (bob, 13),
        ):
            assert detail(client, run_id)["status"] == "active"
            assert log(picker, run_id, movie_id).status_code == 201
        done = detail(client, run_id)
        late = log(client, run_id, 5)
    assert done["status"] == "completed"
    assert done["status_reason"] == "Tug of War won by Alice, 3-0!"
    assert done["completed_at"]
    assert late.status_code == 409  # a finished run is locked


def test_team_b_can_win_and_the_lead_is_relative(client, bob):
    with respx.mock:
        mock_films()
        run_id = tug(client, partner=bob, dimension="era", target_lead=2)["id"]
        log(client, run_id, 12)  # A neutral.
        log(bob, run_id, 7)  # B +1.
        log(client, run_id, 13)  # A neutral.
        log(bob, run_id, 9)  # B +1 -> lead of 2.
        done = detail(client, run_id)
    assert done["status"] == "completed"
    assert done["status_reason"] == "Tug of War won by Bob, 3-0!"


def test_deleting_the_deciding_step_reopens_the_rope(client, bob):
    with respx.mock:
        mock_films()
        run_id = tug(client, partner=bob, dimension="era", target_lead=2)["id"]
        log(client, run_id, 12)
        log(bob, run_id, 7)
        log(client, run_id, 13)
        last = log(bob, run_id, 9).json()
        assert detail(client, run_id)["status"] == "completed"
        assert client.delete(f"/api/runs/{run_id}/steps/{last['id']}").status_code == 204
        got = detail(client, run_id)
    assert got["status"] == "active" and got["status_reason"] is None
    assert got["rules_config"]["tug_scores"] == {"team_a": 0, "team_b": 1}


def test_planned_films_do_not_pull_the_rope(client):
    with respx.mock:
        mock_films()
        run_id = tug(client, dimension="era", target_lead=2)["id"]
        queued = log(client, run_id, 2, status="planned").json()
        assert scores(client, run_id) == {"team_a": 0, "team_b": 0}
        marked = client.patch(f"/api/runs/{run_id}/steps/{queued['id']}/mark-watched", json={})
        assert marked.status_code == 200
    assert scores(client, run_id) == {"team_a": 1, "team_b": 0}


def test_golden_veto_cannot_rewrite_a_finished_tug_run(client, bob):
    with respx.mock:
        mock_films()
        run_id = tug(client, partner=bob, dimension="era", target_lead=2)["id"]
        log(client, run_id, 12)
        log(bob, run_id, 7)
        log(client, run_id, 13)
        log(bob, run_id, 9)  # Bob wins here.
        assert detail(client, run_id)["status"] == "completed"
        # a finished run is locked: the veto can't rewrite the result
        assert client.post(f"/api/runs/{run_id}/veto", json={"target": "step"}).status_code == 409
