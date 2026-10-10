"""Phase 24a: the opt-in generative model (local GGUF / Ollama / OpenAI-compatible)."""

import asyncio
import json
import sys
import time
from typing import ClassVar

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app
from app.services import llm

TMDB_BASE = "https://api.themoviedb.org/3"
OLLAMA_CHAT = "http://localhost:11434/api/chat"


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


@pytest.fixture(autouse=True)
def _clean_llm_state():
    llm.clear_cache()
    llm.unload_local()
    yield
    llm.unload_local()
    llm.clear_cache()


def mock_movies():
    for movie_id, title, overview in (
        (1, "Heat", "A crew of thieves and a obsessive detective collide in Los Angeles."),
        (2, "Collateral", "A cabbie is forced to drive a hitman across the city."),
    ):
        respx.get(f"{TMDB_BASE}/movie/{movie_id}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": movie_id,
                    "title": title,
                    "release_date": "1995-12-15",
                    "poster_path": None,
                    "overview": overview,
                    "origin_country": ["US"],
                    "original_language": "en",
                    "runtime": 120,
                    "genres": [],
                    "popularity": 5.0,
                    "status": "Released",
                },
            )
        )


def enable_ollama(client, **extra):
    resp = client.patch("/api/settings/integrations", json={"llm_provider": "ollama", **extra})
    assert resp.status_code == 200, resp.text


def ollama_reply(text):
    return httpx.Response(200, json={"message": {"role": "assistant", "content": text}})


# --- output hygiene ---


def test_clean_output_strips_reasoning_quotes_and_whitespace():
    assert (
        llm.clean_output('<think>hmm\nlet me think</think>\n  "A neat   pitch."  ')
        == "A neat pitch."
    )
    assert llm.clean_output("unfinished thought</think>The real answer") == "The real answer"


def test_mask_title_hides_the_films_name():
    assert llm.mask_title("Heat is about heat.", "Heat") == "▒▒▒ is about ▒▒▒."
    assert llm.mask_title("It", "It") == "It"  # too short to mask safely


# --- remote providers ---


async def test_ollama_chat_request_shape_and_cleaning():
    config = llm.LlmConfig(provider="ollama")
    with respx.mock:
        route = respx.post(OLLAMA_CHAT).mock(
            return_value=ollama_reply("<think>x</think>Hello there.")
        )
        text = await llm.generate(config, "sys", "user prompt", max_tokens=40)
    body = json.loads(route.calls[0].request.content)
    assert text == "Hello there."
    assert body["model"] == "qwen3.5:0.8b" and body["stream"] is False
    assert body["options"]["num_predict"] == 40
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["messages"][1]["content"] == "user prompt /no_think"  # Qwen soft switch


async def test_openai_chat_sends_the_key_and_normalises_the_url():
    config = llm.LlmConfig(
        provider="openai", base_url="https://llm.example/v1", api_key="sk-x", model="qwen"
    )
    with respx.mock:
        route = respx.post("https://llm.example/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "Pitch."}}]})
        )
        assert await llm.generate(config, "s", "p") == "Pitch."
    assert route.calls[0].request.headers["authorization"] == "Bearer sk-x"
    assert json.loads(route.calls[0].request.content)["model"] == "qwen"
    assert (
        llm.openai_url(llm.LlmConfig(provider="openai", base_url="http://h:1"))
        == "http://h:1/v1/chat/completions"
    )


@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(500, json={"error": "model not found"}),
        httpx.Response(200, json={"unexpected": 1}),
        httpx.Response(200, json={"message": {"content": "   "}}),
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
    ],
)
async def test_every_failure_is_an_llm_unavailable(failure):
    with respx.mock:
        route = respx.post(OLLAMA_CHAT)
        route.mock(side_effect=failure) if isinstance(failure, Exception) else route.mock(
            return_value=failure
        )
        with pytest.raises(llm.LlmUnavailable):
            await llm.generate(llm.LlmConfig(provider="ollama"), "s", "p")


async def test_off_refuses_to_generate():
    with pytest.raises(llm.LlmUnavailable, match="off"):
        await llm.generate(llm.LlmConfig(), "s", "p")


async def test_structured_generation_strips_reasoning_and_json_fences():
    class Reply(BaseModel):
        text: str = Field(min_length=3)

    with respx.mock:
        respx.post(OLLAMA_CHAT).mock(
            return_value=ollama_reply('<think>reason</think>```json\n{"text":"Grounded."}\n```')
        )
        result = await llm.generate_structured(
            llm.LlmConfig(provider="ollama"), "system", {"fact": "Grounded."}, Reply
        )
    assert result.value.text == "Grounded."
    assert result.source == "ai"


