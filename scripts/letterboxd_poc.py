#!/usr/bin/env python3
"""
CineChain Letterboxd Ingestion Toolkit (Production/Stealth POC)
Expert-grade scraper focusing on IP reputation, anti-bot mitigation, and high data integrity.
"""

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
import random
import unicodedata
import difflib
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse, urlunparse
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any, List, Iterable, Callable

try:
    from curl_cffi import requests as curl_requests
    from bs4 import BeautifulSoup
    from bs4.element import Tag
except ImportError:
    print("[Error] Missing dependencies. Please run:")
    print("pip install curl_cffi beautifulsoup4")
    sys.exit(1)


# Core Settings
CACHE_DIR = ".cache_letterboxd"
CHECKPOINT_DIR = ".checkpoints_letterboxd"
CACHE_TTL = 86400 * 7  # 7 days for cached HTML
BASE_URL = "https://letterboxd.com"
IMPERSONATE_PROFILE = "chrome124"

# Headers layered on top of the curl_cffi browser fingerprint.
BROWSER_HEADERS = {
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://letterboxd.com/",
}

PRESETS: Dict[str, Dict[str, str]] = {
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

STATS = {"hits": 0, "misses": 0, "api_calls": 0,
         "items": 0, "start_time": time.time()}

# Broadened Cloudflare detection signatures
CF_SIGNATURES = [
    'cf-turnstile',
    '<title>Just a moment...</title>',
    '<title>Verify you are human',
    'Attention Required!',
    'cloudflare-core',
    'cf-mitigated'
]

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Director credits memo, keyed by TMDB movie id, shared for the whole process.
tmdb_director_cache: Dict[int, List[str]] = {}


class CloudflareBlock(Exception):
    pass


# ---------------------------------------------------------
# Small DOM helpers (BeautifulSoup attrs may be str or list)
# ---------------------------------------------------------
def attr_str(node: Optional[Tag], name: str) -> Optional[str]:
    if node is None:
        return None
    value = node.get(name)
    if isinstance(value, list):
        value = value[0] if value else None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def node_classes(node: Optional[Tag]) -> List[str]:
    if node is None:
        return []
    classes = node.get("class") or []
    if isinstance(classes, str):
        return [classes]
    return [str(c) for c in classes]


def parse_year(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    match = re.search(r'\b((?:18|19|20)\d{2})\b', value)
    return int(match.group(1)) if match else None


def parse_id(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    return int(value) if str(value).isdigit() else None


def year_from_slug(slug: str) -> Optional[int]:
    """`dune-2021` -> 2021."""
    match = re.search(r'-((?:18|19|20)\d{2})$', slug.strip('/'))
    if not match:
        return None
    year = int(match.group(1))
    return year if 1870 <= year <= datetime.now().year + 5 else None


def normalize_slug(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    slug = str(raw).strip()
    slug = re.sub(r'^https?://[^/]+', '', slug)
    slug = slug.replace('/film/', '').strip('/')
    # Drop any trailing sub-page segment such as `/crew` or `/1/`.
    slug = slug.split('/')[0]
    return slug or None


def rating_from_classes(node: Optional[Tag]) -> Optional[float]:
    """Letterboxd encodes stars as `rated-N` where N is out of 10."""
    for cls in node_classes(node):
        match = re.match(r'^rated-(\d{1,2})$', str(cls))
        if match:
            return int(match.group(1)) / 2.0
    return None


# ---------------------------------------------------------
# Networking
# ---------------------------------------------------------
def new_session() -> curl_requests.Session:
    session = curl_requests.Session(
        impersonate=IMPERSONATE_PROFILE, timeout=15.0)
    session.headers.update(BROWSER_HEADERS)
    return session


def polite_delay(is_deep_mode: bool = False) -> None:
    base_min, base_max = (3.0, 6.5) if is_deep_mode else (2.5, 5.0)
    time.sleep(random.uniform(base_min, base_max))

    chance = random.random()
    if chance < 0.015:
        print(
            "    [Stealth] Macro-break triggered. Simulating tab switch/distraction (20-45s)...", flush=True)
        time.sleep(random.uniform(20.0, 45.0))
    elif chance < 0.095:
        print(
            "    [Stealth] Micro-break triggered. Simulating reading time (6-12s)...", flush=True)
        time.sleep(random.uniform(6.0, 12.0))


def fetch_html(client: curl_requests.Session, url: str, no_cache: bool = False, max_retries: int = 4) -> Tuple[str, bool]:
    os.makedirs(CACHE_DIR, exist_ok=True)
    url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()
    cache_path = os.path.join(CACHE_DIR, f"{url_hash}.html")

    if not no_cache and os.path.exists(cache_path):
        if time.time() - os.path.getmtime(cache_path) < CACHE_TTL:
            with open(cache_path, "r", encoding="utf-8") as f:
                html_content = f.read()
            # Strict check to ensure we don't load poisoned caches
            if 'Letterboxd' in html_content and not any(sig in html_content for sig in CF_SIGNATURES):
                STATS["hits"] += 1
                return html_content, True

    polite_delay()

    for attempt in range(max_retries):
        try:
            STATS["misses"] += 1
            response = client.get(url)

            if response.status_code == 403 or any(sig in response.text for sig in CF_SIGNATURES):
                raise CloudflareBlock(f"Cloudflare challenge active on: {url}")

            if response.status_code in (429, 503):
                base_wait = int(response.headers.get(
                    "Retry-After", 2 ** (attempt + 2)))
                wait_time = min(base_wait + random.uniform(1.0, 5.0), 60.0)
                print(
                    f"    [Rate Limited {response.status_code}] Backing off for {wait_time:.1f}s...",
                    file=sys.stderr, flush=True)
                time.sleep(wait_time)
                continue

            response.raise_for_status()

            if not no_cache:
                with open(cache_path, "w", encoding="utf-8") as f:
                    f.write(response.text)

            return response.text, False

        except curl_requests.exceptions.HTTPError:
            raise
        except curl_requests.exceptions.RequestException:
            if attempt == max_retries - 1:
                raise
            time.sleep((2 ** attempt) + random.uniform(0.5, 2.0))

    raise CloudflareBlock(
        f"Exhausted {max_retries} attempts (persistently rate limited) on: {url}")


def build_paginated_url(base_url: str, page_num: int, detail_mode: bool = False) -> str:
    """
    Canonical Letterboxd pagination (trailing slash always present so we never
    pay for a 301 redirect roundtrip).

      detail:    /{path}/detail/  ->  /{path}/page/2/detail/
      standard:  /{path}/         ->  /{path}/page/2/
    """
    parsed = urlparse(base_url)
    path = parsed.path.strip('/')

    # Normalize away any pagination/detail segments already present in the input.
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
# Checkpointing (idempotent, keyed by slug)
# ---------------------------------------------------------
class CheckpointManager:
    def __init__(self, target_url: str):
        os.makedirs(CHECKPOINT_DIR, exist_ok=True)
        safe_name = hashlib.md5(target_url.encode('utf-8')).hexdigest()
        self.filepath = os.path.join(
            CHECKPOINT_DIR, f"checkpoint_{safe_name}.json")
        self.current_page: int = 1
        self.items: List[Dict[str, Any]] = []
        self._index: Dict[str, int] = {}
        self.load()

    def _key(self, item: Dict[str, Any]) -> str:
        slug = item.get("slug")
        if not slug:
            return f"__anon_{len(self._index)}"
        # A diary legitimately holds the same film on multiple dates.
        watched = item.get("watched_at")
        return f"{slug}@{watched}" if watched else str(slug)

    def load(self) -> None:
        if not os.path.exists(self.filepath):
            return
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            print(
                "    [Checkpoint] Corrupt checkpoint ignored; starting fresh.", flush=True)
            return

        self.current_page = int(data.get("current_page", 1))
        self.extend(data.get("items", []))
        print(
            f"    [Checkpoint] Resuming from Page {self.current_page} ({len(self.items)} items cached).",
            flush=True)

    def upsert(self, item: Dict[str, Any]) -> None:
        """Insert, or replace in place when this slug was already captured."""
        key = self._key(item)
        position = self._index.get(key)
        if position is None:
            self._index[key] = len(self.items)
            self.items.append(item)
        else:
            self.items[position] = item

    def extend(self, items: Iterable[Dict[str, Any]]) -> None:
        for item in items:
            self.upsert(item)

    def save(self, current_page: int) -> None:
        self.current_page = current_page
        tmp_path = f"{self.filepath}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({"current_page": current_page,
                      "items": self.items}, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, self.filepath)

    def clear(self) -> None:
        """Only ever called once a scrape has run to completion."""
        if os.path.exists(self.filepath):
            os.remove(self.filepath)


# ---------------------------------------------------------
# TMDB Multi-Pass Resolution
# ---------------------------------------------------------
def clean_title_str(title: str) -> str:
    ascii_title = unicodedata.normalize('NFKD', title).encode(
        'ASCII', 'ignore').decode('utf-8')
    return ascii_title.replace('"', '').replace("'", "").strip()


def tmdb_auth(api_key: str) -> Tuple[Dict[str, str], Dict[str, str]]:
    """v4 bearer tokens are long JWTs; v3 keys are 32-char hex."""
    if len(api_key) > 50:
        return {"Authorization": f"Bearer {api_key}"}, {}
    return {}, {"api_key": api_key}


def tmdb_get(client: curl_requests.Session, url: str, params: Dict[str, Any],
             headers: Dict[str, str], max_retries: int = 3) -> Optional[Dict[str, Any]]:
    """Single TMDB call with 429 backoff. Returns None instead of raising."""
    for attempt in range(max_retries):
        try:
            STATS["api_calls"] += 1
            resp = client.get(url, params=params,
                              headers=headers, timeout=10.0)

            if resp.status_code == 429:
                time.sleep(1)
                continue
            if not resp.ok:
                return None
            return resp.json()
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"    [TMDB Error] {e}", file=sys.stderr, flush=True)
                return None
            time.sleep(1)
    return None


def fetch_tmdb_directors(client: curl_requests.Session, tmdb_id: int, api_key: str) -> List[str]:
    if tmdb_id in tmdb_director_cache:
        return tmdb_director_cache[tmdb_id]

    headers, base_params = tmdb_auth(api_key)
    data = tmdb_get(client, f"https://api.themoviedb.org/3/movie/{tmdb_id}/credits",
                    dict(base_params), headers)

    directors: List[str] = []
    if data:
        directors = [c.get("name", "") for c in data.get("crew", [])
                     if c.get("job") == "Director"]
    tmdb_director_cache[tmdb_id] = directors
    return directors


def tmdb_search(client: curl_requests.Session, query: str, headers: Dict[str, str],
                base_params: Dict[str, str], year: Optional[int] = None) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        **base_params,
        "query": query,
        "include_adult": "false",
        "language": "en-US",
    }
    if year:
        params["primary_release_year"] = year
    data = tmdb_get(
        client, "https://api.themoviedb.org/3/search/movie", params, headers)
    return data.get("results", []) if data else []


