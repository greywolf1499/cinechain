"""Curated canon ingestion engine: ports the vetted `scripts/letterboxd_poc.py`
into a native CineChain service.

Covers: anti-bot session handling (chrome impersonation, jittered polite
delays with micro/macro breaks, Retry-After backoff, Cloudflare detection,
HTML cache), `/detail/` pagination, per-URL checkpoint resume, multi-pass TMDB
resolution (year -> near-year -> title-only -> slug, director disambiguation),
deep per-film metadata (IMDb id / TMDB link), RSS diary + full diary/films
history ingestion, and SSE-friendly progress reporting. NOT ported: HQ account
discovery / user-list discovery (CLI exploration tools with no in-app use).

Network layer is sync (curl_cffi, like the POC) and MUST be run inside a
worker thread by callers (see `run_sync_in_thread` in routes_curated.py) -
never call anything in this module directly from the asyncio event loop.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import math
import os
import random
import re
import time
import unicodedata
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
from bs4.element import Tag
from curl_cffi import requests as curl_requests
from defusedxml import ElementTree as SafeET

from app.config import get_settings

logger = logging.getLogger(__name__)

BASE_URL = "https://letterboxd.com"
IMPERSONATE_PROFILE = "chrome124"
BROWSER_HEADERS = {
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://letterboxd.com/",
}
CACHE_TTL_SECONDS = 86400 * 7  # 7 days
# a stale checkpoint would resume a list that has since changed
CHECKPOINT_TTL_SECONDS = 86400
MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Director credits memo keyed by TMDB movie id, shared for the process lifetime.
_tmdb_director_cache: dict[int, list[str]] = {}

# Preset canon lists (badge codes match the Phase 15.5 spec exactly).
PRESETS: dict[str, dict[str, str]] = {
    "sight-and-sound-2022": {
        "url": "https://letterboxd.com/sightsoundmag/list/sight-and-sounds-greatest-films-of-all-time/",
        "title": "Sight & Sound Top 100 (2022)",
        "badge": "SS22",
    },
    "sight-and-sound-directors": {
        "url": "https://letterboxd.com/sightsoundmag/list/sight-and-sounds-directors-100-greatest-films/",
        "title": "Sight & Sound Directors' Top 100 (2022)",
        "badge": "SSD22",
    },
    "letterboxd-top-250": {
        "url": "https://letterboxd.com/ale890/list/official-top-250-narrative-feature-films/",
        "title": "Letterboxd Official Top 250",
        "badge": "LB250",
    },
    "letterboxd-docs-250": {
        "url": "https://letterboxd.com/official/list/top-250-documentary-films/",
        "title": "Letterboxd Official Top 250 Docs",
        "badge": "LBDOC250",
    },
}

CF_SIGNATURES = [
    "cf-turnstile",
    "<title>Just a moment...</title>",
    "<title>Verify you are human",
    "Attention Required!",
    "cloudflare-core",
    "cf-mitigated",
]

USERNAME_RE = re.compile(r'[A-Za-z0-9_]{1,40}')

# Tier 0 curator accounts tracked out of the box; HQ discovery can add more.
SEED_ACCOUNTS: list[dict[str, str]] = [
    {"username": "criterion", "display_name": "The Criterion Collection"},
    {"username": "sightsoundmag", "display_name": "Sight & Sound"},
    {"username": "bfi", "display_name": "BFI"},
    {"username": "mubi", "display_name": "MUBI"},
    {"username": "a24", "display_name": "A24"},
]


class CloudflareBlock(Exception):
    pass


class WatchlistNotFound(Exception):
    """The user's watchlist page 404'd (private, renamed or deleted account)."""

    def __init__(self, username: str):
        self.username = username
        super().__init__(
            f"Letterboxd watchlist for '{username}' was not found. The account may be "
            "private, renamed or deleted.")


ProgressCallback = Callable[[dict[str, Any]], None] | None


def report_progress(callback: ProgressCallback, stage: str, current: int | None,
                    total: int | None, message: str) -> None:
    if callback is not None:
        callback({"stage": stage, "current": current,
                 "total": total, "message": message})


# ---------------------------------------------------------
# Small DOM helpers (ported verbatim from the POC)
# ---------------------------------------------------------
def attr_str(node: Tag | None, name: str) -> str | None:
    if node is None:
        return None
    value = node.get(name)
    if isinstance(value, list):
        value = value[0] if value else None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def node_classes(node: Tag | None) -> list[str]:
    if node is None:
        return []
    classes = node.get("class") or []
    if isinstance(classes, str):
        return [classes]
    return [str(c) for c in classes]


def rating_from_classes(node: Tag | None) -> float | None:
    """Letterboxd encodes stars as `rated-N` where N is out of 10."""
    for cls in node_classes(node):
        match = re.match(r'^rated-(\d{1,2})$', cls)
        if match:
            return int(match.group(1)) / 2.0
    return None


def parse_year(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r'\b((?:18|19|20)\d{2})\b', value)
    return int(match.group(1)) if match else None


def parse_id(value: str | None) -> int | None:
    if not value:
        return None
    return int(value) if str(value).isdigit() else None


def year_from_slug(slug: str) -> int | None:
    match = re.search(r'-((?:18|19|20)\d{2})$', slug.strip('/'))
    if not match:
        return None
    year = int(match.group(1))
    return year if 1870 <= year <= datetime.now(UTC).year + 5 else None


def normalize_slug(raw: str | None) -> str | None:
    if not raw:
        return None
    slug = str(raw).strip()
    slug = re.sub(r'^https?://[^/]+', '', slug)
    slug = slug.replace('/film/', '').strip('/')
    slug = slug.split('/')[0]
    return slug or None


def clean_title_str(title: str) -> str:
    ascii_title = unicodedata.normalize('NFKD', title).encode(
        'ASCII', 'ignore').decode('utf-8')
    return ascii_title.replace('"', '').replace("'", "").strip()


def build_paginated_url(base_url: str, page_num: int, detail_mode: bool = False) -> str:
    """Canonical Letterboxd pagination (trailing slash always present).

      detail:    /{path}/detail/  ->  /{path}/page/2/detail/
      standard:  /{path}/         ->  /{path}/page/2/
    """
    parsed = urlparse(base_url)
    path = parsed.path.strip('/')
    path = re.sub(r'/page/\d+', '', path)
    had_detail = path == 'detail' or path.endswith('/detail')
    if had_detail:
        path = re.sub(r'/?detail$', '', path)
    path = path.strip('/')

    segments = [path] if path else []
    if page_num > 1:
        segments.append(f"page/{page_num}")
    if detail_mode or had_detail:
        segments.append("detail")

    final_path = "/" + "/".join(s for s in segments if s)
    if not final_path.endswith('/'):
        final_path += '/'
    return urlunparse((parsed.scheme or "https", parsed.netloc or "letterboxd.com",
                       final_path, parsed.params, parsed.query, parsed.fragment))


# ---------------------------------------------------------
# DOM parsers (ported verbatim from the POC)
# ---------------------------------------------------------
def extract_poster(container: Tag) -> Tag | None:
    if attr_str(container, "data-film-slug") or attr_str(container, "data-item-slug"):
        return container
    found = container.select_one(
        ".film-poster[data-film-slug], [data-film-slug], [data-item-slug], "
        "[data-film-link], [data-item-link], [data-target-link]")
    return found if isinstance(found, Tag) else None


def slug_from_poster(poster: Tag | None) -> str | None:
    if poster is None:
        return None
    for attr in ("data-film-slug", "data-item-slug", "data-film-link",
                 "data-item-link", "data-target-link"):
        slug = normalize_slug(attr_str(poster, attr))
        if slug:
            return slug
    return None


def title_year_from_img(poster: Tag | None, slug: str) -> tuple[str, int | None]:
    img = poster.select_one("img") if poster is not None else None
    candidates = [
        attr_str(poster, "data-item-full-display-name"),
        attr_str(poster, "data-item-name"),
        attr_str(img, "alt"),
        attr_str(poster, "data-film-name"),
    ]
    raw = ""
    for candidate in candidates:
        if not candidate:
            continue
        raw = raw or candidate.strip()
        match = re.match(
            r'^(.*)\s+\(((?:18|19|20)\d{2})\)$', candidate.strip())
        if match:
            return match.group(1).strip(), int(match.group(2))

    slug_year = year_from_slug(slug)
    if raw:
        return raw, slug_year
    bare = re.sub(r'-(?:18|19|20)\d{2}$', '', slug) if slug_year else slug
    return bare.replace('-', ' ').title(), slug_year


def parse_detail_entries(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """`/detail/` view: one entry per parent container, inline title/year/director."""
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()

    for container in soup.select("li.film-detail, .film-list-summary, .listitem"):
        if not isinstance(container, Tag):
            continue
        poster = extract_poster(container)
        slug = slug_from_poster(poster)
        if not slug or slug in seen:
            continue
        seen.add(slug)

        headline = container.select_one(
            ".headline-2 a, h2.primaryname a, h2 a")
        title = headline.get_text(strip=True) if headline else None

        year_tag = container.select_one(
            ".headline-2 small, h2 small, .releasedate a, .releasedate")
        year = parse_year(year_tag.get_text(strip=True)) if year_tag else None

        alt_title, alt_year = title_year_from_img(poster, slug)
        title = title or alt_title
        year = year or alt_year or year_from_slug(slug)

        directors = [d.get_text(strip=True) for d in container.select(
            ".film-detail-content p a[href*='/director/'], "
            ".credits a[href*='/director/'], a[href*='/director/']")]

        entries.append({
            "title": title,
            "year": year,
            "slug": slug,
            "directors": directors,
            "tmdb_id": parse_id(attr_str(poster, "data-tmdb-id")),
        })

    return entries


def parse_grid_entries(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Standard poster grid (watchlists, non-ranked lists without /detail/ data)."""
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()

    for container in soup.select("li.poster-container, li.griditem, ul.poster-list > li"):
        if not isinstance(container, Tag):
            continue
        poster = extract_poster(container)
        slug = slug_from_poster(poster)
        if not slug or slug in seen:
            continue
        seen.add(slug)

        title, year = title_year_from_img(poster, slug)
        entries.append({
            "title": title,
            "year": year,
            "slug": slug,
            "directors": [],
            "tmdb_id": parse_id(attr_str(poster, "data-tmdb-id")),
        })

    return entries


