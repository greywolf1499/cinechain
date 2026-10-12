"""Fixture-only tests for the opt-in TVTropes link-list client."""

import runpy
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from app.models.cache import CachedMovie
from app.services import embeddings, llm, movie_features, tvtropes


@pytest.fixture(autouse=True)
def reset_tvtropes_process_state(monkeypatch):
    monkeypatch.setattr(tvtropes, "_last_request", 0.0)


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


@pytest.mark.parametrize("enabled", [True, False])
def test_one_shot_wrapper_closes_client_on_success_and_error(tmp_path, monkeypatch, enabled):
    client = tvtropes.TVTropesClient(tmp_path, lambda *_: response(404), min_interval=0)
    closed = []
    monkeypatch.setattr(client, "close", lambda: closed.append(True))
    monkeypatch.setattr(tvtropes, "TVTropesClient", lambda **_kwargs: client)
    if enabled:
        assert tvtropes.fetch_movie_tropes(movie(), enabled=True).tropes == ()
    else:
        with pytest.raises(tvtropes.TVTropesDisabled):
            tvtropes.fetch_movie_tropes(movie(), enabled=False)
    assert closed == [True]


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_poc_help_never_opens_a_network_session(monkeypatch, capsys, flag):
    script = Path(__file__).resolve().parents[2] / "scripts" / "tvtropes_scraper.py"
    monkeypatch.setattr(sys, "argv", [str(script), flag])
    monkeypatch.setattr(
        tvtropes.curl_requests,
        "Session",
        lambda **_kwargs: pytest.fail("CLI help must not create a network session"),
    )
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(script), run_name="__main__")
    assert result.value.code == 0
    assert "usage:" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("value", "year", "expected"),
    [
        ("The Matrix", None, f"{tvtropes.BASE_URL}/pmwiki/pmwiki.php/Film/TheMatrix"),
        ("Film/Spider-Man", None, f"{tvtropes.BASE_URL}/pmwiki/pmwiki.php/Film/SpiderMan"),
        ("Don't Look Up", None, f"{tvtropes.BASE_URL}/pmwiki/pmwiki.php/Film/DontLookUp"),
        ("The Matrix", 2000, f"{tvtropes.BASE_URL}/pmwiki/pmwiki.php/Film/TheMatrix2000"),
    ],
)
def test_normalize_url(value, year, expected):
    assert tvtropes.normalize_url(value, year=year) == expected


@pytest.mark.parametrize(
    "url", ["http://tvtropes.org/pmwiki/pmwiki.php/Film/Alien", "https://evil.example/Film/Alien"]
)
def test_normalize_url_rejects_unsafe_urls(url):
    with pytest.raises(ValueError):
        tvtropes.normalize_url(url)


def test_poc_parser_extracts_descriptions_without_nested_list_text():
    html = """
    <div id="main-article"><ul><li>
      <a href="/pmwiki/pmwiki.php/Main/TimeLoop">Time Loop</a> - The hero relives a day.
      <ul><li><a href="/pmwiki/pmwiki.php/Main/Heist">Heist</a> - A separate plan.</li></ul>
    </li></ul></div>
    """
    links = tvtropes.extract_trope_links(html, tvtropes.BASE_URL)
    parent = next(item for item in links if item.mapped_slug == "time-loop")
    assert parent.description == "The hero relives a day."
    child = next(item for item in links if item.mapped_slug == "heist")
    assert child.description == "A separate plan."


def test_find_subpages_limits_results_to_same_secure_host():
    soup = BeautifulSoup(
        """
        <div id="main-article">
          <a href="/pmwiki/pmwiki.php/Film/AlienTropesAtoD">Tropes A to D</a>
          <a href="https://evil.example/Film/AlienTropesEtoM">Tropes E to M</a>
          <a href="/pmwiki/pmwiki.php/Film/AlienTropesNtoZ">Other link</a>
        </div>
        """,
        "html.parser",
    )
    assert tvtropes.find_subpages(soup, f"{tvtropes.BASE_URL}/pmwiki/pmwiki.php/Film/Alien") == [
        f"{tvtropes.BASE_URL}/pmwiki/pmwiki.php/Film/AlienTropesAtoD",
        f"{tvtropes.BASE_URL}/pmwiki/pmwiki.php/Film/AlienTropesNtoZ",
    ]


