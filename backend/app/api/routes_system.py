from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlmodel import Session, func, select

from app.api.deps import get_current_admin, get_current_user
from app.config import get_settings
from app.db import get_session
from app.models.cache import CachedActor, CachedMovie, CachedMovieCast
from app.models.user import User
from app.services import cache_flush

router = APIRouter(tags=["system"])


class CacheStats(BaseModel):
    cached_movies: int
    cached_actors: int
    cached_cast_edges: int
    db_size_bytes: int | None = None


class CacheFlushResult(BaseModel):
    max_age_days: int
    ratings_removed: int
    movies_cast_reset: int
    cast_edges_removed: int
    actors_marked_stale: int
    actors_removed: int
    vacuumed: bool
    db_size_before: int | None = None
    db_size_after: int | None = None


@router.get("/health")
def health() -> dict:
    """Liveness/readiness probe used by the Docker healthcheck and manual checks."""
    try:
        from sqlalchemy import text

        from app.db import engine

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:  # noqa: BLE001 - any DB failure should report unhealthy, not crash
        db_ok = False

    rss_bytes = _current_rss_bytes()

    return {"status": "ok", "db_ok": db_ok, "rss_bytes": rss_bytes}


@router.get("/system/cache/stats", response_model=CacheStats)
def cache_stats(
    session: Session = Depends(get_session),
    _current_user: User = Depends(get_current_user),
) -> CacheStats:
    """Read-only counts for the JIT adjacency cache - no pruning in v1."""
    movies = session.exec(select(func.count()).select_from(CachedMovie)).one()
    actors = session.exec(select(func.count()).select_from(CachedActor)).one()
    cast_edges = session.exec(
        select(func.count()).select_from(CachedMovieCast)).one()

    db_path = get_settings().database_path
    db_size_bytes = db_path.stat().st_size if db_path.exists() else None

    return CacheStats(
        cached_movies=movies,
        cached_actors=actors,
        cached_cast_edges=cast_edges,
        db_size_bytes=db_size_bytes,
    )


@router.post("/system/cache/flush", response_model=CacheFlushResult)
def flush_cache(
    max_age_days: int = Query(default=7, ge=1, le=365),
    vacuum: bool = True,
    session: Session = Depends(get_session),
    _admin: User = Depends(get_current_admin),
) -> CacheFlushResult:
    """Drops cache entries older than `max_age_days` (they re-fetch lazily from
    TMDB/OMDb on next use) and optionally VACUUMs so the disk space comes back."""
    db_path = get_settings().database_path

    def size() -> int | None:
        return db_path.stat().st_size if db_path.exists() else None

    before = size()
    counts = cache_flush.flush_stale_cache(session, max_age_days)
    vacuumed = cache_flush.vacuum(session.get_bind()) if vacuum else False
    return CacheFlushResult(
        max_age_days=max_age_days, **counts, vacuumed=vacuumed,
        db_size_before=before, db_size_after=size())


def _current_rss_bytes() -> int | None:
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except FileNotFoundError:
        return None
    return None
