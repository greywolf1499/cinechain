"""Opt-in, paced TVTropes link ingestion with batch-local backpressure."""

from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests
from sqlmodel import Session

from app.config import get_settings
from app.facets.tropes import normalize_trope
from app.models.cache import CachedMovie
from app.services import settings_repo

logger = logging.getLogger(__name__)

BASE_URL = "https://tvtropes.org"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
CACHE_TTL = timedelta(days=90)
MAX_PAGES_PER_BATCH = 150
_request_lock = threading.Lock()
_last_request = 0.0


class CloudflareBlock(RuntimeError):
    """A challenge or rate-limit response requires an immediate batch halt."""


class TVTropesDisabled(RuntimeError):
    """TVTropes integration was not explicitly enabled."""


class BatchPageLimit(RuntimeError):
    """Internal control flow: the configured network-page budget is consumed."""


@dataclass(frozen=True)
class TropeLink:
    name: str
    slug: str
    mapped_slug: str | None
    url: str
    description: str = ""


@dataclass(frozen=True)
class ScrapeResult:
    work_url: str | None
    tropes: tuple[TropeLink, ...]
    cached: bool = False
    stopped_reason: str | None = None


@dataclass(frozen=True)
class Response:
    status_code: int
    headers: dict[str, str]
    text: str = ""


def enabled(session: Session) -> bool:
    override = settings_repo.get_overrides(session).get("tvtropes_enabled")
    if override is not None:
        return override.lower() == "true"
    return get_settings().tvtropes_enabled


def normalize_url(user_input: str, *, year: int | None = None) -> str:
    """Normalize a title or accept only an HTTPS TVTropes Film URL."""
    raw = user_input.strip()
    if raw.startswith(("http://", "https://")):
        parsed = urlparse(raw)
        if (
            parsed.scheme != "https"
            or parsed.netloc.lower() != urlparse(BASE_URL).netloc
            or not parsed.path.startswith("/pmwiki/pmwiki.php/Film/")
        ):
            raise ValueError("Only HTTPS TVTropes Film pages are accepted")
        return urlunparse(("https", parsed.netloc.lower(), parsed.path, "", "", ""))
    raw = raw.removeprefix("Film/")
    cleaned = re.sub(r"[^\w\s\-]", "", raw)
    parts = [part for part in re.split(r"[\s\-_]+", cleaned) if part]
    if not parts:
        raise ValueError("Invalid movie name provided")
    if len(parts) == 1 and not parts[0].islower():
        slug = parts[0]
    else:
        slug = "".join(part.capitalize() for part in parts)
    if year is not None:
        slug += str(year)
    return f"{BASE_URL}/pmwiki/pmwiki.php/Film/{quote(slug)}"


def _site_url(base_url: str, href: str) -> str | None:
    absolute = urljoin(base_url, href)
    parsed = urlparse(absolute)
    if parsed.scheme != "https" or parsed.netloc.lower() != urlparse(BASE_URL).netloc:
        return None
    return urlunparse(("https", parsed.netloc.lower(), parsed.path, "", "", ""))


def camel_case_slug(value: str) -> str:
    """Convert a TVTropes Main/CamelCase identifier to the taxonomy's kebab form."""
    value = value.rsplit("/", 1)[-1]
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", value)
    value = re.sub(r"([A-Z])([A-Z][a-z])", r"\1-\2", value)
    return re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()


def _year_matches(html: str, expected_year: int) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    header = soup.find(["h1", "title"])
    article = soup.find("div", id="main-article") or soup.find("div", class_="article-content")
    text = " ".join(
        (
            header.get_text(" ", strip=True) if header else "",
            (article or soup).get_text(" ", strip=True)[:800],
        )
    )
    years = [int(value) for value in re.findall(r"\b(18\d{2}|19\d{2}|20\d{2})\b", text)]
    return expected_year in years


def find_subpages(soup: BeautifulSoup, base_url: str) -> list[str]:
    article = soup.find("div", id="main-article") or soup.find("div", class_="article-content")
    if not article:
        return []
    text_pattern = re.compile(r"Tropes\s+[A-Za-z0-9]+\s+to\s+[A-Za-z0-9]+", re.IGNORECASE)
    href_pattern = re.compile(r"Tropes[A-Za-z0-9]+To[A-Za-z0-9]+", re.IGNORECASE)
    found: list[str] = []
    seen = {base_url}
    for anchor in article.find_all("a", href=True):
        href = str(anchor["href"])
        if not (text_pattern.search(anchor.get_text(" ", strip=True)) or href_pattern.search(href)):
            continue
        url = _site_url(base_url, href)
        if url is not None and url not in seen:
            found.append(url)
            seen.add(url)
    return found


