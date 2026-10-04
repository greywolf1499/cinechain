"""Phase 25c: LLM discrete trope extraction and shared-trope hops in the Semantic Trope Web."""

import json
import re

import httpx
import pytest
import respx
from sqlmodel import Session

from app.models.cache import CachedMovie
from app.services import llm
from tests.test_algorithm_sandbox import (
    PLOTS,
    TMDB_BASE,
    client,
    create_run,
    db_engine,
    fake_model,
    log,
    mock_universe,
    run_steps,
)

OLLAMA_CHAT = "http://localhost:11434/api/chat"

# What the fake model "extracts" for each fake overview.
TROPES_BY_PLOT = {
    "heist": ["heist", "crime"],
    "heist-ish": ["double-cross", "crime"],
    "romance": ["heist", "love-story"],  # shares a trope with "heist" despite an unrelated plot
    "": ["never-asked"],
}

__all__ = ["client", "db_engine", "fake_model"]


@pytest.fixture(autouse=True)
def _clean_llm_state():
    llm.clear_cache()
    yield
    llm.clear_cache()


def mock_llm(client, replies: dict[str, object] | None = None) -> respx.Route:
    """Enable Ollama and answer every extraction prompt from `replies` (plot -> tropes)."""
    resp = client.patch("/api/settings/integrations", json={"llm_provider": "ollama"})
    assert resp.status_code == 200, resp.text
    replies = TROPES_BY_PLOT if replies is None else replies

    def answer(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        plot = re.search(r"Plot: (.*)\n", body["messages"][1]["content"]).group(1)
        reply = replies[plot]
        text = reply if isinstance(reply, str) else json.dumps(reply)
        return httpx.Response(200, json={"message": {"role": "assistant", "content": text}})

    return respx.post(OLLAMA_CHAT).mock(side_effect=answer)


# --- extract_tropes ---


@pytest.mark.parametrize("reply,expected", [
    ('["heist", "time-loop", "cyberpunk"]', ["heist", "time-loop", "cyberpunk"]),
    ('```json\n["Time Loop", "Heist!", "heist"]\n```', ["time-loop", "heist"]),
    ('<think>hmm</think>["unreliable-narrator"]', ["unreliable-narrator"]),
    ("heist, time loop; cyberpunk", ["heist", "time-loop", "cyberpunk"]),
    ('["a","b","c","d","e","f","g"]', ["a", "b", "c", "d", "e"]),
    ('[1, null, "ok"]', ["ok"]),
])
def test_parse_tropes_normalizes_to_kebab_case(reply, expected):
    assert llm.parse_tropes(reply) == expected


@pytest.mark.anyio
async def test_extract_tropes_is_empty_when_the_llm_is_off():
    assert await llm.extract_tropes("A crew plans a heist.", llm.LlmConfig()) == []


@pytest.mark.anyio
async def test_extract_tropes_prompts_the_configured_model():
    config = llm.LlmConfig(provider="ollama")
    with respx.mock:
        route = respx.post(OLLAMA_CHAT).mock(return_value=httpx.Response(200, json={
            "message": {"role": "assistant", "content": '["heist", "Time Loop"]'}}))
        tropes = await llm.extract_tropes("A crew plans a heist.", config)
    assert tropes == ["heist", "time-loop"]
    assert "kebab-case" in json.loads(route.calls[0].request.content)["messages"][0]["content"]


@pytest.mark.anyio
async def test_extract_tropes_raises_when_the_model_fails():
    config = llm.LlmConfig(provider="ollama")
    with respx.mock:
        respx.post(OLLAMA_CHAT).mock(return_value=httpx.Response(500))
        with pytest.raises(llm.LlmUnavailable):
            await llm.extract_tropes("A crew plans a heist.", config)


# --- POST /movies/{id}/tropes/extract ---


def test_extract_endpoint_returns_empty_and_caches_nothing_when_off(client, db_engine):
    with respx.mock:
        mock_universe(PLOTS)
        resp = client.post("/api/movies/1/tropes/extract")
    assert resp.status_code == 200
    assert resp.json() == {"tmdb_id": 1, "tropes": [], "cached": False, "enabled": False}
    with Session(db_engine) as session:
        assert session.get(CachedMovie, 1).extracted_tropes is None


def test_extract_endpoint_caches_the_result_and_only_asks_once(client, db_engine):
    with respx.mock:
        mock_universe(PLOTS)
        route = mock_llm(client)
        first = client.post("/api/movies/1/tropes/extract").json()
        second = client.post("/api/movies/1/tropes/extract").json()
        detail = client.get("/api/movies/1").json()

    assert first == {"tmdb_id": 1, "tropes": ["heist", "crime"], "cached": False, "enabled": True}
    assert second["cached"] is True and second["tropes"] == ["heist", "crime"]
    assert route.call_count == 1
    assert detail["extracted_tropes"] == ["heist", "crime"]
    with Session(db_engine) as session:
        assert session.get(CachedMovie, 1).extracted_tropes == ["heist", "crime"]


def test_extract_endpoint_reports_a_failing_model_without_caching(client, db_engine):
    with respx.mock:
        mock_universe(PLOTS)
        client.patch("/api/settings/integrations", json={"llm_provider": "ollama"})
        respx.post(OLLAMA_CHAT).mock(return_value=httpx.Response(500))
        resp = client.post("/api/movies/1/tropes/extract")
    assert resp.status_code == 503
    with Session(db_engine) as session:
        assert session.get(CachedMovie, 1).extracted_tropes is None


def test_changing_the_overview_drops_the_stale_tropes(client, db_engine):
    with respx.mock:
        mock_universe(PLOTS)
        mock_llm(client)
        client.post("/api/movies/1/tropes/extract")
        respx.get(f"{TMDB_BASE}/movie/1").mock(return_value=httpx.Response(200, json={
            "id": 1, "title": "Heist One", "release_date": "2000-01-01", "poster_path": None,
            "overview": "a new plot", "origin_country": ["US"], "original_language": "en",
            "runtime": 100, "genres": [], "popularity": 9, "status": "Released"}))
        refreshed = client.get("/api/movies/1", params={"refresh": True}).json()
    assert refreshed["extracted_tropes"] is None


# --- shared-trope hops ---


def test_a_shared_trope_validates_a_hop_the_plots_would_block(client, fake_model):
    run_id = create_run(client, "semantic_trope")
    with respx.mock:
        mock_universe(PLOTS)
        mock_llm(client)
        assert log(client, run_id, 1).status_code == 201
        hop = log(client, run_id, 3)  # orthogonal plot, but both are tagged "heist"

    assert hop.status_code == 201, hop.text
    meta = hop.json()["transition_metadata"]
    assert meta["shared_trope"] == "heist"
    assert run_steps(client, run_id)[1]["transition_metadata"]["shared_trope"] == "heist"


def test_the_trope_is_recorded_alongside_a_plot_match(client, fake_model):
    run_id = create_run(client, "semantic_trope")
    with respx.mock:
        mock_universe(PLOTS)
        mock_llm(client)
        log(client, run_id, 1)
        hop = log(client, run_id, 2)  # close plot, and shares "crime"

    meta = hop.json()["transition_metadata"]
    assert meta["shared_trope"] == "crime"
    assert meta["semantic_score"] == pytest.approx(0.8944, abs=1e-3)


def test_dissimilar_plots_without_a_shared_trope_stay_blocked(client, fake_model):
    run_id = create_run(client, "semantic_trope")
    replies = {**TROPES_BY_PLOT, "romance": ["love-story"]}
    with respx.mock:
        mock_universe(PLOTS)
        mock_llm(client, replies)
        log(client, run_id, 1)
        for force in (False, True):
            blocked = log(client, run_id, 3, force=force)
            assert blocked.status_code == 409
            assert blocked.json()["detail"]["blocked"] is True
        assert "share no trope" in blocked.json()["detail"]["reason"]


def test_a_client_cannot_forge_a_shared_trope(client, fake_model):
    run_id = create_run(client, "semantic_trope")
    with respx.mock:
        mock_universe(PLOTS)
        mock_llm(client)
        log(client, run_id, 1)
        hop = log(client, run_id, 2, transition_metadata={"shared_trope": "time-loop"})
    assert hop.json()["transition_metadata"]["shared_trope"] == "crime"


def test_without_the_llm_the_plot_rule_alone_applies(client, fake_model):
    run_id = create_run(client, "semantic_trope")
    with respx.mock:
        mock_universe(PLOTS)
        log(client, run_id, 1)
        assert log(client, run_id, 3).status_code == 409
        ok = log(client, run_id, 2)
    assert ok.status_code == 201
    assert "shared_trope" not in ok.json()["transition_metadata"]


def test_a_failing_llm_never_blocks_a_plot_match(client, fake_model):
    run_id = create_run(client, "semantic_trope")
    with respx.mock:
        mock_universe(PLOTS)
        client.patch("/api/settings/integrations", json={"llm_provider": "ollama"})
        respx.post(OLLAMA_CHAT).mock(return_value=httpx.Response(500))
        log(client, run_id, 1)
        assert log(client, run_id, 2).status_code == 201


def test_pick_next_offers_trope_sharers_and_exposes_tropes(client, fake_model):
    run_id = create_run(client, "semantic_trope")
    related = [
        {"id": i, "title": PLOTS[i]["title"], "release_date": "2000-06-01", "poster_path": None,
         "genre_ids": [], "original_language": "en", "popularity": PLOTS[i]["popularity"]}
        for i in (2, 3)]
    with respx.mock:
        mock_universe(PLOTS)
        mock_llm(client)
        respx.get(f"{TMDB_BASE}/movie/1/recommendations").mock(
            return_value=httpx.Response(200, json={"results": related}))
        respx.get(f"{TMDB_BASE}/movie/1/similar").mock(
            return_value=httpx.Response(200, json={"results": []}))
        log(client, run_id, 1)
        pool = {c["movie_id"]: c for c in client.get(
            f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 1}).json()}

    # "Love Story" has an orthogonal plot, but shares the "heist" trope with the frontier.
    assert set(pool) == {2, 3}
    assert pool[3]["tropes"] == ["heist", "love-story"]
    assert pool[2]["tropes"] == ["double-cross", "crime"]
