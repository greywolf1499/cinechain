"""Phase 21c Algorithm Sandbox: Aesthetic Gradient (Pillow) and Semantic Trope Web (ONNX)."""

import io

import httpx
import numpy as np
import pytest
import respx
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app
from app.models.cache import CachedMovie
from app.services import aesthetic, embeddings

TMDB_BASE = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p/w185"


@pytest.fixture()
def db_engine(config_dir):
    engine = create_engine(
        f"sqlite:///{config_dir}/app_test.db", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture()
def client(db_engine):
    def override_get_session():
        with Session(db_engine) as session:
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


def png(rgb: tuple[int, int, int], size=(200, 300)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, rgb).save(buffer, format="PNG")
    return buffer.getvalue()


# --- image / vector helpers ---


def test_dominant_color_of_a_solid_poster():
    assert aesthetic.extract_dominant_color(png((200, 30, 40))) == "#c81e28"


def test_dominant_color_picks_the_largest_region():
    image = Image.new("RGB", (100, 100), (10, 20, 200))
    image.paste((250, 250, 10), (0, 0, 100, 25))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    red, _green, blue = aesthetic.hex_to_rgb(aesthetic.extract_dominant_color(buffer.getvalue()))
    assert blue > 150 and red < 60


def test_dominant_color_rejects_garbage_bytes():
    assert aesthetic.extract_dominant_color(b"not an image") is None


def test_color_distance_is_euclidean_rgb():
    assert aesthetic.color_distance("#000000", "#000000") == 0
    assert aesthetic.color_distance("#000000", "#030400") == pytest.approx(5.0)
    assert aesthetic.color_distance("#000000", "#ffffff") == pytest.approx(
        aesthetic.MAX_RGB_DISTANCE
    )
    assert aesthetic.color_distance("#zzzzzz", "#000000") is None


def test_embedding_round_trip_and_cosine():
    vector = np.random.default_rng(1).normal(size=embeddings.EMBEDDING_DIM).astype(np.float32)
    restored = embeddings.decode_embedding(embeddings.encode_embedding(vector))
    assert np.array_equal(restored, vector)
    assert embeddings.cosine_similarity(vector, vector) == pytest.approx(1.0)
    assert embeddings.cosine_similarity(vector, -vector) == pytest.approx(-1.0)
    assert (
        embeddings.decode_embedding(b"short") is None and embeddings.decode_embedding(None) is None
    )


def test_embed_texts_reports_an_unavailable_model(config_dir, monkeypatch):
    def offline(url, destination):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(embeddings, "_download", offline)
    with pytest.raises(embeddings.EmbeddingUnavailable):
        embeddings.embed_texts(["anything"])


@pytest.fixture(autouse=True)
def _legacy_local_preset(config_dir, monkeypatch):
    """The fake embedders below produce MiniLM-scaled cosines: keep the unscaled legacy preset."""
    from app.config import get_settings

    monkeypatch.setenv("EMBEDDING_LOCAL_PRESET", "all-minilm-l6-v2")
    get_settings.cache_clear()


@pytest.mark.parametrize("key", list(embeddings.LOCAL_PRESETS))
def test_each_preset_is_downloaded_once_into_its_own_dir(config_dir, monkeypatch, key):
    preset = embeddings.LOCAL_PRESETS[key]
    fetched = []

    def fake_download(url, destination):
        fetched.append((url, destination.name))
        destination.write_bytes(b"x")

    monkeypatch.setattr(embeddings, "_download", fake_download)
    assert not preset.downloaded
    embeddings.ensure_model_files(preset)
    embeddings.ensure_model_files(preset)
    assert sorted(fetched) == sorted(
        [
            (preset.model_url, embeddings.MODEL_FILE),
            (preset.tokenizer_url, embeddings.TOKENIZER_FILE),
        ]
    )
    assert (config_dir / "models" / preset.model_name / embeddings.MODEL_FILE).exists()
    assert preset.downloaded


# --- fake universe ---


def unit(*components: float) -> np.ndarray:
    vector = np.zeros(embeddings.EMBEDDING_DIM, dtype=np.float32)
    vector[: len(components)] = components
    return vector / np.linalg.norm(vector)


VECTORS = {
    "heist": unit(1, 0, 0),
    "heist-ish": unit(1, 0.5, 0),  # cosine ~0.89 with "heist"
    "romance": unit(0, 0, 1),  # orthogonal
}


@pytest.fixture()
def fake_model(monkeypatch):
    from app.config import get_settings

    # MiniLM-scaled cosines: keep the unscaled legacy preset whichever module uses this fixture
    monkeypatch.setenv("EMBEDDING_LOCAL_PRESET", "all-minilm-l6-v2")
    get_settings.cache_clear()
    calls: list[list[str]] = []

    def embed_texts(texts, preset=None):
        calls.append(list(texts))
        return [VECTORS[text] for text in texts]

    monkeypatch.setattr(embeddings, "embed_texts", embed_texts)
    return calls


def mock_universe(movies: dict[int, dict]) -> dict[int, respx.Route]:
    """movies: id -> {title, color (rgb | None), overview, cast, popularity}; all share actor ids.
    Returns the poster routes by movie id."""
    people = {a for m in movies.values() for a in m["cast"]}
    posters: dict[int, respx.Route] = {}
    for movie_id, m in movies.items():
        poster = f"/p{movie_id}.png" if m.get("color") else None
        respx.get(f"{TMDB_BASE}/movie/{movie_id}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": movie_id,
                    "title": m["title"],
                    "release_date": "2000-01-01",
                    "poster_path": poster,
                    "overview": m.get("overview", ""),
                    "origin_country": ["US"],
                    "original_language": "en",
                    "runtime": 100,
                    "genres": [{"id": genre} for genre in m.get("genre_ids", [])],
                    "popularity": m.get("popularity", 1.0),
                    "status": "Released",
                },
            )
        )
        if poster:
            posters[movie_id] = respx.get(f"{IMAGE_BASE}{poster}").mock(
                return_value=httpx.Response(200, content=png(m["color"]))
            )
        respx.get(f"{TMDB_BASE}/movie/{movie_id}/credits").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": movie_id,
                    "cast": [
                        {
                            "id": a,
                            "name": f"Actor {a}",
                            "profile_path": None,
                            "character": f"Role {a}",
                            "order": i,
                        }
                        for i, a in enumerate(m["cast"])
                    ],
                    "crew": [],
                },
            )
        )
    for person in people:
        respx.get(f"{TMDB_BASE}/person/{person}/movie_credits").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": person,
                    "cast": [
                        {
                            "id": i,
                            "title": m["title"],
                            "release_date": "2000-01-01",
                            "poster_path": f"/p{i}.png" if m.get("color") else None,
                            "genre_ids": [],
                            "original_language": "en",
                            "popularity": m.get("popularity", 1.0),
                            "character": "x",
                        }
                        for i, m in movies.items()
                        if person in m["cast"]
                    ],
                    "crew": [],
                },
            )
        )
    return posters