def has_next_page(soup: BeautifulSoup) -> bool:
    return soup.select_one(
        "div.paginate-pages a.next, a.next, a.paginate-next, .pagination a.next") is not None


# ---------------------------------------------------------
# TMDB multi-pass resolution (ported verbatim from the POC)
# ---------------------------------------------------------
def tmdb_auth(api_key: str) -> tuple[dict[str, str], dict[str, str]]:
    if len(api_key) > 50:
        return {"Authorization": f"Bearer {api_key}"}, {}
    return {}, {"api_key": api_key}


def tmdb_get(client: curl_requests.Session, url: str, params: dict[str, Any],
             headers: dict[str, str], max_retries: int = 3) -> dict[str, Any] | None:
    """Single TMDB call with 429 backoff. Returns None instead of raising."""
    for attempt in range(max_retries):
        try:
            resp = client.get(url, params=params,
                              headers=headers, timeout=10.0)
            if resp.status_code == 429:
                time.sleep(1)
                continue
            if not resp.ok:
                return None
            return resp.json()
        except Exception:  # noqa: BLE001 - never let a flaky TMDB call kill the sync
            if attempt == max_retries - 1:
                return None
            time.sleep(1)
    return None


def fetch_tmdb_directors(client: curl_requests.Session, tmdb_id: int, api_key: str) -> list[str]:
    if tmdb_id in _tmdb_director_cache:
        return _tmdb_director_cache[tmdb_id]
    headers, base_params = tmdb_auth(api_key)
    data = tmdb_get(
        client, f"https://api.themoviedb.org/3/movie/{tmdb_id}/credits", dict(base_params), headers)
    directors = [c.get("name", "") for c in (data or {}).get("crew", [])
                 if c.get("job") == "Director"]
    if data is not None:  # don't memoize transient failures
        _tmdb_director_cache[tmdb_id] = directors
    return directors


def tmdb_search(client: curl_requests.Session, query: str, headers: dict[str, str],
                base_params: dict[str, str], year: int | None = None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        **base_params, "query": query, "include_adult": "false", "language": "en-US",
    }
    if year:
        params["primary_release_year"] = year
    data = tmdb_get(
        client, "https://api.themoviedb.org/3/search/movie", params, headers)
    return data.get("results", []) if data else []


