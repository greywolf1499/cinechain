"""Phase F10: pure Vibe Controller and culture-neutral runtime tests."""

from types import SimpleNamespace

import numpy as np
import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.facets import semantic
from app.models.cache import CachedMovie
from app.services import embeddings, vibe_controller
from app.services.pool_options import is_chaser, needs_chaser


def test_language_medians_and_160_minute_hindi_runtime_are_relative():
    medians = vibe_controller.language_runtime_medians(
        [("hi", 150), ("hi", 150), ("en", 110), ("en", 120), ("ko", 125)]
    )
    assert medians["hi"] == 150
    assert vibe_controller.length_load(160, "hi", medians) == pytest.approx(0.5333, abs=0.001)
    assert not is_chaser(0.3, 160, "hi", medians)
    assert is_chaser(0.3, 140, "hi", medians)


def test_unknown_runtime_or_language_stays_unknown():
    medians = {"hi": 150.0}
    assert vibe_controller.length_load(None, "hi", medians) is None
    assert vibe_controller.length_load(90, "en", medians) is None
    assert vibe_controller.calculate_load(0.4, None) is None


def test_controller_fatigues_then_recovers_with_hysteresis():
    state = {"state": "steady", "integral": 0.0}
    state = vibe_controller.update_controller(state, 0.8, 0.55)
    assert state["state"] == "fatigued"
    assert state["pid_output"] > 0
    state = vibe_controller.update_controller(state, 0.4, 0.55)
    assert state["state"] == "recovering"
    state = vibe_controller.update_controller(state, 0.4, 0.55)
    assert state["state"] == "recovering"
    state = vibe_controller.update_controller(state, 0.55, 0.55)
    assert state["state"] == "steady"


def test_pid_integral_decays_and_anti_windup_is_bounded():
    state = {"state": "steady", "integral": 1.9}
    for _ in range(100):
        state = vibe_controller.update_controller(state, 1.0, 0.2)
    assert state["integral"] <= vibe_controller.INTEGRAL_LIMIT
    assert state["integral"] >= -vibe_controller.INTEGRAL_LIMIT
    old = dict(state)
    unknown = vibe_controller.update_controller(state, None, 0.2)
    assert unknown["integral"] == old["integral"]
    assert unknown["load"] == old["load"]


def test_pid_uses_the_specified_proportional_integral_and_derivative_weights():
    state = {
        "state": "steady",
        "integral": 0.2,
        "previous_error": 0.1,
    }
    updated = vibe_controller.update_controller(state, 0.7, 0.5)
    expected_integral = 0.2 * vibe_controller.INTEGRAL_DECAY + 0.2
    expected_output = 0.6 * 0.2 + 0.3 * expected_integral + 0.1 * 0.1
    assert updated["integral"] == pytest.approx(expected_integral)
    assert updated["pid_output"] == pytest.approx(expected_output)


def test_recent_loads_trigger_chaser_only_above_setpoint():
    assert needs_chaser([0.8, 0.7], 0.55)
    assert not needs_chaser([0.5, 0.55], 0.55)


@pytest.mark.anyio
async def test_neutral_drama_embeddings_have_no_language_specific_heaviness_bias(
    tmp_path, monkeypatch
):
    engine = create_engine(f"sqlite:///{tmp_path}/vibe.db")
    SQLModel.metadata.create_all(engine)
    fingerprint = "fixture-vibe-model"
    vector = np.array([1.0, 0.0], dtype=np.float32)
    centroids = {
        "valence_positive": np.array([1.0, 0.0], dtype=np.float32),
        "valence_negative": np.array([-1.0, 0.0], dtype=np.float32),
        "arousal_high": np.array([0.0, 1.0], dtype=np.float32),
        "arousal_low": np.array([0.0, -1.0], dtype=np.float32),
        "heaviness": np.array([1.0, 0.0], dtype=np.float32),
        "spectacle": np.array([-1.0, 0.0], dtype=np.float32),
    }
    monkeypatch.setattr(
        embeddings, "load_config", lambda _session: SimpleNamespace(fingerprint=fingerprint)
    )
    monkeypatch.setattr(embeddings, "row_fingerprint", lambda _stored: fingerprint)

    async def anchor_centroids(_session):
        return fingerprint, centroids

    monkeypatch.setattr(semantic, "anchor_centroids", anchor_centroids)
    with Session(engine) as session:
        session.add_all(
            [
                CachedMovie(
                    tmdb_id=movie_id,
                    title=title,
                    original_language=language,
                    runtime=runtime,
                    genre_ids=[18],
                    overview="A neutral fixture overview.",
                    overview_embedding=vector.tobytes(),
                    overview_embedding_model=fingerprint,
                )
                for movie_id, title, language, runtime in [
                    (1, "Hindi Drama", "hi", 160),
                    (2, "Korean Drama", "ko", 110),
                    (3, "English Drama", "en", 100),
                ]
            ]
        )
        session.commit()
        heaviness = await vibe_controller.cached_heaviness(session, [1, 2, 3])

    values = [heaviness[movie_id] for movie_id in (1, 2, 3)]
    assert max(values) - min(values) <= 0.15
    medians = {"hi": 150.0, "ko": 110.0, "en": 100.0}
    assert vibe_controller.length_load(160, "hi", medians) < 0.55