def test_reuses_one_chrome_session_without_preflight(tmp_path, monkeypatch):
    calls = []

    class FixtureSession:
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return response(404)

    def session(*, impersonate):
        assert impersonate == "chrome"
        return FixtureSession()

    monkeypatch.setattr(tvtropes.curl_requests, "Session", session)
    client = tvtropes.TVTropesClient(tmp_path, min_interval=0)
    client.sync_movie(movie(), enabled=True)
    assert len(calls) == 2 and all("/Film/" in url for url, _ in calls)
    assert calls[0][1]["headers"]["User-Agent"] == tvtropes.USER_AGENT


def test_challenge_stops_the_batch_immediately(tmp_path):
    requests = []

    def get(url, headers):
        requests.append(url)
        return response(200, "<title>Just a moment...</title><div id='cf-challenge'>")

    client = tvtropes.TVTropesClient(tmp_path, get, min_interval=0)
    result = client.sync_movie(movie(), enabled=True)
    assert result.stopped_reason == "rate_limited_or_challenge"
    client.sync_movie(movie(tmdb_id=43), enabled=True)

    assert len(requests) == 1
    assert requests[-1].endswith("/Film/HeistOne")


def test_work_resolution_extracts_only_local_links_and_maps_camel_case(tmp_path):
    requests = []
    work_page = """
    <html><h1>Heist One (2000)</h1><div id="main-article"><ul>
      <li><a href="/pmwiki/pmwiki.php/Main/TimeLoop">time loop</a> - Repeats a day.</li>
      <li><a href="https://evil.example/pmwiki/pmwiki.php/Main/Heist">external</a></li>
      <li><a href="/pmwiki/pmwiki.php/Main/AlienInvasion">alien invasion</a></li>
    </ul>
    </div></html>
    """

    def get(url, headers):
        requests.append(url)
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
    assert client.pages_fetched == 1


def test_sync_crawls_split_trope_subpages_and_caches_descriptions(tmp_path):
    requests = []
    work_url = f"{tvtropes.BASE_URL}/pmwiki/pmwiki.php/Film/HeistOne"
    main_page = """
    <h1>Heist One (2000)</h1><div id="main-article">
      <a href="/pmwiki/pmwiki.php/Film/HeistOneTropesAtoD">Tropes A to D</a>
      <ul><li><a href="/pmwiki/pmwiki.php/Main/Heist">Heist</a> - A crew plans a robbery.</li></ul>
    </div>
    """
    subpage = """
    <div class="article-content"><ul>
      <li><a href="/pmwiki/pmwiki.php/Main/DoubleCross">Double Cross</a> - An ally betrays the crew.</li>
    </ul></div>
    """

    def get(url, headers):
        requests.append(url)
        if url == work_url:
            return response(200, main_page)
        if url.endswith("/Film/HeistOneTropesAtoD"):
            return response(200, subpage)
        raise AssertionError(f"Unexpected fixture request: {url}")

    client = tvtropes.TVTropesClient(tmp_path, get, min_interval=0)
    result = client.sync_movie(movie(), enabled=True)
    assert {link.mapped_slug for link in result.tropes} == {"heist", "double-cross"}
    assert next(link for link in result.tropes if link.mapped_slug == "heist").description == (
        "A crew plans a robbery."
    )
    assert requests[-1].endswith("/Film/HeistOneTropesAtoD")
    cached = tvtropes._read_cache(tmp_path / "42.json")
    assert len(cached["pages"]) == 2


def test_expired_cache_revalidates_with_etag_without_storing_html(tmp_path):
    now = [1_000_000.0]
    requests: list[tuple[str, dict[str, str]]] = []
    page = '<h1>Heist One (2000)</h1><ul><li><a href="/pmwiki/pmwiki.php/Main/Heist">Heist</a> - A robbery plan.</li></ul>'

    def get(url, headers):
        requests.append((url, headers))
        if len([entry for entry in requests if entry[0] == url]) == 1:
            return response(200, page, {"ETag": '"work-v1"'})
        return response(304)

    client = tvtropes.TVTropesClient(
        tmp_path, get, min_interval=0, now=lambda: now[0], sleep=lambda _: None
    )
    first = client.sync_movie(
        movie(tvtropes_work_url=f"{tvtropes.BASE_URL}/Film/HeistOne"), enabled=True
    )
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
    assert (
        datetime.fromisoformat(tvtropes._read_cache(tmp_path / "42.json")["fetched_at"]).tzinfo
        == UTC
    )


