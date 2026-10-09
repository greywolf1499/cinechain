"""Semantic anchor projections reuse the configured embedding space without storing vectors."""

import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from app.facets import semantic
from app.models.cache import CachedMovie
from app.services import embeddings


@pytest.fixture(autouse=True)
def clear_centroids():
    semantic._centroids.clear()
    semantic._async_locks.clear()
    yield
    semantic._centroids.clear()
    semantic._async_locks.clear()


@pytest.mark.anyio
async def test_anchor_centroids_embed_once_per_fingerprint_and_write_cache(tmp_path, monkeypatch):
    calls = []
    fingerprint = "local_onnx:test-preset"
    config = SimpleNamespace(fingerprint=fingerprint)
    monkeypatch.setattr(embeddings, "load_config", lambda _session: config)
    monkeypatch.setattr(
        semantic,
        "centroid_cache_path",
        lambda _config_dir, _fingerprint: tmp_path / f"{_fingerprint}.npz",
    )

    async def embed_batch(_config, texts):
        calls.append(texts)
        await asyncio.sleep(0)
        return SimpleNamespace(
            fingerprint=fingerprint,
            vectors=[
                np.array([1.0, float(index + 1)], dtype=np.float32) for index, _ in enumerate(texts)
            ],
        )

    monkeypatch.setattr(embeddings, "embed_batch", embed_batch)
    first, second = await asyncio.gather(
        semantic.anchor_centroids(None), semantic.anchor_centroids(None)
    )
    centroids = first[1]

    assert first[0] == fingerprint and second[0] == fingerprint
    assert set(centroids) == set(semantic.ANCHORS)
    assert len(calls) == 1
    assert (tmp_path / f"{fingerprint}.npz").is_file()


@pytest.mark.anyio
async def test_a_different_fingerprint_gets_its_own_anchor_centroids(tmp_path, monkeypatch):
    fingerprints = iter(["preset-a", "preset-b"])
    calls = []
    monkeypatch.setattr(
        embeddings,
        "load_config",
        lambda _session: SimpleNamespace(fingerprint=next(fingerprints)),
    )
    monkeypatch.setattr(
        semantic,
        "centroid_cache_path",
        lambda _config_dir, fingerprint: tmp_path / f"{fingerprint}.npz",
    )

    async def embed_batch(config, texts):
        calls.append(config.fingerprint)
        return SimpleNamespace(
            fingerprint=config.fingerprint,
            vectors=[np.array([1.0, 1.0], dtype=np.float32) for _ in texts],
        )

    monkeypatch.setattr(embeddings, "embed_batch", embed_batch)
    first = await semantic.anchor_centroids(None)
    second = await semantic.anchor_centroids(None)

    assert first[0] == "preset-a"
    assert second[0] == "preset-b"
    assert calls == ["preset-a", "preset-b"]


@pytest.mark.parametrize(
    ("valence", "arousal", "expected"),
    [
        (0.1, 0.1, "euphoric"),
        (0.1, -0.1, "serene"),
        (-0.1, 0.1, "tense"),
        (-0.1, -0.1, "melancholic"),
        (0.099, 0.4, None),
        (-0.6, -0.099, None),
    ],
)
def test_quadrant_thresholds(valence, arousal, expected):
    assert semantic.quadrant(valence, arousal) == expected


def test_mismatched_vector_is_unknown():
    centroids = {name: np.array([1.0, 0.0], dtype=np.float32) for name in semantic.ANCHORS}
    assert (
        semantic.project_vector(np.array([1.0], dtype=np.float32), centroids, "fingerprint") == {}
    )


@pytest.mark.anyio
async def test_movie_without_current_fingerprint_embedding_is_unknown(monkeypatch):
    fingerprint = "active-model"
    monkeypatch.setattr(
        embeddings, "load_config", lambda _session: SimpleNamespace(fingerprint=fingerprint)
    )
    monkeypatch.setattr(embeddings, "row_fingerprint", lambda _stored: fingerprint)
    movie = CachedMovie(tmdb_id=9, title="Unknown", overview_embedding=None)

    assert await semantic.movie_semantics(None, movie) is None