async def test_structured_generation_retries_then_uses_validated_fallback():
    class Reply(BaseModel):
        text: str

    with respx.mock:
        route = respx.post(OLLAMA_CHAT).mock(
            side_effect=[
                ollama_reply("not JSON"),
                ollama_reply('{"text":"still not allowed"}'),
            ]
        )
        result = await llm.generate_structured(
            llm.LlmConfig(provider="ollama"),
            "system",
            {},
            Reply,
            fallback=Reply(text="Template."),
            validator=lambda value: value.text == "Template.",
        )
    assert route.call_count == 2
    assert result.value.text == "Template."
    assert result.source == "template"


async def test_structured_generation_rejects_invalid_fallback():
    class Reply(BaseModel):
        text: str

    with pytest.raises(llm.LlmUnavailable, match="fallback"):
        await llm.generate_structured(
            llm.LlmConfig(),
            "system",
            {},
            Reply,
            fallback=Reply(text="bad"),
            validator=lambda value: value.text == "good",
        )


# --- local GGUF: JIT load, idle unload ---


class FakeLlama:
    instances: ClassVar[list["FakeLlama"]] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = False
        FakeLlama.instances.append(self)

    def create_chat_completion(self, messages, max_tokens, **_):
        return {"choices": [{"message": {"content": f"Local says: {messages[-1]['content'][:5]}"}}]}

    def close(self):
        self.closed = True


@pytest.fixture()
def fake_llama(monkeypatch, tmp_path):
    FakeLlama.instances = []
    monkeypatch.setattr(llm, "_import_llama", lambda: FakeLlama)
    monkeypatch.setattr(llm, "ensure_model_file", lambda config: tmp_path / "model.gguf")
    return FakeLlama


async def test_local_model_unloads_immediately_when_keep_alive_is_zero(fake_llama):
    config = llm.LlmConfig(provider="local_gguf", keep_alive_seconds=0)
    assert (await llm.generate(config, "s", "hello")).startswith("Local says")
    assert not llm.local_model_loaded()
    assert fake_llama.instances[0].closed


async def test_local_model_stays_loaded_between_calls_then_unloads_when_idle(fake_llama):
    config = llm.LlmConfig(provider="local_gguf", keep_alive_seconds=1)
    await llm.generate(config, "s", "one")
    await llm.generate(config, "s", "two")
    assert len(fake_llama.instances) == 1 and llm.local_model_loaded()  # loaded once, reused
    deadline = time.monotonic() + 4
    while llm.local_model_loaded() and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
    assert not llm.local_model_loaded() and fake_llama.instances[0].closed


async def test_local_model_is_loaded_with_a_small_footprint(fake_llama):
    await llm.generate(llm.LlmConfig(provider="local_gguf", keep_alive_seconds=0), "s", "x")
    kwargs = fake_llama.instances[0].kwargs
    assert kwargs["n_ctx"] <= 2048 and kwargs["use_mlock"] is False and kwargs["n_gpu_layers"] == 0


async def test_local_provider_explains_a_missing_runtime(monkeypatch):
    monkeypatch.setitem(sys.modules, "llama_cpp", None)  # makes `import llama_cpp` fail
    with pytest.raises(llm.LlmUnavailable, match="llama-cpp-python"):
        await llm.generate(llm.LlmConfig(provider="local_gguf"), "s", "p")


async def test_a_crashing_local_model_is_unloaded_and_reported(monkeypatch, tmp_path):
    class Crashy(FakeLlama):
        def create_chat_completion(self, *args, **kwargs):
            raise RuntimeError("out of memory")

    monkeypatch.setattr(llm, "_import_llama", lambda: Crashy)
    monkeypatch.setattr(llm, "ensure_model_file", lambda config: tmp_path / "m.gguf")
    with pytest.raises(llm.LlmUnavailable, match="out of memory"):
        await llm.generate(llm.LlmConfig(provider="local_gguf", keep_alive_seconds=300), "s", "p")
    assert not llm.local_model_loaded()