def test_404_negative_cache_survives_new_client_and_has_no_network_delay(tmp_path):
    calls = []

    def get(url, headers):
        calls.append(url)
        return response(404)

    client = tvtropes.TVTropesClient(tmp_path, get, min_interval=0)
    result = client.sync_movie(movie(), enabled=True)
    assert result.work_url is None and len(calls) == 2
    assert calls[0].endswith("HeistOne") and calls[1].endswith("HeistOne2000")
    next_client = tvtropes.TVTropesClient(
        tmp_path,
        get,
        sleep=lambda _: pytest.fail("Cache hits must not sleep"),
    )
    assert next_client.sync_movie(movie(), enabled=True).cached
    assert next_client.pages_fetched == 0 and len(calls) == 2


@pytest.mark.parametrize(
    "base",
    [
        (404, ""),
        (200, "<h1>Disambiguation</h1>"),
        (200, "<h1>Heist One (1990)</h1>"),
    ],
)
def test_year_probe_only_after_miss_or_ambiguity(tmp_path, base):
    calls = []

    def request(url, headers):
        calls.append(url)
        if url.endswith("2000"):
            return response(200, '<h1>Heist One</h1><div id="main-article">A 2000 film.</div>')
        return response(*base)

    client = tvtropes.TVTropesClient(tmp_path, request, min_interval=0)
    result = client.sync_movie(movie(), enabled=True)
    assert result.work_url.endswith("2000") and client.pages_fetched == 2
    assert calls[0].endswith("HeistOne")


@pytest.mark.parametrize(
    "status,text",
    [
        (403, ""),
        (429, ""),
        (200, "Just a moment..."),
        (200, "cf-browser-verification"),
        (200, "Attention Required!"),
    ],
)
def test_all_backpressure_signals_trip_batch_breaker(tmp_path, status, text):
    client = tvtropes.TVTropesClient(
        tmp_path,
        lambda *_: response(status, text),
        min_interval=0,
    )
    assert client.sync_movie(movie(), enabled=True).stopped_reason == "rate_limited_or_challenge"
    assert (
        client.sync_movie(movie(tmdb_id=43), enabled=True).stopped_reason
        == "rate_limited_or_challenge"
    )
    assert client.pages_fetched == 1


def test_page_cap_returns_partial_evidence_without_negative_caching(tmp_path):
    html = (
        '<h1>Heist One (2000)</h1><div id="main-article"><ul><li>'
        '<a href="/Main/Heist">Heist</a></li></ul>'
        '<a href="/Film/HeistOneTropesAtoD">Tropes A to D</a></div>'
    )
    client = tvtropes.TVTropesClient(
        tmp_path,
        lambda *_: response(200, html),
        min_interval=0,
        max_pages=1,
    )
    result = client.sync_movie(movie(), enabled=True)
    assert result.stopped_reason == "page_cap_reached"
    assert client.pages_fetched == 1 and result.tropes[0].mapped_slug == "heist"
    assert not (tmp_path / "42.json").exists()


def test_subpage_crawl_is_capped_at_six(tmp_path):
    html = (
        '<h1>Heist One (2000)</h1><div id="main-article">'
        + "".join(f'<a href="/Film/HeistOneTropes{i}ToZ">Tropes {i} to Z</a>' for i in range(10))
        + "</div>"
    )
    client = tvtropes.TVTropesClient(
        tmp_path,
        lambda *_: response(200, html),
        min_interval=0,
    )
    assert client.sync_movie(movie(), enabled=True).stopped_reason is None
    assert client.pages_fetched == 7


@pytest.mark.anyio
async def test_scraped_work_evidence_uses_genre_gate_without_embedding_floor(monkeypatch):
    fingerprint = embeddings.LOCAL_FINGERPRINT
    config = SimpleNamespace(fingerprint=fingerprint, local_fingerprint=fingerprint)
    monkeypatch.setattr(embeddings, "load_config", lambda _session: config)

    async def embed_batch(_config, texts):
        raise AssertionError("Verified work-page evidence should not need a model")

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

    assert result["heist"] == 1.0
    assert result["unmapped-story-page"] is None
    assert "cyberpunk" not in result