def _match_fields(cand: dict[str, Any]) -> dict[str, Any]:
    return {
        "tmdb_id": cand.get("id"),
        "tmdb_type": "movie",
        "title": cand.get("title"),
        "original_title": cand.get("original_title"),
        "original_language": cand.get("original_language"),
    }


def resolve_tmdb_multipass(
    client: curl_requests.Session, title: str, year: int | None, directors: list[str],
    api_key: str, slug: str | None = None,
) -> dict[str, Any] | None:
    """Multi-pass resolution: exact year -> near year -> title-only fuzzy ->
    slug fallback (handles international/non-Latin titles). Returns the
    matched TMDB fields (`tmdb_id`, `title`, `original_title`, ...) or None."""
    headers, base_params = tmdb_auth(api_key)
    clean_q = clean_title_str(title) or title.strip()
    if not clean_q:
        return None

    def best_candidate(results: list[dict[str, Any]]) -> dict[str, Any] | None:
        candidates: list[tuple[float, dict[str, Any]]] = []
        for cand in results[:10]:
            cand_title = cand.get("title") or cand.get("original_title") or ""
            if not cand_title:
                continue
            cand_year = parse_year(str(cand.get("release_date") or "")[:4])
            ratio = difflib.SequenceMatcher(
                None, clean_q.lower(), clean_title_str(cand_title).lower()).ratio()
            if ratio < 0.88:
                continue
            if year and cand_year and abs(cand_year - year) > 2:
                continue
            candidates.append((ratio, cand))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        if directors and len(candidates) > 1:
            for _, cand in candidates:
                for cand_director in fetch_tmdb_directors(client, cand["id"], api_key):
                    for target in directors:
                        if difflib.SequenceMatcher(
                            None, target.lower(), cand_director.lower()
                        ).ratio() > 0.8:
                            return cand
        return candidates[0][1]

    search_years: list[int] = []
    if year is not None:
        search_years.append(year)
        for offset in (-1, 1):
            candidate_year = year + offset
            if candidate_year not in search_years:
                search_years.append(candidate_year)

    for search_year in search_years:
        match = best_candidate(tmdb_search(
            client, clean_q, headers, base_params, search_year))
        if match:
            return _match_fields(match)

    match = best_candidate(tmdb_search(client, clean_q, headers, base_params))
    if match:
        return _match_fields(match)

    if slug:
        cleaned_slug = slug.strip('/').split('/')[-1]
        cleaned_slug = re.sub(r'-(?:18|19|20)\d{2}$', '', cleaned_slug)
        slug_query = cleaned_slug.replace('-', ' ').strip()
        if slug_query and slug_query.lower() != clean_q.lower():
            slug_results = (
                tmdb_search(client, slug_query, headers, base_params, year)
                or tmdb_search(client, slug_query, headers, base_params)
            )
            match = best_candidate(slug_results)
            if match:
                return _match_fields(match)

    return None


# ---------------------------------------------------------
# Networking (curl_cffi, sync - caller must run in a worker thread)
# ---------------------------------------------------------
def cache_dir() -> Path:
    """`/config/cache_letterboxd` in production; honors CONFIG_DIR overrides
    (tests/dev already point CONFIG_DIR at a throwaway tmp directory)."""
    path = get_settings().config_dir / "cache_letterboxd"
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_session() -> curl_requests.Session:
    session = curl_requests.Session(
        impersonate=IMPERSONATE_PROFILE, timeout=15.0)
    session.headers.update(BROWSER_HEADERS)
    return session


def polite_delay(is_deep_mode: bool = False, progress_callback: ProgressCallback = None) -> None:
    """Jittered human-like pacing, with occasional micro/macro "distraction" breaks."""
    base_min, base_max = (3.0, 6.5) if is_deep_mode else (2.5, 5.0)
    time.sleep(random.uniform(base_min, base_max))

    chance = random.random()
    if chance < 0.015:
        report_progress(progress_callback, "stealth_break", None, None,
                        "Macro-break: pausing 20-45s to avoid bot detection")
        time.sleep(random.uniform(20.0, 45.0))
    elif chance < 0.095:
        report_progress(progress_callback, "stealth_break", None, None,
                        "Micro-break: pausing 6-12s to avoid bot detection")
        time.sleep(random.uniform(6.0, 12.0))


def fetch_html(client: curl_requests.Session, url: str, no_cache: bool = False,
               max_retries: int = 4, is_deep_mode: bool = False,
               progress_callback: ProgressCallback = None) -> str:
    cache_path = cache_dir() / \
        f"{hashlib.md5(url.encode('utf-8')).hexdigest()}.html"

    if not no_cache and cache_path.exists() and time.time() - cache_path.stat().st_mtime < CACHE_TTL_SECONDS:
        html_content = cache_path.read_text(encoding="utf-8")
        # never trust a poisoned (challenge-page) cache entry
        if "Letterboxd" in html_content and not any(
            sig in html_content for sig in CF_SIGNATURES
        ):
            return html_content

    polite_delay(is_deep_mode, progress_callback)

    for attempt in range(max_retries):
        try:
            response = client.get(url)

            if response.status_code == 403 or any(sig in response.text for sig in CF_SIGNATURES):
                raise CloudflareBlock(f"Cloudflare challenge active on: {url}")

            if response.status_code in (429, 503):
                base_wait = int(response.headers.get(
                    "Retry-After", 2 ** (attempt + 2)))
                wait_time = min(base_wait + random.uniform(1.0, 5.0), 60.0)
                report_progress(progress_callback, "rate_limited", None, None,
                                f"Rate limited ({response.status_code}); backing off {wait_time:.0f}s")
                time.sleep(wait_time)
                continue

            response.raise_for_status()
            if not no_cache:
                cache_path.write_text(response.text, encoding="utf-8")
            return response.text

        except curl_requests.exceptions.HTTPError:
            raise
        except curl_requests.exceptions.RequestException:
            if attempt == max_retries - 1:
                raise
            time.sleep((2 ** attempt) + random.uniform(0.5, 2.0))

    raise CloudflareBlock(
        f"Exhausted {max_retries} attempts (persistently rate limited) on: {url}")


def is_not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    return response is not None and getattr(response, "status_code", None) == 404


