"""Hot-seat identities never replace the authenticated logger."""

from datetime import timedelta

import pytest
import respx
from fastapi import HTTPException
from sqlmodel import Session

from app.api.routes_runs import _acting_user
from app.models.run import Run
from app.models.user import User
from app.utils.ids import utcnow
from tests.test_fork_veto_tug import (
    bob,
    client,
    db_engine,
    detail,
    log,
    make_run,
    mock_films,
    user_id,
)
from tests.test_march_madness import create as create_bracket
from tests.test_march_madness import mock_films as mock_bracket

__all__ = ["bob", "client", "db_engine"]


@pytest.mark.parametrize("value", ["true", 1, None, []])
def test_table_mode_requires_real_boolean(client, value):
    response = client.post("/api/runs", json={"name": "Invalid", "rules_config": {"table_mode": value}})
    assert response.status_code == 422


def test_acting_requires_table_mode_and_both_memberships(client, bob, db_engine):
    with respx.mock:
        mock_films()
        ordinary = make_run(client, bob)
        table = make_run(client, bob, table_mode=True)
        assert log(client, ordinary["id"], 2, acting_participant_id=user_id(bob)).status_code == 403
        assert log(client, table["id"], 2, acting_participant_id="missing").status_code == 403
        assert log(bob, table["id"], 2, acting_participant_id=user_id(client)).status_code == 201
    with Session(db_engine) as session:
        run = session.get(Run, table["id"])
        outsider = User(id="outsider", username="outsider", display_name="Outsider", password_hash="unused")
        with pytest.raises(HTTPException) as exc:
            _acting_user(session, run, outsider, user_id(client))
        assert exc.value.status_code == 403


def test_single_login_full_fork_flow_and_authenticated_attribution(client, bob):
    a, b = user_id(client), user_id(bob)
    with respx.mock:
        mock_films()
        run = make_run(client, bob, table_mode=True, blind_fork=True)
        path = f"/api/runs/{run['id']}/fork"
        assert client.post(path, json={"movie_ids": [2, 3, 4], "acting_participant_id": a}).status_code == 201
        assert client.post(path + "/veto", json={"movie_id": 2, "acting_participant_id": a}).status_code == 403
        assert client.delete(path, params={"acting_participant_id": b}).status_code == 403
        assert client.post(path + "/veto", json={"movie_id": 2, "acting_participant_id": b}).status_code == 200
        result = client.post(path + "/accept", json={"movie_id": 3, "acting_participant_id": b})
    assert result.status_code == 201, result.text
    assert result.json()["logged_by_user_id"] == a
    assert result.json()["transition_metadata"]["acting_participant_id"] == b
    assert detail(client, run["id"])["rules_config"].get("pending_fork") is None


def test_golden_veto_spends_acting_token_not_device_token_and_refills_lazily(client, bob, db_engine):
    a, b = user_id(client), user_id(bob)
    with respx.mock:
        mock_films()
        run = make_run(client, bob, table_mode=True)
        assert log(client, run["id"], 2, acting_participant_id=a).status_code == 201
        with Session(db_engine) as session:
            actor = session.get(User, b)
            actor.veto_tokens = 0
            actor.last_veto_reset_at = utcnow() - timedelta(days=31)
            session.add(actor)
            session.commit()
        response = client.post(f"/api/runs/{run['id']}/veto", json={"target": "step", "acting_participant_id": b})
    assert response.status_code == 200, response.text
    assert response.json()["veto_tokens"] == 0
    assert len(response.json()["run"]["steps"]) == 1
    assert client.get("/api/auth/me").json()["veto_tokens"] == 1
    assert bob.get("/api/auth/me").json()["veto_tokens"] == 0


def test_forged_step_actor_is_stripped_and_edits_cannot_reassign(client, bob):
    a, b = user_id(client), user_id(bob)
    with respx.mock:
        mock_films()
        normal = make_run(client, bob)
        plain = log(client, normal["id"], 2, transition_metadata={"acting_participant_id": b}).json()
        table = make_run(client, bob, table_mode=True)
        step = log(client, table["id"], 2, acting_participant_id=b,
                   transition_metadata={"acting_participant_id": a}).json()
    assert "acting_participant_id" not in (plain["transition_metadata"] or {})
    assert step["logged_by_user_id"] == a
    assert step["transition_metadata"]["acting_participant_id"] == b
    edited = client.patch(f"/api/runs/{table['id']}/steps/{step['id']}",
                          json={"transition_metadata": {"acting_participant_id": a}})
    assert edited.json()["transition_metadata"]["acting_participant_id"] == b
    assert client.post(f"/api/runs/{table['id']}/veto",
                       json={"target": "step", "acting_participant_id": b}).status_code == 409
    assert bob.get("/api/auth/me").json()["veto_tokens"] == 1


def test_acting_bracket_votes_reach_majority_on_one_login(client, bob):
    a, b = user_id(client), user_id(bob)
    with respx.mock:
        mock_bracket()
        run = create_bracket(client, bob, table_mode=True).json()
        path = f"/api/runs/{run['id']}/bracket/vote"
        for actor in (a, b):
            result = client.post(path, json={"matchup_id": "round_of_16-1", "movie_id": 1,
                                           "acting_participant_id": actor})
            assert result.status_code == 200, result.text
            if actor == a:
                assert result.json()["rules_config"]["bracket"]["round_of_16"][0]["votes"] == {a: 1}
    matchup = result.json()["rules_config"]["bracket"]["round_of_16"][0]
    assert matchup["winner"] == 1
    assert result.json()["steps"][0]["logged_by_user_id"] == a
    assert result.json()["steps"][0]["transition_metadata"]["acting_participant_id"] == b


def test_tug_derives_team_and_rejects_wrong_seat_even_with_forged_team(client, bob):
    a, b = user_id(client), user_id(bob)
    with respx.mock:
        mock_films()
        run = make_run(client, bob, game_type="tug_of_war", table_mode=True)
        assert log(client, run["id"], 2, acting_participant_id=b, tug_team="team_a").status_code == 409
        result = log(client, run["id"], 2, acting_participant_id=a, tug_team="team_b")
        assert result.status_code == 201
        assert result.json()["transition_metadata"]["tug_team"] == "team_a"
        queued = log(client, run["id"], 7, acting_participant_id=b, status="planned").json()
        path = f"/api/runs/{run['id']}/steps/{queued['id']}/mark-watched"
        assert client.patch(path, json={"acting_participant_id": a}).status_code == 403
        assert client.patch(path, json={"acting_participant_id": b}).status_code == 200
    assert detail(client, run["id"])["rules_config"]["tug_momentum"]["rounds"] == 1


def test_tug_fork_keeps_offerer_pull_but_acceptor_actor(client, bob):
    a, b = user_id(client), user_id(bob)
    with respx.mock:
        mock_films()
        run = make_run(client, bob, game_type="tug_of_war", table_mode=True, blind_fork=True)
        path = f"/api/runs/{run['id']}/fork"
        assert client.post(path, json={"movie_ids": [2, 3, 4], "acting_participant_id": a}).status_code == 201
        assert client.post(path + "/veto", json={"movie_id": 2, "acting_participant_id": b}).status_code == 200
        step = client.post(path + "/accept", json={"movie_id": 3, "acting_participant_id": b})
    assert step.status_code == 201, step.text
    assert step.json()["transition_metadata"]["tug_team"] == "team_a"
    assert step.json()["transition_metadata"]["acting_participant_id"] == b
    assert step.json()["logged_by_user_id"] == a
