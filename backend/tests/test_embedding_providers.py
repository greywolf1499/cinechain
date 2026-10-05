"""Phase 23b: pluggable embedding providers (local ONNX / Ollama / OpenAI-compatible)."""

import json

import httpx
import numpy as np
import pytest
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app
from app.services import embeddings

OLLAMA = embeddings.EmbeddingConfig(provider="ollama", model="all-minilm")
OPENAI = embeddings.EmbeddingConfig(
    provider="openai", base_url="https://llm.example/v1", api_key="sk-secret", model="emb-1"
)


@pytest.fixture()
def client(config_dir):
    engine = create_engine(
        f"sqlite:///{config_dir}/app_test.db", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)

    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        test_client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password123", "display_name": "Alice"},
        )
        test_client.post("/api/auth/login", json={"username": "alice", "password": "password123"})
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def local_model(monkeypatch):
    calls = []

    def embed_texts(texts, preset=None):
        calls.append(list(texts))
        return [np.ones(384, dtype=np.float32) / np.sqrt(384) for _ in texts]

    monkeypatch.setattr(embeddings, "embed_texts", embed_texts)
    return calls


async def test_ollama_embeddings_are_unit_vectors_of_the_models_width():
    with respx.mock:
        route = respx.post("http://localhost:11434/api/embeddings").mock(
            return_value=httpx.Response(200, json={"embedding": [3.0, 4.0, 0.0]})
        )
        batch = await embeddings.embed_batch(OLLAMA, ["a", "b"])
    assert route.call_count == 2
    assert json.loads(route.calls[0].request.content)["model"] == "all-minilm"
    assert batch.fingerprint == "ollama:all-minilm" and not batch.fell_back
    assert [v.shape for v in batch.vectors] == [(3,), (3,)]
    assert np.allclose(batch.vectors[0], [0.6, 0.8, 0.0])


@pytest.mark.parametrize(
    "base_url, expected",
    [
        ("https://llm.example/v1", "https://llm.example/v1/embeddings"),
        ("https://llm.example", "https://llm.example/v1/embeddings"),
        ("https://llm.example/v1/embeddings", "https://llm.example/v1/embeddings"),
        ("", "https://api.openai.com/v1/embeddings"),
    ],
)
def test_openai_endpoint_is_normalised(base_url, expected):
    assert (
        embeddings.openai_url(embeddings.EmbeddingConfig(provider="openai", base_url=base_url))
        == expected
    )


async def test_openai_embeddings_send_the_key_and_respect_index_order():
    with respx.mock:
        route = respx.post("https://llm.example/v1/embeddings").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {"index": 1, "embedding": [0.0, 2.0]},
                        {"index": 0, "embedding": [2.0, 0.0]},
                    ]
                },
            )
        )
        batch = await embeddings.embed_batch(OPENAI, ["first", "second"])
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer sk-secret"
    assert json.loads(request.content) == {"model": "emb-1", "input": ["first", "second"]}
    assert np.allclose(batch.vectors[0], [1.0, 0.0]) and np.allclose(batch.vectors[1], [0.0, 1.0])
    assert batch.fingerprint == "openai:emb-1"


@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(500, text="boom"),
        httpx.Response(200, json={"unexpected": True}),
        httpx.Response(200, text="not json"),
        httpx.ReadTimeout("slow"),
        httpx.ConnectError("refused"),
    ],
)
async def test_a_failing_provider_falls_back_to_local_without_raising(failure, local_model):
    with respx.mock:
        route = respx.post("http://localhost:11434/api/embeddings")
        route.mock(side_effect=failure) if isinstance(failure, Exception) else route.mock(
            return_value=failure
        )
        batch = await embeddings.embed_batch(OLLAMA, ["a"])
        again = await embeddings.embed_batch(OLLAMA, ["b"])
    assert batch.fell_back and batch.fingerprint == OLLAMA.local_fingerprint
    assert again.fell_back and local_model == [["a"], ["b"]]
    assert route.call_count == 1  # suspended: the dead server costs one timeout, not one per call


async def test_slow_providers_are_capped(monkeypatch, local_model):
    import asyncio

    monkeypatch.setattr(embeddings, "EXTERNAL_TIMEOUT_SECONDS", 0.05)

    async def slow(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json={"embedding": [1.0]})

    with respx.mock:
        respx.post("http://localhost:11434/api/embeddings").mock(side_effect=slow)
        batch = await embeddings.embed_batch(OLLAMA, ["a"])
    assert batch.fell_back


