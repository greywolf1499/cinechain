"""Zero-daemon task runner: FastAPI `BackgroundTasks` + a SQLite row per job.

No Celery, no Redis, no broker, no scheduler. A request calls `submit_task`,
which inserts a `SystemTask` row and registers the work with the request's own
`BackgroundTasks`; the HTTP response (carrying the task id) is sent right away
and the work then runs in a Starlette worker thread, writing its progress back
to the same row. Clients poll `GET /api/tasks/{id}` or stream `GET /api/tasks/stream`.

Trade-off: a job lives in the API process, so a restart interrupts it. Startup
marks such orphans `failed` (`fail_interrupted_tasks`) instead of leaving them
"running" forever; scrapers checkpoint, so re-running resumes where it stopped.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import anyio
from fastapi import BackgroundTasks
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, delete, select

from app.models.system import SystemTask
from app.utils.ids import utcnow

logger = logging.getLogger(__name__)

PENDING, RUNNING, COMPLETED, FAILED = "pending", "running", "completed", "failed"
ACTIVE_STATUSES = (PENDING, RUNNING)

# Heavy jobs hold a worker thread for minutes; cap them so scrapes can never
# starve the thread pool that ordinary requests use for DB access.
MAX_CONCURRENT_TASKS = 2
PROGRESS_FLUSH_SECONDS = 0.5
RETENTION = timedelta(days=7)

_slots = threading.BoundedSemaphore(MAX_CONCURRENT_TASKS)

TaskWork = Callable[["TaskContext"], dict[str, Any] | None | Awaitable[dict[str, Any] | None]]
ErrorDescriber = Callable[[Exception], dict[str, Any]]


class TaskContext:
    """Handed to the job: report progress and open short-lived DB sessions.

    The job must not use the request's session (it is closed once the response
    is sent); `session()` opens a fresh one on the same engine.
    """

    def __init__(self, engine: Engine, task_id: str, initial: dict[str, Any] | None = None) -> None:
        self._engine = engine
        self.task_id = task_id
        self._data: dict[str, Any] = dict(initial or {})
        self._last_flush = 0.0
        self._lock = threading.Lock()

    @property
    def engine(self) -> Engine:
        return self._engine

    def session(self) -> Session:
        return Session(self._engine)

    def progress(self, payload: dict[str, Any]) -> None:
        """Thread-safe and throttled, so a chatty scraper can't hammer SQLite."""
        with self._lock:
            self._data["progress"] = payload
            if time.monotonic() - self._last_flush < PROGRESS_FLUSH_SECONDS:
                return
            self._write()

    def flush(self, **fields: Any) -> None:
        with self._lock:
            self._write(**fields)

    async def aprogress(self, payload: dict[str, Any]) -> None:
        """`progress` for async jobs: the (throttled) SQLite write runs off the event loop."""
        await anyio.to_thread.run_sync(self.progress, payload)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value

    def _write(self, **fields: Any) -> None:
        with Session(self._engine) as session:
            task = session.get(SystemTask, self.task_id)
            if task is None:  # pruned or deleted underneath us
                return
            task.progress_data = dict(self._data)
            for key, value in fields.items():
                setattr(task, key, value)
            task.updated_at = utcnow()
            session.add(task)
            session.commit()
        self._last_flush = time.monotonic()