def create_run(client, game_type, **extra_rules):
    resp = client.post(
        "/api/runs",
        json={
            "name": "Run",
            "game_type": game_type,
            "rules_config": {
                "allow_repeats": "strict",
                "no_consecutive_actor": False,
                "min_runtime": 0,
                "wildcards_budget": 2,
                **extra_rules,
            },
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def log(client, run_id, movie_id, **extra):
    return client.post(f"/api/runs/{run_id}/steps", json={"movie_id": movie_id, **extra})


def run_steps(client, run_id):
    return client.get(f"/api/runs/{run_id}").json()["steps"]


def test_engines_are_registered(client):
    engines = {e["game_type"]: e for e in client.get("/api/engines").json()}
    for game_type in ("aesthetic_gradient", "semantic_trope"):
        assert {"discover_candidates", "json_rules"} <= set(engines[game_type]["capabilities"])
        assert "solve_bridge" not in engines[game_type]["capabilities"]


# --- Aesthetic Gradient ---

COLORS = {
    1: {"title": "Crimson", "color": (200, 30, 40), "cast": [100]},
    2: {"title": "Rose", "color": (210, 60, 70), "cast": [100], "popularity": 5},  # distance ~50
    3: {"title": "Azure", "color": (30, 60, 220), "cast": [100], "popularity": 4},
    4: {"title": "No Poster", "color": None, "cast": [100], "popularity": 3},
}


def test_aesthetic_blocks_distant_colors_even_with_a_wildcard(client, db_engine):
    run_id = create_run(client, "aesthetic_gradient")
    with respx.mock:
        mock_universe(COLORS)
        assert log(client, run_id, 1).status_code == 201
        for force in (False, True):
            blocked = log(client, run_id, 3, force=force)
            assert blocked.status_code == 409
            assert blocked.json()["detail"]["blocked"] is True
        assert "Aesthetic Gradient" in blocked.json()["detail"]["reason"]
        ok = log(client, run_id, 2)

    assert ok.status_code == 201
    assert 0 < ok.json()["transition_metadata"]["color_distance"] < aesthetic_threshold()
    assert ok.json()["movie_dominant_color"] == "#d23c46"
    with Session(db_engine) as session:
        assert session.get(CachedMovie, 1).dominant_color == "#c81e28"
        assert session.get(CachedMovie, 3).dominant_color == "#1e3cdc"
    assert [s["movie_dominant_color"] for s in run_steps(client, run_id)] == ["#c81e28", "#d23c46"]


def aesthetic_threshold() -> float:
    from app.engines.algorithms import COLOR_DISTANCE_THRESHOLD

    return COLOR_DISTANCE_THRESHOLD


def test_aesthetic_never_blocks_a_film_without_a_poster(client):
    run_id = create_run(client, "aesthetic_gradient")
    with respx.mock:
        mock_universe(COLORS)
        log(client, run_id, 1)
        resp = log(client, run_id, 4)
    assert resp.status_code == 201
    assert "color_distance" not in (resp.json()["transition_metadata"] or {})


def test_aesthetic_hybrid_pool_is_filtered_and_annotated(client):
    run_id = create_run(client, "aesthetic_gradient", require_cast_link=True)
    with respx.mock:
        mock_universe(COLORS)
        log(client, run_id, 1)
        pool = {
            c["movie_id"]: c
            for c in client.get(
                f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 1}
            ).json()
        }
        constraint = client.get(f"/api/runs/{run_id}/constraint").json()

    assert set(pool) == {2, 4}  # Azure is too far away
    assert pool[2]["dominant_color"] == "#d23c46"
    assert pool[2]["constraint_unverified"] is False
    assert pool[4]["dominant_color"] is None and pool[4]["constraint_unverified"] is True
    assert constraint["kind"] == "color" and "#c81e28" in constraint["title"]


def test_poster_is_only_downloaded_once(client):
    run_id = create_run(client, "aesthetic_gradient")
    with respx.mock:
        poster = mock_universe(COLORS)[1]
        log(client, run_id, 1)
        log(client, run_id, 2)
        client.post(f"/api/runs/{run_id}/validate", json={"movie_id": 3})
    assert poster.call_count == 1


# --- Semantic Trope Web ---

PLOTS = {
    1: {"title": "Heist One", "overview": "heist", "cast": [100], "popularity": 9},
    2: {"title": "Heist Two", "overview": "heist-ish", "cast": [100], "popularity": 8},
    3: {"title": "Love Story", "overview": "romance", "cast": [100], "popularity": 7},
    4: {"title": "Mystery Plot", "overview": "", "cast": [100], "popularity": 6},
}


def test_semantic_blocks_dissimilar_plots_even_with_a_wildcard(client, fake_model):
    run_id = create_run(client, "semantic_trope")
    with respx.mock:
        mock_universe(PLOTS)
        assert log(client, run_id, 1).status_code == 201
        for force in (False, True):
            blocked = log(client, run_id, 3, force=force)
            assert blocked.status_code == 409
            assert blocked.json()["detail"]["blocked"] is True
        assert "plot match" in blocked.json()["detail"]["reason"]
        ok = log(client, run_id, 2)

    assert ok.status_code == 201
    assert ok.json()["transition_metadata"]["semantic_score"] == pytest.approx(0.8944, abs=1e-3)


def test_semantic_embeddings_are_persisted_and_models_run_in_batches(client, db_engine, fake_model):
    run_id = create_run(client, "semantic_trope")
    with respx.mock:
        mock_universe(PLOTS)
        log(client, run_id, 1)
        log(client, run_id, 2)
        client.post(f"/api/runs/{run_id}/validate", json={"movie_id": 2})

    with Session(db_engine) as session:
        stored = embeddings.decode_embedding(session.get(CachedMovie, 1).overview_embedding)
    assert np.allclose(stored, VECTORS["heist"])
    assert sum(len(batch) for batch in fake_model) == 2  # each overview embedded exactly once


def test_semantic_hybrid_pool_is_filtered_and_scored(client, fake_model):
    run_id = create_run(client, "semantic_trope", require_cast_link=True)
    with respx.mock:
        mock_universe(PLOTS)
        log(client, run_id, 1)
        pool = {
            c["movie_id"]: c
            for c in client.get(
                f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 1}
            ).json()
        }
        constraint = client.get(f"/api/runs/{run_id}/constraint").json()

    assert set(pool) == {2, 4}  # Love Story is a poor plot match
    assert pool[2]["semantic_score"] == pytest.approx(0.8944, abs=1e-3)
    assert pool[4]["semantic_score"] is None and pool[4]["constraint_unverified"] is True
    assert constraint["kind"] == "semantic"


