"""Local image-cache proxy: the browser asks us for Letterboxd/curator images
instead of hotlinking them (privacy + link-rot protection)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

from app.api.deps import get_current_user
from app.models.user import User
from app.services import image_cache

router = APIRouter(prefix="/images", tags=["images"])


@router.get("/proxy")
async def proxy_image(
    request: Request,
    url: str = Query(..., min_length=1, max_length=2048),
    _current_user: User = Depends(get_current_user),
) -> FileResponse:
    try:
        cached = await image_cache.get_image(request.app.state.http_client, url)
    except image_cache.ImageProxyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return FileResponse(
        cached.path,
        media_type=cached.content_type,
        headers={"Cache-Control": "private, max-age=86400"},
    )
