"""Passport: Letterboxd history import (CSV / RSS) and cross-run aggregation."""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any

import anyio
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.api.deps import get_current_user, get_tmdb_client
from app.api.routes_curated import _error_payload
from app.api.routes_tasks import TaskOut
from app.config import get_settings
from app.db import get_session
from app.models.user import User
from app.schemas.passport import PassportOut
from app.services import cache_repo, letterboxd, passport, passport_import, task_runner
from app.services.task_runner import TaskContext
from app.services.tmdb import TMDBClient
from app.services.tmdb_backoff import fetch_with_backoff

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/passport", tags=["passport"])

UPLOAD_CHUNK_BYTES = 1024 * 1024


class RssImportRequest(BaseModel):
    letterboxd_username: str = Field(min_length=1, max_length=64)


def _import_error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, passport_import.ImportAborted):
        return {"code": "import_aborted", "message": str(exc)}
    return _error_payload(exc)


def _require_tmdb_key(tmdb: TMDBClient) -> None:
    if not (tmdb._overrides.get("tmdb_api_key") or get_settings().tmdb_api_key):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="TMDB API key is not configured - add it under Settings > Integrations.",
        )


def _imports_dir() -> Path:
    path = get_settings().config_dir / "imports"
    path.mkdir(parents=True, exist_ok=True)
    return path


@router.post("/import/csv", status_code=status.HTTP_202_ACCEPTED, response_model=TaskOut)
async def import_csv(
    background_tasks: BackgroundTasks,
    file: UploadFile,
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    current_user: User = Depends(get_current_user),
) -> TaskOut:
    """Accepts a Letterboxd diary CSV (multipart field `file`), returns the `SystemTask` immediately and
    imports in the background. The upload is spooled to disk in 1MB chunks (the
    request's file object is closed once the response is sent)."""
    _require_tmdb_key(tmdb)
    path = _imports_dir() / f"{uuid.uuid4().hex}.csv"
    size = 0
    try:
        with path.open("wb") as out:
            while chunk := await file.read(UPLOAD_CHUNK_BYTES):
                size += len(chunk)
                if size > passport_import.MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        413,
                        detail="CSV is larger than 25MB - upload diary.csv, not the whole export.",
                    )
                await anyio.to_thread.run_sync(out.write, chunk)
        try:
            await anyio.to_thread.run_sync(passport_import.validate_csv_header, path)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except BaseException:
        path.unlink(missing_ok=True)
        raise

    task, created = task_runner.submit_task(
        background_tasks,
        session,
        "diary_import_csv",
        passport_import.csv_job(path, tmdb, current_user.id),
        user_id=current_user.id,
        dedupe_key=f"diary_import:{current_user.id}",
        label=f"Importing {file.filename or 'diary.csv'}",
        link="/passport",
        describe_error=_import_error,
    )
    if not created:  # another import is already running for this user
        path.unlink(missing_ok=True)
    else:
        # A cancellation while pending skips the job's own finally block.
        background_tasks.add_task(path.unlink, missing_ok=True)
    return TaskOut.from_model(task)


@router.post("/import/rss", status_code=status.HTTP_202_ACCEPTED, response_model=TaskOut)
async def import_rss(
    payload: RssImportRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    current_user: User = Depends(get_current_user),
) -> TaskOut:
    """Imports the (public) RSS diary of a Letterboxd account - the latest ~100 entries."""
    _require_tmdb_key(tmdb)
    try:
        username = letterboxd.clean_username(payload.letterboxd_username)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    task, _ = task_runner.submit_task(
        background_tasks,
        session,
        "diary_import_rss",
        passport_import.rss_job(username, tmdb, current_user.id),
        user_id=current_user.id,
        dedupe_key=f"diary_import:{current_user.id}",
        label=f"Importing {username}'s RSS diary",
        link="/passport",
        describe_error=_import_error,
    )
    return TaskOut.from_model(task)


@router.get("/me", response_model=PassportOut)
def my_passport(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> PassportOut:
    return passport.build_passport(session, current_user.id)


@router.post("/backfill-directors", status_code=status.HTTP_202_ACCEPTED, response_model=TaskOut)
def backfill_directors(
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    current_user: User = Depends(get_current_user),
) -> TaskOut:
    """Looks up directors for watched films that have none cached yet (e.g. films
    logged in ordinary runs), so `top_directors` covers the whole history."""
    _require_tmdb_key(tmdb)
    user_id = current_user.id
    missing = passport.movie_ids_missing_directors(session, user_id)

    async def work(ctx: TaskContext) -> dict[str, Any]:
        done = failed = 0
        with ctx.session() as db:
            for index, movie_id in enumerate(missing, start=1):
                await ctx.acheck_cancelled()
                try:
                    found = await fetch_with_backoff(
                        lambda movie_id=movie_id: cache_repo.get_movie_directors(
                            db, tmdb, movie_id
                        ),
                        time.monotonic() + passport_import.PER_CALL_DEADLINE_SECONDS,
                        None,
                    )
                    done += 0 if found is None else 1  # None = the film is gone from TMDB
                    failed += 1 if found is None else 0
                except Exception:  # noqa: BLE001 - one bad film must not stop the backfill
                    failed += 1
                if index % 10 == 0 or index == len(missing):
                    await ctx.aprogress(
                        {"stage": "directors", "current": index, "total": len(missing)}
                    )
        return {"looked_up": done, "failed": failed, "total": len(missing)}

    task, _ = task_runner.submit_task(
        background_tasks,
        session,
        "passport_backfill_directors",
        work,
        user_id=user_id,
        dedupe_key=f"passport_backfill_directors:{user_id}",
        label=f"Looking up directors for {len(missing)} films",
        link="/passport",
    )
    return TaskOut.from_model(task)