def test_semantic_is_lenient_for_later_hops_when_the_model_is_unavailable(
    client, monkeypatch, fake_model
):
    def offline(texts, preset=None):
        raise embeddings.EmbeddingUnavailable("offline")

    run_id = create_run(client, "semantic_trope")
    with respx.mock:
        mock_universe(PLOTS)
        assert log(client, run_id, 1).status_code == 201
        monkeypatch.setattr(embeddings, "embed_texts", offline)
        resp = log(client, run_id, 3)
    assert resp.status_code == 201
    assert "semantic_score" not in (resp.json()["transition_metadata"] or {})


def test_semantic_seed_setup_is_actionable_and_does_not_create_a_run(
    client, db_engine, monkeypatch
):
    from app.models.run import Run

    def offline(texts, preset=None):
        raise embeddings.EmbeddingUnavailable("offline")

    monkeypatch.setattr(embeddings, "embed_texts", offline)
    with respx.mock:
        mock_universe(PLOTS)
        response = client.post(
            "/api/runs",
            json={
                "name": "Cold seed",
                "game_type": "semantic_trope",
                "seed_movie_id": 1,
            },
        )
    assert response.status_code == 422, response.text
    assert "Download the embedding model in Settings > AI & Embeddings" in response.json()["detail"]
    with Session(db_engine) as session:
        from sqlmodel import select

        assert session.exec(select(Run)).all() == []


