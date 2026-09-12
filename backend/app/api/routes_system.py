from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session, func, select

from app.api.deps import get_current_user
from app.config import get_settings
from app.db import get_session
from app.models.cache import CachedActor, CachedMovie, CachedMovieCast
from app.models.user import User

router = APIRouter(tags=["system"])


class CacheStats(BaseModel):
    cached_movies: int
    cached_actors: int
    cached_cast_edges: int
    db_size_bytes: int | None = None


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


def _current_rss_bytes() -> int | None:
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except FileNotFoundError:
        return None
    return None
