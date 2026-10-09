"""Fixture-only tests for the opt-in TVTropes link-list client."""

from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
import pytest

from app.models.cache import CachedMovie
from app.services import embeddings, llm, movie_features, tvtropes


@pytest.fixture(autouse=True)
def reset_tvtropes_process_state(monkeypatch):
    monkeypatch.setattr(tvtropes, "_robots_cache", None)
    monkeypatch.setattr(tvtropes, "_last_request", 0.0)
    monkeypatch.setattr(tvtropes, "TERMS_APPROVED", True)


def response(status_code: int, text: str = "", headers: dict[str, str] | None = None):
    return tvtropes.Response(status_code, headers or {}, text)


def movie(**values) -> CachedMovie:
    data = {
        "tmdb_id": 42,
        "title": "Heist One",
        "release_date": "2000-01-01",
        "overview": "A crew plans a heist.",
        "genre_ids": [80],
        **values,
    }
    return CachedMovie(**data)


def test_disabled_client_does_not_make_requests(tmp_path):
    requests = []
    client = tvtropes.TVTropesClient(tmp_path, lambda url, headers: requests.append(url))

    with pytest.raises(tvtropes.TVTropesDisabled):
        client.sync_movie(movie(), enabled=False)

    assert requests == []


def test_unreviewed_terms_gate_rejects_even_an_enabled_setting(tmp_path, monkeypatch):
    monkeypatch.setattr(tvtropes, "TERMS_APPROVED", False)
    client = tvtropes.TVTropesClient(tmp_path, lambda *_args: pytest.fail("unexpected request"))

    with pytest.raises(tvtropes.TVTropesDisabled, match="approved site terms"):
        client.sync_movie(movie(), enabled=True)


def test_robots_disallow_is_honored_before_any_work_page_request(tmp_path):
    requests = []

    def get(url, headers):
        requests.append(url)
        return response(200, "User-agent: *\nDisallow: /\n")

    client = tvtropes.TVTropesClient(tmp_path, get, min_interval=0)
    with pytest.raises(tvtropes.RobotsDisallowed):
        client.sync_movie(movie(), enabled=True)

    assert requests == [f"{tvtropes.BASE_URL}/robots.txt"]


def test_challenge_stops_the_batch_immediately(tmp_path):
    requests = []

    def get(url, headers):
        requests.append(url)
        if url.endswith("/robots.txt"):
            return response(200, "User-agent: *\nAllow: /\n")
        return response(200, "<title>Just a moment...</title><div id='cf-challenge'>")

    client = tvtropes.TVTropesClient(tmp_path, get, min_interval=0)
    with pytest.raises(tvtropes.CloudflareBlock):
        client.sync_movie(movie(), enabled=True)

    assert len(requests) == 2
    assert requests[-1].endswith("/Film/HeistOne2000")


def test_work_resolution_extracts_only_local_links_and_maps_camel_case(tmp_path):
    requests = []
    work_page = """
    <html><h1>Heist One (2000)</h1><div id="main-article">
      <a href="/pmwiki/pmwiki.php/Main/TimeLoop">time loop</a>
      <a href="https://evil.example/pmwiki/pmwiki.php/Main/Heist">external</a>
      <a href="/pmwiki/pmwiki.php/Main/AlienInvasion">alien invasion</a>
    </div></html>
    """

    def get(url, headers):
        requests.append(url)
        if url.endswith("/robots.txt"):
            return response(200, "User-agent: *\nAllow: /\n")
        if url.endswith("/Film/HeistOne2000"):
            return response(404)
        if url.endswith("/Film/HeistOne") and requests.count(url) == 1:
            return response(200, "<h1>Heist One (2000)</h1>")
        if url.endswith("/Film/HeistOne"):
            return response(200, work_page, {"ETag": '"work-v1"'})
        raise AssertionError(f"Unexpected fixture request: {url}")

    client = tvtropes.TVTropesClient(tmp_path, get, min_interval=0)
    result = client.sync_movie(movie(), enabled=True)

    assert result.work_url == f"{tvtropes.BASE_URL}/pmwiki/pmwiki.php/Film/HeistOne"
    assert [(link.name, link.mapped_slug) for link in result.tropes] == [
        ("TimeLoop", "time-loop"),
        ("AlienInvasion", "alien-invasion"),
    ]
    assert all("evil.example" not in link.url for link in result.tropes)
    assert client.pages_fetched == 4


def test_expired_cache_revalidates_with_etag_without_storing_html(tmp_path):
    now = [1_000_000.0]
    requests: list[tuple[str, dict[str, str]]] = []
    page = '<h1>Heist One (2000)</h1><a href="/pmwiki/pmwiki.php/Main/Heist">Heist</a>'

    def get(url, headers):
        requests.append((url, headers))
        if url.endswith("/robots.txt"):
            return response(200, "User-agent: *\nAllow: /\n")
        if len([entry for entry in requests if entry[0] == url]) == 1:
            return response(200, page, {"ETag": '"work-v1"'})
        return response(304)

    client = tvtropes.TVTropesClient(
        tmp_path, get, min_interval=0, now=lambda: now[0], sleep=lambda _: None
    )
    first = client.sync_movie(movie(tvtropes_work_url=f"{tvtropes.BASE_URL}/Film/HeistOne"), enabled=True)
    now[0] += tvtropes.CACHE_TTL.total_seconds() + 1
    second_client = tvtropes.TVTropesClient(
        tmp_path, get, min_interval=0, now=lambda: now[0], sleep=lambda _: None
    )
    second = second_client.sync_movie(
        movie(tvtropes_work_url=f"{tvtropes.BASE_URL}/Film/HeistOne"), enabled=True
    )

    assert first.tropes == second.tropes
    assert second.tropes[0].mapped_slug == "heist"
    assert requests[-1][1]["If-None-Match"] == '"work-v1"'
    cache_text = (tmp_path / "42.json").read_text(encoding="utf-8")
    assert "Main/Heist" in cache_text
    assert "<h1>" not in cache_text
    assert datetime.fromisoformat(tvtropes._read_cache(tmp_path / "42.json")["fetched_at"]).tzinfo == UTC


@pytest.mark.anyio
async def test_scraped_trope_validation_uses_normalized_floor_and_genre_gate(monkeypatch):
    fingerprint = embeddings.LOCAL_FINGERPRINT
    config = SimpleNamespace(fingerprint=fingerprint, local_fingerprint=fingerprint)
    monkeypatch.setattr(embeddings, "load_config", lambda _session: config)
    overview = np.array([1.0, 0.0], dtype=np.float32)
    concept = np.array([0.85, np.sqrt(1 - 0.85**2)], dtype=np.float32)

    async def embed_batch(_config, texts):
        assert len(texts) == 2
        return embeddings.EmbeddingBatch([overview, concept], fingerprint)

    monkeypatch.setattr(embeddings, "embed_batch", embed_batch)
    film = movie(overview="A crew plans a heist.", genre_ids=[80])

    result = await movie_features.validate_tvtropes(
        None,
        film,
        [
            ("heist", "heist"),
            ("cyberpunk", "cyberpunk"),
            ("unmapped-story-page", None),
        ],
        llm.LlmConfig(),
    )

    assert result["heist"] >= movie_features.TROPE_CONFIDENCE_THRESHOLD
    assert result["unmapped-story-page"] is None
    assert "cyberpunk" not in result