def test_semantic_first_film_requires_an_overview(client, fake_model):
    run_id = create_run(client, "semantic_trope")
    with respx.mock:
        mock_universe(PLOTS)
        response = log(client, run_id, 4)
    assert response.status_code == 409
    assert "no plot overview" in response.json()["detail"]["reason"]
    assert fake_model == []


def test_cold_semantic_pool_widens_via_discover_with_llm_off(client, fake_model):
    from app.config import get_settings

    assert get_settings().llm_provider == "off"
    with respx.mock:
        universe = {
            1: {**PLOTS[1], "title": "The Fabelmans", "genre_ids": [18]},
            2: {**PLOTS[2], "genre_ids": [18]},
        }
        mock_universe(universe)
        for kind in ("recommendations", "similar"):
            respx.get(f"{TMDB_BASE}/movie/1/{kind}").mock(
                return_value=httpx.Response(200, json={"results": []})
            )
        respx.get(f"{TMDB_BASE}/movie/1/keywords").mock(
            return_value=httpx.Response(200, json={"keywords": [{"id": 42, "name": "family"}]})
        )
        discover = respx.get(f"{TMDB_BASE}/discover/movie").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": 2,
                            "title": "Heist Two",
                            "release_date": "2000-01-01",
                            "genre_ids": [18],
                            "popularity": 8,
                        }
                    ]
                },
            )
        )
        response = client.post(
            "/api/runs",
            json={
                "name": "Cold Fabelmans",
                "game_type": "semantic_trope",
                "seed_movie_id": 1,
                "rules_config": {"min_runtime": 0},
            },
        )
        assert response.status_code == 201, response.text
        run_id = response.json()["id"]
        response = client.get(
            f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 1, "envelope": 1}
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert [candidate["movie_id"] for candidate in payload["candidates"]] == [2]
        assert payload["diagnostics"] == {
            "engine_pool": 1,
            "after_modifiers": 1,
            "after_filters": 1,
            "widened": True,
            "reason": None,
        }
        assert "with_genres=18" in str(discover.calls[0].request.url)
        assert "with_keywords=42" in str(discover.calls[0].request.url)
    assert fake_model == [["heist"], ["heist-ish"]]


