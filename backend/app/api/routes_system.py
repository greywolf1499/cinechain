from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, col, func, select

from app.api.deps import get_current_admin, get_current_user
from app.api.routes_tasks import TaskOut
from app.config import get_settings
from app.db import get_session
from app.facets.registry import FAMILY_VERSIONS
from app.models.cache import CachedActor, CachedMovie, CachedMovieCast
from app.models.system import SystemTask
from app.models.user import User
from app.services import cache_flush, data_spa, task_runner

router = APIRouter(tags=["system"])


class SpaRequest(BaseModel):
    batch_cap: int = Field(default=200, ge=1, le=2000)


@router.get("/system/cache/health")
def cache_health(
    session: Session = Depends(get_session),
    _current_user: User = Depends(get_current_user),
) -> dict:
    return data_spa.health(session)


@router.post("/system/spa/{treatment}", response_model=TaskOut)
def spa_treatment(
    treatment: str,
    background_tasks: BackgroundTasks,
    payload: SpaRequest | None = None,
    session: Session = Depends(get_session),
    admin: User = Depends(get_current_admin),
) -> TaskOut:
    if treatment not in (*data_spa.TREATMENTS, "fix_all"):
        raise HTTPException(status_code=404, detail="Unknown spa treatment")
    return TaskOut.from_model(
        data_spa.submit(
            background_tasks,
            session,
            treatment,
            admin.id,
            batch_cap=payload.batch_cap if payload else 200,
        )
    )


@router.post("/system/facets/backfill", response_model=TaskOut)
def backfill_facets(
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    admin: User = Depends(get_current_admin),
) -> TaskOut:
    dedupe_key = "facets_backfill:" + ",".join(
        f"{family}:{version}" for family, version in FAMILY_VERSIONS.items()
    )
    previous = session.exec(
        select(SystemTask)
        .where(SystemTask.dedupe_key == dedupe_key)
        .order_by(col(SystemTask.created_at).desc())
    ).first()
    resume = (
        dict(previous.progress_data or {})
        if previous is not None and previous.status == task_runner.FAILED
        else None
    )
    task, created = task_runner.submit_task(
        background_tasks,
        session,
        "facets_backfill",
        task_runner.facets_backfill,
        user_id=admin.id,
        dedupe_key=dedupe_key,
        label="Compute cached movie facets",
        link="/settings",
    )
    if created and resume is not None:
        task.progress_data = {
            **(task.progress_data or {}),
            "cursor": resume.get("cursor", 0),
            "processed": resume.get("processed", 0),
        }
        session.add(task)
        session.commit()
    return TaskOut.from_model(task)


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
    cast_edges = session.exec(select(func.count()).select_from(CachedMovieCast)).one()

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
        max_age_days=max_age_days,
        **counts,
        vacuumed=vacuumed,
        db_size_before=before,
        db_size_after=size(),
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
