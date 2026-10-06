"""Server-declared presets, strict field validation and unknown JSON rule compatibility."""

from dataclasses import asdict

import httpx
import pytest
from sqlmodel import Session

from app.engines.registry import ENGINE_REGISTRY
from app.engines.rulebook import render
from app.models.curated import CuratedList
from app.models.run import DEFAULT_RULES_CONFIG
from app.services.tmdb import TMDBClient
from tests.test_graph_mutators import client, db_engine

__all__ = ["client", "db_engine"]

REQUIRED = {
    "method_actor": {"actor_id": 1},
    "auteur_marathon": {"director_id": 1},
    "canon_island": {"allowed_curated_list_id": "fixture"},
    "decade_sieve": {"target_decade": 1970},
    "regional_deep_dive": {"curated_list_id": "fixture", "target_country": "JP"},
    "march_madness": {"bracket_movie_ids": list(range(1, 17))},
}


@pytest.mark.parametrize("mode", ENGINE_REGISTRY)
def test_every_preset_validates_against_its_engine(db_engine, mode):
    cls = ENGINE_REGISTRY[mode]
    assert len({field.key for field in cls.rule_fields}) == len(cls.rule_fields)
    if cls.presets:
        assert cls.default_preset in {preset.id for preset in cls.presets}
    with Session(db_engine) as session:
        session.add(CuratedList(id="fixture", title="Fixture", url="https://example.test/list",
                                badge_prefix="FIX", is_enabled=True))
        session.commit()
        engine = cls(session, TMDBClient(httpx.AsyncClient()))
        for preset in cls.presets:
            rules = {**REQUIRED.get(mode, {}), **preset.values}
            assert engine.validate_rules_config(rules) == [], (mode, preset.id)
            assert set(preset.values) <= {field.key for field in cls.rule_fields}


@pytest.mark.parametrize("rules", [
    {"max_cast_order": 31}, {"max_cast_order": True}, {"min_runtime": -1},
    {"no_consecutive_actor": 1}, {"allow_repeats": "anything"}, {"wildcards_budget": -2},
])
def test_declared_field_rejected_before_hydration(client, rules):
    response = client.post("/api/runs", json={"name": "Bad rules", "rules_config": rules})
    assert response.status_code == 422


def test_unknown_json_rules_pass_through(client):
    response = client.post("/api/runs", json={
        "name": "Custom", "rules_config": {"future_rule": {"nested": 42}},
    })
    assert response.status_code == 201
    assert response.json()["rules_config"]["future_rule"] == {"nested": 42}


def test_engine_metadata_exposes_presets_and_fields(client):
    metadata = {item["game_type"]: item for item in client.get("/api/engines").json()}
    for mode, cls in ENGINE_REGISTRY.items():
        assert metadata[mode]["default_preset"] == cls.default_preset
        assert metadata[mode]["presets"] == [preset.model_dump() for preset in cls.presets]
        assert metadata[mode]["rule_fields"] == [field.model_dump() for field in cls.rule_fields]
        defaults = {
            **DEFAULT_RULES_CONFIG,
            **{field.key: field.default for field in cls.rule_fields},
            **next((preset.values for preset in cls.presets if preset.id == cls.default_preset), {}),
        }
        assert metadata[mode]["rulebook"] == asdict(render(cls.rulebook, cls.rulebook_values(defaults)))
    assert "7" in metadata["tug_of_war"]["rulebook"]["goal"]


def test_rule_edits_validate_declared_fields(client):
    run_id = client.post("/api/runs", json={"name": "Editable"}).json()["id"]
    response = client.patch(f"/api/runs/{run_id}/rules", json={"max_cast_order": 31})
    assert response.status_code == 422
    assert client.patch(f"/api/runs/{run_id}/rules", json={"no_consecutive_actor": "false"}).status_code == 422