def test_discovery_envelope_distinguishes_filters_and_preserves_legacy_list(client, fake_model):
    run_id = create_run(client, "semantic_trope", require_cast_link=True)
    with respx.mock:
        mock_universe(PLOTS)
        assert log(client, run_id, 1).status_code == 201
        base = {"frontier_movie_id": 1}
        legacy = client.get(f"/api/runs/{run_id}/discover", params=base)
        assert isinstance(legacy.json(), list)
        shaped = client.get(
            f"/api/runs/{run_id}/discover", params={**base, "envelope": 1, "chaser": 1}
        ).json()
    diagnostics = shaped["diagnostics"]
    assert diagnostics["engine_pool"] >= diagnostics["after_modifiers"] == 2
    assert diagnostics["after_filters"] == 0
    assert "filters hid" in diagnostics["reason"]
    assert shaped["candidates"] == []


@pytest.mark.asyncio
async def test_discovery_deadline_preserves_partial_pool_and_one_hydration_budget(
    db_engine, monkeypatch
):
    import asyncio
    import time

    from app.engines import base
    from app.engines.cinechain import CineChainEngine
    from app.schemas.discovery import DiscoveryCandidate
    from app.services.tmdb import TMDBClient

    monkeypatch.setattr(base, "DISCOVERY_SECONDS", 0.04)
    candidates = [DiscoveryCandidate(movie_id=2, title="Known")]
    http = httpx.AsyncClient()
    with Session(db_engine) as session:
        engine = CineChainEngine(session, TMDBClient(http))

        async def initial(*args, **kwargs):
            return candidates

        async def filtered(frontier, pool, rules, history):
            return pool

        async def widen(frontier, rules, history, rung):
            assert engine._hydration_left == base.HYDRATE_BUDGET
            await asyncio.sleep(1)
            return []

        monkeypatch.setattr(engine, "discover_candidates", initial)
        monkeypatch.setattr(engine, "filter_by_modifiers", filtered)
        monkeypatch.setattr(engine, "widen_pool", widen)
        start = time.monotonic()
        assert await engine.discover_with_modifiers(1) == candidates
        assert time.monotonic() - start < 0.3
        assert engine.discovery_diagnostics.after_filters == 1
        assert "12-second limit" in engine.discovery_diagnostics.reason
    await http.aclose()


