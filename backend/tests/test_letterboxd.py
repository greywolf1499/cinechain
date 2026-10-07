"""Curated canon ingestion engine tests.

Pure-function DOM parsing / pagination / TMDB-resolution helpers are tested
directly against canned HTML/fake HTTP responses - no real network or
curl_cffi socket I/O needed. `scrape_letterboxd_list` is tested end-to-end by
monkeypatching `new_session()` to return a fake session whose `.get()`
returns canned per-URL HTML, proving the full fetch -> parse -> TMDB-resolve
pipeline wires together correctly.
"""

from __future__ import annotations

import os
import time

import pytest
from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests

from app.services import letterboxd


# ---------------------------------------------------------
# Pure parsing / helper functions
# ---------------------------------------------------------
def test_build_paginated_url_detail_mode():
    url = letterboxd.build_paginated_url(
        "https://letterboxd.com/official/list/top-250-documentary-films/", 1, detail_mode=True
    )
    assert url == "https://letterboxd.com/official/list/top-250-documentary-films/detail/"

    url_page_2 = letterboxd.build_paginated_url(
        "https://letterboxd.com/official/list/top-250-documentary-films/", 2, detail_mode=True
    )
    assert url_page_2 == (
        "https://letterboxd.com/official/list/top-250-documentary-films/page/2/detail/"
    )


def test_build_paginated_url_standard_mode():
    url = letterboxd.build_paginated_url("https://letterboxd.com/alice/watchlist/", 3)
    assert url == "https://letterboxd.com/alice/watchlist/page/3/"


def test_year_from_slug():
    assert letterboxd.year_from_slug("dune-2021") == 2021
    assert letterboxd.year_from_slug("stalker-1979") == 1979
    assert letterboxd.year_from_slug("parasite") is None


def test_normalize_slug_strips_prefixes():
    assert (
        letterboxd.normalize_slug("https://letterboxd.com/film/parasite-2019/") == "parasite-2019"
    )
    assert letterboxd.normalize_slug("/film/parasite-2019/crew/") == "parasite-2019"


def test_clean_title_str_strips_accents_and_quotes():
    assert letterboxd.clean_title_str("Amélie") == "Amelie"
    assert letterboxd.clean_title_str('"The Godfather"') == "The Godfather"


def test_derive_badge_prefix_uses_explicit_value():
    assert (
        letterboxd.derive_badge_prefix("https://letterboxd.com/any/list/anything/", "ss22")
        == "SS22"
    )


def test_derive_badge_prefix_falls_back_to_slug():
    prefix = letterboxd.derive_badge_prefix(
        "https://letterboxd.com/someone/list/top-100-greatest-films/"
    )
    assert prefix  # non-empty
    assert prefix == prefix.upper()


DETAIL_HTML = """
<ul>
  <li class="film-detail">
    <div class="film-poster" data-film-slug="parasite-2019"></div>
    <h2 class="headline-2"><a href="/film/parasite-2019/">Parasite</a> <small>2019</small></h2>
    <div class="film-detail-content">
      <p>Directed by <a href="/director/bong-joon-ho/">Bong Joon-ho</a></p>
    </div>
  </li>
  <li class="film-detail">
    <div class="film-poster" data-film-slug="stalker-1979"></div>
    <h2 class="headline-2"><a href="/film/stalker-1979/">Stalker</a> <small>1979</small></h2>
    <div class="film-detail-content">
      <p>Directed by <a href="/director/andrei-tarkovsky/">Andrei Tarkovsky</a></p>
    </div>
  </li>
</ul>
"""

GRID_HTML = """
<ul class="poster-list">
  <li class="poster-container">
    <div class="film-poster" data-film-slug="dune-2021" data-film-name="Dune (2021)"></div>
  </li>
  <li class="poster-container">
    <div class="film-poster" data-film-slug="amelie-2001" data-film-name="Amelie (2001)"></div>
  </li>
</ul>
"""


def test_parse_detail_entries_extracts_title_year_slug_directors():
    soup = BeautifulSoup(DETAIL_HTML, "html.parser")
    entries = letterboxd.parse_detail_entries(soup)

    assert len(entries) == 2
    assert entries[0]["slug"] == "parasite-2019"
    assert entries[0]["title"] == "Parasite"
    assert entries[0]["year"] == 2019
    assert entries[0]["directors"] == ["Bong Joon-ho"]
    assert entries[1]["slug"] == "stalker-1979"
    assert entries[1]["year"] == 1979