def extract_trope_links(html: str, base_url: str = BASE_URL) -> tuple[TropeLink, ...]:
    soup = BeautifulSoup(html, "html.parser")
    root = (
        soup.find("div", id="main-article")
        or soup.find("div", class_="article-content")
        or soup.body
        or soup
    )
    if not root:
        return ()
    found: dict[tuple[str, str], TropeLink] = {}
    for item in root.find_all("li"):
        link = item.find(
            "a",
            href=lambda href: href and ("/pmwiki/pmwiki.php/Main/" in href or "/Main/" in href),
        )
        if link is None:
            continue
        href = str(link.get("href", ""))
        match = re.search(r"/Main/([^/?#]+)", href)
        url = _site_url(base_url, href)
        if match is None or url is None:
            continue
        name = match.group(1)
        slug = camel_case_slug(name)
        if not slug:
            continue
        li_clone = BeautifulSoup(str(item), "html.parser").find("li")
        if li_clone is None:
            continue
        for sub_list in li_clone.find_all(["ul", "ol"]):
            sub_list.decompose()
        full_text = re.sub(r"\s+", " ", li_clone.get_text(separator=" ", strip=True))
        label = re.sub(r"\s+", " ", link.get_text(" ", strip=True))
        description = full_text
        if label and description.lower().startswith(label.lower()):
            description = description[len(label) :].lstrip(" :-\u2013\u2014").strip()
        key = (slug, description[:60])
        found.setdefault(
            key,
            TropeLink(
                name=name,
                slug=slug,
                mapped_slug=normalize_trope(name),
                url=url,
                description=description,
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
            if isinstance(item, dict) and {"name", "slug", "mapped_slug", "url"} <= item.keys()
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
        min_interval: float | None = None,
        max_pages: int = MAX_PAGES_PER_BATCH,
        now=time.time,
        sleep=time.sleep,
        check_cancelled: Callable[[], None] | None = None,
    ) -> None:
        self.cache_dir = cache_dir or (get_settings().config_dir / "tvtropes")
        self.request = request or self._request
        self._session = None
        self.min_interval = min_interval
        self.now = now
        self.sleep = sleep
        self.check_cancelled = check_cancelled
        self.pages_fetched = 0
        self.max_pages = max_pages
        self.stopped_reason: str | None = None
        self._resolved_pages: dict[str, Response] = {}
        self._partial_pages: list[dict] = []
        self._partial_url: str | None = None

    def close(self) -> None:
        if self._session is not None:
            self._session.close()

    def _request(self, url: str, headers: dict[str, str]) -> Response:
        if self._session is None:
            self._session = curl_requests.Session(impersonate="chrome")
        response = self._session.get(url, headers=headers, timeout=15, allow_redirects=False)
        return Response(
            status_code=response.status_code,
            headers=dict(response.headers),
            text=response.text,
        )

    def _send(self, url: str, headers: dict[str, str] | None = None) -> Response:
        global _last_request
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc.lower() != urlparse(BASE_URL).netloc:
            raise ValueError("TVTropes client refuses non-TV Tropes URLs")
        if self.pages_fetched >= self.max_pages:
            raise BatchPageLimit("TVTropes batch page cap reached")
        if self.check_cancelled is not None:
            self.check_cancelled()
        with _request_lock:
            interval = (
                self.min_interval if self.min_interval is not None else random.uniform(1.0, 2.2)
            )
            wait = interval - (self.now() - _last_request)
            if wait > 0:
                self.sleep(wait)
            self.pages_fetched += 1
            try:
                response = self.request(
                    url,
                    {
                        "User-Agent": USER_AGENT,
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9",
                        **(headers or {}),
                    },
                )
            finally:
                _last_request = self.now()
        return response

    def _fetch(self, url: str, headers: dict[str, str] | None = None) -> Response:
        response = self._send(url, headers)
        if response.status_code in (403, 429) or _is_challenge(response.text):
            raise CloudflareBlock(f"TVTropes blocked the batch (HTTP {response.status_code})")
        if 300 <= response.status_code < 400 and response.status_code != 304:
            raise RuntimeError("Unexpected redirect from TVTropes; refusing to follow it")
        return response

    def _work_candidates(self, movie: CachedMovie) -> list[str]:
        if not movie.title.strip():
            return []
        year = (
            int(movie.release_date[:4])
            if movie.release_date and movie.release_date[:4].isdigit()
            else None
        )
        candidates = [normalize_url(movie.title)]
        if year is not None:
            candidates.append(normalize_url(movie.title, year=year))
        return candidates

    def _resolve_work_url(self, movie: CachedMovie) -> str | None:
        year = (
            int(movie.release_date[:4])
            if movie.release_date and movie.release_date[:4].isdigit()
            else None
        )
        for url in self._work_candidates(movie):
            response = self._fetch(url)
            if response.status_code == 404:
                continue
            if response.status_code != 200:
                raise RuntimeError(f"TVTropes returned HTTP {response.status_code}")
            soup = BeautifulSoup(response.text, "html.parser")
            heading = soup.find("h1") or soup.find("title")
            article = soup.find("div", id="main-article")
            disambiguation = (
                "disambiguation"
                in " ".join(
                    (
                        heading.get_text(" ", strip=True) if heading else "",
                        article.get_text(" ", strip=True)[:300] if article else "",
                    )
                ).lower()
            )
            if not disambiguation and (year is None or _year_matches(response.text, year)):
                self._resolved_pages[url] = response
                return url
        return None

    def sync_movie(self, movie: CachedMovie, *, enabled: bool) -> ScrapeResult:
        if self.stopped_reason:
            return ScrapeResult(None, (), stopped_reason=self.stopped_reason)
        self._partial_pages = []
        self._partial_url = None
        try:
            return self._sync_movie(movie, enabled=enabled)
        except (BatchPageLimit, CloudflareBlock) as exc:
            self.stopped_reason = (
                "page_cap_reached"
                if isinstance(exc, BatchPageLimit)
                else "rate_limited_or_challenge"
            )
            logger.info("TVTropes batch stopped after %s pages: %s", self.pages_fetched, exc)
            return ScrapeResult(
                self._partial_url,
                _flatten_page_tropes(self._partial_pages),
                stopped_reason=self.stopped_reason,
            )

    def _sync_movie(self, movie: CachedMovie, *, enabled: bool) -> ScrapeResult:
        if not enabled:
            raise TVTropesDisabled("TVTropes ingestion requires explicit opt-in")
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
        old_pages = {
            page["url"]: page for page in (data or {}).get("pages", []) if isinstance(page, dict)
        }
        if data is not None and not old_pages and data.get("work_url") == work_url:
            old_pages[work_url] = {
                "url": work_url,
                "etag": data.get("etag"),
                "last_modified": data.get("last_modified"),
                "tropes": data.get("tropes", []),
            }
        main_page, main_html = self._fetch_page(work_url, old_pages.get(work_url))
        self._partial_url = work_url
        self._partial_pages = [main_page]
        if main_html is None:
            subpage_urls = [url for url in old_pages if url != work_url]
        else:
            subpage_urls = find_subpages(BeautifulSoup(main_html, "html.parser"), work_url)
        pages = [main_page]
        for subpage_url in subpage_urls[:6]:
            page, _ = self._fetch_page(subpage_url, old_pages.get(subpage_url))
            pages.append(page)
            self._partial_pages.append(page)
        links = _flatten_page_tropes(pages)
        data = {
            "fetched_at": now.isoformat(),
            "work_url": work_url,
            "etag": main_page.get("etag"),
            "last_modified": main_page.get("last_modified"),
            "pages": pages,
            "tropes": [link.__dict__ for link in links],
        }
        self._save(path, data)
        movie.tvtropes_work_url = data.get("work_url")
        return _cached_result(data, cached=False)

    def _fetch_page(self, url: str, previous: dict | None) -> tuple[dict, str | None]:
        headers = {}
        if previous and previous.get("etag"):
            headers["If-None-Match"] = previous["etag"]
        if previous and previous.get("last_modified"):
            headers["If-Modified-Since"] = previous["last_modified"]
        response = self._resolved_pages.pop(url, None) or self._fetch(url, headers)
        if response.status_code == 304 and previous is not None:
            return previous, None
        if response.status_code == 404:
            return {"url": url, "tropes": []}, ""
        if response.status_code != 200:
            raise RuntimeError(f"TVTropes returned HTTP {response.status_code}")
        links = extract_trope_links(response.text, url)
        return (
            {
                "url": url,
                "etag": response.headers.get("ETag") or response.headers.get("etag"),
                "last_modified": response.headers.get("Last-Modified")
                or response.headers.get("last-modified"),
                "tropes": [link.__dict__ for link in links],
            },
            response.text,
        )

    @staticmethod
    def _save(path: Path, data: dict) -> None:
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        temporary.replace(path)


def _is_challenge(html: str) -> bool:
    lowered = html.lower()
    return any(
        phrase in lowered
        for phrase in (
            "just a moment...",
            "cf-challenge",
            "cf-browser-verification",
            "cloudflare ray id",
            "attention required",
        )
    )


def _flatten_page_tropes(pages: list[dict]) -> tuple[TropeLink, ...]:
    found: dict[tuple[str, str], TropeLink] = {}
    for page in pages:
        for item in page.get("tropes", []):
            if not isinstance(item, dict):
                continue
            try:
                link = TropeLink(**item)
            except TypeError:
                continue
            found.setdefault((link.slug, link.description[:60]), link)
    return tuple(found.values())


def fetch_movie_tropes(
    movie: CachedMovie,
    *,
    enabled: bool,
    check_cancelled: Callable[[], None] | None = None,
) -> ScrapeResult:
    """Use the default cache/client; caller must run this blocking operation in a worker thread."""
    client = TVTropesClient(check_cancelled=check_cancelled)
    try:
        return client.sync_movie(movie, enabled=enabled)
    finally:
        client.close()