@pytest.mark.asyncio
async def test_widening_cannot_bypass_modifier_filters_and_stops_at_eight(db_engine, monkeypatch):
    from app.engines.cinechain import CineChainEngine
    from app.schemas.discovery import DiscoveryCandidate
    from app.services.tmdb import TMDBClient

    rungs = []
    http = httpx.AsyncClient()
    with Session(db_engine) as session:
        engine = CineChainEngine(session, TMDBClient(http))

        async def initial(*args, **kwargs):
            return []

        async def filtered(frontier, pool, rules, history):
            return [candidate for candidate in pool if candidate.movie_id != 2]

        async def widen(frontier, rules, history, rung):
            rungs.append(rung)
            return [DiscoveryCandidate(movie_id=i, title=str(i)) for i in range(2, 11)]

        monkeypatch.setattr(engine, "discover_candidates", initial)
        monkeypatch.setattr(engine, "filter_by_modifiers", filtered)
        monkeypatch.setattr(engine, "widen_pool", widen)
        pool = await engine.discover_with_modifiers(1)
        assert len(pool) == 8
        assert rungs == [1]
        assert engine.discovery_diagnostics.engine_pool == 9
        assert engine.discovery_diagnostics.after_modifiers == 8
    await http.aclose()


@pytest.mark.asyncio
async def test_hydration_cap_is_shared_across_initial_pool_and_all_rungs(db_engine, monkeypatch):
    from app.engines import base
    from app.engines.cinechain import CineChainEngine
    from app.schemas.discovery import DiscoveryCandidate
    from app.services import cache_repo
    from app.services.tmdb import TMDBClient

    fetched = []
    http = httpx.AsyncClient()
    with Session(db_engine) as session:
        for movie_id in range(1, 43):
            session.add(CachedMovie(tmdb_id=movie_id, title=str(movie_id), genre_ids=[35]))
        session.commit()
        engine = CineChainEngine(session, TMDBClient(http))

        async def initial(*args, **kwargs):
            return [DiscoveryCandidate(movie_id=i, title=str(i)) for i in range(2, 12)]

        async def widen(frontier, rules, history, rung):
            return [
                DiscoveryCandidate(movie_id=i, title=str(i))
                for i in range(12 + (rung - 1) * 10, 22 + (rung - 1) * 10)
            ]

        async def get_movie(session, tmdb, movie_id, refresh=False):
            fetched.append(movie_id)
            row = session.get(CachedMovie, movie_id)
            row.runtime = 95
            return row

        async def filtered(frontier, pool, rules, history):
            await engine._hydrate_pool(pool, needs=frozenset({"runtime"}))
            return pool

        monkeypatch.setattr(engine, "discover_candidates", initial)
        monkeypatch.setattr(engine, "widen_pool", widen)
        monkeypatch.setattr(engine, "filter_by_modifiers", filtered)
        monkeypatch.setattr(cache_repo, "get_movie", get_movie)
        assert len(await engine.discover_with_modifiers(1, wider=True)) == 40
        assert len(fetched) == base.HYDRATE_BUDGET == 30
        assert len(set(fetched)) == 30
        assert engine._hydration_left == 0
    await http.aclose()


@pytest.mark.asyncio
async def test_graph_widening_recovers_valid_links_without_relaxing_billing(db_engine):
    from app.engines.cinechain import CineChainEngine
    from app.services import cache_repo
    from app.services.tmdb import TMDBClient

    universe = {
        1: {"title": "Frontier", "cast": list(range(100, 125))},
        2: {"title": "Known link", "cast": [100]},
        3: {"title": "Missing from an actor's feed", "cast": [100, 119]},
        4: {"title": "Too deep in the frontier's billing", "cast": [119]},
    }
    with respx.mock:
        mock_universe(universe)
        respx.get(f"{TMDB_BASE}/person/100/movie_credits").mock(
            return_value=httpx.Response(
                200,
                json={
                    "cast": [
                        {
                            "id": 2,
                            "title": "Known link",
                            "release_date": "2000-01-01",
                        }
                    ],
                    "crew": [],
                },
            )
        )
        async with httpx.AsyncClient() as http:
            with Session(db_engine) as session:
                tmdb = TMDBClient(http)
                await cache_repo.get_movie(session, tmdb, 1)
                engine = CineChainEngine(session, tmdb)
                pool = await engine.discover_with_modifiers(
                    1, cast_limit=15, rules={"min_runtime": 0}
                )
                assert {candidate.movie_id for candidate in pool} == {2, 3}
                assert all(
                    connection.actor_id < 115
                    for candidate in pool
                    for connection in candidate.connections
                )
                assert engine.discovery_diagnostics.widened
                assert engine._hydration_left >= 0


