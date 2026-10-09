"""Phase 25c: LLM discrete trope extraction and shared-trope hops in the Semantic Trope Web."""

import json
import re
from unittest.mock import Mock

import httpx
import numpy as np
import pytest
import respx
from sqlmodel import Session

from app.engines.algorithms import SemanticTropeEngine
from app.facets import tropes as trope_facets
from app.facets.models import MovieFacet
from app.models.cache import CachedMovie
from app.services import embeddings, llm, movie_features
from app.services.tmdb import TMDBClient
from tests.test_algorithm_sandbox import (
    PLOTS,
    TMDB_BASE,
    VECTORS,
    client,
    create_run,
    db_engine,
    fake_model,
    log,
    run_steps,
)
from tests.test_algorithm_sandbox import (
    mock_universe as _mock_universe,
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


def mock_universe(movies):
    return _mock_universe(
        {
            key: {**movie, "genre_ids": [80] if key != 3 else [35, 10749]}
            for key, movie in movies.items()
        }
    )


@pytest.fixture(autouse=True)
def _concept_vectors(fake_model, monkeypatch):
    vectors = {
        **VECTORS,
        movie_features.TROPE_DESCRIPTIONS["heist"]: VECTORS["heist"],
        trope_facets.DEFINITIONS["crime"]: VECTORS["heist"],
        trope_facets.DEFINITIONS["double-cross"]: VECTORS["heist-ish"],
        trope_facets.DEFINITIONS["love-story"]: VECTORS["romance"],
        trope_facets.DEFINITIONS["romance"]: VECTORS["romance"],
        trope_facets.DEFINITIONS["never-asked"]: VECTORS["heist"],
    }

    def embed(texts, preset=None):
        return [vectors[text] for text in texts]

    monkeypatch.setattr(embeddings, "embed_texts", embed)


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


@pytest.mark.parametrize(
    "reply,expected",
    [
        ('["heist", "time-loop", "cyberpunk"]', ["heist", "time-loop", "cyberpunk"]),
        ('```json\n["Time Loop", "Heist!", "heist"]\n```', ["time-loop", "heist"]),
        ('<think>hmm</think>["unreliable-narrator"]', ["unreliable-narrator"]),
        ("heist, time loop; cyberpunk", ["heist", "time-loop", "cyberpunk"]),
        ('["a","b","c","d","e","f","g"]', ["a", "b", "c", "d", "e"]),
        ('[1, null, "ok"]', ["ok"]),
    ],
)
def test_parse_tropes_normalizes_to_kebab_case(reply, expected):
    assert llm.parse_tropes(reply) == expected


@pytest.mark.anyio
async def test_extract_tropes_is_empty_when_the_llm_is_off():
    assert await llm.extract_tropes("A crew plans a heist.", llm.LlmConfig()) == []


@pytest.mark.anyio
async def test_extract_tropes_prompts_the_configured_model():
    config = llm.LlmConfig(provider="ollama")
    with respx.mock:
        route = respx.post(OLLAMA_CHAT).mock(
            return_value=httpx.Response(
                200, json={"message": {"role": "assistant", "content": '["heist", "Time Loop"]'}}
            )
        )
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
        respx.get(f"{TMDB_BASE}/movie/1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 1,
                    "title": "Heist One",
                    "release_date": "2000-01-01",
                    "poster_path": None,
                    "overview": "a new plot",
                    "origin_country": ["US"],
                    "original_language": "en",
                    "runtime": 100,
                    "genres": [],
                    "popularity": 9,
                    "status": "Released",
                },
            )
        )
        refreshed = client.get("/api/movies/1", params={"refresh": True}).json()
    assert refreshed["extracted_tropes"] is None


# --- shared-trope hops ---


def test_an_ungrounded_shared_trope_cannot_bypass_the_plot_guard(client, fake_model):
    run_id = create_run(client, "semantic_trope")
    with respx.mock:
        mock_universe(PLOTS)
        mock_llm(client)
        assert log(client, run_id, 1).status_code == 201
        hop = log(client, run_id, 3)  # orthogonal plot, but both are tagged "heist"

    assert hop.status_code == 409, hop.text
    assert len(run_steps(client, run_id)) == 1


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


