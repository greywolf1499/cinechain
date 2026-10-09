"""Polite, opt-in TVTropes link-list ingestion with fail-closed robots handling.

Only canonical work URLs and trope identifiers are cached; response HTML is discarded.
The current site's general robots policy disallows crawling, so normal requests are denied
before a work page is fetched.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests
from sqlmodel import Session

from app.config import get_settings
from app.facets.tropes import normalize_trope
from app.models.cache import CachedMovie
from app.services import settings_repo

logger = logging.getLogger(__name__)

BASE_URL = "https://tvtropes.org"
USER_AGENT = "CineChain/1.0 (+https://github.com/greywolf1499/cinechain)"
CACHE_TTL = timedelta(days=90)
ROBOTS_TTL = timedelta(hours=1)
MIN_REQUEST_INTERVAL = 8.0
MAX_PAGES_PER_BATCH = 150
# Keep external ingestion unavailable until the site's licence and terms have been reviewed.
TERMS_APPROVED = False
_request_lock = threading.Lock()
_last_request = 0.0
_robots_cache: tuple[float, RobotFileParser] | None = None


class RobotsDisallowed(RuntimeError):
    """The website's current robots policy does not permit this request."""


class CloudflareBlock(RuntimeError):
    """A challenge or rate-limit response requires an immediate batch halt."""


class TVTropesDisabled(RuntimeError):
    """TVTropes integration was not explicitly enabled."""


class BatchPageLimit(RuntimeError):
    """The 150-page TVTropes batch budget has been consumed."""


@dataclass(frozen=True)
class TropeLink:
    name: str
    slug: str
    mapped_slug: str | None
    url: str


@dataclass(frozen=True)
class ScrapeResult:
    work_url: str | None
    tropes: tuple[TropeLink, ...]
    cached: bool = False


@dataclass(frozen=True)
class Response:
    status_code: int
    headers: dict[str, str]
    text: str = ""


def enabled(session: Session) -> bool:
    if not TERMS_APPROVED:
        return False
    override = settings_repo.get_overrides(session).get("tvtropes_enabled")
    if override is not None:
        return override.lower() == "true"
    return get_settings().tvtropes_enabled