def test_semantic_seed_extracts_tropes_synchronously_for_only_the_seed(
    client, fake_model, monkeypatch
):
    from app.services import movie_features

    calls = []

    async def extract(session, movies):
        calls.append([movie.tmdb_id for movie in movies])
        return {movie.tmdb_id: ["Family Saga"] for movie in movies}

    monkeypatch.setattr(movie_features, "ensure_tropes", extract)
    with respx.mock:
        mock_universe(PLOTS)
        response = client.post(
            "/api/runs",
            json={
                "name": "Prepared seed",
                "game_type": "semantic_trope",
                "seed_movie_id": 1,
            },
        )
    assert response.status_code == 201, response.text
    assert [batch for batch in calls if batch] == [[1]]
    assert fake_model == [["heist"]]


# --- pluggable embedding providers (Phase 23b) ---

OLLAMA_URL = "http://localhost:11434/api/embeddings"


def configure_embeddings(client, **values):
    resp = client.patch("/api/settings/integrations", json=values)
    assert resp.status_code == 200, resp.text


def mock_ollama(vectors: dict[str, np.ndarray]):
    import json

    def reply(request):
        prompt = json.loads(request.content)["prompt"]
        return httpx.Response(200, json={"embedding": vectors[prompt].tolist()})

    return respx.post(OLLAMA_URL).mock(side_effect=reply)


def test_semantic_uses_the_configured_ollama_provider(client, db_engine, fake_model):
    configure_embeddings(client, embedding_provider="ollama", embedding_model="nomic-embed-text")
    run_id = create_run(client, "semantic_trope")
    with respx.mock:
        mock_universe(PLOTS)
        ollama = mock_ollama(VECTORS)
        log(client, run_id, 1)
        ok = log(client, run_id, 2)

    assert ok.status_code == 201
    assert ok.json()["transition_metadata"]["semantic_score"] == pytest.approx(0.8944, abs=1e-3)
    assert ollama.call_count == 2 and fake_model == []  # the local model never ran
    with Session(db_engine) as session:
        assert session.get(CachedMovie, 1).overview_embedding_model == "ollama:nomic-embed-text"


def test_semantic_falls_back_to_local_when_ollama_is_down(client, db_engine, fake_model):
    configure_embeddings(client, embedding_provider="ollama")
    run_id = create_run(client, "semantic_trope")
    with respx.mock:
        mock_universe(PLOTS)
        ollama = respx.post(OLLAMA_URL).mock(side_effect=httpx.ConnectError("refused"))
        assert log(client, run_id, 1).status_code == 201
        ok = log(client, run_id, 2)
        blocked = log(client, run_id, 3)

    assert ok.status_code == 201 and blocked.status_code == 409  # still enforced, via ONNX
    assert ollama.call_count == 1  # suspended after the first failure
    with Session(db_engine) as session:
        assert session.get(CachedMovie, 1).overview_embedding_model == embeddings.LOCAL_FINGERPRINT


def test_semantic_never_compares_vectors_from_different_models():
    from app.engines.algorithms import SemanticTropeEngine

    vector = embeddings.encode_embedding(unit(1, 0, 0))
    local = CachedMovie(tmdb_id=1, title="A", overview_embedding=vector)
    same = CachedMovie(
        tmdb_id=2,
        title="B",
        overview_embedding=vector,
        overview_embedding_model=embeddings.LOCAL_FINGERPRINT,
    )
    other = CachedMovie(
        tmdb_id=3, title="C", overview_embedding=vector, overview_embedding_model="ollama:x"
    )
    engine = SemanticTropeEngine(None, None)
    assert engine.measure(local, same) == pytest.approx(1.0)  # NULL model = the local one
    assert engine.measure(local, other) is None  # different vector spaces: can't tell
