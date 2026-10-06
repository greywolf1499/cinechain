import json
import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.api.deps import get_current_user
from app.db import get_session
from app.engines import chaos, modifiers
from app.engines.registry import ENGINE_REGISTRY
from app.engines.rulebook import GLOSSARY, render
from app.main import app
from app.models.run import DEFAULT_RULES_CONFIG, Run, RunParticipant
from app.models.user import User
from app.services import blind_fork, bounties, veto

CUSTOM_RULES = {
    **DEFAULT_RULES_CONFIG,
    "target_lead": 7, "target_points": 5, "dimension": "era",
    "era_a_before": 1960, "era_b_after": 2010, "momentum_cap": 4,
    "sudden_death_after": 20, "sudden_death_every": 3,
    "max_lives": 5, "lives_remaining": 2, "escape_depth": 30,
    "genre_cycle": ["Comedy", "Drama"], "swing_frequency": 3,
    "target_decade": 1980, "target_country": "IN", "max_skip": 3,
    "direction": "descent", "chrono_direction": "descent",
    "actor": {"name": "Test Actor"}, "director": {"name": "Test Director"},
    "tunnel_hints_remaining": 4,
    "win_condition": {"type": "movies_watched", "count": 8},
    "fail_condition": {"type": "max_repeats_used", "count": 1},
}


def assert_section(section):
    assert section.goal and section.turn and section.scoring and section.tips
    assert all(section.turn) and all(section.scoring) and all(section.tips)
    assert all(term in GLOSSARY for term in section.glossary)
    for line in [section.goal, *section.turn, *section.scoring, *section.lose, *section.tips]:
        assert not re.search(r"\{[^{}]+\}", line)


@pytest.mark.parametrize("engine_class", ENGINE_REGISTRY.values(), ids=ENGINE_REGISTRY)
@pytest.mark.parametrize("rules", [DEFAULT_RULES_CONFIG, CUSTOM_RULES])
def test_every_engine_declares_and_renders_rulebook(engine_class, rules):
    assert "rulebook" in engine_class.__dict__, "Do not inherit another mode's copy"
    assert engine_class.tagline and engine_class.tags
    assert_section(render(engine_class.rulebook, engine_class.rulebook_values(rules)))


def test_all_overlay_fragments_and_modifier_registry():
    assert set(modifiers.RULEBOOK) == {*modifiers.PAIR_MODIFIER_KEYS, modifiers.CAST_LINK_KEY}
    values = {
        "chaos_label": "Pre-1970", "bounty_reward": "one life",
        "chrono_word": "before", "runtime_word": "shorter", "country_cooldown": 4,
    }
    for section in [bounties.RULEBOOK, chaos.RULEBOOK, blind_fork.RULEBOOK, veto.RULEBOOK, *modifiers.RULEBOOK.values()]:
        assert_section(render(section, values))


@pytest.mark.parametrize("field", ["goal", "turn", "scoring", "lose", "tips"])
def test_missing_placeholder_is_a_hard_error(field):
    section = ENGINE_REGISTRY["cinechain"].rulebook
    with pytest.raises(KeyError, match="missing"):
        render(
            replace(section, **{field: "{missing}" if field == "goal" else ["{missing}"]}),
            ENGINE_REGISTRY["cinechain"].rulebook_values(DEFAULT_RULES_CONFIG),
        )


@pytest.fixture()
def rulebook_client(config_dir):
    db = create_engine(f"sqlite:///{config_dir}/rulebooks.db", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db)
    user = User(username="reader", password_hash="not-a-real-password", display_name="Reader")
    with Session(db) as session:
        session.add(user)
        session.commit()
        session.refresh(user)
        session.expunge(user)

    def session_override():
        with Session(db) as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_current_user] = lambda: user
    with TestClient(app) as client:
        yield client, db, user
    app.dependency_overrides.clear()
    db.dispose()


def new_run(db, user, mode="cinechain", rules=None, partner=False):
    with Session(db) as session:
        run = Run(name="Rules test", game_type=mode, rules_config=rules or dict(DEFAULT_RULES_CONFIG))
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id
        session.add(RunParticipant(run_id=run.id, user_id=user.id, role="owner"))
        if partner:
            other = User(username="partner", password_hash="not-real", display_name="Partner")
            session.add(other)
            session.commit()
            session.refresh(other)
            session.add(RunParticipant(run_id=run.id, user_id=other.id))
        session.commit()
        return run_id


