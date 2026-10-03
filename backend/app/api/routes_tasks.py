"""Task status API: poll (`GET /tasks`, `GET /tasks/{id}`) or stream (`GET /tasks/stream`).

The stream is a plain SSE response that re-reads SQLite once a second for as
long as the client stays connected - it lives and dies with the request, so
there is no broadcaster daemon. Admins see every task; everyone else only the
tasks they started.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from app.api.deps import get_current_user
from app.db import get_session
from app.models.system import SystemTask
from app.models.user import User
from app.services.task_runner import ACTIVE_STATUSES
from app.utils.ids import utcnow

router = APIRouter(prefix="/tasks", tags=["tasks"])

STREAM_POLL_SECONDS = 1.0
STREAM_HEARTBEAT_SECONDS = 15.0
STREAM_BACKFILL = timedelta(minutes=10)


class TaskOut(BaseModel):
    id: str
    name: str
    status: str
    progress_data: dict[str, Any] | None
    error: str | None
    user_id: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_model(cls, task: SystemTask) -> TaskOut:
        return cls(
            id=task.id,
            name=task.name,
            status=task.status,
            progress_data=task.progress_data,
            error=task.error,
            user_id=task.user_id,
            created_at=_iso(task.created_at),
            updated_at=_iso(task.updated_at),
        )


def _iso(value: datetime) -> str:
    # SQLite hands datetimes back naive; they are always stored as UTC.
    return value.replace(tzinfo=None).isoformat() + "Z"


def _visible(statement: Any, user: User) -> Any:
    return statement if user.is_admin else statement.where(SystemTask.user_id == user.id)


@router.get("", response_model=list[TaskOut])
def list_tasks(
    active: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[TaskOut]:
    statement = select(SystemTask)
    if active:
        statement = statement.where(col(SystemTask.status).in_(ACTIVE_STATUSES))
    rows = session.exec(
        _visible(statement, current_user).order_by(col(SystemTask.created_at).desc()).limit(limit)
    ).all()
    return [TaskOut.from_model(task) for task in rows]


def _load_updated_since(engine: Engine, user: User, since: datetime) -> list[SystemTask]:
    with Session(engine) as session:
        statement = select(SystemTask).where(
            (col(SystemTask.updated_at) >= since) | col(SystemTask.status).in_(ACTIVE_STATUSES)
        )
        return list(session.exec(_visible(statement, user).order_by(SystemTask.updated_at)).all())


@router.get("/stream")
async def stream_tasks(
    request: Request,
    once: bool = Query(default=False, description="Send the current snapshot, then close."),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """SSE: a `task` event for every task whose state changed (full snapshot first)."""
    engine = session.get_bind()
    user = current_user

    async def event_source():
        seen: dict[str, datetime] = {}
        since = utcnow() - STREAM_BACKFILL
        last_beat = time.monotonic()
        while True:
            poll_started = utcnow()
            tasks = await anyio.to_thread.run_sync(_load_updated_since, engine, user, since)
            for task in tasks:
                if seen.get(task.id) != task.updated_at:
                    seen[task.id] = task.updated_at
                    last_beat = time.monotonic()
                    yield f"event: task\ndata: {TaskOut.from_model(task).model_dump_json()}\n\n"
            # Overlap the window slightly so a write landing mid-poll is never missed.
            since = poll_started - timedelta(seconds=2)
            if len(seen) > 500:
                seen = {k: v for k, v in seen.items() if v >= since}
            if once:
                yield "event: done\ndata: {}\n\n"
                return
            if time.monotonic() - last_beat > STREAM_HEARTBEAT_SECONDS:
                last_beat = time.monotonic()
                yield ": keep-alive\n\n"
            if await request.is_disconnected():
                return
            await anyio.sleep(STREAM_POLL_SECONDS)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{task_id}", response_model=TaskOut)
def get_task(
    task_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> TaskOut:
    task = session.exec(
        _visible(select(SystemTask).where(SystemTask.id == task_id), current_user)
    ).first()
    if task is None:  # 404 for both "missing" and "someone else's", like runs
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskOut.from_model(task)