def test_trope_evidence_unions_ai_tvtropes_and_manual_sources(client, db_engine):
    with respx.mock:
        mock_universe(PLOTS)
        assert client.get("/api/movies/1").status_code == 200
        assert client.post(
            "/api/movies/1/tropes/manual", json={"tropes": ["Heist"]}
        ).status_code == 200
        rejected = client.post("/api/movies/1/tropes/manual", json={"tropes": ["Cyberpunk"]})
        assert rejected.status_code == 422
    with Session(db_engine) as session:
        session.add(
            MovieFacet(
                movie_id=1,
                facet_id="trope",
                value_text="unmapped-story-page",
                source="tvtropes",
                confidence=None,
            )
        )
        session.add(
            MovieFacet(
                movie_id=1,
                facet_id="trope",
                value_text="heist",
                source="tvtropes",
                confidence=0.9,
                source_url="https://tvtropes.org/pmwiki/pmwiki.php/Main/Heist",
            )
        )
        session.add(
            MovieFacet(
                movie_id=1,
                facet_id="trope",
                value_text="heist",
                source="llm",
                confidence=0.9,
            )
        )
        session.commit()

    evidence = client.get("/api/movies/1/trope-evidence").json()
    assert evidence == {
        "tmdb_id": 1,
        "evidence": [
            {
                "slug": "heist",
                "sources": ["llm", "manual", "tvtropes"],
                "tvtropes_url": "https://tvtropes.org/pmwiki/pmwiki.php/Main/Heist",
            }
        ],
    }
    assert trope_facets.normalize_trope("TimeLoop") == "time-loop"
    assert not trope_facets.genre_allowed("cyberpunk", [35])
    assert trope_facets.genre_allowed("cyberpunk", [878])


@pytest.mark.anyio
async def test_engine_reads_manual_trope_facets_without_embeddings(db_engine):
    async with httpx.AsyncClient() as http_client:
        with Session(db_engine) as session:
            movies = [
                CachedMovie(tmdb_id=11, title="First", genre_ids=[80], overview="First plot"),
                CachedMovie(tmdb_id=12, title="Second", genre_ids=[80], overview="Second plot"),
            ]
            session.add_all(movies)
            session.commit()
            for film in movies:
                trope_facets.replace_source(
                    session, film.tmdb_id, "manual", {"heist": 1.0}
                )
            session.commit()
            engine = SemanticTropeEngine(session, TMDBClient(http_client))

            await engine.prepare_tropes(movies)

            assert all(film.overview_embedding is None for film in movies)
            assert engine._shared_trope(*movies) == "heist"


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
        {
            "id": i,
            "title": PLOTS[i]["title"],
            "release_date": "2000-06-01",
            "poster_path": None,
            "genre_ids": [],
            "original_language": "en",
            "popularity": PLOTS[i]["popularity"],
        }
        for i in (2, 3)
    ]
    with respx.mock:
        mock_universe(PLOTS)
        mock_llm(client)
        respx.get(f"{TMDB_BASE}/movie/1/recommendations").mock(
            return_value=httpx.Response(200, json={"results": related})
        )
        respx.get(f"{TMDB_BASE}/movie/1/similar").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        respx.get(f"{TMDB_BASE}/movie/1/keywords").mock(
            return_value=httpx.Response(200, json={"keywords": []})
        )
        respx.get(f"{TMDB_BASE}/discover/movie").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        log(client, run_id, 1)
        pool = {
            c["movie_id"]: c
            for c in client.get(
                f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 1}
            ).json()
        }

    # The hallucinated heist tag on an unrelated romance is not a discovery shortcut.
    assert set(pool) == {2}
    assert pool[2]["tropes"] == ["crime", "double-cross"]


@pytest.mark.anyio
@pytest.mark.parametrize("raw,expected", [(0.99, ["romance"]), (0.96, []), (0.78, [])])
async def test_trope_confidence_uses_arctic_normalization(db_engine, monkeypatch, raw, expected):
    config = embeddings.EmbeddingConfig(local_preset="arctic-embed-xs")
    monkeypatch.setattr(embeddings, "load_config", lambda session: config)

    async def embed(config, texts):
        assert len(texts) == 2
        return embeddings.EmbeddingBatch(
            [np.array([1.0, 0.0]), np.array([raw, np.sqrt(1 - raw**2)])], config.fingerprint
        )

    monkeypatch.setattr(embeddings, "embed_batch", embed)
    movie = CachedMovie(
        tmdb_id=1, title="Rom-com", genre_ids=[35, 10749], overview="Two people fall in love."
    )
    with Session(db_engine) as session:
        result = await movie_features.guard_tropes(session, [(movie, ["cyberpunk", "romance"])])
    assert result[1] == expected  # Cyberpunk is genre-blocked even at near-identical confidence.