def test_engine_metadata_has_server_copy_and_rulebooks(rulebook_client):
    client, _, _ = rulebook_client
    result = client.get("/api/engines")
    assert result.status_code == 200
    assert {entry["game_type"] for entry in result.json()} == set(ENGINE_REGISTRY)
    for entry in result.json():
        assert entry["tagline"] and entry["tags"]
        assert entry["rulebook"]["goal"] and entry["rulebook"]["turn"] and entry["rulebook"]["scoring"]
        assert set(entry["rulebook"]["glossary"]) <= entry["glossary"].keys()


@pytest.mark.parametrize("mode", ENGINE_REGISTRY)
def test_run_endpoint_renders_all_modes(rulebook_client, mode):
    client, db, user = rulebook_client
    run_id = new_run(db, user, mode, CUSTOM_RULES)
    result = client.get(f"/api/runs/{run_id}/rulebook")
    assert result.status_code == 200
    data = result.json()
    assert data["game_type"] == mode
    assert data["settings"]
    assert not re.search(r"\{[a-z_]+\}", json.dumps(data))
    if mode == "rt_split":
        assert "5 points" in data["rulebook"]["goal"]
        assert "Max Lives" not in data["settings"]
    if mode == "tug_of_war":
        assert "7 points" in data["rulebook"]["goal"]
        assert "1960" in " ".join(data["rulebook"]["turn"])
        assert "Target Points" not in data["settings"]


def test_overlay_activation_and_actual_settings(rulebook_client):
    client, db, user = rulebook_client
    rules = {
        **CUSTOM_RULES, "bounty_board": True, "blind_fork": True,
        "active_chaos": {"id": "pre_1970"}, "runtime_staircase": "descending",
        "country_cooldown": 4, "api_key": "must-not-leak",
    }
    run_id = new_run(db, user, rules=rules, partner=True)
    data = client.get(f"/api/runs/{run_id}/rulebook").json()
    keys = {overlay["key"] for overlay in data["overlays"]}
    assert keys == {"bounty_board", "chaos", "blind_fork", "veto", *modifiers.PAIR_MODIFIER_KEYS}
    assert "before" in data["overlays"][2]["rulebook"]["turn"][0]
    assert "must-not-leak" not in json.dumps(data)
    run_without = new_run(db, user)
    assert client.get(f"/api/runs/{run_without}/rulebook").json()["overlays"] == []


def test_default_and_disabled_modifiers_and_coop_veto(rulebook_client):
    client, db, user = rulebook_client
    passport = new_run(db, user, "world_passport")
    assert [overlay["key"] for overlay in client.get(f"/api/runs/{passport}/rulebook").json()["overlays"]] == ["country_cooldown"]
    disabled = new_run(db, user, "world_passport", {"country_cooldown": 0, "require_cast_link": True})
    assert [overlay["key"] for overlay in client.get(f"/api/runs/{disabled}/rulebook").json()["overlays"]] == ["require_cast_link"]
    tunnel = new_run(db, user, "meet_in_the_middle", partner=True)
    assert client.get(f"/api/runs/{tunnel}/rulebook").json()["overlays"] == []
    bracket = new_run(db, user, "march_madness", {"bounty_board": True})
    assert client.get(f"/api/runs/{bracket}/rulebook").json()["overlays"] == []


def test_rulebook_participant_guard(rulebook_client):
    client, db, user = rulebook_client
    run_id = new_run(db, user)
    with Session(db) as session:
        membership = session.get(RunParticipant, (run_id, user.id))
        session.delete(membership)
        session.commit()
    assert client.get(f"/api/runs/{run_id}/rulebook").status_code == 404
    assert client.get("/api/runs/missing/rulebook").status_code == 404
    app.dependency_overrides.pop(get_current_user)
    assert client.get(f"/api/runs/{run_id}/rulebook").status_code == 401


def test_legacy_tug_copy_and_life_bounty_reward(rulebook_client):
    client, db, user = rulebook_client
    legacy = new_run(db, user, "tug_of_war")
    data = client.get(f"/api/runs/{legacy}/rulebook").json()
    assert "Legacy scoring" in data["rulebook"]["scoring"][0]
    assert "neutral films score neither" in data["rulebook"]["turn"][1]
    modern = new_run(db, user, "tug_of_war", {**CUSTOM_RULES, "tug_rules_version": 2})
    data = client.get(f"/api/runs/{modern}/rulebook").json()
    assert "4" in data["rulebook"]["scoring"][0]
    assert "20 pulls" in data["rulebook"]["scoring"][1]
    rabbit = new_run(db, user, "rabbit_hole", {**CUSTOM_RULES, "bounty_board": True})
    data = client.get(f"/api/runs/{rabbit}/rulebook").json()
    assert "one life" in data["overlays"][0]["rulebook"]["scoring"][0]
    assert "5 lives; 2 remain" in data["rulebook"]["scoring"][0]