def test_parse_grid_entries_extracts_title_year_slug():
    soup = BeautifulSoup(GRID_HTML, "html.parser")
    entries = letterboxd.parse_grid_entries(soup)

    assert len(entries) == 2
    assert entries[0]["slug"] == "dune-2021"
    assert entries[0]["title"] == "Dune"
    assert entries[0]["year"] == 2021


def test_has_next_page_true_and_false():
    with_next = BeautifulSoup(
        '<div class="paginate-pages"><a class="next" href="/page/2/">Next</a></div>', "html.parser"
    )
    without_next = BeautifulSoup("<div class='paginate-pages'></div>", "html.parser")
    assert letterboxd.has_next_page(with_next) is True
    assert letterboxd.has_next_page(without_next) is False


# ---------------------------------------------------------
# TMDB multi-pass resolution (fake curl_cffi session)
# ---------------------------------------------------------
class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self.ok = status_code < 400
        self._payload = payload

    def json(self):
        return self._payload


class _FakeTmdbSession:
    """Routes every GET by matching `query`/`primary_release_year` params
    against the scripted `responses` list, in order - mirrors a real multi-
    pass search flow (exact year -> near year -> title-only -> slug)."""

    def __init__(self, responses: list[dict]):
        self.responses = responses
        self.calls: list[dict] = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(params or {})
        index = len(self.calls) - 1
        if index < len(self.responses):
            return _FakeResponse(200, {"results": self.responses[index]})
        return _FakeResponse(200, {"results": []})


def test_resolve_tmdb_multipass_matches_exact_year():
    session = _FakeTmdbSession(
        [[{"id": 496243, "title": "Parasite", "release_date": "2019-05-30"}]]
    )
    tmdb_id = letterboxd.resolve_tmdb_multipass(
        session, "Parasite", 2019, ["Bong Joon-ho"], "fake-api-key", "parasite-2019"
    )
    assert tmdb_id["tmdb_id"] == 496243


def test_resolve_tmdb_multipass_falls_back_to_title_only():
    # Exact year, year-1, year+1 all empty; title-only pass (4th call) matches.
    session = _FakeTmdbSession(
        [[], [], [], [{"id": 11, "title": "Star Wars", "release_date": "1977-05-25"}]]
    )
    tmdb_id = letterboxd.resolve_tmdb_multipass(
        session, "Star Wars", 1977, [], "fake-api-key", "star-wars-1977"
    )
    assert tmdb_id["tmdb_id"] == 11


def test_resolve_tmdb_multipass_records_unmatched_when_nothing_matches():
    session = _FakeTmdbSession([[], [], [], []])
    tmdb_id = letterboxd.resolve_tmdb_multipass(
        session, "Totally Obscure Film", 2020, [], "fake-api-key", "totally-obscure-film-2020"
    )
    assert tmdb_id["tmdb_id"] is None and tmdb_id["status"] == "unmatched"


def test_resolve_tmdb_multipass_rejects_low_similarity_titles():
    session = _FakeTmdbSession(
        [[{"id": 1, "title": "Completely Different Movie", "release_date": "2019-01-01"}]]
    )
    tmdb_id = letterboxd.resolve_tmdb_multipass(
        session, "Parasite", 2019, [], "fake-api-key", "parasite-2019"
    )
    assert tmdb_id["tmdb_id"] is None and tmdb_id["status"] == "unmatched"


# ---------------------------------------------------------
# End-to-end scrape pipeline (fake session serving canned HTML)
# ---------------------------------------------------------
class _FakeHtmlResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code
        self.headers: dict[str, str] = {}

    def raise_for_status(self):
        pass