@pytest.mark.anyio
@pytest.mark.parametrize("score,accepted", [(0.85, True), (0.8499, False)])
async def test_trope_threshold_is_inclusive(db_engine, monkeypatch, score, accepted):
    async def embed(config, texts):
        return embeddings.EmbeddingBatch([np.array([1.0, 0.0])] * len(texts), config.fingerprint)

    monkeypatch.setattr(embeddings, "embed_batch", embed)
    monkeypatch.setattr(embeddings, "normalize_similarity", lambda cosine, fingerprint: score)
    movie = CachedMovie(tmdb_id=1, title="Heist", genre_ids=[80], overview="A team robs a bank.")
    with Session(db_engine) as session:
        assert (
            bool((await movie_features.guard_tropes(session, [(movie, ["heist"])]))[1]) is accepted
        )


@pytest.mark.anyio
async def test_missing_genres_and_model_mismatch_cannot_admit_tags(db_engine, monkeypatch):
    async def embed(config, texts):
        return embeddings.EmbeddingBatch([np.array([1.0, 0.0])] * len(texts), "unrelated:model")

    monkeypatch.setattr(embeddings, "embed_batch", embed)
    movie = CachedMovie(tmdb_id=1, title="Unknown", overview="A team robs a bank.")
    with Session(db_engine) as session:
        assert (await movie_features.guard_tropes(session, [(movie, ["heist"])]))[1] == []
        movie.genre_ids = [80]
        with pytest.raises(embeddings.EmbeddingUnavailable, match="fingerprint"):
            await movie_features.guard_tropes(session, [(movie, ["heist"])])


@pytest.mark.anyio
async def test_fresh_guard_ignores_stale_vectors_and_accepts_local_fallback(db_engine, monkeypatch):
    movie = CachedMovie(
        tmdb_id=1,
        title="Heist",
        genre_ids=[80],
        overview="A robbery.",
        overview_embedding=b"stale",
        overview_embedding_model="old:space",
    )
    config = embeddings.EmbeddingConfig(
        provider="ollama", model="different", local_preset="all-minilm-l6-v2"
    )
    monkeypatch.setattr(embeddings, "load_config", lambda session: config)

    async def embed(config, texts):
        assert texts == ["A robbery.", movie_features.TROPE_DESCRIPTIONS["heist"]]
        return embeddings.EmbeddingBatch(
            [np.array([1.0, 0.0])] * 2, config.local_fingerprint, fell_back=True
        )

    monkeypatch.setattr(embeddings, "embed_batch", embed)
    with Session(db_engine) as session:
        assert (await movie_features.guard_tropes(session, [(movie, ["heist"])]))[1] == ["heist"]


@pytest.mark.anyio
async def test_unavailable_guard_preserves_cache_but_never_trusts_it(db_engine, monkeypatch):
    async def offline(*args):
        raise embeddings.EmbeddingUnavailable("offline")

    monkeypatch.setattr(embeddings, "embed_batch", offline)
    warning = Mock()
    monkeypatch.setattr(movie_features.logger, "warning", warning)

    async def extract(*args):
        return ["heist"]

    monkeypatch.setattr(llm, "extract_tropes", extract)
    with Session(db_engine) as session:
        movie = CachedMovie(
            tmdb_id=1, title="Heist", genre_ids=[80], overview="heist", extracted_tropes=["heist"]
        )
        session.add(movie)
        session.commit()
        assert (await movie_features.ensure_tropes(session, [movie]))[1] == []
        session.refresh(movie)
        assert movie.extracted_tropes == ["heist"]
        warning.assert_called_once()
        assert "verification unavailable" in warning.call_args.args[0]
        with pytest.raises(embeddings.EmbeddingUnavailable):
            await movie_features.extract_and_store_tropes(
                session, movie, llm.LlmConfig(provider="ollama")
            )


@pytest.mark.anyio
async def test_both_extraction_paths_use_identical_guards(db_engine, monkeypatch):
    async def extract(*args):
        return ["cyberpunk", "romance", "revenge"]

    async def embed(config, texts):
        vectors = [
            np.array([1.0, 0.0])
            if movie_features.TROPE_DESCRIPTIONS["revenge"] not in text
            else np.array([0.0, 1.0])
            for text in texts
        ]
        return embeddings.EmbeddingBatch(vectors, config.fingerprint)

    monkeypatch.setattr(llm, "extract_tropes", extract)
    monkeypatch.setattr(llm, "load_config", lambda session: llm.LlmConfig(provider="ollama"))
    monkeypatch.setattr(embeddings, "embed_batch", embed)
    with Session(db_engine) as session:
        movies = [
            CachedMovie(tmdb_id=i, title="Rom-com", genre_ids=[35, 10749], overview="Love.")
            for i in (1, 2)
        ]
        session.add_all(movies)
        session.commit()
        explicit = await movie_features.extract_and_store_tropes(
            session, movies[0], llm.load_config(session)
        )
        automatic = await movie_features.ensure_tropes(session, [movies[1]])
        assert explicit == automatic[2] == ["romance"]
        assert movies[0].extracted_tropes == movies[1].extracted_tropes == ["romance"]