def test_the_gguf_is_downloaded_once_into_the_config_dir(config_dir, monkeypatch):
    fetched = []

    def fake_download(url, dest, progress=None):
        fetched.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x")

    monkeypatch.setattr(llm, "_download", fake_download)
    config = llm.LlmConfig(provider="local_gguf")
    first = llm.ensure_model_file(config)
    assert llm.ensure_model_file(config) == first and len(fetched) == 1
    assert first == config_dir / "models" / "qwen3.5-0.8b-instruct-q4_k_m.gguf"
    assert "Qwen3.5-0.8B-GGUF" in fetched[0]


def test_a_legacy_download_is_reused_at_the_fixed_path(config_dir):
    legacy = config_dir / "models" / "qwen3.5-0.8b" / "Qwen3.5-0.8B-Q4_K_M.gguf"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"gguf")
    status = llm.local_model_status()
    assert status["downloaded"] and status["size_bytes"] == 4
    assert status["path"] == str(config_dir / "models" / "qwen3.5-0.8b-instruct-q4_k_m.gguf")


def test_a_manual_model_name_never_changes_the_local_file(config_dir):
    config = llm.LlmConfig(provider="local_gguf", model="/etc/passwd")
    assert config.effective_model == llm.LOCAL_MODEL_FILENAME


# --- settings API ---


