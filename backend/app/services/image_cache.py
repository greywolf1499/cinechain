"""JIT local image cache behind `GET /api/images/proxy`.

Letterboxd/curator images are fetched on first request, stored under
`config_dir/cache_images`, and re-served from disk afterwards, so the browser
never talks to Letterboxd directly (privacy) and covers survive link rot.
There is deliberately no background worker: freshness is checked and
revalidated (ETag / Last-Modified) lazily during the request, and the cache
size cap is enforced opportunistically right after a write.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

ALLOWED_HOST_SUFFIXES = ("ltrbxd.com", "letterboxd.com")
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_CACHE_BYTES = 256 * 1024 * 1024
MAX_REDIRECTS = 3
MIN_TTL_SECONDS = 60 * 60
MAX_TTL_SECONDS = 30 * 24 * 60 * 60
PRUNE_EVERY_N_WRITES = 25
# Cap concurrent upstream fetches so a grid of covers can't hammer the CDN.
_UPSTREAM_SEMAPHORE = asyncio.Semaphore(4)
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)

_writes_since_prune = 0


class ImageProxyError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass
class CachedImage:
    path: Path
    content_type: str


def cache_dir() -> Path:
    path = get_settings().config_dir / "cache_images"
    path.mkdir(parents=True, exist_ok=True)
    return path


def validate_image_url(url: str) -> str:
    """SSRF guard: https, no credentials/custom port, and only Letterboxd-owned hosts."""
    try:
        parsed = urlparse(url.strip())
        port = parsed.port
    except ValueError as exc:
        raise ImageProxyError(400, "Invalid image URL") from exc
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.username or parsed.password or port not in (None, 443):
        raise ImageProxyError(400, "Only plain https image URLs are supported")
    if not any(host == suffix or host.endswith(f".{suffix}") for suffix in ALLOWED_HOST_SUFFIXES):
        raise ImageProxyError(400, "Image host is not allowed")
    return parsed.geturl()


def _key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _paths(url: str) -> tuple[Path, Path]:
    key = _key(url)
    directory = cache_dir()
    return directory / f"{key}.img", directory / f"{key}.json"


def _read_meta(meta_path: Path) -> dict | None:
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _ttl_seconds(headers: httpx.Headers) -> int:
    """Freshness lifetime from Cache-Control/Expires, clamped so even
    `no-store`/`no-cache` images are cached briefly and then revalidated."""
    cache_control = headers.get("cache-control", "").lower()
    ttl: int | None = None
    if "no-store" in cache_control or "no-cache" in cache_control:
        ttl = 0
    else:
        match = re.search(r"max-age=(\d+)", cache_control)
        if match:
            ttl = int(match.group(1))
        elif headers.get("expires"):
            try:
                expires = parsedate_to_datetime(headers["expires"]).timestamp()
                ttl = int(expires - time.time())
            except (TypeError, ValueError):
                ttl = None
    if ttl is None:
        ttl = MIN_TTL_SECONDS * 24
    return max(MIN_TTL_SECONDS, min(ttl, MAX_TTL_SECONDS))


async def _fetch(
    client: httpx.AsyncClient, url: str, validators: dict[str, str]
) -> httpx.Response | tuple[httpx.Response, bytes]:
    """GET with manual redirect handling (every hop re-validated) and a hard body cap."""
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "image/avif,image/webp,image/*;q=0.8",
        **validators,
    }
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        async with client.stream(
            "GET", current, headers=headers, follow_redirects=False
        ) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location:
                    raise ImageProxyError(502, "Upstream redirect without a location")
                current = validate_image_url(urljoin(current, location))
                continue
            if response.status_code == 304:
                return response
            if response.status_code != 200:
                raise ImageProxyError(502, f"Upstream returned {response.status_code}")
            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > MAX_IMAGE_BYTES:
                raise ImageProxyError(502, "Image is too large")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_IMAGE_BYTES:
                    raise ImageProxyError(502, "Image is too large")
            return response, bytes(body)
    raise ImageProxyError(502, "Too many upstream redirects")


async def get_image(client: httpx.AsyncClient, url: str) -> CachedImage:
    """Returns the on-disk copy of `url`, fetching/revalidating just-in-time."""
    url = validate_image_url(url)
    image_path, meta_path = _paths(url)
    meta = _read_meta(meta_path) if image_path.exists() else None
    now = time.time()

    if meta and meta.get("expires_at", 0) > now:
        return CachedImage(image_path, meta["content_type"])

    validators: dict[str, str] = {}
    if meta:
        if meta.get("etag"):
            validators["If-None-Match"] = meta["etag"]
        if meta.get("last_modified"):
            validators["If-Modified-Since"] = meta["last_modified"]

    try:
        async with _UPSTREAM_SEMAPHORE:
            result = await _fetch(client, url, validators)
    except (ImageProxyError, httpx.HTTPError) as exc:
        if meta:  # stale copy beats a broken image when the CDN is unreachable
            logger.warning("Image revalidation failed for %s (%s); serving stale copy", url, exc)
            return CachedImage(image_path, meta["content_type"])
        if isinstance(exc, ImageProxyError):
            raise
        raise ImageProxyError(502, "Could not fetch the image") from exc

    if isinstance(result, httpx.Response):  # 304 Not Modified
        assert meta is not None
        meta["expires_at"] = now + _ttl_seconds(result.headers)
        _write_atomic(meta_path, json.dumps(meta).encode("utf-8"))
        return CachedImage(image_path, meta["content_type"])

    response, body = result
    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if not content_type.startswith("image/") or content_type == "image/svg+xml":
        raise ImageProxyError(502, "Upstream did not return a raster image")

    _write_atomic(image_path, body)
    _write_atomic(
        meta_path,
        json.dumps(
            {
                "url": url,
                "content_type": content_type,
                "etag": response.headers.get("etag"),
                "last_modified": response.headers.get("last-modified"),
                "fetched_at": now,
                "expires_at": now + _ttl_seconds(response.headers),
            }
        ).encode("utf-8"),
    )
    _maybe_prune()
    return CachedImage(image_path, content_type)


def _maybe_prune() -> None:
    global _writes_since_prune
    _writes_since_prune += 1
    if _writes_since_prune < PRUNE_EVERY_N_WRITES:
        return
    _writes_since_prune = 0
    prune_cache()


def prune_cache(max_bytes: int = MAX_CACHE_BYTES) -> int:
    """Evicts least-recently-fetched images until the cache fits `max_bytes`."""
    entries: list[tuple[float, int, Path]] = []
    total = 0
    for image in cache_dir().glob("*.img"):
        try:
            stat = image.stat()
        except OSError:
            continue
        total += stat.st_size
        entries.append((stat.st_mtime, stat.st_size, image))
    removed = 0
    for _, size, image in sorted(entries):
        if total <= max_bytes:
            break
        image.unlink(missing_ok=True)
        image.with_suffix(".json").unlink(missing_ok=True)
        total -= size
        removed += 1
    return removed