def resolve_tmdb_multipass(client: curl_requests.Session, title: str, year: Optional[int],
                           directors: List[str], api_key: str,
                           slug: Optional[str] = None) -> Tuple[Optional[int], Optional[str], Optional[str], Optional[str], Optional[str]]:
    headers, base_params = tmdb_auth(api_key)
    clean_q = clean_title_str(title) or title.strip()
    if not clean_q:
        return None, None, None, None, None

    def best_candidate(results: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        candidates: List[Tuple[float, Dict[str, Any]]] = []
        for cand in results[:10]:
            if not cand.get("title") and not cand.get("original_title"):
                continue
            cand_title = cand.get("title") or cand.get("original_title") or ""
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
                        if difflib.SequenceMatcher(None, target.lower(), cand_director.lower()).ratio() > 0.8:
                            return cand

        return candidates[0][1]

    search_years: List[int] = []
    if year is not None:
        search_years.append(year)
        for offset in (-1, 1):
            candidate_year = year + offset
            if candidate_year not in search_years:
                search_years.append(candidate_year)

    # Pass 1/2 - exact and near-year search.
    for search_year in search_years:
        match = best_candidate(tmdb_search(
            client, clean_q, headers, base_params, search_year))
        if match:
            return match.get("id"), "movie", match.get("title"), match.get("original_title"), match.get("original_language")

    # Pass 3 - title-only fuzzy match.
    match = best_candidate(tmdb_search(client, clean_q, headers, base_params))
    if match:
        return match.get("id"), "movie", match.get("title"), match.get("original_title"), match.get("original_language")

    # Pass 4 - slug fallback for international/non-Latin titles.
    if slug:
        cleaned_slug = slug.strip('/').split('/')[-1]
        cleaned_slug = re.sub(r'-(?:18|19|20)\d{2}$', '', cleaned_slug)
        slug_query = cleaned_slug.replace('-', ' ').strip()
        if slug_query and slug_query.lower() != clean_q.lower():
            slug_results = tmdb_search(client, slug_query, headers, base_params, year) or \
                tmdb_search(client, slug_query, headers, base_params)
            match = best_candidate(slug_results)
            if match:
                return match.get("id"), "movie", match.get("title"), match.get("original_title"), match.get("original_language")

    return None, None, None, None, None


# ---------------------------------------------------------
# Deep metadata (individual film page)
# ---------------------------------------------------------
def extract_deep_metadata(client: curl_requests.Session, slug: str, no_cache: bool,
                          api_key: Optional[str] = None) -> Dict[str, Any]:
    meta: Dict[str, Any] = {"tmdb_id": None,
                            "tmdb_type": None, "imdb_id": None}
    try:
        html, _ = fetch_html(
            client, f"{BASE_URL}/film/{slug}/", no_cache=no_cache)
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

        ld = None
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

        if meta["imdb_id"] is None:
            link = soup.select_one(
                'a[data-track-action="IMDb"], a[href*="imdb.com/title/"]')
            if link:
                imdb_match = re.search(
                    r'imdb\.com/title/(tt\d+)', attr_str(link, "href") or "", re.IGNORECASE)
                if imdb_match:
                    meta["imdb_id"] = imdb_match.group(1)

        if meta["imdb_id"] is None and ld is not None:
            for value in ld.get("sameAs", []):
                imdb_match = re.search(
                    r'imdb\.com/title/(tt\d+)', str(value), re.IGNORECASE)
                if imdb_match:
                    meta["imdb_id"] = imdb_match.group(1)
                    break

        if meta["imdb_id"] is None:
            html_match = re.search(
                r'imdb\.com/title/(tt\d+)', html, re.IGNORECASE)
            if html_match:
                meta["imdb_id"] = html_match.group(1)

        if meta["imdb_id"] is None and meta["tmdb_id"] is not None and api_key:
            headers, params = tmdb_auth(api_key)
            external = tmdb_get(
                client,
                f"https://api.themoviedb.org/3/movie/{meta['tmdb_id']}/external_ids",
                params,
                headers,
            )
            if external and isinstance(external, dict):
                imdb_id = external.get("imdb_id")
                if imdb_id:
                    meta["imdb_id"] = imdb_id
    except CloudflareBlock:
        raise
    except Exception as e:
        print(
            f"    [Deep Error] Failed on {slug}: {e}", file=sys.stderr, flush=True)
    return meta


def report_progress(callback: Optional[Callable[[Dict[str, Any]], None]],
                    stage: str, current: Optional[int], total: Optional[int],
                    message: str) -> None:
    payload = {"stage": stage, "current": current,
               "total": total, "message": message}
    if callback is None:
        if total is not None and total > 0:
            print(
                f"    [Progress] {stage}: {current}/{total} - {message}", flush=True)
        else:
            print(f"    [Progress] {stage}: {message}", flush=True)
        return
    callback(payload)


# ---------------------------------------------------------
# DOM Parsers
# ---------------------------------------------------------
def extract_poster(container: Tag) -> Optional[Tag]:
    """Return the poster node for a container, or the container itself if it is one."""
    if attr_str(container, "data-film-slug") or attr_str(container, "data-item-slug"):
        return container
    # Legacy markup puts the slug on `.film-poster`; the current React markup
    # puts it on the wrapping `.react-component` as `data-item-slug`.
    found = container.select_one(
        ".film-poster[data-film-slug], [data-film-slug], [data-item-slug], "
        "[data-film-link], [data-item-link], [data-target-link]")
    return found if isinstance(found, Tag) else None


def slug_from_poster(poster: Optional[Tag]) -> Optional[str]:
    if poster is None:
        return None
    for attr in ("data-film-slug", "data-item-slug", "data-film-link",
                 "data-item-link", "data-target-link"):
        slug = normalize_slug(attr_str(poster, attr))
        if slug:
            return slug
    return None


def title_year_from_img(poster: Optional[Tag], slug: str) -> Tuple[str, Optional[int]]:
    """Posters carry `Title (YYYY)` in the display-name attrs or the image alt."""
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

    # Slug fallback: `stalker-1979` -> "Stalker", not "Stalker 1979".
    bare = re.sub(r'-(?:18|19|20)\d{2}$', '', slug) if slug_year else slug
    return bare.replace('-', ' ').title(), slug_year


def parse_detail_entries(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    """`/detail/` view: one entry per parent container, poster is a child node."""
    entries: List[Dict[str, Any]] = []
    seen: set = set()

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

        rating_node = container.select_one("span.rating[class*='rated-']")
        letterboxd_rating = rating_from_classes(rating_node) if isinstance(
            rating_node, Tag) else None

        entries.append({
            "title": title,
            "year": year,
            "slug": slug,
            "directors": directors,
            "letterboxd_rating": letterboxd_rating,
            "tmdb_id": parse_id(attr_str(poster, "data-tmdb-id")),
            "tmdb_type": None,
        })

    return entries


def parse_grid_entries(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    """Standard poster grid (watchlists, user film history)."""
    entries: List[Dict[str, Any]] = []
    seen: set = set()

    for container in soup.select("li.poster-container, li.griditem, ul.poster-list > li"):
        if not isinstance(container, Tag):
            continue

        poster = extract_poster(container)
        slug = slug_from_poster(poster)
        if not slug or slug in seen:
            continue
        seen.add(slug)

        title, year = title_year_from_img(poster, slug)

        rating_node = container.select_one("span.rating[class*='rated-']")
        user_rating = rating_from_classes(rating_node) if isinstance(
            rating_node, Tag) else None

        entries.append({
            "title": title,
            "year": year,
            "slug": slug,
            "user_rating": user_rating,
            "tmdb_id": parse_id(attr_str(poster, "data-tmdb-id")),
            "tmdb_type": None,
        })

    return entries


def parse_diary_header(node: Tag) -> Tuple[Optional[int], Optional[int]]:
    """Pull `2024` / `May` out of a date header row or calendar cell."""
    text = node.get_text(" ", strip=True)

    year = parse_year(text)

    month = None
    month_match = re.search(
        r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b',
        text, re.IGNORECASE)
    if month_match:
        month = MONTHS.get(month_match.group(1).lower())

    # `/user/diary/films/for/2026/09/` links are authoritative when present.
    for candidate in node.select("a[href*='/for/']"):
        match = re.search(r'/for/(\d{4})(?:/(\d{1,2}))?',
                          attr_str(candidate, "href") or "")
        if match:
            year = int(match.group(1))
            if match.group(2):
                month = int(match.group(2))

    return year, month


def diary_date_for_row(row: Tag, current_year: Optional[int],
                       current_month: Optional[int]) -> Tuple[Optional[str], Optional[int], Optional[int]]:
    """
    Build an ISO `YYYY-MM-DD` for a diary entry row, preferring the row's own
    `/for/YYYY/MM/DD/` link and falling back to tracked header state.
    """
    # The month/year cell is only populated on the first row of each month.
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

    day_text = day_cell.get_text(strip=True) if isinstance(
        day_cell, Tag) else ""
    day_match = re.search(r'(\d{1,2})', day_text)
    if day_match and current_year and current_month:
        return (f"{current_year:04d}-{current_month:02d}-{int(day_match.group(1)):02d}",
                current_year, current_month)

    return None, current_year, current_month


def parse_diary_entries(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    """
    Diary table: the month/year cell is only rendered on the first row of each
    month (or in a separate header row), so it is carried forward as state; the
    day lives in the day cell of every entry row.
    """
    entries: List[Dict[str, Any]] = []
    current_year: Optional[int] = None
    current_month: Optional[int] = None

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
        title = headline.get_text(strip=True) if headline else None

        alt_title, alt_year = title_year_from_img(poster, slug)
        title = title or alt_title

        released = row.select_one("td.col-released, td.td-released")
        year = parse_year(released.get_text(strip=True)) if released else None
        year = year or alt_year or year_from_slug(slug)

        watched_at, current_year, current_month = diary_date_for_row(
            row, current_year, current_month)

        # The rewatch cell is always present; `icon-status-off` means first watch.
        rewatch_td = row.select_one("td.col-rewatch, td.td-rewatch")
        is_rewatch = rewatch_td is not None and "icon-status-off" not in node_classes(
            rewatch_td)

        rating_node = row.select_one(
            "td.col-rating span.rating[class*='rated-'], "
            "td.td-rating span.rating[class*='rated-'], "
            "span.rating[class*='rated-']")
        user_rating = rating_from_classes(rating_node) if isinstance(
            rating_node, Tag) else None

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


def has_next_page(soup: BeautifulSoup) -> bool:
    return soup.select_one(
        "div.paginate-pages a.next, a.next, a.paginate-next, .pagination a.next") is not None


def dump_debug_html(html: str, page_num: int) -> None:
    path = f"debug_html_dump_page_{page_num}.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"    [Debug] Raw HTML written to {path}", flush=True)


# ---------------------------------------------------------
# Feature: Account Discovery and Inspection
# ---------------------------------------------------------
def username_from_profile_link(link: Optional[Tag]) -> Optional[str]:
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


def extract_account_summary(container: Tag) -> Optional[Dict[str, Any]]:
    profile_link = container.select_one("a.avatar[href], a.name[href]")
    username = username_from_profile_link(profile_link)
    if not username:
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


def discover_hq_accounts(target: Optional[str], max_pages: Optional[int], no_cache: bool,
                         include_all: bool = False) -> Dict[str, Any]:
    target = target.strip().strip('/') if target else None
    is_hq_directory = target is None
    base_url = (f"{BASE_URL}/members/hq/" if is_hq_directory
                else f"{BASE_URL}/{target}/following/")
    accounts: List[Dict[str, Any]] = []
    seen_usernames: set = set()
    page_num = 1

    print(
        f"Discovering {'all followed accounts' if include_all else 'HQ accounts'} from: {base_url}", flush=True)
    with new_session() as client:
        try:
            while not max_pages or page_num <= max_pages:
                page_url = build_paginated_url(base_url, page_num)
                print(f"Fetching Page {page_num}: {page_url}", flush=True)
                try:
                    html, _ = fetch_html(client, page_url, no_cache=no_cache)
                except curl_requests.exceptions.HTTPError as exc:
                    if exc.response is not None and exc.response.status_code == 404:
                        break
                    raise

                soup = BeautifulSoup(html, "html.parser")
                cards = [node for node in soup.select(
                    ".person-summary, tr.person-summary, ul.person-list li, "
                    "ul.member-directory > li"
                ) if isinstance(node, Tag)]
                if not cards:
                    print(
                        "  [!] No account cards found. Ending pagination.", flush=True)
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

                if not has_next_page(soup):
                    break
                page_num += 1
        except (CloudflareBlock, KeyboardInterrupt) as exc:
            print(
                f"\n[!] Discovery aborted: {exc}. Exporting collected accounts.", flush=True)

    STATS["items"] = len(accounts)
    print(f"Discovered {len(accounts)} matching accounts.", flush=True)
    return {
        "source": "letterboxd_hq_directory" if is_hq_directory else "letterboxd_following",
        "total_hqs": sum(1 for account in accounts if account["is_hq"]),
        "accounts": accounts,
    }


def inspect_account(username: str, no_cache: bool) -> Dict[str, Any]:
    username = username.strip().strip('/')
    profile_url = f"{BASE_URL}/{username}/"
    print(f"Inspecting account: {profile_url}", flush=True)

    with new_session() as client:
        html, _ = fetch_html(client, profile_url, no_cache=no_cache)

    soup = BeautifulSoup(html, "html.parser")
    profile = soup.select_one(
        ".profile-header, .profile-header-wrapper, header.profile-header")
    container = profile if isinstance(profile, Tag) else soup
    name_node = container.select_one(".displayname .label, h1 .label")
    if not name_node:
        name_node = container.select_one(".profile-name, h1, .title-1, .name")
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

    payload = {
        "source": "letterboxd_profile",
        "username": username,
        "display_name": name_node.get_text(" ", strip=True) if name_node else username,
        "avatar_url": urljoin(BASE_URL, attr_str(avatar_node, "src") or attr_str(avatar_node, "data-src") or "") or None,
        "bio": bio_node.get_text(" ", strip=True) if bio_node else None,
        "account_tier": account_tier,
        "total_public_lists": lists_count,
    }
    STATS["items"] = 1
    print(json.dumps(payload, indent=2, ensure_ascii=False), flush=True)
    return payload


# ---------------------------------------------------------
# Feature: User Lists Discovery
# ---------------------------------------------------------
def discover_user_lists(username: str, output_file: str, max_pages: Optional[int], no_cache: bool,
                        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
    print(f"Discovering lists for account: {username}", flush=True)

    discovered: List[Dict[str, Any]] = []
    seen_urls: set = set()
    page_num = 1
    pages_processed = 0
    base_url = f"{BASE_URL}/{username}/lists/"

    def emit(stage: str, current: Optional[int], total: Optional[int], message: str) -> None:
        report_progress(progress_callback, stage, current, total, message)

    with new_session() as client:
        try:
            while True:
                if max_pages and page_num > max_pages:
                    break

                page_url = build_paginated_url(base_url, page_num)
                emit("fetch_page", page_num, None,
                     f"Fetching lists page {page_num}: {page_url}")
                print(f"Fetching Page {page_num}: {page_url}", flush=True)

                try:
                    html, _ = fetch_html(client, page_url, no_cache=no_cache)
                except curl_requests.exceptions.HTTPError as e:
                    if e.response is not None and e.response.status_code == 404:
                        print(
                            "  Reached the end of the lists (404 Not Found).", flush=True)
                        break
                    raise

                soup = BeautifulSoup(html, "html.parser")
                cards: List[Tag] = [c for c in soup.select(
                    "section.list, section.film-list-summary, div.film-list-summary, "
                    ".list-summary, .list-set > section") if isinstance(c, Tag)]

                if not cards:
                    for link in soup.select("h2 a[href*='/list/'], h3 a[href*='/list/']"):
                        parent = link.find_parent(['section', 'div', 'li'])
                        if isinstance(parent, Tag) and parent not in cards:
                            cards.append(parent)

                if not cards:
                    print(
                        "  [!] No list cards found on this page. Ending pagination.", flush=True)
                    break

                pages_processed += 1

                for card in cards:
                    link_el = card.select_one("h2 a, h3 a, a[href*='/list/']")
                    href = attr_str(link_el, "href")
                    if not href:
                        continue

                    list_url = urljoin(BASE_URL, href)
                    if not list_url.endswith('/'):
                        list_url += '/'
                    if list_url in seen_urls:
                        continue
                    seen_urls.add(list_url)

                    val_tag = card.select_one("small.value, small, span.value")
                    digits = re.sub(
                        r'\D', '', val_tag.get_text()) if val_tag else ""

                    desc_el = card.select_one(
                        ".list-description, .body-text, .description, .notes")

                    preview_posters: List[str] = []
                    preview_slugs: List[str] = []
                    for preview in card.select("ul.poster-list li, li.poster-container, li.griditem, .film-list-summary")[:5]:
                        if preview is None:
                            continue
                        poster_node = preview.select_one("img")
                        if poster_node:
                            poster_src = attr_str(poster_node, "src") or attr_str(
                                poster_node, "data-src")
                            if poster_src and poster_src not in preview_posters:
                                preview_posters.append(poster_src)
                        for attr in ("data-film-slug", "data-item-slug"):
                            slug = normalize_slug(attr_str(preview, attr))
                            if slug and slug not in preview_slugs:
                                preview_slugs.append(slug)

                    discovered.append({
                        "title": link_el.get_text(strip=True) if link_el else list_url,
                        "slug": list_url.strip('/').split('/')[-1],
                        "url": list_url,
                        "total_films": int(digits) if digits else 0,
                        "description": desc_el.get_text(separator="\n", strip=True) if desc_el else None,
                        "preview_posters": preview_posters,
                        "preview_slugs": preview_slugs,
                    })

                emit("page_done", pages_processed, None,
                     f"Processed {len(discovered)} list cards so far")
                if not has_next_page(soup):
                    break
                page_num += 1

        except (CloudflareBlock, KeyboardInterrupt) as e:
            print(
                f"\n[!] Aborted: {e}. Salvaging discovered lists so far...", flush=True)

    print(
        f"\nDiscovered {len(discovered)} lists across {pages_processed} pages for user '{username}':",
        flush=True)
    for d in discovered:
        print(f"[{d['title']}] ({d['total_films']} films) -> {d['url']}", flush=True)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(discovered, f, indent=2, ensure_ascii=False)
    print(f"\nList inventory exported to {output_file}", flush=True)


# ---------------------------------------------------------
# Feature: RSS Diary Ingestion
# ---------------------------------------------------------
def clean_title_and_year(raw_title: str, slug: str) -> Tuple[str, Optional[int]]:
    match = re.search(r'^(.*)\s+\(((?:18|19|20)\d{2})\)$', raw_title.strip())
    if match:
        return match.group(1).strip(), int(match.group(2))
    return raw_title.strip(), year_from_slug(slug)


def ingest_rss_diary(username: str) -> Dict[str, Any]:
    url = f"{BASE_URL}/{username}/rss/"
    print(f"Ingesting RSS diary for: {username}", flush=True)

    with new_session() as client:
        resp = client.get(url)
        resp.raise_for_status()
        raw_feed = resp.text

    root = ET.fromstring(raw_feed)
    ns = {'letterboxd': 'https://letterboxd.com'}
    entries: List[Dict[str, Any]] = []

    for item in root.findall('./channel/item'):
        link_node = item.find('link')
        lb_url = link_node.text if link_node is not None else ""
        slug = normalize_slug(urlparse(lb_url or "").path) or ""

        title_node = item.find('letterboxd:filmTitle', ns)
        year_node = item.find('letterboxd:filmYear', ns)

        if title_node is None or title_node.text is None:
            raw_title_node = item.find('title')
            raw_title = raw_title_node.text if raw_title_node is not None else "Unknown"
            clean_title, year = clean_title_and_year(str(raw_title), slug)
        else:
            clean_title = title_node.text
            year = parse_year(
                year_node.text) if year_node is not None else year_from_slug(slug)

        date_node = item.find('letterboxd:watchedDate', ns)
        rating_node = item.find('letterboxd:memberRating', ns)
        rewatch_node = item.find('letterboxd:rewatch', ns)

        desc_node = item.find('description')
        desc = ""
        if desc_node is not None and desc_node.text:
            desc = BeautifulSoup(desc_node.text, "html.parser").get_text(
                separator="\n", strip=True)

        entries.append({
            "title": clean_title,
            "year": year,
            "slug": slug,
            "letterboxd_url": lb_url,
            "watched_at": date_node.text if date_node is not None else None,
            "user_rating": float(rating_node.text) if rating_node is not None and rating_node.text else None,
            "is_rewatch": rewatch_node is not None and rewatch_node.text == "Yes",
            "review_snippet": desc,
        })

    STATS["items"] += len(entries)
    print(f"Ingested {len(entries)} diary entries.", flush=True)
    return {
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "list_type": "diary",
        "username": username,
        "list_title": f"{username}'s RSS Diary",
        "list_url": url,
        "total_films": len(entries),
        "films": entries,
    }


# ---------------------------------------------------------
# Core Extractors (Lists & Full History)
# ---------------------------------------------------------
def enrich_entry(client: curl_requests.Session, entry: Dict[str, Any], is_deep: bool,
                 tmdb_api_key: Optional[str], no_cache: bool, label: str) -> None:
    title = str(entry.get("title") or entry.get("slug"))
    year = entry.get("year")

    if is_deep:
        entry.update(extract_deep_metadata(
            client, entry["slug"], no_cache=no_cache, api_key=tmdb_api_key))
        print(
            f"    [Deep] {label} {title} -> TMDB {entry.get('tmdb_id')} | IMDb {entry.get('imdb_id')}", flush=True)
        return

    if entry.get("tmdb_id"):
        entry.setdefault("tmdb_type", "movie")
        print(
            f"    [Inline] {label} {title} -> TMDB {entry['tmdb_id']}", flush=True)
        return

    if tmdb_api_key:
        tid, ttype, resolved_title, original_title, original_language = resolve_tmdb_multipass(
            client, title, year, entry.get("directors", []), tmdb_api_key, entry.get("slug"))
        entry["tmdb_id"], entry["tmdb_type"] = tid, ttype
        if resolved_title:
            entry["title"] = resolved_title
        if original_title is not None:
            entry["original_title"] = original_title
        if original_language is not None:
            entry["original_language"] = original_language
        print(
            f"    [API Match] {label} {title} ({year}) -> TMDB {tid}", flush=True)
        return

    print(f"    [Scraped] {label} {title}", flush=True)


def scrape_letterboxd_list(raw_url: str, is_deep: bool, tmdb_api_key: Optional[str],
                           max_pages: Optional[int], no_cache: bool,
                           progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    checkpoint = CheckpointManager(raw_url)
    page_num = checkpoint.current_page

    is_list = (
        '/list/' in urlparse(raw_url).path or '/watchlist' in urlparse(raw_url).path)
    use_detail_mode = is_list and not is_deep
    completed = False
    is_ranked = False
    total_films: Optional[int] = None
    total_pages: Optional[int] = None

    def emit(stage: str, current: Optional[int], total: Optional[int], message: str) -> None:
        report_progress(progress_callback, stage, current, total, message)

    print(
        f"Targeting: {raw_url} | Detail Mode: {use_detail_mode} | Deep: {is_deep}", flush=True)
    print("-" * 60, flush=True)

    with new_session() as client:
        try:
            while True:
                if max_pages and page_num > max_pages:
                    completed = True
                    break

                page_url = build_paginated_url(
                    raw_url, page_num, detail_mode=use_detail_mode)
                emit("fetch_page", page_num, total_pages,
                     f"Fetching Page {page_num}: {page_url}")
                print(f"Fetching Page {page_num}: {page_url}", flush=True)

                try:
                    html, _ = fetch_html(client, page_url, no_cache=no_cache)
                except curl_requests.exceptions.HTTPError as e:
                    if e.response is not None and e.response.status_code == 404:
                        print(
                            "    [!] Reached 404 (End of list or Invalid URL).", flush=True)
                        completed = True
                        break
                    raise

                soup = BeautifulSoup(html, "html.parser")
                if page_num == 1:
                    total_count_candidates = [
                        *soup.select('span.footnote, span.value, small.value'),
                        *soup.select('meta[property="og:description"]'),
                    ]
                    for candidate in total_count_candidates:
                        text = candidate.get(
                            'content') if candidate.name == 'meta' else candidate.get_text(' ', strip=True)
                        match = re.search(
                            r'(\d+)\s*(?:films?|movies?)', str(text), re.IGNORECASE)
                        if match:
                            total_films = int(match.group(1))
                            total_pages = math.ceil(
                                total_films / 100) if total_films else None
                            break
                    is_ranked = bool(soup.select_one(
                        '.list-number, span[class*=list-number], .list-numbering, .listitem .list-number'))

                page_entries = parse_detail_entries(
                    soup) if use_detail_mode else []
                if not page_entries:
                    page_entries = parse_grid_entries(soup)

                if not page_entries:
                    print(
                        f"    [!] No film entries found on page {page_num}. Ending.", flush=True)
                    dump_debug_html(html, page_num)
                    completed = True
                    break

                for entry in page_entries:
                    entry.setdefault("directors", [])
                    entry["rank"] = len(checkpoint.items) + 1
                    enrich_entry(client, entry, is_deep, tmdb_api_key, no_cache,
                                 label=f"#{entry['rank']:03d}")
                    checkpoint.upsert(entry)

                checkpoint.save(page_num)
                emit("page_done", len(checkpoint.items), total_films,
                     f"Processed page {page_num} ({len(checkpoint.items)} items scraped)")

                if not has_next_page(soup):
                    completed = True
                    break

                page_num += 1
                checkpoint.save(page_num)

        except (CloudflareBlock, KeyboardInterrupt) as e:
            print(
                f"\n[!] Scrape Aborted: {e}. Progress saved to checkpoint.", flush=True)
            checkpoint.save(page_num)

    results = checkpoint.items
    if completed:
        checkpoint.clear()

    # Re-sequence ranks so resumed runs stay contiguous after de-duplication.
    for idx, item in enumerate(results, start=1):
        item["rank"] = idx

    STATS["items"] = len(results)
    return {
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "list_type": "list",
        "list_url": raw_url,
        "is_ranked": is_ranked,
        "total_films": total_films or len(results),
        "total_pages": total_pages,
        "films": results,
    }


def scrape_user_diary_history(username: str, mode: str, tmdb_api_key: Optional[str],
                              max_pages: Optional[int], no_cache: bool,
                              progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    base_url = (f"{BASE_URL}/{username}/films/diary/"
                if mode == "diary" else f"{BASE_URL}/{username}/films/")
    checkpoint = CheckpointManager(base_url)
    page_num = checkpoint.current_page
    completed = False
    total_films: Optional[int] = None

    def emit(stage: str, current: Optional[int], total: Optional[int], message: str) -> None:
        report_progress(progress_callback, stage, current, total, message)

    print(f"Targeting Full {mode.title()}: {base_url}", flush=True)

    with new_session() as client:
        try:
            while True:
                if max_pages and page_num > max_pages:
                    completed = True
                    break

                page_url = build_paginated_url(base_url, page_num)
                emit("fetch_page", page_num, total_films,
                     f"Fetching Page {page_num}: {page_url}")
                print(f"Fetching Page {page_num}: {page_url}", flush=True)

                try:
                    html, _ = fetch_html(client, page_url, no_cache=no_cache)
                except curl_requests.exceptions.HTTPError as e:
                    if e.response is not None and e.response.status_code == 404:
                        print(
                            "    [!] Reached 404 (End of list or Invalid URL).", flush=True)
                        completed = True
                        break
                    raise

                soup = BeautifulSoup(html, "html.parser")
                page_entries = (parse_diary_entries(soup)
                                if mode == "diary" else parse_grid_entries(soup))

                if not page_entries:
                    print(
                        f"    [!] No film entries found on page {page_num}. Ending.", flush=True)
                    dump_debug_html(html, page_num)
                    completed = True
                    break

                for entry in page_entries:
                    enrich_entry(client, entry, False, tmdb_api_key, no_cache,
                                 label=f"[{mode.title()}]")
                    checkpoint.upsert(entry)

                checkpoint.save(page_num)
                emit("page_done", len(checkpoint.items), total_films,
                     f"Processed page {page_num} ({len(checkpoint.items)} items scraped)")

                if not has_next_page(soup):
                    completed = True
                    break

                page_num += 1
                checkpoint.save(page_num)

        except (CloudflareBlock, KeyboardInterrupt) as e:
            print(
                f"\n[!] Scrape Aborted: {e}. Progress saved to checkpoint.", flush=True)
            checkpoint.save(page_num)

    results = checkpoint.items
    if completed:
        checkpoint.clear()

    STATS["items"] = len(results)
    return {
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "list_type": mode,
        "username": username,
        "list_url": base_url,
        "total_films": len(results),
        "films": results,
    }


# ---------------------------------------------------------
# Output Schema (CineChain)
# ---------------------------------------------------------
def derive_canon_key(payload: Dict[str, Any], preset_key: Optional[str]) -> str:
    if preset_key:
        return preset_key
    path = urlparse(payload.get("list_url", "")).path.strip('/')
    slug = path.split('/')[-1] if path else "unknown"
    return slug.replace('-', '_') or "unknown"


def derive_badge_prefix(canon_key: str, preset_key: Optional[str], badge_prefix: Optional[str] = None) -> str:
    """Short uppercase tag, e.g. `SS22`, `LB250`."""
    if badge_prefix:
        return badge_prefix.strip().upper()

    if preset_key and preset_key in PRESETS:
        return PRESETS[preset_key]["badge"]

    tokens = [t for t in re.split(r'[^A-Za-z0-9]+', canon_key) if t]
    if not tokens:
        return "LB"

    digits = "".join(t for t in tokens if t.isdigit())
    initials = "".join(t[0] for t in tokens if not t.isdigit())[:4].upper()
    return (f"{initials}{digits}" or "LB")[:12]


def convert_to_cinechain_format(payload: Dict[str, Any], preset_key: Optional[str],
                                badge_prefix: Optional[str] = None) -> Dict[str, Any]:
    list_type = payload.get("list_type", "")
    films = payload.get("films", [])
    is_ranked = bool(payload.get("is_ranked", False))

    if list_type == "list":
        canon_key = derive_canon_key(payload, preset_key)
        badge_label_prefix = derive_badge_prefix(
            canon_key, preset_key, badge_prefix)
        entries = []
        for idx, f in enumerate(films, start=1):
            rank_value = idx if is_ranked else None
            badge_label = f"{badge_label_prefix} #{idx}" if is_ranked else badge_label_prefix
            entries.append({
                "tmdb_id": f.get("tmdb_id"),
                "title": f.get("title"),
                "original_title": f.get("original_title"),
                "original_language": f.get("original_language"),
                "imdb_id": f.get("imdb_id"),
                "year": f.get("year"),
                "rank": rank_value,
                "directors": f.get("directors", []),
                "letterboxd_rating": f.get("letterboxd_rating"),
                "badge_label": badge_label,
            })

        return {
            "meta": {
                "source": "letterboxd",
                "type": "canon",
                "canon_key": canon_key,
                "total_items": len(entries),
                "is_ranked": is_ranked,
            },
            "entries": entries,
        }

    entries = [{
        "tmdb_id": f.get("tmdb_id"),
        "title": f.get("title"),
        "original_title": f.get("original_title"),
        "original_language": f.get("original_language"),
        "imdb_id": f.get("imdb_id"),
        "year": f.get("year"),
        "watched_at": f.get("watched_at"),
        "user_rating": f.get("user_rating"),
        "is_rewatch": bool(f.get("is_rewatch", False)),
    } for f in films]

    return {
        "meta": {
            "source": "letterboxd",
            "type": list_type or "diary",
            "username": payload.get("username"),
            "total_items": len(entries),
        },
        "entries": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CineChain Letterboxd Ingestion Toolkit")

    # Core Targets
    parser.add_argument("url", nargs="?", help="Target Letterboxd URL")
    parser.add_argument("--preset", help="Use a pre-configured canon list key")

    # Multi-Purpose Targets
    parser.add_argument("--list-presets", action="store_true",
                        help="Show available canon presets")
    parser.add_argument("--user-lists", metavar="USERNAME",
                        help="Discover and export all lists published by an account")
    parser.add_argument("--watchlist", metavar="USERNAME",
                        help="Scrape a user's watchlist")
    parser.add_argument("--rss", metavar="USERNAME",
                        help="Ingest a user's recent RSS diary feed")
    parser.add_argument("--diary", metavar="USERNAME",
                        help="Scrape entire diary log for a user")
    parser.add_argument("--history", metavar="USERNAME",
                        help="Scrape entire film history for a user")
    parser.add_argument("--discover-hqs", nargs="?", const="",
                        metavar="TARGET_ACCOUNT",
                        help="Discover the Letterboxd HQ directory, or HQs followed by an optional account")
    parser.add_argument("--all-following", action="store_true",
                        help="Include non-HQ accounts with --discover-hqs")
    parser.add_argument("--inspect", metavar="USERNAME",
                        help="Inspect an account profile and public list count")

    # Modifiers
    parser.add_argument("--output", "-o", default="letterboxd_export.json",
                        help="Destination JSON file")
    parser.add_argument("--badge-prefix", metavar="PREFIX",
                        help="Override the generated badge prefix for CineChain output")
    parser.add_argument("--cinechain-format", action="store_true",
                        help="Format output for SQLite ingestion")
    parser.add_argument("--deep", action="store_true",
                        help="Opt-in to visit individual film pages")
    parser.add_argument(
        "--tmdb-key", help="TMDB API Key. Checks TMDB_API_KEY env var.")
    parser.add_argument("--max-pages", type=int,
                        help="Stop after scraping N pages")
    parser.add_argument("--no-cache", action="store_true",
                        help="Bypass local HTML file cache")

    args = parser.parse_args()
    api_key = args.tmdb_key or os.environ.get("TMDB_API_KEY")

    if args.list_presets:
        print("\nAvailable Pre-configured Canon Presets:")
        print("-" * 75)
        for key, cfg in PRESETS.items():
            print(f"--preset {key:<25} | {cfg['title']}")
        print("-" * 75)
        sys.exit(0)

    try:
        if args.user_lists:
            discover_user_lists(args.user_lists, args.output,
                                args.max_pages, args.no_cache)
            sys.exit(0)

        elif args.discover_hqs is not None:
            payload = discover_hq_accounts(
                args.discover_hqs, args.max_pages, args.no_cache, args.all_following)

        elif args.inspect:
            payload = inspect_account(args.inspect, args.no_cache)

        elif args.rss:
            payload = ingest_rss_diary(args.rss)

        elif args.diary or args.history:
            mode = "diary" if args.diary else "history"
            payload = scrape_user_diary_history(
                args.diary or args.history, mode, api_key, args.max_pages, args.no_cache)

        elif args.watchlist:
            target_url = f"https://letterboxd.com/{args.watchlist}/watchlist/"
            payload = scrape_letterboxd_list(
                target_url, args.deep, api_key, args.max_pages, args.no_cache)

        elif args.preset or args.url:
            if args.preset:
                if args.preset not in PRESETS:
                    print(f"[Error] Unknown preset '{args.preset}'. Use --list-presets.",
                          file=sys.stderr)
                    sys.exit(1)
                target_url = PRESETS[args.preset]["url"]
            else:
                target_url = args.url
            payload = scrape_letterboxd_list(
                target_url, args.deep, api_key, args.max_pages, args.no_cache)

        else:
            parser.print_help()
            sys.exit(1)

        if args.cinechain_format:
            payload = convert_to_cinechain_format(
                payload, args.preset, args.badge_prefix)

        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        duration = time.time() - STATS["start_time"]
        if payload.get("source") == "letterboxd_profile":
            item_count = 1
        else:
            item_count = len(payload.get(
                "accounts", payload.get("entries", payload.get("films", []))))
        print("\n" + "=" * 60, flush=True)
        print(f"Export Complete: {args.output}", flush=True)
        print(f"Stats: {item_count} items | Cache Hits: {STATS['hits']} | "
              f"Net Requests: {STATS['misses']} | TMDB: {STATS['api_calls']} | "
              f"Time: {duration:.2f}s", flush=True)

    except KeyboardInterrupt:
        print("\n\n[WARNING] Process killed by user.",
              file=sys.stderr, flush=True)
        sys.exit(130)


if __name__ == "__main__":
    main()
