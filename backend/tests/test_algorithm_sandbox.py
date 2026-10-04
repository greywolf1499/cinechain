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
        test_client.post(
            "/api/auth/login", json={"username": "alice", "password": "password123"})
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
    assert aesthetic.color_distance("#000000", "#ffffff") == pytest.approx(aesthetic.MAX_RGB_DISTANCE)
    assert aesthetic.color_distance("#zzzzzz", "#000000") is None


def test_embedding_round_trip_and_cosine():
    vector = np.random.default_rng(1).normal(size=embeddings.EMBEDDING_DIM).astype(np.float32)
    restored = embeddings.decode_embedding(embeddings.encode_embedding(vector))
    assert np.array_equal(restored, vector)
    assert embeddings.cosine_similarity(vector, vector) == pytest.approx(1.0)
    assert embeddings.cosine_similarity(vector, -vector) == pytest.approx(-1.0)
    assert embeddings.decode_embedding(b"short") is None and embeddings.decode_embedding(None) is None


def test_embed_texts_reports_an_unavailable_model(config_dir, monkeypatch):
    def offline(url, destination):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(embeddings, "_download", offline)
    with pytest.raises(embeddings.EmbeddingUnavailable):
        embeddings.embed_texts(["anything"])


def test_model_is_downloaded_once_into_the_config_dir(config_dir, monkeypatch):
    fetched = []

    def fake_download(url, destination):
        fetched.append(destination.name)
        destination.write_bytes(b"x")

    monkeypatch.setattr(embeddings, "_download", fake_download)
    embeddings.ensure_model_files()
    embeddings.ensure_model_files()
    assert sorted(fetched) == [embeddings.MODEL_FILE, embeddings.TOKENIZER_FILE]
    assert (config_dir / "models" / "all-MiniLM-L6-v2" / embeddings.MODEL_FILE).exists()


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
    calls: list[list[str]] = []

    def embed_texts(texts):
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
        respx.get(f"{TMDB_BASE}/movie/{movie_id}").mock(return_value=httpx.Response(200, json={
            "id": movie_id, "title": m["title"], "release_date": "2000-01-01",
            "poster_path": poster, "overview": m.get("overview", ""), "origin_country": ["US"],
            "original_language": "en", "runtime": 100, "genres": [],
            "popularity": m.get("popularity", 1.0), "status": "Released",
        }))
        if poster:
            posters[movie_id] = respx.get(f"{IMAGE_BASE}{poster}").mock(
                return_value=httpx.Response(200, content=png(m["color"])))
        respx.get(f"{TMDB_BASE}/movie/{movie_id}/credits").mock(return_value=httpx.Response(200, json={
            "id": movie_id,
            "cast": [{"id": a, "name": f"Actor {a}", "profile_path": None,
                      "character": f"Role {a}", "order": i} for i, a in enumerate(m["cast"])],
            "crew": [],
        }))
    for person in people:
        respx.get(f"{TMDB_BASE}/person/{person}/movie_credits").mock(
            return_value=httpx.Response(200, json={
                "id": person,
                "cast": [{
                    "id": i, "title": m["title"], "release_date": "2000-01-01",
                    "poster_path": f"/p{i}.png" if m.get("color") else None, "genre_ids": [],
                    "original_language": "en", "popularity": m.get("popularity", 1.0),
                    "character": "x",
                } for i, m in movies.items() if person in m["cast"]],
                "crew": [],
            }))
    return posters


def create_run(client, game_type, **extra_rules):
    resp = client.post("/api/runs", json={"name": "Run", "game_type": game_type, "rules_config": {
        "allow_repeats": "strict", "no_consecutive_actor": False, "min_runtime": 0,
        "wildcards_budget": 2, **extra_rules}})
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
        pool = {c["movie_id"]: c for c in client.get(
            f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 1}).json()}
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
        pool = {c["movie_id"]: c for c in client.get(
            f"/api/runs/{run_id}/discover", params={"frontier_movie_id": 1}).json()}
        constraint = client.get(f"/api/runs/{run_id}/constraint").json()

    assert set(pool) == {2, 4}  # Love Story is a poor plot match
    assert pool[2]["semantic_score"] == pytest.approx(0.8944, abs=1e-3)
    assert pool[4]["semantic_score"] is None and pool[4]["constraint_unverified"] is True
    assert constraint["kind"] == "semantic"


def test_semantic_is_lenient_when_the_model_is_unavailable(client, monkeypatch):
    def offline(texts):
        raise embeddings.EmbeddingUnavailable("offline")

    monkeypatch.setattr(embeddings, "embed_texts", offline)
    run_id = create_run(client, "semantic_trope")
    with respx.mock:
        mock_universe(PLOTS)
        log(client, run_id, 1)
        resp = log(client, run_id, 3)
    assert resp.status_code == 201
    assert "semantic_score" not in (resp.json()["transition_metadata"] or {})