def test_llm_download_status_and_one_click_download(client, config_dir, monkeypatch):
    before = client.get("/api/settings/integrations/llm/download-status").json()
    assert before["downloaded"] is False and before["size_bytes"] == 0
    assert before["path"] == str(config_dir / "models" / "qwen3.5-0.8b-instruct-q4_k_m.gguf")
    assert before["downloading"] is False

    def fake_download(url, dest, progress=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        progress(50, 100)
        dest.write_bytes(b"x" * 10)

    monkeypatch.setattr(llm, "_download", fake_download)
    started = client.post("/api/settings/integrations/llm/download")
    assert started.status_code == 200
    after = client.get("/api/settings/integrations/llm/download-status").json()
    assert after["downloaded"] is True and after["size_bytes"] == 10

    # Already on disk: a second request neither downloads nor errors.
    monkeypatch.setattr(llm, "_download", lambda *a, **k: pytest.fail("downloaded twice"))
    assert client.post("/api/settings/integrations/llm/download").json()["downloaded"] is True


def test_a_failed_download_is_reported(client, monkeypatch):
    def broken(url, dest, progress=None):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(llm, "_download", broken)
    client.post("/api/settings/integrations/llm/download")
    status = client.get("/api/settings/integrations/llm/download-status").json()
    assert status["downloaded"] is False and status["downloading"] is False
    assert "offline" in status["error"]


def test_download_endpoints_need_an_admin(config_dir):
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/settings/integrations/llm/download-status").status_code == 401
        assert anonymous.post("/api/settings/integrations/llm/download").status_code == 401


def test_llm_settings_default_to_off_and_mask_the_key(client):
    initial = client.get("/api/settings/integrations").json()
    assert initial["llm_provider"] == "off" and initial["llm_keep_alive_seconds"] == 300
    assert isinstance(initial["llm_local_available"], bool)

    saved = client.patch(
        "/api/settings/integrations",
        json={
            "llm_provider": "openai",
            "llm_base_url": "http://x:1",
            "llm_api_key": "sk-abcdef1234",
            "llm_model": "qwen",
            "llm_keep_alive_seconds": 0,
        },
    ).json()
    assert saved["llm_provider"] == "openai" and saved["llm_keep_alive_seconds"] == 0
    assert saved["llm_api_key_masked"] == "****1234" and "sk-abcdef1234" not in json.dumps(saved)


@pytest.mark.parametrize(
    "payload",
    [{"llm_provider": "magic"}, {"llm_keep_alive_seconds": -1}, {"llm_keep_alive_seconds": 99999}],
)
def test_invalid_llm_settings_are_rejected(client, payload):
    assert client.patch("/api/settings/integrations", json=payload).status_code == 422


def test_test_llm_endpoint(client):
    with respx.mock:
        respx.post(OLLAMA_CHAT).mock(return_value=ollama_reply("Movie night rules!"))
        ok = client.post("/api/settings/integrations/test-llm", json={"llm_provider": "ollama"})
    assert ok.status_code == 200
    assert ok.json()["ok"] is True and ok.json()["output"] == "Movie night rules!"
    assert ok.json()["model"] == "qwen3.5:0.8b" and ok.json()["latency_ms"] is not None

    with respx.mock:
        respx.post(OLLAMA_CHAT).mock(side_effect=httpx.ConnectError("refused"))
        down = client.post("/api/settings/integrations/test-llm", json={"llm_provider": "ollama"})
    assert down.json()["ok"] is False and "refused" in down.json()["detail"]
    off = client.post("/api/settings/integrations/test-llm", json={"llm_provider": "off"})
    assert off.json()["ok"] is False


# --- pitch + teaser endpoints ---


def test_status_reflects_the_setting(client):
    assert client.get("/api/engine/llm/status").json() == {"enabled": False, "provider": "off"}
    enable_ollama(client)
    assert client.get("/api/engine/llm/status").json() == {"enabled": True, "provider": "ollama"}


def test_pitch_is_refused_while_the_model_is_off(client):
    resp = client.post("/api/engine/pitch", json={"previous_movie_id": 1, "candidate_movie_id": 2})
    assert resp.status_code == 409 and "off" in resp.json()["detail"]


def test_pitch_endpoint_grounds_the_prompt_and_caches(client):
    enable_ollama(client)
    with respx.mock:
        mock_movies()
        chat = respx.post(OLLAMA_CHAT).mock(
            return_value=ollama_reply('{"text":"Two LA nights, one stolen heartbeat."}')
        )
        payload = {"previous_movie_id": 1, "candidate_movie_id": 2, "link_label": "Jamie Foxx"}
        first = client.post("/api/engine/pitch", json=payload)
        second = client.post("/api/engine/pitch", json=payload)

    assert first.status_code == 200 and first.json() == {
        "pitch": "Two LA nights, one stolen heartbeat."
    }
    assert second.json() == first.json() and chat.call_count == 1  # cached
    prompt = json.loads(chat.calls[0].request.content)["messages"][1]["content"]
    assert "Heat (1995)" in prompt and "Collateral (1995)" in prompt and "Jamie Foxx" in prompt


def test_critic_style_uses_the_veto_advice_prompt_and_its_own_cache(client):
    enable_ollama(client)
    with respx.mock:
        mock_movies()
        chat = respx.post(OLLAMA_CHAT).mock(
            return_value=ollama_reply('{"text":"Brace for a three-hour slog."}')
        )
        base = {"previous_movie_id": 1, "candidate_movie_id": 2}
        critic = client.post("/api/engine/pitch", json={**base, "style": "critic"})
        plain = client.post("/api/engine/pitch", json=base)

    assert critic.status_code == 200 and plain.status_code == 200
    assert chat.call_count == 2  # the critic answer is not served for a plain pitch
    system = json.loads(chat.calls[0].request.content)["messages"][0]["content"]
    assert "exhausting" in system


def test_pitch_failure_uses_a_deterministic_fallback(client):
    enable_ollama(client)
    with respx.mock:
        mock_movies()
        respx.post(OLLAMA_CHAT).mock(side_effect=httpx.ConnectError("refused"))
        resp = client.post(
            "/api/engine/pitch", json={"previous_movie_id": 1, "candidate_movie_id": 2}
        )
    assert resp.status_code == 200
    assert "Collateral" in resp.json()["pitch"] and "Heat" in resp.json()["pitch"]


def test_teasers_are_title_free_and_partial_failures_are_tolerated(client):
    enable_ollama(client)
    replies = iter(
        [
            ollama_reply('{"text":"Neon streets hide a quiet pursuit."}'),
            httpx.ConnectError("boom"),
            httpx.ConnectError("boom"),
        ]
    )

    def chat(request):
        item = next(replies)
        if isinstance(item, Exception):
            raise item
        return item

    with respx.mock:
        mock_movies()
        respx.post(OLLAMA_CHAT).mock(side_effect=chat)
        resp = client.post("/api/engine/teasers", json={"movie_ids": [1, 2]})
    assert resp.status_code == 200
    assert resp.json()["teasers"] == {
        "1": "Neon streets hide a quiet pursuit.",
        "2": "A curious journey shifts between intimate choices and larger stakes.",
    }


def test_teasers_all_failing_use_title_free_templates(client):
    enable_ollama(client)
    with respx.mock:
        mock_movies()
        respx.post(OLLAMA_CHAT).mock(side_effect=httpx.ConnectError("refused"))
        resp = client.post("/api/engine/teasers", json={"movie_ids": [1]})
    assert resp.status_code == 200
    assert "1" in resp.json()["teasers"]


def test_teasers_validate_the_batch_size(client):
    assert client.post("/api/engine/teasers", json={"movie_ids": []}).status_code == 422
    assert client.post("/api/engine/teasers", json={"movie_ids": list(range(9))}).status_code == 422
