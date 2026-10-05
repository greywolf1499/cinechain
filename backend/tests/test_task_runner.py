"""Zero-daemon task runner: BackgroundTasks + SQLite row lifecycle (no broker)."""

import asyncio
import time
from datetime import timedelta

import pytest
from fastapi import BackgroundTasks
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.system import SystemTask
from app.services import task_runner
from app.utils.ids import utcnow


@pytest.fixture()
def engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path}/tasks.db", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _status(engine, task_id):
    with Session(engine) as session:
        return session.get(SystemTask, task_id)


async def test_task_id_is_returned_before_the_work_runs_then_completes(engine):
    seen_statuses = []

    def work(ctx):
        seen_statuses.append(_status(engine, ctx.task_id).status)
        ctx.progress({"stage": "fetch_page", "current": 1, "total": 3, "message": "page 1"})
        ctx.flush()
        seen_statuses.append(_status(engine, ctx.task_id).progress_data["progress"]["current"])
        return {"matched": 7}

    background = BackgroundTasks()
    with Session(engine) as session:
        task, created = task_runner.submit_task(background, session, "demo", work, label="Demo job")

    assert created and task.status == "pending"
    assert _status(engine, task.id).status == "pending"  # response could be sent right now

    await background()  # what Starlette does after the response goes out

    done = _status(engine, task.id)
    assert seen_statuses == ["running", 1]
    assert done.status == "completed"
    assert done.error is None
    assert done.progress_data["label"] == "Demo job"
    assert done.progress_data["result"] == {"matched": 7}
    assert done.updated_at >= done.created_at


async def test_failure_is_recorded_not_raised(engine):
    class Boom(Exception):
        pass

    def work(ctx):
        raise Boom("scrape exploded")

    background = BackgroundTasks()
    with Session(engine) as session:
        task, _ = task_runner.submit_task(
            background,
            session,
            "demo",
            work,
            describe_error=lambda exc: {"code": "boom", "status": 418},
        )

    await background()  # must not raise

    failed = _status(engine, task.id)
    assert failed.status == "failed"
    assert failed.error == "scrape exploded"
    assert failed.progress_data["error"] == {
        "code": "boom",
        "message": "scrape exploded",
        "status": 418,
    }


async def test_duplicate_active_task_returns_existing_and_schedules_nothing(engine):
    background = BackgroundTasks()
    with Session(engine) as session:
        first, created_first = task_runner.submit_task(
            background, session, "demo", lambda ctx: None, dedupe_key="same"
        )
        second, created_second = task_runner.submit_task(
            background, session, "demo", lambda ctx: None, dedupe_key="same"
        )

        assert (created_first, created_second) == (True, False)
        assert second.id == first.id
        assert len(background.tasks) == 1

        await background()
        third, created_third = task_runner.submit_task(
            BackgroundTasks(), session, "demo", lambda ctx: None, dedupe_key="same"
        )
        assert created_third and third.id != first.id  # finished tasks don't block a re-run


async def test_progress_writes_are_throttled(engine, monkeypatch):
    monkeypatch.setattr(task_runner, "PROGRESS_FLUSH_SECONDS", 60)
    writes = []

    def work(ctx):
        for index in range(50):
            ctx.progress({"current": index})
            writes.append(_status(engine, ctx.task_id).progress_data.get("progress"))

    background = BackgroundTasks()
    with Session(engine) as session:
        task, _ = task_runner.submit_task(background, session, "demo", work)
    await background()

    # The first tick flushes (RUNNING flush set the clock), the rest are held back in memory.
    assert len({str(w) for w in writes}) <= 2
    assert _status(engine, task.id).progress_data["progress"] == {"current": 49}


def test_startup_marks_orphaned_tasks_failed(engine):
    with Session(engine) as session:
        session.add(SystemTask(name="a", status="running"))
        session.add(SystemTask(name="b", status="pending"))
        session.add(SystemTask(name="c", status="completed"))
        session.commit()

    assert task_runner.fail_interrupted_tasks(engine) == 2

    with Session(engine) as session:
        rows = {t.name: t for t in session.exec(select(SystemTask)).all()}
    assert rows["a"].status == rows["b"].status == "failed"
    assert "restart" in rows["a"].error
    assert rows["a"].progress_data["error"]["code"] == "interrupted"
    assert rows["c"].status == "completed"


async def test_old_finished_tasks_are_pruned_on_submit(engine):
    with Session(engine) as session:
        session.add(
            SystemTask(name="old", status="completed", updated_at=utcnow() - timedelta(days=8))
        )
        session.add(
            SystemTask(name="recent", status="completed", updated_at=utcnow() - timedelta(days=1))
        )
        session.add(
            SystemTask(
                name="old-but-active", status="running", updated_at=utcnow() - timedelta(days=30)
            )
        )
        session.commit()
        task_runner.submit_task(BackgroundTasks(), session, "demo", lambda ctx: None)
        names = {t.name for t in session.exec(select(SystemTask)).all()}

    assert names == {"recent", "old-but-active", "demo"}


async def test_worker_slots_cap_concurrency(engine, monkeypatch):
    import threading

    monkeypatch.setattr(task_runner, "_slots", threading.BoundedSemaphore(1))
    running, peak, lock = 0, 0, threading.Lock()

    def work(ctx):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with lock:
            running -= 1

    background = BackgroundTasks()
    with Session(engine) as session:
        for _ in range(3):
            task_runner.submit_task(background, session, "demo", work)

    threads = [
        threading.Thread(target=task_runner._execute, args=(engine, t.id, work, None))
        for t in _all_tasks(engine)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert peak == 1
    assert all(t.status == "completed" for t in _all_tasks(engine))


def _all_tasks(engine):
    with Session(engine) as session:
        return list(session.exec(select(SystemTask)).all())


async def test_async_work_runs_on_the_event_loop_and_reports_progress(engine):
    async def work(ctx):
        await ctx.aprogress({"stage": "x", "current": 1, "total": 2})
        await asyncio.sleep(0)
        return {"ok": True}

    background = BackgroundTasks()
    with Session(engine) as session:
        task, _ = task_runner.submit_task(background, session, "demo", work)
    assert background.tasks[0].func is task_runner._execute_async

    await background()

    done = _status(engine, task.id)
    assert done.status == "completed"
    assert done.progress_data["result"] == {"ok": True}
    assert done.progress_data["progress"]["current"] == 1


async def test_async_failure_is_recorded_not_raised(engine):
    async def work(ctx):
        raise RuntimeError("async boom")

    background = BackgroundTasks()
    with Session(engine) as session:
        task, _ = task_runner.submit_task(background, session, "demo", work)

    await background()

    failed = _status(engine, task.id)
    assert (failed.status, failed.error) == ("failed", "async boom")


async def test_backoff_honors_a_zero_retry_after_instead_of_defaulting_to_two_seconds():
    import time

    from app.services.tmdb import TMDBRateLimitError
    from app.services.tmdb_backoff import fetch_with_backoff

    attempts = {"n": 0}

    async def fetch():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise TMDBRateLimitError("slow down", retry_after=0.0)
        return "ok"

    start = time.monotonic()
    assert await fetch_with_backoff(fetch, time.monotonic() + 30) == "ok"
    assert time.monotonic() - start < 1.0