async def test_local_failure_is_the_only_hard_error(monkeypatch):
    def offline(texts, preset=None):
        raise embeddings.EmbeddingUnavailable("no model")

    monkeypatch.setattr(embeddings, "embed_texts", offline)
    with respx.mock:
        respx.post("http://localhost:11434/api/embeddings").mock(
            side_effect=httpx.ConnectError("refused")
        )
        with pytest.raises(embeddings.EmbeddingUnavailable):
            await embeddings.embed_batch(OLLAMA, ["a"])


async def test_check_connection_reports_latency_and_dimension():
    with respx.mock:
        respx.post("http://localhost:11434/api/embeddings").mock(
            return_value=httpx.Response(200, json={"embedding": [0.1] * 768})
        )
        ok = await embeddings.check_connection(OLLAMA)
        respx.post("http://localhost:11434/api/embeddings").mock(
            return_value=httpx.Response(404, json={"error": "model not found"})
        )
        bad = await embeddings.check_connection(OLLAMA)
    assert ok["ok"] is True and ok["dimension"] == 768 and ok["latency_ms"] >= 0
    assert bad["ok"] is False and bad["dimension"] is None
    assert "404" in bad["detail"] and "model not found" in bad["detail"]


# --- settings API ---


def test_embedding_settings_default_to_local_and_mask_the_key(client):
    initial = client.get("/api/settings/integrations").json()
    assert initial["embedding_provider"] == "local_onnx"
    assert initial["embedding_api_key_masked"] is None

    saved = client.patch(
        "/api/settings/integrations",
        json={
            "embedding_provider": "openai",
            "embedding_base_url": "https://llm.example/v1",
            "embedding_api_key": "sk-abcdef1234",
            "embedding_model": "emb-1",
        },
    ).json()
    assert saved["embedding_provider"] == "openai" and saved["embedding_model"] == "emb-1"
    assert saved["embedding_api_key_masked"] == "****1234"
    assert "sk-abcdef1234" not in json.dumps(saved)

    reverted = client.patch(
        "/api/settings/integrations",
        json={"embedding_provider": "local_onnx", "embedding_api_key": ""},
    ).json()
    assert reverted["embedding_provider"] == "local_onnx"
    assert reverted["embedding_api_key_masked"] is None


def test_unknown_embedding_provider_is_rejected(client):
    resp = client.patch("/api/settings/integrations", json={"embedding_provider": "magic"})
    assert resp.status_code == 422


def test_test_connection_endpoint(client):
    with respx.mock:
        respx.post("http://localhost:11434/api/embeddings").mock(
            return_value=httpx.Response(200, json={"embedding": [0.5] * 384})
        )
        ok = client.post(
            "/api/settings/integrations/test-embeddings",
            json={"embedding_provider": "ollama", "embedding_model": "all-minilm"},
        )
    assert ok.status_code == 200
    assert ok.json()["ok"] is True and ok.json()["dimension"] == 384
    assert ok.json()["model"] == "all-minilm" and ok.json()["latency_ms"] is not None

    with respx.mock:
        respx.post("http://localhost:11434/api/embeddings").mock(
            side_effect=httpx.ConnectError("refused")
        )
        down = client.post(
            "/api/settings/integrations/test-embeddings", json={"embedding_provider": "ollama"}
        )
    assert down.json()["ok"] is False and "refused" in down.json()["detail"]


def test_test_connection_uses_the_stored_key_when_blank(client):
    client.patch("/api/settings/integrations", json={"embedding_api_key": "sk-stored"})
    with respx.mock:
        route = respx.post("https://llm.example/v1/embeddings").mock(
            return_value=httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]})
        )
        client.post(
            "/api/settings/integrations/test-embeddings",
            json={"embedding_provider": "openai", "embedding_base_url": "https://llm.example/v1"},
        )
    assert route.calls[0].request.headers["authorization"] == "Bearer sk-stored"


def test_settings_require_an_admin(client):
    client.post("/api/auth/logout")
    assert client.post(
        "/api/settings/integrations/test-embeddings", json={"embedding_provider": "local_onnx"}
    ).status_code in (401, 403)


# --- Phase 25a: selectable local ONNX presets ---