def submit_task(
    background_tasks: BackgroundTasks,
    session: Session,
    name: str,
    work: TaskWork,
    *,
    user_id: str | None = None,
    dedupe_key: str | None = None,
    label: str | None = None,
    describe_error: ErrorDescriber | None = None,
) -> tuple[SystemTask, bool]:
    """Creates the task row and schedules `work`; returns `(task, created)`.

    If a task with the same `dedupe_key` is already pending/running, that one is
    returned (`created=False`) and nothing new is scheduled.
    """
    if dedupe_key is not None:
        existing = session.exec(
            select(SystemTask).where(
                SystemTask.dedupe_key == dedupe_key, col(SystemTask.status).in_(ACTIVE_STATUSES)
            )
        ).first()
        if existing is not None:
            return existing, False

    prune_finished(session)
    task = SystemTask(
        name=name,
        status=PENDING,
        user_id=user_id,
        dedupe_key=dedupe_key,
        progress_data={"label": label} if label else {},
    )
    session.add(task)
    session.commit()
    session.refresh(task)

    # A sync `work` runs in a Starlette worker thread, so blocking scrapers never
    # stall the event loop. An `async def` work runs on the event loop itself - it
    # is for jobs that share the app's async clients (e.g. TMDBClient) and must
    # `await` (and use `ctx.aprogress`) rather than block.
    runner = _execute_async if inspect.iscoroutinefunction(work) else _execute
    background_tasks.add_task(runner, session.get_bind(), task.id, work, describe_error)
    return task, True


def _load_context(engine: Engine, task_id: str) -> tuple[str, TaskContext] | None:
    with Session(engine) as session:
        task = session.get(SystemTask, task_id)
        if task is None:
            return None
        return task.name, TaskContext(engine, task_id, task.progress_data)


def _record_failure(
    ctx: TaskContext, name: str, exc: Exception, describe_error: ErrorDescriber | None
) -> None:
    logger.error("Task %s (%s) failed", ctx.task_id, name, exc_info=exc)
    described = describe_error(exc) if describe_error else {}
    ctx.set("error", {"code": "task_failed", "message": str(exc), **described})
    ctx.flush(status=FAILED, error=str(exc))


def _record_success(ctx: TaskContext, result: dict[str, Any] | None) -> None:
    if result is not None:
        ctx.set("result", result)
    ctx.flush(status=COMPLETED)


def _execute(
    engine: Engine, task_id: str, work: TaskWork, describe_error: ErrorDescriber | None
) -> None:
    loaded = _load_context(engine, task_id)
    if loaded is None:
        return
    name, ctx = loaded
    with _slots:  # waits (status stays "pending") until a worker slot frees up
        ctx.flush(status=RUNNING)
        try:
            result = work(ctx)
        except Exception as exc:  # noqa: BLE001 - recorded on the task row, never raised into the worker
            _record_failure(ctx, name, exc, describe_error)
        else:
            _record_success(ctx, result)  # type: ignore[arg-type]


async def _execute_async(
    engine: Engine, task_id: str, work: TaskWork, describe_error: ErrorDescriber | None
) -> None:
    loaded = await anyio.to_thread.run_sync(_load_context, engine, task_id)
    if loaded is None:
        return
    name, ctx = loaded
    # Poll for a slot instead of blocking a thread on the semaphore while queued.
    while not _slots.acquire(blocking=False):
        await asyncio.sleep(0.5)
    try:
        await anyio.to_thread.run_sync(lambda: ctx.flush(status=RUNNING))
        try:
            result = await work(ctx)  # type: ignore[misc]
        except Exception as exc:  # noqa: BLE001 - recorded on the task row
            await anyio.to_thread.run_sync(_record_failure, ctx, name, exc, describe_error)
        else:
            await anyio.to_thread.run_sync(_record_success, ctx, result)
    finally:
        _slots.release()


def fail_interrupted_tasks(engine: Engine) -> int:
    """Startup hook: anything still pending/running belonged to a process that is gone."""
    with Session(engine) as session:
        orphans = session.exec(
            select(SystemTask).where(col(SystemTask.status).in_(ACTIVE_STATUSES))
        ).all()
        for task in orphans:
            task.status = FAILED
            task.error = "Interrupted by a server restart - run it again to resume."
            task.progress_data = {
                **(task.progress_data or {}),
                "error": {"code": "interrupted", "message": task.error},
            }
            task.updated_at = utcnow()
            session.add(task)
        session.commit()
        return len(orphans)


def prune_finished(session: Session) -> None:
    session.exec(
        delete(SystemTask).where(
            col(SystemTask.status).in_((COMPLETED, FAILED)),
            col(SystemTask.updated_at) < utcnow() - RETENTION,
        )
    )
    session.commit()