def _title_slug(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", title)


def camel_case_slug(value: str) -> str:
    """Convert a TVTropes Main/CamelCase identifier to the taxonomy's kebab form."""
    value = value.rsplit("/", 1)[-1]
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", value)
    value = re.sub(r"([A-Z])([A-Z][a-z])", r"\1-\2", value)
    return re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()


def _year_matches(html: str, expected_year: int) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    header = soup.find(["h1", "title"])
    text = header.get_text(" ", strip=True) if header else soup.get_text(" ", strip=True)[:500]
    years = [int(value) for value in re.findall(r"\b(18\d{2}|19\d{2}|20\d{2})\b", text)]
    return any(abs(year - expected_year) <= 1 for year in years)


def extract_trope_links(html: str) -> tuple[TropeLink, ...]:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one("#main-article") or soup
    found: dict[str, TropeLink] = {}
    for anchor in root.select("a[href]"):
        href = str(anchor.get("href", ""))
        match = re.search(r"/Main/([^/?#]+)", href)
        if match is None:
            continue
        absolute = urljoin(BASE_URL, href)
        if urlparse(absolute).netloc != urlparse(BASE_URL).netloc:
            continue
        name = match.group(1)
        slug = camel_case_slug(name)
        if not slug:
            continue
        found.setdefault(
            slug,
            TropeLink(
                name=name,
                slug=slug,
                mapped_slug=normalize_trope(name),
                url=absolute,
            ),
        )
    return tuple(found.values())


def _json_path(cache_dir: Path, movie_id: int) -> Path:
    return cache_dir / f"{movie_id}.json"


def _read_cache(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or not isinstance(value.get("tropes"), list):
        return None
    return value


def _cached_result(data: dict, *, cached: bool) -> ScrapeResult:
    return ScrapeResult(
        work_url=data.get("work_url"),
        tropes=tuple(
            TropeLink(**item)
            for item in data["tropes"]
            if isinstance(item, dict)
            and {"name", "slug", "mapped_slug", "url"} <= item.keys()
        ),
        cached=cached,
    )


class TVTropesClient:
    """Single-concurrency client; `request` injection keeps tests entirely fixture-local."""

    def __init__(
        self,
        cache_dir: Path | None = None,
        request=None,
        *,
        min_interval: float = MIN_REQUEST_INTERVAL,
        now=time.time,
        sleep=time.sleep,
        check_cancelled: Callable[[], None] | None = None,
    ) -> None:
        self.cache_dir = cache_dir or (get_settings().config_dir / "tvtropes")
        self.request = request or self._request
        self.min_interval = min_interval
        self.now = now
        self.sleep = sleep
        self.check_cancelled = check_cancelled
        self.pages_fetched = 0
        self._robots: RobotFileParser | None = None
        self._robots_loaded = 0.0

    @staticmethod
    def _request(url: str, headers: dict[str, str]) -> Response:
        response = curl_requests.get(
            url,
            headers=headers,
            timeout=20,
            allow_redirects=False,
            impersonate="chrome124",
        )
        return Response(
            status_code=response.status_code,
            headers=dict(response.headers),
            text=response.text,
        )

    def _send(self, url: str, headers: dict[str, str] | None = None) -> Response:
        global _last_request
        if urlparse(url).netloc != urlparse(BASE_URL).netloc:
            raise ValueError("TVTropes client refuses non-TV Tropes URLs")
        if self.pages_fetched >= MAX_PAGES_PER_BATCH:
            raise BatchPageLimit("TVTropes batch page cap reached")
        if self.check_cancelled is not None:
            self.check_cancelled()
        with _request_lock:
            wait = self.min_interval - (self.now() - _last_request)
            if wait > 0:
                self.sleep(wait)
            response = self.request(url, {"User-Agent": USER_AGENT, **(headers or {})})
            _last_request = self.now()
        self.pages_fetched += 1
        return response

    def _fetch(self, url: str, headers: dict[str, str] | None = None) -> Response:
        response = self._send(url, headers)
        if response.status_code in (403, 429) or _is_challenge(response.text):
            raise CloudflareBlock(f"TVTropes blocked the batch (HTTP {response.status_code})")
        if 300 <= response.status_code < 400 and response.status_code != 304:
            raise RuntimeError("Unexpected redirect from TVTropes; refusing to follow it")
        return response

    def _robots_allowed(self, url: str) -> bool:
        global _robots_cache
        current = self.now()
        robots = self._robots
        if robots is None or current - self._robots_loaded > ROBOTS_TTL.total_seconds():
            if _robots_cache is None or current - _robots_cache[0] > ROBOTS_TTL.total_seconds():
                response = self._send(f"{BASE_URL}/robots.txt")
                if response.status_code in (403, 429) or _is_challenge(response.text):
                    raise CloudflareBlock(
                        f"TVTropes blocked the batch while checking robots.txt (HTTP {response.status_code})"
                    )
                if response.status_code != 200:
                    raise RobotsDisallowed("Could not verify TVTropes robots.txt; failing closed")
                parser = RobotFileParser()
                parser.set_url(f"{BASE_URL}/robots.txt")
                parser.parse(response.text.splitlines())
                _robots_cache = (current, parser)
            robots = _robots_cache[1]
            self._robots = robots
            self._robots_loaded = current
        return robots.can_fetch(USER_AGENT, url)

    def _allowed_fetch(self, url: str, headers: dict[str, str] | None = None) -> Response:
        if not self._robots_allowed(url):
            raise RobotsDisallowed(f"TVTropes robots.txt disallows {url}")
        return self._fetch(url, headers)

    def _work_candidates(self, movie: CachedMovie) -> list[str]:
        title = _title_slug(movie.title)
        if not title:
            return []
        year = int(movie.release_date[:4]) if movie.release_date and movie.release_date[:4].isdigit() else None
        candidates = [f"{BASE_URL}/pmwiki/pmwiki.php/Film/{quote(title)}"]
        if year:
            candidates.insert(0, f"{BASE_URL}/pmwiki/pmwiki.php/Film/{quote(title + str(year))}")
        search = f"{BASE_URL}/pmwiki/search_result.php?q={quote(movie.title)}"
        candidates.append(search)
        return candidates

    def _resolve_work_url(self, movie: CachedMovie) -> str | None:
        year = int(movie.release_date[:4]) if movie.release_date and movie.release_date[:4].isdigit() else None
        if year is None:
            return None
        for url in self._work_candidates(movie):
            response = self._allowed_fetch(url)
            if response.status_code == 404:
                continue
            if response.status_code != 200:
                raise RuntimeError(f"TVTropes returned HTTP {response.status_code}")
            if url.endswith("search_result.php?q=" + quote(movie.title)):
                soup = BeautifulSoup(response.text, "html.parser")
                candidates = []
                for link in soup.select("a[href*='/Film/']"):
                    candidate = urljoin(BASE_URL, str(link.get("href")))
                    if urlparse(candidate).netloc == urlparse(BASE_URL).netloc:
                        candidates.append(candidate)
                for candidate in dict.fromkeys(candidates):
                    page = self._allowed_fetch(candidate)
                    if page.status_code == 200 and _year_matches(page.text, year):
                        return candidate
            elif _year_matches(response.text, year):
                return url
        return None

    def sync_movie(self, movie: CachedMovie, *, enabled: bool) -> ScrapeResult:
        if not enabled or not TERMS_APPROVED:
            raise TVTropesDisabled(
                "TVTropes ingestion requires an explicit opt-in and approved site terms"
            )
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = _json_path(self.cache_dir, movie.tmdb_id)
        data = _read_cache(path)
        now = datetime.fromtimestamp(self.now(), UTC)
        if data is not None:
            try:
                fetched = datetime.fromisoformat(data["fetched_at"])
            except (KeyError, ValueError):
                fetched = now - CACHE_TTL
            if now - fetched < CACHE_TTL:
                movie.tvtropes_work_url = data.get("work_url")
                return _cached_result(data, cached=True)
        work_url = (data or {}).get("work_url") or movie.tvtropes_work_url
        if work_url is None:
            work_url = self._resolve_work_url(movie)
        if work_url is None:
            data = {
                "fetched_at": now.isoformat(),
                "work_url": None,
                "tropes": [],
            }
            self._save(path, data)
            return ScrapeResult(None, ())
        headers = {}
        if data and data.get("work_url") == work_url:
            if data.get("etag"):
                headers["If-None-Match"] = data["etag"]
            if data.get("last_modified"):
                headers["If-Modified-Since"] = data["last_modified"]
        response = self._allowed_fetch(work_url, headers)
        if response.status_code == 304 and data is not None:
            data["fetched_at"] = now.isoformat()
        elif response.status_code == 200:
            links = extract_trope_links(response.text)
            data = {
                "fetched_at": now.isoformat(),
                "work_url": work_url,
                "etag": response.headers.get("ETag") or response.headers.get("etag"),
                "last_modified": response.headers.get("Last-Modified")
                or response.headers.get("last-modified"),
                "tropes": [link.__dict__ for link in links],
            }
        elif response.status_code == 404:
            data = {"fetched_at": now.isoformat(), "work_url": None, "tropes": []}
        else:
            raise RuntimeError(f"TVTropes returned HTTP {response.status_code}")
        self._save(path, data)
        movie.tvtropes_work_url = data.get("work_url")
        return _cached_result(data, cached=False)

    @staticmethod
    def _save(path: Path, data: dict) -> None:
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        temporary.replace(path)


def _is_challenge(html: str) -> bool:
    lowered = html.lower()
    return any(
        phrase in lowered
        for phrase in ("just a moment...", "cf-challenge", "cloudflare ray id", "attention required")
    )


def fetch_movie_tropes(
    movie: CachedMovie,
    *,
    enabled: bool,
    check_cancelled: Callable[[], None] | None = None,
) -> ScrapeResult:
    """Use the default cache/client; caller must run this blocking operation in a worker thread."""
    return TVTropesClient(check_cancelled=check_cancelled).sync_movie(movie, enabled=enabled)