def test_presets_are_384d_hf_hosted_and_the_default_is_arctic():
    assert set(embeddings.LOCAL_PRESETS) == {
        "arctic-embed-xs",
        "multilingual-e5-small",
        "all-minilm-l6-v2",
    }
    assert embeddings.DEFAULT_LOCAL_PRESET == "arctic-embed-xs"
    assert embeddings.LOCAL_PRESETS["arctic-embed-xs"].recommended
    for preset in embeddings.LOCAL_PRESETS.values():
        assert preset.model_url.startswith("https://huggingface.co/")
        assert preset.tokenizer_url.startswith("https://huggingface.co/")
        assert preset.model_url.endswith(".onnx")
    assert embeddings.LOCAL_PRESETS["arctic-embed-xs"].pooling == "cls"
    assert embeddings.LOCAL_PRESETS["multilingual-e5-small"].text_prefix == "query: "
    # vectors stored before presets existed (NULL model) are still the legacy preset's
    assert embeddings.LOCAL_PRESETS["all-minilm-l6-v2"].fingerprint == embeddings.LOCAL_FINGERPRINT


def test_unknown_presets_fall_back_to_the_default():
    assert embeddings.get_preset("nope").key == embeddings.DEFAULT_LOCAL_PRESET
    assert embeddings.get_preset(None).key == embeddings.DEFAULT_LOCAL_PRESET


def test_each_preset_has_its_own_fingerprint():
    fingerprints = {
        embeddings.EmbeddingConfig(local_preset=key).fingerprint for key in embeddings.LOCAL_PRESETS
    }
    assert fingerprints == {
        "local_onnx:arctic-embed-xs",
        "local_onnx:multilingual-e5-small",
        "local_onnx:all-MiniLM-L6-v2",
    }


def test_similarity_is_rescaled_above_each_presets_floor():
    arctic = embeddings.LOCAL_PRESETS["arctic-embed-xs"]
    assert embeddings.normalize_similarity(1.0, arctic.fingerprint) == pytest.approx(1.0)
    assert embeddings.normalize_similarity(
        arctic.similarity_floor, arctic.fingerprint
    ) == pytest.approx(0.0)
    assert embeddings.normalize_similarity(0.8, arctic.fingerprint) < 0.2  # "unrelated" for Arctic
    # legacy / external models are used as they are
    assert embeddings.normalize_similarity(0.8, embeddings.LOCAL_FINGERPRINT) == 0.8
    assert embeddings.normalize_similarity(0.8, "ollama:nomic-embed-text") == 0.8


async def test_batches_use_the_selected_preset_and_record_its_fingerprint(monkeypatch):
    used = []

    def embed_texts(texts, preset=None):
        used.append(preset.key)
        return [np.ones(384, dtype=np.float32) for _ in texts]

    monkeypatch.setattr(embeddings, "embed_texts", embed_texts)
    config = embeddings.EmbeddingConfig(local_preset="multilingual-e5-small")
    batch = await embeddings.embed_batch(config, ["a"])
    assert used == ["multilingual-e5-small"]
    assert batch.fingerprint == "local_onnx:multilingual-e5-small" and not batch.fell_back


def test_preset_setting_is_saved_listed_and_validated(client):
    current = client.get("/api/settings/integrations").json()
    assert current["embedding_local_preset"] == "arctic-embed-xs"
    by_key = {p["key"]: p for p in current["embedding_local_presets"]}
    assert set(by_key) == {"arctic-embed-xs", "multilingual-e5-small", "all-minilm-l6-v2"}
    assert "High-Precision Retrieval" in by_key["arctic-embed-xs"]["badge"]
    assert "100+ Languages" in by_key["multilingual-e5-small"]["badge"]
    assert by_key["arctic-embed-xs"]["recommended"] and not by_key["arctic-embed-xs"]["downloaded"]

    saved = client.patch(
        "/api/settings/integrations", json={"embedding_local_preset": "multilingual-e5-small"}
    ).json()
    assert saved["embedding_local_preset"] == "multilingual-e5-small"
    rejected = client.patch(
        "/api/settings/integrations", json={"embedding_local_preset": "bert-xl"}
    )
    assert rejected.status_code == 422


def test_the_connection_test_uses_the_candidate_preset(client, monkeypatch):
    seen = []

    def embed_texts(texts, preset=None):
        seen.append(preset.key)
        return [np.ones(384, dtype=np.float32)]

    monkeypatch.setattr(embeddings, "embed_texts", embed_texts)
    result = client.post(
        "/api/settings/integrations/test-embeddings",
        json={"embedding_provider": "local_onnx", "embedding_local_preset": "all-minilm-l6-v2"},
    ).json()
    assert result["ok"] and result["model"] == "all-MiniLM-L6-v2" and result["dimension"] == 384
    assert seen == ["all-minilm-l6-v2"]