def dump_debug_html(html: str, page_num: int) -> None:
    """Keeps the raw page of an unexpectedly empty result for selector debugging."""
    (cache_dir() /
     f"debug_html_dump_page_{page_num}.html").write_text(html, encoding="utf-8")


# ---------------------------------------------------------
# Checkpointing (idempotent, keyed by slug)
# ---------------------------------------------------------
def checkpoint_dir() -> Path:
    path = get_settings().config_dir / "checkpoints_letterboxd"
    path.mkdir(parents=True, exist_ok=True)
    return path


class CheckpointManager:
    def __init__(self, target_url: str, mode: str = ""):
        directory = checkpoint_dir()
        safe_name = hashlib.md5(f"{mode}|{target_url}".encode()).hexdigest()
        self.filepath = directory / f"checkpoint_{safe_name}.json"
        self.current_page: int = 1
        self.resumed = False
        self.items: list[dict[str, Any]] = []
        self._index: dict[str, int] = {}
        self.load()

    def _key(self, item: dict[str, Any]) -> str:
        slug = item.get("slug")
        if not slug:
            return f"__anon_{len(self._index)}"
        # a diary legitimately holds the same film on multiple dates
        watched = item.get("watched_at")
        return f"{slug}@{watched}" if watched else str(slug)

    def load(self) -> None:
        if not self.filepath.exists():
            return
        if time.time() - self.filepath.stat().st_mtime > CHECKPOINT_TTL_SECONDS:
            self.clear()
            return
        try:
            data = json.loads(self.filepath.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        self.current_page = int(data.get("current_page", 1))
        self.extend(data.get("items", []))
        self.resumed = bool(self.items) or self.current_page > 1

    def has(self, item: dict[str, Any]) -> bool:
        return self._key(item) in self._index

    def upsert(self, item: dict[str, Any]) -> None:
        """Insert, or replace in place when this key was already captured."""
        key = self._key(item)
        position = self._index.get(key)
        if position is None:
            self._index[key] = len(self.items)
            self.items.append(item)
        else:
            self.items[position] = item

    def extend(self, items: Iterable[dict[str, Any]]) -> None:
        for item in items:
            self.upsert(item)

    def save(self, current_page: int) -> None:
        self.current_page = current_page
        tmp_path = self.filepath.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps({"current_page": current_page, "items": self.items},
                       indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp_path, self.filepath)

    def clear(self) -> None:
        """Only called once a scrape has run to completion."""
        self.filepath.unlink(missing_ok=True)


# ---------------------------------------------------------
# Deep metadata (individual film page)
# ---------------------------------------------------------
def _imdb_id_from(text: str | None) -> str | None:
    match = re.search(r'imdb\.com/title/(tt\d+)', text or "", re.IGNORECASE)
    return match.group(1) if match else None


def extract_deep_metadata(
    client: curl_requests.Session, slug: str, no_cache: bool, api_key: str | None = None,
    progress_callback: ProgressCallback = None,
) -> dict[str, Any]:
    """Fetches the film's own page for TMDB/IMDb ids and the Letterboxd rating."""
    meta: dict[str, Any] = {"tmdb_id": None,
                            "tmdb_type": None, "imdb_id": None}
    try:
        html = fetch_html(client, f"{BASE_URL}/film/{slug}/", no_cache=no_cache,
                          is_deep_mode=True, progress_callback=progress_callback)
        soup = BeautifulSoup(html, "html.parser")

        body = soup.find("body")
        body_tag = body if isinstance(body, Tag) else None
        body_tmdb = parse_id(attr_str(body_tag, "data-tmdb-id"))
        if body_tmdb:
            meta["tmdb_id"] = body_tmdb
            meta["tmdb_type"] = attr_str(body_tag, "data-tmdb-type") or "movie"

        if not meta["tmdb_id"]:
            tmdb_link = soup.select_one(
                'a[data-track-action="TMDb"], a[href*="themoviedb.org/"]')
            match = re.search(r'themoviedb\.org/(movie|tv)/(\d+)',
                              attr_str(tmdb_link, "href") or "")
            if match:
                meta["tmdb_type"] = match.group(1)
                meta["tmdb_id"] = int(match.group(2))

        ld: dict[str, Any] | None = None
        script = soup.find("script", type="application/ld+json")
        if isinstance(script, Tag) and script.string:
            try:
                raw_ld = json.loads(
                    re.sub(r'/\*.*?\*/', '', script.string, flags=re.DOTALL))
                ld = raw_ld[0] if isinstance(raw_ld, list) else raw_ld
                rating = ld.get("aggregateRating", {}).get("ratingValue")
                if rating is not None:
                    meta["letterboxd_rating"] = float(rating)
            except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
                ld = None

        link = soup.select_one(
            'a[data-track-action="IMDb"], a[href*="imdb.com/title/"]')
        meta["imdb_id"] = _imdb_id_from(attr_str(link, "href"))
        if meta["imdb_id"] is None and ld is not None:
            for value in ld.get("sameAs", []):
                meta["imdb_id"] = _imdb_id_from(str(value))
                if meta["imdb_id"]:
                    break
        if meta["imdb_id"] is None:
            meta["imdb_id"] = _imdb_id_from(html)

        if meta["imdb_id"] is None and meta["tmdb_id"] is not None and api_key:
            headers, params = tmdb_auth(api_key)
            external = tmdb_get(
                client, f"https://api.themoviedb.org/3/movie/{meta['tmdb_id']}/external_ids",
                params, headers)
            if external and external.get("imdb_id"):
                meta["imdb_id"] = external["imdb_id"]
    except CloudflareBlock:
        raise
    except Exception:
        logger.warning("Deep metadata fetch failed for %s",
                       slug, exc_info=True)
    return meta


def enrich_entry(
    client: curl_requests.Session, entry: dict[str, Any], is_deep: bool,
    tmdb_api_key: str | None, no_cache: bool, progress_callback: ProgressCallback = None,
) -> None:
    """Resolves the TMDB id (inline attribute, deep page, or multi-pass search) in place."""
    if is_deep:
        meta = extract_deep_metadata(
            client, entry["slug"], no_cache=no_cache, api_key=tmdb_api_key,
            progress_callback=progress_callback)
        entry.update({k: v for k, v in meta.items() if v is not None})
        return

    if entry.get("tmdb_id"):
        entry.setdefault("tmdb_type", "movie")
        return

    if tmdb_api_key:
        match = resolve_tmdb_multipass(
            client, str(entry.get("title")
                        or entry["slug"]), entry.get("year"),
            entry.get("directors", []), tmdb_api_key, entry.get("slug"))
        if match:
            entry.update({k: v for k, v in match.items() if v is not None})
        else:
            entry["tmdb_id"], entry["tmdb_type"] = None, None


def _detect_total_films(soup: BeautifulSoup) -> int | None:
    candidates = [
        *soup.select('span.footnote, span.value, small.value'),
        *soup.select('meta[property="og:description"]'),
    ]
    for candidate in candidates:
        text = candidate.get(
            'content') if candidate.name == 'meta' else candidate.get_text(' ', strip=True)
        match = re.search(r'([\d,]+)\s*(?:films?|movies?)',
                          str(text), re.IGNORECASE)
        if match:
            return int(match.group(1).replace(',', ''))
    return None


def _scrape_paginated(
    base_url: str, *, mode: str, detail_mode: bool, parse_page: Callable[[BeautifulSoup], list[dict[str, Any]]],
    is_deep: bool, tmdb_api_key: str | None, max_pages: int | None, no_cache: bool,
    progress_callback: ProgressCallback, ranked_output: bool,
) -> dict[str, Any]:
    """Shared fetch -> parse -> enrich -> checkpoint loop (lists, watchlists, diary).

    Progress is checkpointed after every page; on any failure (Cloudflare block,
    network error) the checkpoint is kept and the error re-raised, so the next
    call resumes at the failed page instead of persisting a partial result."""
    checkpoint = CheckpointManager(base_url, mode=f"{mode}|deep={is_deep}")
    page_num = checkpoint.current_page
    is_ranked = False
    total_films: int | None = None
    total_pages: int | None = None

    if checkpoint.resumed:
        report_progress(progress_callback, "resume", len(checkpoint.items), None,
                        f"Resuming from page {page_num} ({len(checkpoint.items)} films already captured)")

    with new_session() as client:
        try:
            while True:
                if max_pages and page_num > max_pages:
                    break

                page_url = build_paginated_url(
                    base_url, page_num, detail_mode=detail_mode)
                report_progress(progress_callback, "fetch_page", page_num, total_pages,
                                f"Fetching page {page_num}: {page_url}")

                try:
                    html = fetch_html(client, page_url, no_cache=no_cache,
                                      progress_callback=progress_callback)
                except curl_requests.exceptions.HTTPError as exc:
                    if is_not_found(exc) and (page_num > 1 or checkpoint.items):
                        break  # end of pagination
                    raise

                soup = BeautifulSoup(html, "html.parser")
                if page_num == 1 or (checkpoint.resumed and total_films is None):
                    total_films = _detect_total_films(soup)
                    total_pages = math.ceil(
                        total_films / 100) if total_films else None
                if page_num == 1:
                    is_ranked = bool(soup.select_one(
                        ".list-number, span[class*=list-number], .list-numbering, "
                        ".listitem .list-number"))

                page_entries = parse_page(soup)
                if not page_entries:
                    dump_debug_html(html, page_num)
                    break

                for entry in page_entries:
                    if checkpoint.has(entry):
                        continue
                    entry.setdefault("directors", [])
                    enrich_entry(client, entry, is_deep, tmdb_api_key, no_cache,
                                 progress_callback=progress_callback)
                    checkpoint.upsert(entry)

                checkpoint.save(page_num)
                report_progress(progress_callback, "page_done", len(checkpoint.items), total_films,
                                f"Processed page {page_num} ({len(checkpoint.items)} films so far)")

                if not has_next_page(soup):
                    break
                page_num += 1
                checkpoint.save(page_num)
        except Exception:
            checkpoint.save(page_num)
            report_progress(progress_callback, "aborted", len(checkpoint.items), total_films,
                            "Scrape aborted; progress checkpointed and will resume on the next sync")
            raise

    films = checkpoint.items
    checkpoint.clear()
    if ranked_output:
        for idx, item in enumerate(films, start=1):
            item["rank"] = idx

    return {
        "scraped_at": datetime.now(UTC).isoformat(),
        "list_url": base_url,
        "is_ranked": is_ranked,
        "total_films": len(films),
        "total_pages": total_pages,
        "films": films,
    }


def scrape_letterboxd_list(
    url: str, tmdb_api_key: str | None = None, max_pages: int | None = None,
    no_cache: bool = False, progress_callback: ProgressCallback = None, deep: bool = False,
) -> dict[str, Any]:
    """Scrape a Letterboxd list/watchlist. Uses the `/detail/` view (inline
    title/year/director, far fewer requests) unless `deep` is set, which instead
    visits every film page for TMDB/IMDb ids and the Letterboxd rating."""
    path = urlparse(url).path
    is_list = '/list/' in path or '/watchlist' in path
    detail_mode = is_list and not deep

    def parse_page(soup: BeautifulSoup) -> list[dict[str, Any]]:
        entries = parse_detail_entries(soup) if detail_mode else []
        return entries or parse_grid_entries(soup)

    return _scrape_paginated(
        url, mode="list", detail_mode=detail_mode, parse_page=parse_page, is_deep=deep,
        tmdb_api_key=tmdb_api_key, max_pages=max_pages, no_cache=no_cache,
        progress_callback=progress_callback, ranked_output=True)


def clean_username(username: str) -> str:
    """Letterboxd usernames are interpolated into URL paths, so reject anything else."""
    cleaned = username.strip().strip('/')
    if not USERNAME_RE.fullmatch(cleaned):
        raise ValueError(f"Invalid Letterboxd username: {username!r}")
    return cleaned


def scrape_letterboxd_watchlist(
    username: str, tmdb_api_key: str | None = None, max_pages: int | None = None,
    no_cache: bool = False, progress_callback: ProgressCallback = None, deep: bool = False,
) -> dict[str, Any]:
    username = clean_username(username)
    url = f"{BASE_URL}/{username}/watchlist/"
    try:
        return scrape_letterboxd_list(url, tmdb_api_key=tmdb_api_key, max_pages=max_pages,
                                      no_cache=no_cache, progress_callback=progress_callback,
                                      deep=deep)
    except curl_requests.exceptions.HTTPError as exc:
        if is_not_found(exc):
            raise WatchlistNotFound(username) from exc
        raise


# ---------------------------------------------------------
# Diary / watch history
# ---------------------------------------------------------
def parse_diary_header(node: Tag) -> tuple[int | None, int | None]:
    """Pull `2024` / `May` out of a date header row or calendar cell."""
    text = node.get_text(" ", strip=True)
    year = parse_year(text)

    month = None
    month_match = re.search(
        r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b',
        text, re.IGNORECASE)
    if month_match:
        month = MONTHS.get(month_match.group(1).lower())

    # `/for/YYYY/MM/` links are authoritative when present
    for candidate in node.select("a[href*='/for/']"):
        match = re.search(r'/for/(\d{4})(?:/(\d{1,2}))?',
                          attr_str(candidate, "href") or "")
        if match:
            year = int(match.group(1))
            if match.group(2):
                month = int(match.group(2))

    return year, month


def diary_date_for_row(
    row: Tag, current_year: int | None, current_month: int | None,
) -> tuple[str | None, int | None, int | None]:
    """ISO `YYYY-MM-DD` for a diary row, preferring its own `/for/Y/M/D/` link
    and falling back to the tracked month/year header state."""
    # the month/year cell is only populated on the first row of each month
    calendar = row.select_one(
        "td.col-monthdate, div.monthdate, td.td-calendar, div.date")
    if isinstance(calendar, Tag):
        head_year, head_month = parse_diary_header(calendar)
        current_year = head_year or current_year
        current_month = head_month or current_month

    day_cell = row.select_one("td.col-daydate, td.td-day")
    link = day_cell.select_one("a") if isinstance(day_cell, Tag) else None
    full = re.search(r'/for/(\d{4})/(\d{1,2})/(\d{1,2})',
                     attr_str(link, "href") or "")
    if full:
        year, month, day = int(full.group(1)), int(
            full.group(2)), int(full.group(3))
        return f"{year:04d}-{month:02d}-{day:02d}", year, month

    day_text = day_cell.get_text(
        strip=True) if isinstance(day_cell, Tag) else ""
    day_match = re.search(r'(\d{1,2})', day_text)
    if day_match and current_year and current_month:
        return (f"{current_year:04d}-{current_month:02d}-{int(day_match.group(1)):02d}",
                current_year, current_month)

    return None, current_year, current_month


def parse_diary_entries(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Diary table: the month/year is rendered only on each month's first row (or
    a header row), so it is carried forward as state; the day is on every row."""
    entries: list[dict[str, Any]] = []
    current_year: int | None = None
    current_month: int | None = None

    for row in soup.select("tr.date-row, tr.headline-row, h2.diary-date, tr.diary-entry-row"):
        if not isinstance(row, Tag):
            continue

        if "diary-entry-row" not in node_classes(row):
            year, month = parse_diary_header(row)
            current_year = year or current_year
            current_month = month or current_month
            continue

        poster = extract_poster(row)
        slug = slug_from_poster(poster)
        if not slug:
            continue

        headline = row.select_one(
            "h3.headline-3 a, .headline-3 a, h2.primaryname a, "
            "td.col-production a[href^='/film/'], td.td-film-details a")
        alt_title, alt_year = title_year_from_img(poster, slug)
        title = (headline.get_text(strip=True)
                 if headline else None) or alt_title

        released = row.select_one("td.col-released, td.td-released")
        year = (parse_year(released.get_text(strip=True)) if released else None) \
            or alt_year or year_from_slug(slug)

        watched_at, current_year, current_month = diary_date_for_row(
            row, current_year, current_month)

        # the rewatch cell is always present; `icon-status-off` means first watch
        rewatch_td = row.select_one("td.col-rewatch, td.td-rewatch")
        is_rewatch = rewatch_td is not None and "icon-status-off" not in node_classes(
            rewatch_td)

        rating_node = row.select_one(
            "td.col-rating span.rating[class*='rated-'], "
            "td.td-rating span.rating[class*='rated-'], "
            "span.rating[class*='rated-']")
        user_rating = rating_from_classes(
            rating_node) if isinstance(rating_node, Tag) else None

        entries.append({
            "title": title,
            "year": year,
            "slug": slug,
            "watched_at": watched_at,
            "user_rating": user_rating,
            "is_rewatch": is_rewatch,
            "tmdb_id": parse_id(attr_str(poster, "data-tmdb-id")),
            "tmdb_type": None,
        })

    return entries


def scrape_user_diary_history(
    username: str, mode: str = "diary", tmdb_api_key: str | None = None,
    max_pages: int | None = None, no_cache: bool = False,
    progress_callback: ProgressCallback = None,
) -> dict[str, Any]:
    """Full paginated history: `mode="diary"` (dated entries, ratings, rewatches)
    or `mode="films"` (every film the user has logged, undated)."""
    if mode not in ("diary", "films"):
        raise ValueError("mode must be 'diary' or 'films'")
    user = clean_username(username)
    base_url = f"{BASE_URL}/{user}/films/diary/" if mode == "diary" else f"{BASE_URL}/{user}/films/"
    result = _scrape_paginated(
        base_url, mode=mode, detail_mode=False,
        parse_page=parse_diary_entries if mode == "diary" else parse_grid_entries,
        is_deep=False, tmdb_api_key=tmdb_api_key, max_pages=max_pages, no_cache=no_cache,
        progress_callback=progress_callback, ranked_output=False)
    return {**result, "list_type": mode, "username": user}


def clean_title_and_year(raw_title: str, slug: str) -> tuple[str, int | None]:
    match = re.search(r'^(.*)\s+\(((?:18|19|20)\d{2})\)$', raw_title.strip())
    if match:
        return match.group(1).strip(), int(match.group(2))
    return raw_title.strip(), year_from_slug(slug)


def ingest_rss_diary(username: str) -> dict[str, Any]:
    """Recent diary activity from the user's public RSS feed (one request, ~50 items)."""
    user = clean_username(username)
    url = f"{BASE_URL}/{user}/rss/"

    with new_session() as client:
        resp = client.get(url)
        resp.raise_for_status()
        raw_feed = resp.text

    root = SafeET.fromstring(raw_feed)
    ns = {'letterboxd': 'https://letterboxd.com'}
    entries: list[dict[str, Any]] = []

    for item in root.findall('./channel/item'):
        link_node = item.find('link')
        lb_url = link_node.text if link_node is not None else ""
        # RSS links are user-scoped (`/alice/film/<slug>/`), which normalize_slug can't unpick
        film_match = re.search(r'/film/([^/]+)', urlparse(lb_url or "").path)
        slug = film_match.group(1) if film_match else normalize_slug(
            urlparse(lb_url or "").path) or ""

        title_node = item.find('letterboxd:filmTitle', ns)
        year_node = item.find('letterboxd:filmYear', ns)

        if title_node is None or title_node.text is None:
            raw_title_node = item.find('title')
            raw_title = raw_title_node.text if raw_title_node is not None else "Unknown"
            title, year = clean_title_and_year(str(raw_title), slug)
        else:
            title = title_node.text
            year = parse_year(
                year_node.text) if year_node is not None else year_from_slug(slug)

        date_node = item.find('letterboxd:watchedDate', ns)
        rating_node = item.find('letterboxd:memberRating', ns)
        rewatch_node = item.find('letterboxd:rewatch', ns)
        tmdb_node = item.find('letterboxd:movieId', ns)

        desc_node = item.find('description')
        desc = ""
        if desc_node is not None and desc_node.text:
            desc = BeautifulSoup(desc_node.text, "html.parser").get_text(
                separator="\n", strip=True)

        entries.append({
            "title": title,
            "year": year,
            "slug": slug,
            "letterboxd_url": lb_url,
            "watched_at": date_node.text if date_node is not None else None,
            "user_rating": float(rating_node.text) if rating_node is not None and rating_node.text else None,
            "is_rewatch": rewatch_node is not None and rewatch_node.text == "Yes",
            "review_snippet": desc,
            "tmdb_id": parse_id(tmdb_node.text) if tmdb_node is not None else None,
        })

    return {
        "scraped_at": datetime.now(UTC).isoformat(),
        "list_type": "diary",
        "username": user,
        "list_title": f"{user}'s RSS Diary",
        "list_url": url,
        "total_films": len(entries),
        "films": entries,
    }


# ---------------------------------------------------------
# Tier 0/1 discovery: curator accounts and their published lists
# ---------------------------------------------------------
def username_from_profile_link(link: Tag | None) -> str | None:
    href = attr_str(link, "href")
    if not href:
        return None
    path_parts = [part for part in urlparse(href).path.split('/') if part]
    return path_parts[0] if len(path_parts) == 1 else None


def account_has_badge(container: Tag, tier: str) -> bool:
    tier = tier.lower()
    selectors = {
        "hq": "span.badge.-hq, span.badge-hq, .badge[href*='hq']",
        "patron": "span.badge.-patron, span.badge-patron",
        "pro": "span.badge.-pro, span.badge-pro",
    }
    if container.select_one(selectors[tier]):
        return True
    return container.find(string=re.compile(
        rf'^\s*{re.escape(tier)}\s*$', re.IGNORECASE)) is not None


def extract_account_summary(container: Tag) -> dict[str, Any] | None:
    profile_link = container.select_one("a.avatar[href], a.name[href]")
    username = username_from_profile_link(profile_link)
    if not username or not USERNAME_RE.fullmatch(username):
        return None

    name_node = container.select_one("h1, h2, h3, .name")
    avatar_node = container.select_one("img.avatar, a.avatar img")
    avatar_src = attr_str(avatar_node, "src") or attr_str(
        avatar_node, "data-src")
    bio_node = container.select_one(".bio, .person-summary p")
    return {
        "username": username,
        "display_name": name_node.get_text(" ", strip=True) if name_node else username,
        "avatar_url": urljoin(BASE_URL, avatar_src) if avatar_src else None,
        "bio": bio_node.get_text(" ", strip=True) if bio_node else None,
        "is_hq": account_has_badge(container, "hq"),
    }


def discover_hq_accounts(
    target: str | None = None, max_pages: int | None = None, no_cache: bool = False,
    include_all: bool = False, progress_callback: ProgressCallback = None,
) -> dict[str, Any]:
    """Walks the HQ member directory (or, with `target`, that member's following
    list filtered to HQ accounts unless `include_all`). A Cloudflare block
    returns what was collected so far with `partial=True`."""
    target = clean_username(target) if target else None
    is_hq_directory = target is None
    base_url = (f"{BASE_URL}/members/hq/" if is_hq_directory
                else f"{BASE_URL}/{target}/following/")
    accounts: list[dict[str, Any]] = []
    seen_usernames: set[str] = set()
    page_num = 1
    error: str | None = None

    with new_session() as client:
        try:
            while not max_pages or page_num <= max_pages:
                page_url = build_paginated_url(base_url, page_num)
                report_progress(progress_callback, "fetch_page", page_num, None,
                                f"Fetching accounts page {page_num}: {page_url}")
                try:
                    html = fetch_html(client, page_url, no_cache=no_cache,
                                      progress_callback=progress_callback)
                except curl_requests.exceptions.HTTPError as exc:
                    if is_not_found(exc) and page_num > 1:
                        break
                    raise

                soup = BeautifulSoup(html, "html.parser")
                cards = [node for node in soup.select(
                    ".person-summary, tr.person-summary, ul.person-list li, "
                    "ul.member-directory > li") if isinstance(node, Tag)]
                if not cards:
                    dump_debug_html(html, page_num)
                    break

                for card in cards:
                    account = extract_account_summary(card)
                    if not account or account["username"] in seen_usernames:
                        continue
                    seen_usernames.add(account["username"])
                    if is_hq_directory:
                        account["is_hq"] = True
                    if include_all or account["is_hq"]:
                        accounts.append(account)

                report_progress(progress_callback, "page_done", len(accounts), None,
                                f"Found {len(accounts)} accounts so far")
                if not has_next_page(soup):
                    break
                page_num += 1
        except CloudflareBlock as exc:
            error = str(exc)
            if not accounts:
                raise

    return {
        "source": "letterboxd_hq_directory" if is_hq_directory else "letterboxd_following",
        "total_hqs": sum(1 for account in accounts if account["is_hq"]),
        "accounts": accounts,
        "partial": error is not None,
        "error": error,
    }


def inspect_account(username: str, no_cache: bool = False,
                    progress_callback: ProgressCallback = None) -> dict[str, Any]:
    """Reads one profile page: display name, avatar, bio, tier (HQ/Patron/Pro), list count."""
    username = clean_username(username)
    profile_url = f"{BASE_URL}/{username}/"

    with new_session() as client:
        html = fetch_html(client, profile_url, no_cache=no_cache,
                          progress_callback=progress_callback)

    soup = BeautifulSoup(html, "html.parser")
    profile = soup.select_one(
        ".profile-header, .profile-header-wrapper, header.profile-header")
    container = profile if isinstance(profile, Tag) else soup
    name_node = container.select_one(".displayname .label, h1 .label") \
        or container.select_one(".profile-name, h1, .title-1, .name")
    avatar_node = container.select_one(
        "img.avatar, .profile-avatar img, a.avatar img")
    bio_node = container.select_one(".bio, .profile-bio")

    account_tier = None
    for tier in ("hq", "patron", "pro"):
        if account_has_badge(container, tier):
            account_tier = tier.upper() if tier == "hq" else tier.title()
            break

    lists_count = 0
    lists_link = soup.select_one(
        f"a[href='/{username}/lists/'], a[href$='/{username}/lists/']")
    if lists_link:
        count_value = attr_str(
            lists_link, "data-count") or lists_link.get_text(" ", strip=True)
        count_match = re.search(r'([\d,]+)', count_value)
        if count_match:
            lists_count = int(count_match.group(1).replace(',', ''))

    avatar_src = attr_str(avatar_node, "src") or attr_str(
        avatar_node, "data-src")
    return {
        "source": "letterboxd_profile",
        "username": username,
        "display_name": name_node.get_text(" ", strip=True) if name_node else username,
        "avatar_url": urljoin(BASE_URL, avatar_src) if avatar_src else None,
        "bio": bio_node.get_text(" ", strip=True) if bio_node else None,
        "account_tier": account_tier,
        "total_public_lists": lists_count,
    }


def discover_user_lists(
    username: str, max_pages: int | None = None, no_cache: bool = False,
    progress_callback: ProgressCallback = None,
) -> dict[str, Any]:
    """Inventories an account's public lists (title, URL, film count, description,
    preview posters). A Cloudflare block returns what was collected with `partial=True`."""
    username = clean_username(username)
    base_url = f"{BASE_URL}/{username}/lists/"
    discovered: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    page_num = 1
    pages_processed = 0
    error: str | None = None

    with new_session() as client:
        try:
            while not max_pages or page_num <= max_pages:
                page_url = build_paginated_url(base_url, page_num)
                report_progress(progress_callback, "fetch_page", page_num, None,
                                f"Fetching lists page {page_num}: {page_url}")
                try:
                    html = fetch_html(client, page_url, no_cache=no_cache,
                                      progress_callback=progress_callback)
                except curl_requests.exceptions.HTTPError as exc:
                    if is_not_found(exc) and page_num > 1:
                        break
                    raise

                soup = BeautifulSoup(html, "html.parser")
                cards: list[Tag] = [c for c in soup.select(
                    "section.list, section.film-list-summary, div.film-list-summary, "
                    ".list-summary, .list-set > section") if isinstance(c, Tag)]
                if not cards:
                    for link in soup.select("h2 a[href*='/list/'], h3 a[href*='/list/']"):
                        parent = link.find_parent(['section', 'div', 'li'])
                        if isinstance(parent, Tag) and parent not in cards:
                            cards.append(parent)
                if not cards:
                    dump_debug_html(html, page_num)
                    break

                pages_processed += 1
                for card in cards:
                    entry = _parse_list_card(card)
                    if entry is None or entry["url"] in seen_urls:
                        continue
                    seen_urls.add(entry["url"])
                    discovered.append(entry)

                report_progress(progress_callback, "page_done", len(discovered), None,
                                f"Processed {len(discovered)} list cards so far")
                if not has_next_page(soup):
                    break
                page_num += 1
        except CloudflareBlock as exc:
            error = str(exc)
            if not discovered:
                raise

    return {"username": username, "pages": pages_processed, "lists": discovered,
            "partial": error is not None, "error": error}


def _parse_list_card(card: Tag) -> dict[str, Any] | None:
    link_el = card.select_one("h2 a, h3 a, a[href*='/list/']")
    href = attr_str(link_el, "href")
    if not href or '/list/' not in href:
        return None

    list_url = urljoin(BASE_URL, href)
    if not list_url.endswith('/'):
        list_url += '/'

    val_tag = card.select_one("small.value, small, span.value")
    digits = re.sub(r'\D', '', val_tag.get_text()) if val_tag else ""
    desc_el = card.select_one(
        ".list-description, .body-text, .description, .notes")

    preview_posters: list[str] = []
    preview_slugs: list[str] = []
    for preview in card.select(
            "ul.poster-list li, li.poster-container, li.griditem, .film-list-summary")[:5]:
        poster_node = preview.select_one("img")
        if poster_node:
            poster_src = attr_str(poster_node, "src") or attr_str(
                poster_node, "data-src")
            if poster_src and poster_src not in preview_posters:
                preview_posters.append(urljoin(BASE_URL, poster_src))
        for attr in ("data-film-slug", "data-item-slug"):
            slug = normalize_slug(attr_str(preview, attr))
            if slug and slug not in preview_slugs:
                preview_slugs.append(slug)

    return {
        "title": link_el.get_text(strip=True) if link_el else list_url,
        "slug": list_url.strip('/').split('/')[-1],
        "url": list_url,
        "total_films": int(digits) if digits else 0,
        "description": desc_el.get_text(separator="\n", strip=True) if desc_el else None,
        "preview_posters": preview_posters,
        "preview_slugs": preview_slugs,
    }


def derive_badge_prefix(url: str, badge_prefix: str | None = None) -> str:
    """Short uppercase tag, e.g. `SS22`, `LB250` - falls back to deriving one
    from the list's own URL slug for custom imports with no explicit prefix."""
    if badge_prefix:
        return badge_prefix.strip().upper()
    slug = urlparse(url).path.strip('/').split('/')[-1] or "list"
    tokens = [t for t in re.split(r'[^A-Za-z0-9]+', slug) if t]
    if not tokens:
        return "LB"
    digits = "".join(t for t in tokens if t.isdigit())
    initials = "".join(t[0] for t in tokens if not t.isdigit())[:4].upper()
    return (f"{initials}{digits}" or "LB")[:12]
