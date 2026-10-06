"""Registry aliases, scope enforcement and title-overlay integration."""

import pytest

from app.engines import modifiers
from app.engines.modifier_registry import contexts, registry
from app.engines.registry import ENGINE_REGISTRY
from app.models.cache import CachedMovie


@pytest.mark.parametrize(("key", "params", "flat"), [
    ("chrono_direction", {"direction": "descent"}, "descent"),
    ("runtime_staircase", {"direction": "ascending"}, "ascending"),
    ("country_cooldown", {"steps": 4}, 4),
    ("require_cast_link", {"enabled": True}, True),
])
def test_registry_aliases_override_flat_keys(key, params, flat):
    assert modifiers.merge_modifiers({}, {"modifiers": [{"key": key, "params": params}]}) == {key: flat}
    assert not modifiers.modifier_problems({"modifiers": [{"key": key, "params": params}]})


def test_registry_checks_keep_legacy_semantics():
    active = modifiers.merge_modifiers({}, {
        "modifiers": [{"key": "runtime_staircase", "params": {"direction": "ascending"}}],
    })
    a = CachedMovie(tmdb_id=1, title="A", runtime=100)
    b = CachedMovie(tmdb_id=2, title="B", runtime=80)
    spec, ctx = contexts(active, earlier=a)[0]
    assert spec.check(ctx, b).ok is False
    b.runtime = None
    assert spec.check(ctx, b).ok is None


def test_registry_param_validation_and_scope():
    for entries in [
        [{"key": "unknown"}],
        [{"key": "country_cooldown", "params": {"steps": True}}],
        [{"key": "chrono_direction", "params": {"direction": "up"}}],
        [{"key": "country_cooldown"}, {"key": "country_cooldown"}],
    ]:
        assert modifiers.modifier_problems({"modifiers": entries})
    assert registry()["runtime_staircase"].compatible(ENGINE_REGISTRY["rt_split"])
    assert registry()["country_cooldown"].compatible(ENGINE_REGISTRY["roulette"])
    assert registry()["chrono_direction"].compatible(ENGINE_REGISTRY["cinechain"]) is None