class _FakeScrapeSession:
    """Serves canned HTML per exact URL, then a 404-shaped failure for
    anything else (end of pagination)."""

    def __init__(self, pages: dict[str, str]):
        self.pages = pages

    def get(self, url, params=None, headers=None, timeout=None):
        if url in self.pages:
            return _FakeHtmlResponse(self.pages[url])
        raise RuntimeError(f"unexpected fetch of {url}")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_scrape_letterboxd_list_end_to_end(tmp_path, monkeypatch):
    list_url = "https://letterboxd.com/official/list/top-250-documentary-films/"
    page_1_url = letterboxd.build_paginated_url(list_url, 1, detail_mode=True)

    monkeypatch.setattr(
        letterboxd, "new_session", lambda: _FakeScrapeSession({page_1_url: DETAIL_HTML})
    )
    monkeypatch.setattr(letterboxd, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(letterboxd, "checkpoint_dir", lambda: tmp_path)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    tmdb_ids_by_slug = {"parasite-2019": 496243, "stalker-1979": 10543}
    monkeypatch.setattr(
        letterboxd,
        "resolve_tmdb_multipass",
        lambda client, title, year, directors, api_key, slug=None, **kwargs: {
            "tmdb_id": tmdb_ids_by_slug.get(slug),
            "tmdb_type": "movie",
        },
    )

    progress_events = []
    result = letterboxd.scrape_letterboxd_list(
        list_url, tmdb_api_key="fake-key", progress_callback=progress_events.append
    )

    assert result["total_films"] == 2
    assert result["films"][0]["slug"] == "parasite-2019"
    assert result["films"][0]["tmdb_id"] == 496243
    assert result["films"][1]["tmdb_id"] == 10543
    assert result["films"][0]["rank"] == 1
    assert any(event["stage"] == "fetch_page" for event in progress_events)
    assert any(event["stage"] == "page_done" for event in progress_events)


# ---------------------------------------------------------
# Anti-bot session handling
# ---------------------------------------------------------
class _SeqResponse:
    def __init__(
        self,
        text: str = "<html>Letterboxd</html>",
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise curl_requests.exceptions.HTTPError(str(self.status_code), response=self)


class _SeqSession:
    """Returns scripted responses per URL (a list is consumed in order) and records requests."""

    def __init__(self, pages: dict[str, list[_SeqResponse] | _SeqResponse]):
        self.pages = pages
        self.requested: list[str] = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.requested.append(url)
        entry = self.pages.get(url)
        if entry is None:
            return _SeqResponse(status_code=404)
        if isinstance(entry, list):
            return entry.pop(0)
        return entry

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr(letterboxd, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(letterboxd, "checkpoint_dir", lambda: tmp_path)


def test_fetch_html_backs_off_on_429_then_succeeds(tmp_path, monkeypatch):
    _patch_dirs(monkeypatch, tmp_path)
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    events: list[dict] = []
    session = _SeqSession(
        {
            "https://letterboxd.com/x/": [
                _SeqResponse(status_code=429, headers={"Retry-After": "7"}),
                _SeqResponse("<html>Letterboxd ok</html>"),
            ]
        }
    )

    html = letterboxd.fetch_html(
        session, "https://letterboxd.com/x/", progress_callback=events.append
    )

    assert "ok" in html
    assert any(s >= 8 for s in sleeps)  # Retry-After plus jitter
    assert any(e["stage"] == "rate_limited" for e in events)


def test_fetch_html_raises_cloudflare_block(tmp_path, monkeypatch):
    _patch_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    session = _SeqSession(
        {
            "https://letterboxd.com/x/": _SeqResponse(
                "<title>Just a moment...</title>", status_code=200
            )
        }
    )

    with pytest.raises(letterboxd.CloudflareBlock):
        letterboxd.fetch_html(session, "https://letterboxd.com/x/")


def test_fetch_html_ignores_poisoned_cache(tmp_path, monkeypatch):
    _patch_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    url = "https://letterboxd.com/x/"
    import hashlib

    (tmp_path / f"{hashlib.md5(url.encode()).hexdigest()}.html").write_text(
        "<title>Just a moment...</title> Letterboxd", encoding="utf-8"
    )
    session = _SeqSession({url: _SeqResponse("<html>Letterboxd fresh</html>")})

    assert "fresh" in letterboxd.fetch_html(session, url)
    assert session.requested == [url]


def test_polite_delay_triggers_macro_break(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    monkeypatch.setattr(letterboxd.random, "random", lambda: 0.01)
    events: list[dict] = []

    letterboxd.polite_delay(progress_callback=events.append)

    assert max(sleeps) >= 20
    assert events[0]["stage"] == "stealth_break"


# ---------------------------------------------------------
# Checkpointing / resume
# ---------------------------------------------------------
LIST_URL = "https://letterboxd.com/someone/list/my-list/"
PAGE_1_HTML = DETAIL_HTML + '<div class="paginate-pages"><a class="next" href="/p2/">Next</a></div>'
PAGE_2_HTML = """
<ul><li class="film-detail">
  <div class="film-poster" data-film-slug="dune-2021"></div>
  <h2 class="headline-2"><a href="/film/dune-2021/">Dune</a> <small>2021</small></h2>
</li></ul>
"""


def _resolver_by_slug(ids: dict[str, int]):
    return lambda client, title, year, directors, api_key, slug=None, **kwargs: {
        "tmdb_id": ids.get(slug),
        "tmdb_type": "movie",
        "original_language": "en",
    }


def test_scrape_resumes_from_checkpoint_after_cloudflare_block(tmp_path, monkeypatch):
    _patch_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        letterboxd,
        "resolve_tmdb_multipass",
        _resolver_by_slug({"parasite-2019": 1, "stalker-1979": 2, "dune-2021": 3}),
    )
    p1 = letterboxd.build_paginated_url(LIST_URL, 1, detail_mode=True)
    p2 = letterboxd.build_paginated_url(LIST_URL, 2, detail_mode=True)

    blocked = _SeqSession(
        {p1: _SeqResponse(PAGE_1_HTML), p2: _SeqResponse("<title>Just a moment...</title>")}
    )
    monkeypatch.setattr(letterboxd, "new_session", lambda: blocked)
    events: list[dict] = []
    with pytest.raises(letterboxd.CloudflareBlock):
        letterboxd.scrape_letterboxd_list(
            LIST_URL, tmdb_api_key="k", progress_callback=events.append
        )
    assert any(e["stage"] == "aborted" for e in events)
    assert len(list(tmp_path.glob("checkpoint_*.json"))) == 1

    healthy = _SeqSession({p2: _SeqResponse(PAGE_2_HTML)})
    monkeypatch.setattr(letterboxd, "new_session", lambda: healthy)
    events.clear()
    result = letterboxd.scrape_letterboxd_list(
        LIST_URL, tmdb_api_key="k", progress_callback=events.append
    )

    assert healthy.requested == [p2]  # page 1 is not re-fetched
    assert [f["slug"] for f in result["films"]] == ["parasite-2019", "stalker-1979", "dune-2021"]
    assert [f["rank"] for f in result["films"]] == [1, 2, 3]
    assert any(e["stage"] == "resume" for e in events)
    assert list(tmp_path.glob("checkpoint_*.json")) == []  # cleared on completion


def test_scrape_treats_404_on_later_page_as_end_of_list(tmp_path, monkeypatch):
    _patch_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        letterboxd,
        "resolve_tmdb_multipass",
        _resolver_by_slug({"parasite-2019": 1, "stalker-1979": 2}),
    )
    p1 = letterboxd.build_paginated_url(LIST_URL, 1, detail_mode=True)
    session = _SeqSession({p1: _SeqResponse(PAGE_1_HTML)})  # page 2 -> 404
    monkeypatch.setattr(letterboxd, "new_session", lambda: session)

    result = letterboxd.scrape_letterboxd_list(LIST_URL, tmdb_api_key="k")

    assert result["total_films"] == 2


def test_scrape_404_on_first_page_is_an_error(tmp_path, monkeypatch):
    _patch_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    monkeypatch.setattr(letterboxd, "new_session", lambda: _SeqSession({}))

    with pytest.raises(curl_requests.exceptions.HTTPError):
        letterboxd.scrape_letterboxd_list(LIST_URL, tmdb_api_key="k")


def test_checkpoint_manager_upserts_by_slug_and_expires(tmp_path, monkeypatch):
    monkeypatch.setattr(letterboxd, "checkpoint_dir", lambda: tmp_path)
    cp = letterboxd.CheckpointManager("https://x/list/")
    cp.upsert({"slug": "a", "title": "old"})
    cp.upsert({"slug": "a", "title": "new"})
    cp.save(3)

    reloaded = letterboxd.CheckpointManager("https://x/list/")
    assert reloaded.current_page == 3
    assert reloaded.items == [{"slug": "a", "title": "new"}]

    old = time.time() - letterboxd.CHECKPOINT_TTL_SECONDS - 10
    os.utime(reloaded.filepath, (old, old))
    assert letterboxd.CheckpointManager("https://x/list/").items == []


# ---------------------------------------------------------
# TMDB director disambiguation, deep metadata
# ---------------------------------------------------------
def test_resolve_tmdb_multipass_uses_directors_to_break_ties(monkeypatch):
    monkeypatch.setattr(letterboxd, "_tmdb_director_cache", {})

    class Session:
        def get(self, url, params=None, headers=None, timeout=None):
            if "/credits" in url:
                name = "Andrei Tarkovsky" if "/movie/1/" in url else "Steven Soderbergh"
                return _FakeResponse(200, {"crew": [{"job": "Director", "name": name}]})
            return _FakeResponse(
                200,
                {
                    "results": [
                        {"id": 1, "title": "Solaris", "release_date": "1972-03-20"},
                        {"id": 2, "title": "Solaris", "release_date": "2002-11-27"},
                    ]
                },
            )

    match = letterboxd.resolve_tmdb_multipass(
        Session(), "Solaris", None, ["Steven Soderbergh"], "k"
    )

    assert match is not None and match["tmdb_id"] == 2


def test_enrichment_fetches_imdb_lazily_and_preserves_source_title(monkeypatch):
    visits = []

    def deep(*args, **kwargs):
        visits.append(args[1])
        return {"imdb_id": "tt1234567"}

    monkeypatch.setattr(letterboxd, "extract_deep_metadata", deep)

    class Session:
        def get(self, url, params=None, headers=None, timeout=None):
            if "/find/" in url:
                assert params["external_source"] == "imdb_id"
                return _FakeResponse(200, {"movie_results": [{"id": 42, "title": "TMDB title"}]})
            if params["query"] == "Known":
                return _FakeResponse(
                    200, {"results": [{"id": 1, "title": "Known", "release_date": "2000-01-01"}]}
                )
            return _FakeResponse(200, {"results": []})

    known = {"title": "Known", "year": 2000, "slug": "known"}
    letterboxd.enrich_entry(Session(), known, False, "key", False)
    assert known["match_tier"] == "exact" and not visits
    missing = {"title": "Source title", "year": 2000, "slug": "source"}
    letterboxd.enrich_entry(Session(), missing, False, "key", False)
    assert visits == ["source"] and missing["imdb_id"] == "tt1234567"
    assert missing["tmdb_id"] == 42 and missing["match_tier"] == "imdb"
    assert missing["title"] == "Source title"


def test_enrichment_records_provider_failure_and_missing_key(monkeypatch):
    monkeypatch.setattr(letterboxd, "tmdb_get", lambda *args, **kwargs: None)
    entry = {"title": "Film", "year": 2000, "slug": "film"}
    letterboxd.enrich_entry(object(), entry, False, "key", False)
    assert entry["status"] == "unmatched" and entry["tmdb_id"] is None
    assert "failed after retries" in entry["reason"]
    letterboxd.enrich_entry(object(), entry, False, None, False)
    assert "not configured" in entry["reason"]


def test_inline_and_lazily_discovered_tv_entries_never_match_movies(monkeypatch):
    html = """<ul class="poster-list"><li class="poster-container">
      <div class="film-poster" data-film-slug="series" data-tmdb-id="99"
           data-tmdb-type="tv"><img alt="Series (2000)"></div></li></ul>"""
    entry = letterboxd.parse_grid_entries(BeautifulSoup(html, "html.parser"))[0]
    letterboxd.enrich_entry(object(), entry, False, "key", False)
    assert entry["status"] == "tv_title" and entry["tmdb_id"] is None
    monkeypatch.setattr(letterboxd, "tmdb_get", lambda *args, **kwargs: {"results": []})
    monkeypatch.setattr(
        letterboxd,
        "extract_deep_metadata",
        lambda *args, **kwargs: {
            "tmdb_id": 99,
            "tmdb_type": "tv",
            "imdb_id": "tt1234567",
        },
    )
    lazy = {"title": "Series", "year": 2000, "slug": "series"}
    letterboxd.enrich_entry(object(), lazy, False, "key", False)
    assert lazy["status"] == "tv_title" and lazy["tmdb_id"] is None
    assert "television" in lazy["reason"]


def test_extract_deep_metadata_reads_tmdb_and_imdb_ids(tmp_path, monkeypatch):
    _patch_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    html = (
        '<html><body data-tmdb-id="496243" data-tmdb-type="movie">Letterboxd'
        '<a href="https://www.imdb.com/title/tt6751668/maps">IMDb</a></body></html>'
    )
    session = _SeqSession({"https://letterboxd.com/film/parasite-2019/": _SeqResponse(html)})

    meta = letterboxd.extract_deep_metadata(session, "parasite-2019", no_cache=True)

    assert meta["tmdb_id"] == 496243
    assert meta["tmdb_type"] == "movie"
    assert meta["imdb_id"] == "tt6751668"


# ---------------------------------------------------------
# Diary / RSS
# ---------------------------------------------------------
DIARY_HTML = """
<table>
<tr class="diary-entry-row">
  <td class="col-monthdate"><a href="/u/films/diary/for/2024/05/">May 2024</a></td>
  <td class="col-daydate"><a href="/u/films/diary/for/2024/05/03/">3</a></td>
  <td class="col-production">
    <div class="film-poster" data-film-slug="parasite-2019" data-tmdb-id="496243"></div>
    <h3 class="headline-3"><a href="/film/parasite-2019/">Parasite</a></h3>
  </td>
  <td class="col-released">2019</td>
  <td class="col-rating"><span class="rating rated-9"></span></td>
  <td class="col-rewatch icon-status-off"></td>
</tr>
<tr class="diary-entry-row">
  <td class="col-daydate"><a href="#">12</a></td>
  <td class="col-production">
    <div class="film-poster" data-film-slug="stalker-1979"></div>
    <h3 class="headline-3"><a href="/film/stalker-1979/">Stalker</a></h3>
  </td>
  <td class="col-released">1979</td>
  <td class="col-rating"><span class="rating rated-7"></span></td>
  <td class="col-rewatch"></td>
</tr>
</table>
"""


def test_parse_diary_entries_carries_month_state_and_flags_rewatches():
    entries = letterboxd.parse_diary_entries(BeautifulSoup(DIARY_HTML, "html.parser"))

    assert [e["slug"] for e in entries] == ["parasite-2019", "stalker-1979"]
    assert entries[0]["watched_at"] == "2024-05-03"
    assert entries[0]["user_rating"] == 4.5
    assert entries[0]["is_rewatch"] is False
    assert entries[0]["tmdb_id"] == 496243
    assert entries[1]["watched_at"] == "2024-05-12"
    assert entries[1]["user_rating"] == 3.5
    assert entries[1]["is_rewatch"] is True


def test_scrape_user_diary_history_keeps_repeat_watches(tmp_path, monkeypatch):
    _patch_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    url = "https://letterboxd.com/alice/films/diary/"
    session = _SeqSession(
        {
            url: _SeqResponse(
                DIARY_HTML + DIARY_HTML.replace("3</a>", "9</a>").replace("/03/", "/09/")
            )
        }
    )
    monkeypatch.setattr(letterboxd, "new_session", lambda: session)

    result = letterboxd.scrape_user_diary_history("alice", mode="diary")

    assert result["list_type"] == "diary"
    # parasite on two dates + stalker (same date de-duplicated)
    assert len(result["films"]) == 3
    assert "rank" not in result["films"][0]


RSS_XML = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:letterboxd="https://letterboxd.com" xmlns:tmdb="https://themoviedb.org">
 <channel>
  <item>
   <title>Parasite, 2019 - 4.5 stars</title>
   <link>https://letterboxd.com/alice/film/parasite-2019/</link>
   <description>&lt;p&gt;Great film&lt;/p&gt;</description>
   <letterboxd:watchedDate>2024-05-03</letterboxd:watchedDate>
   <letterboxd:rewatch>No</letterboxd:rewatch>
   <letterboxd:filmTitle>Parasite</letterboxd:filmTitle>
   <letterboxd:filmYear>2019</letterboxd:filmYear>
   <letterboxd:memberRating>4.5</letterboxd:memberRating>
   <tmdb:movieId>496243</tmdb:movieId>
  </item>
 </channel>
</rss>"""


def test_ingest_rss_diary_parses_entries(monkeypatch):
    session = _SeqSession({"https://letterboxd.com/alice/rss/": _SeqResponse(RSS_XML)})
    monkeypatch.setattr(letterboxd, "new_session", lambda: session)

    result = letterboxd.ingest_rss_diary("alice")

    film = result["films"][0]
    assert film["slug"] == "parasite-2019"
    assert film["title"] == "Parasite"
    assert film["year"] == 2019
    assert film["user_rating"] == 4.5
    assert film["watched_at"] == "2024-05-03"
    assert film["is_rewatch"] is False
    assert film["review_snippet"] == "Great film"
    assert film["tmdb_id"] == 496243


def test_ingest_rss_diary_rejects_entity_expansion(monkeypatch):
    bomb = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss><channel/></rss>'
    session = _SeqSession({"https://letterboxd.com/alice/rss/": _SeqResponse(bomb)})
    monkeypatch.setattr(letterboxd, "new_session", lambda: session)

    with pytest.raises(Exception, match="(?i)entit"):
        letterboxd.ingest_rss_diary("alice")


# ---------------------------------------------------------
# Tier 0/1 discovery: HQ accounts, account inspection, account lists
# ---------------------------------------------------------
HQ_DIRECTORY_HTML = """
<html><body>Letterboxd
<ul class="member-directory">
  <li class="person-summary">
    <a class="avatar" href="/criterion/"><img class="avatar" src="/avatars/criterion.jpg"></a>
    <h3 class="name"><a href="/criterion/">The Criterion Collection</a></h3>
    <span class="badge -hq">HQ</span>
    <p class="bio">Curated cinema.</p>
  </li>
  <li class="person-summary">
    <a class="avatar" href="/bfi/"><img class="avatar" src="/avatars/bfi.jpg"></a>
    <h3 class="name"><a href="/bfi/">BFI</a></h3>
    <span class="badge -hq">HQ</span>
  </li>
</ul>
</body></html>
"""

LISTS_HTML = """
<html><body>Letterboxd
<section class="list film-list-summary">
  <h2 class="title-2"><a href="/criterion/list/the-criterion-collection/">The Criterion Collection</a></h2>
  <small class="value">1,234 films</small>
  <div class="body-text">Every spine number.</div>
  <ul class="poster-list">
    <li class="poster-container" data-film-slug="seven-samurai-1954"><img src="/posters/1.jpg"></li>
    <li class="poster-container" data-film-slug="stalker-1979"><img src="/posters/2.jpg"></li>
  </ul>
</section>
<section class="list film-list-summary">
  <h2 class="title-2"><a href="/criterion/list/janus-films/">Janus Films</a></h2>
  <small class="value">12 films</small>
</section>
</body></html>
"""

PROFILE_HTML = """
<html><body>Letterboxd
<header class="profile-header">
  <h1 class="displayname"><span class="label">The Criterion Collection</span></h1>
  <img class="avatar" src="/avatars/criterion.jpg">
  <span class="badge -hq">HQ</span>
  <div class="bio">Curated cinema.</div>
</header>
<nav><a href="/criterion/lists/" data-count="42">Lists</a></nav>
</body></html>
"""


def test_clean_username_rejects_path_injection():
    assert letterboxd.clean_username(" /criterion/ ") == "criterion"
    for bad in ("../etc", "a/b", "", "x y", "a" * 41):
        with pytest.raises(ValueError):
            letterboxd.clean_username(bad)


def test_discover_hq_accounts_parses_directory(tmp_path, monkeypatch):
    _patch_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    session = _SeqSession({"https://letterboxd.com/members/hq/": _SeqResponse(HQ_DIRECTORY_HTML)})
    monkeypatch.setattr(letterboxd, "new_session", lambda: session)

    result = letterboxd.discover_hq_accounts()

    assert [a["username"] for a in result["accounts"]] == ["criterion", "bfi"]
    assert result["accounts"][0]["display_name"] == "The Criterion Collection"
    assert result["accounts"][0]["avatar_url"] == "https://letterboxd.com/avatars/criterion.jpg"
    assert result["accounts"][0]["is_hq"] is True
    assert result["total_hqs"] == 2
    assert result["partial"] is False


def test_discover_hq_accounts_following_filters_non_hq(tmp_path, monkeypatch):
    _patch_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    html = HQ_DIRECTORY_HTML.replace(
        '<span class="badge -hq">HQ</span>\n    <p class="bio">', '<p class="bio">'
    )
    session = _SeqSession({"https://letterboxd.com/alice/following/": _SeqResponse(html)})
    monkeypatch.setattr(letterboxd, "new_session", lambda: session)

    only_hq = letterboxd.discover_hq_accounts("alice")
    everyone = letterboxd.discover_hq_accounts("alice", include_all=True, no_cache=True)

    assert [a["username"] for a in only_hq["accounts"]] == ["bfi"]
    assert [a["username"] for a in everyone["accounts"]] == ["criterion", "bfi"]


def test_discover_hq_accounts_returns_partial_on_cloudflare_block(tmp_path, monkeypatch):
    _patch_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    page_1 = HQ_DIRECTORY_HTML.replace(
        "</ul>", '</ul><div class="paginate-pages"><a class="next" href="/p2/">Next</a></div>'
    )
    session = _SeqSession(
        {
            "https://letterboxd.com/members/hq/": _SeqResponse(page_1),
            "https://letterboxd.com/members/hq/page/2/": _SeqResponse(
                "<title>Just a moment...</title>"
            ),
        }
    )
    monkeypatch.setattr(letterboxd, "new_session", lambda: session)

    result = letterboxd.discover_hq_accounts()

    assert len(result["accounts"]) == 2
    assert result["partial"] is True
    assert "Cloudflare" in result["error"]


def test_inspect_account_reads_profile(tmp_path, monkeypatch):
    _patch_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    session = _SeqSession({"https://letterboxd.com/criterion/": _SeqResponse(PROFILE_HTML)})
    monkeypatch.setattr(letterboxd, "new_session", lambda: session)

    profile = letterboxd.inspect_account("criterion")

    assert profile["display_name"] == "The Criterion Collection"
    assert profile["account_tier"] == "HQ"
    assert profile["total_public_lists"] == 42
    assert profile["bio"] == "Curated cinema."
    assert profile["avatar_url"] == "https://letterboxd.com/avatars/criterion.jpg"


def test_discover_user_lists_parses_cards(tmp_path, monkeypatch):
    _patch_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    session = _SeqSession({"https://letterboxd.com/criterion/lists/": _SeqResponse(LISTS_HTML)})
    monkeypatch.setattr(letterboxd, "new_session", lambda: session)

    result = letterboxd.discover_user_lists("criterion")

    first, second = result["lists"]
    assert first["title"] == "The Criterion Collection"
    assert first["url"] == "https://letterboxd.com/criterion/list/the-criterion-collection/"
    assert first["slug"] == "the-criterion-collection"
    assert first["total_films"] == 1234
    assert first["description"] == "Every spine number."
    assert first["preview_posters"] == [
        "https://letterboxd.com/posters/1.jpg",
        "https://letterboxd.com/posters/2.jpg",
    ]
    assert first["preview_slugs"] == ["seven-samurai-1954", "stalker-1979"]
    assert second["total_films"] == 12
    assert result["partial"] is False


# ---------------------------------------------------------
# Watchlists (Phase 22a): no /detail/ view exists for them
# ---------------------------------------------------------
def test_watchlist_scrape_uses_the_plain_grid_url_not_detail(tmp_path, monkeypatch):
    _patch_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        letterboxd, "resolve_tmdb_multipass", _resolver_by_slug({"dune-2021": 1, "amelie-2001": 2})
    )
    grid = _SeqResponse(
        GRID_HTML + '<div class="paginate-pages"><a class="next" href="/p2/">Next</a></div>'
    )
    session = _SeqSession(
        {
            "https://letterboxd.com/rsrax/watchlist/": grid,
            "https://letterboxd.com/rsrax/watchlist/page/2/": _SeqResponse(GRID_HTML),
            # what Letterboxd really serves for the old (broken) URL shape
            "https://letterboxd.com/rsrax/watchlist/detail/": _SeqResponse(status_code=404),
        }
    )
    monkeypatch.setattr(letterboxd, "new_session", lambda: session)

    result = letterboxd.scrape_letterboxd_watchlist("/rsrax/", tmdb_api_key="k")

    assert result["total_films"] == 2
    assert session.requested == [
        "https://letterboxd.com/rsrax/watchlist/",
        "https://letterboxd.com/rsrax/watchlist/page/2/",
    ]


def test_watchlist_404_on_the_first_page_is_reported_as_not_found(tmp_path, monkeypatch):
    _patch_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    monkeypatch.setattr(letterboxd, "new_session", lambda: _SeqSession({}))

    with pytest.raises(letterboxd.WatchlistNotFound):
        letterboxd.scrape_letterboxd_watchlist("ghost", tmdb_api_key="k")


def test_build_paginated_url_collapses_double_slashes():
    assert (
        letterboxd.build_paginated_url("https://letterboxd.com//rsrax///watchlist", 2)
        == "https://letterboxd.com/rsrax/watchlist/page/2/"
    )


def test_sessions_follow_redirects():
    session = letterboxd.new_session()
    assert session.allow_redirects is True and session.max_redirects == 5


# ---------------------------------------------------------
# Curator list cards (Phase 22a): title lives in `h2.name a`, not the poster overlay link
# ---------------------------------------------------------
LIST_CARD_HTML = """
<article class="list-summary js-list-summary">
  <figure class="figure posterset">
    <a class="poster-list-link" href="/criterion/list/october-leaving-soon/">
      <ul class="posterlist"><li class="posteritem">
        <div data-item-slug="stalker-1979"><img class="image" src="https://s.ltrbxd.com/static/img/empty-poster-70.png"></div>
      </li></ul>
    </a>
  </figure>
  <div class="body"><h2 class="name prettify">
    <a href="/criterion/list/october-leaving-soon/">October Leaving Soon | Criterion</a></h2>
    <span class="value">28 films</span></div>
</article>
"""


def test_parse_list_card_takes_the_title_from_the_headline_not_the_poster_overlay():
    card = BeautifulSoup(LIST_CARD_HTML, "html.parser").select_one("article")
    entry = letterboxd._parse_list_card(card)

    assert entry["title"] == "October Leaving Soon | Criterion"
    assert entry["total_films"] == 28
    assert entry["preview_slugs"] == ["stalker-1979"]
    assert entry["preview_posters"] == []  # lazy-load placeholder is not a poster


def test_parse_list_card_falls_back_to_a_readable_title():
    html = (
        '<article><a class="poster-list-link" href="/x/list/best-of-2020/">'
        '<img src="p.jpg"></a></article>'
    )
    entry = letterboxd._parse_list_card(BeautifulSoup(html, "html.parser").select_one("article"))
    assert entry["title"] == "Best Of 2020"
