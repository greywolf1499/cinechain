"""Shared contracts and HTTP plumbing for homelab integration clients.

Jellyfin implements `MediaServerClient`; Radarr and Seerr implement
`RequestClient`. The *arr-style clients (Radarr/Seerr) authenticate with an
`X-Api-Key` header and raise `IntegrationError` (never raw httpx errors) so
routes can translate failures into a clean HTTP status.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from app.schemas.integrations import JellyfinItemSummary


class MediaServerClient(Protocol):
    async def lookup_movies(self, tmdb_ids: list[int]) -> dict[int, JellyfinItemSummary]: ...

    async def check_health(self) -> dict: ...


class RequestClient(Protocol):
    @property
    def enabled(self) -> bool: ...

    async def check_health(self) -> dict: ...


class IntegrationError(Exception):
    def __init__(self, detail: str, status_code: int = 502) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200] or f"HTTP {response.status_code}"
    if isinstance(body, list) and body:
        body = body[0]
    if isinstance(body, dict):
        for key in ("errorMessage", "message", "error"):
            if body.get(key):
                return str(body[key])
    return f"HTTP {response.status_code}"


async def request_json(
    client: httpx.AsyncClient, method: str, url: str, api_key: str, **kwargs: Any
) -> Any:
    """One authenticated call; returns parsed JSON (None for an empty body)."""
    try:
        response = await client.request(
            method, url, headers={"X-Api-Key": api_key}, timeout=10.0, **kwargs
        )
    except httpx.HTTPError as exc:
        raise IntegrationError(f"Service unreachable: {exc}", 502) from exc

    if response.is_success:
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise IntegrationError("Service returned a non-JSON response", 502) from exc

    detail = _error_detail(response)
    if response.status_code in (401, 403):
        raise IntegrationError("The service rejected the API key", 502)
    if response.status_code == 409 or "already" in detail.lower():
        raise IntegrationError(detail, 409)
    if response.status_code == 404:
        raise IntegrationError(detail, 404)
    if 400 <= response.status_code < 500:
        raise IntegrationError(detail, 400)
    raise IntegrationError(f"Service error: {detail}", 502)