@pytest.mark.anyio
async def test_verified_shared_trope_still_links_below_plot_threshold(db_engine, monkeypatch):
    first = np.zeros(embeddings.EMBEDDING_DIM, dtype=np.float32)
    first[0] = 1
    second = np.zeros_like(first)
    second[:2] = [0.45, np.sqrt(1 - 0.45**2)]
    concept = (first + second) / np.linalg.norm(first + second)

    async def embed(config, texts):
        return embeddings.EmbeddingBatch(
            [
                first if text == "plot one" else second if text == "plot two" else concept
                for text in texts
            ],
            config.fingerprint,
        )

    monkeypatch.setattr(embeddings, "embed_batch", embed)
    async with httpx.AsyncClient() as http_client:
        with Session(db_engine) as session:
            config = embeddings.load_config(session)
            a = CachedMovie(
                tmdb_id=1,
                title="One",
                genre_ids=[80],
                overview="plot one",
                extracted_tropes=["heist"],
                overview_embedding=embeddings.encode_embedding(first),
                overview_embedding_model=config.fingerprint,
            )
            b = CachedMovie(
                tmdb_id=2,
                title="Two",
                genre_ids=[80],
                overview="plot two",
                extracted_tropes=["heist"],
                overview_embedding=embeddings.encode_embedding(second),
                overview_embedding_model=config.fingerprint,
            )
            session.add_all([a, b])
            session.commit()
            engine = SemanticTropeEngine(session, TMDBClient(http_client))
            assert engine._shared_trope(a, b) is None
            await engine.prepare_tropes([a, b])
            assert engine._shared_trope(a, b) == "heist"
            assert engine.measure(a, b) == pytest.approx(0.45)
            assert engine.violation(a, b, 0.45) is None
            a.overview = "changed plot"
            trope_facets.invalidate_movie_evidence(session, a.tmdb_id, overview_changed=True)

            async def offline(*args):
                raise embeddings.EmbeddingUnavailable("offline")

            monkeypatch.setattr(embeddings, "embed_batch", offline)
            await engine.prepare_tropes([a])
            assert engine._shared_trope(a, b) is None


def test_extract_endpoint_reports_embedding_failure_and_leaves_cache_unverified(
    client, db_engine, monkeypatch
):
    async def offline(*args):
        raise embeddings.EmbeddingUnavailable("offline")

    monkeypatch.setattr(embeddings, "embed_batch", offline)
    with respx.mock:
        mock_universe(PLOTS)
        mock_llm(client)
        response = client.post("/api/movies/1/tropes/extract")
    assert response.status_code == 503
    with Session(db_engine) as session:
        assert session.get(CachedMovie, 1).extracted_tropes is None


@pytest.mark.anyio
async def test_trope_verification_bounds_each_jit_batch_and_does_not_share_spaces(
    db_engine, monkeypatch
):
    sizes = []

    async def embed(config, texts):
        sizes.append(len(texts))
        assert len(texts) <= movie_features.EMBED_BATCH_SIZE
        return embeddings.EmbeddingBatch([np.array([1.0, 0.0])] * len(texts), config.fingerprint)

    monkeypatch.setattr(embeddings, "embed_batch", embed)
    movies = [
        (CachedMovie(tmdb_id=i, title="Heist", genre_ids=[80], overview="Robbery."), ["heist"] * 2)
        for i in range(12)
    ]
    with Session(db_engine) as session:
        verified = await movie_features.guard_tropes(session, movies)
    assert len(sizes) == 3
    assert all(tags == ["heist"] for tags in verified.values())


@pytest.mark.anyio
@pytest.mark.parametrize(
    "vectors",
    [
        [np.array([1.0, 0.0]), np.array([1.0, 0.0, 0.0])],
        [np.array([1.0, 0.0]), np.array([np.nan, 0.0])],
        [np.array([1.0, 0.0]), np.array([0.0, 0.0])],
    ],
)
async def test_malformed_provider_vectors_cannot_verify_tropes(db_engine, monkeypatch, vectors):
    async def embed(config, texts):
        return embeddings.EmbeddingBatch(vectors, config.fingerprint)

    monkeypatch.setattr(embeddings, "embed_batch", embed)
    movie = CachedMovie(tmdb_id=1, title="Heist", genre_ids=[80], overview="Robbery.")
    with Session(db_engine) as session, pytest.raises(embeddings.EmbeddingUnavailable):
        await movie_features.guard_tropes(session, [(movie, ["heist"])])
