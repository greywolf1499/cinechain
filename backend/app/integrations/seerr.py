"""Overseerr/Jellyseerr client stub - signature-complete for `RequestClient`, wired up in v1.1."""

from __future__ import annotations

from fastapi import HTTPException, status

from app.config import Settings, get_settings


class SeerrClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def enabled(self) -> bool:
        return bool(self._settings.seerr_url)

    async def request_movie(self, tmdb_id: int, title: str) -> bool:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Seerr requests are not implemented yet",
        )
