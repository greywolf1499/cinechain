"""Zero-daemon task runner: BackgroundTasks + SQLite row lifecycle (no broker)."""

import asyncio
import time
from contextlib import nullcontext
from datetime import timedelta
from pathlib import Path

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


@pytest.mark.parametrize("async_job", [False, True])
async def test_cancel_stops_at_checkpoint_and_keeps_progress(engine, monkeypatch, async_job):
    monkeypatch.setattr(task_runner, "PROGRESS_FLUSH_SECONDS", 0)
    visited = []

    def item(ctx, index):
        ctx.check_cancelled()
        visited.append(index)
        ctx.progress({"current": index, "total": 10})
        if index == 2:
            with Session(engine) as session:
                row = session.get(SystemTask, ctx.task_id)
                row.cancel_requested = True
                session.add(row)
                session.commit()

    def sync_work(ctx):
        for index in range(10):
            item(ctx, index)

    async def async_work(ctx):
        for index in range(10):
            item(ctx, index)
            await asyncio.sleep(0)

    background = BackgroundTasks()
    with Session(engine) as session:
        task, _ = task_runner.submit_task(
            background,
            session,
            "demo",
            async_work if async_job else sync_work,
            link="/passport",
            describe_error=lambda exc: {"code": "wrong"},
        )
    await background()
    failed = _status(engine, task.id)
    assert visited == [0, 1, 2]
    assert failed.status == "failed"
    assert failed.cancel_requested is True
    assert failed.link == "/passport"
    assert failed.progress_data["progress"]["current"] == 2
    assert failed.progress_data["error"]["code"] == "cancelled"
    assert "result" not in failed.progress_data


async def test_pending_cancellation_never_runs_work(engine):
    background = BackgroundTasks()
    visited = []
    with Session(engine) as session:
        task, _ = task_runner.submit_task(
            background, session, "demo", lambda ctx: visited.append(1)
        )
        task.cancel_requested = True
        session.add(task)
        session.commit()
        session.refresh(task)
    await background()
    assert visited == []
    assert _status(engine, task.id).progress_data["error"]["code"] == "cancelled"


@pytest.mark.parametrize("async_job", [False, True])
async def test_pending_cancel_does_not_wait_for_busy_workers(engine, monkeypatch, async_job):
    import threading

    semaphore = threading.BoundedSemaphore(1)
    semaphore.acquire()
    monkeypatch.setattr(task_runner, "_slots", semaphore)
    visited = []

    def work(ctx):
        visited.append(1)

    async def async_work(ctx):
        visited.append(1)

    with Session(engine) as session:
        task, _ = task_runner.submit_task(
            BackgroundTasks(), session, "demo", async_work if async_job else work
        )
        task.cancel_requested = True
        session.add(task)
        session.commit()
        session.refresh(task)
    thread = None
    try:
        if async_job:
            await asyncio.wait_for(task_runner._execute_async(engine, task.id, async_work, None), 2)
        else:
            thread = threading.Thread(
                target=task_runner._execute, args=(engine, task.id, work, None)
            )
            thread.start()
            thread.join(timeout=2)
            assert not thread.is_alive()
        assert _status(engine, task.id).progress_data["error"]["code"] == "cancelled"
        assert not visited
    finally:
        semaphore.release()
        if thread:
            thread.join(timeout=2)


def test_cancel_polling_is_throttled_but_sticky(engine, monkeypatch):
    monkeypatch.setattr(task_runner, "PROGRESS_FLUSH_SECONDS", 60)
    with Session(engine) as session:
        task = SystemTask(name="demo")
        session.add(task)
        session.commit()
        ctx = task_runner.TaskContext(engine, task.id)
        assert ctx.cancelled() is False
        task.cancel_requested = True
        session.add(task)
        session.commit()
    assert ctx.cancelled() is False
    assert ctx.cancelled(force=True) is True
    assert ctx.cancelled() is True


async def test_cancelled_scrape_keeps_checkpoint_and_resumes(engine, config_dir, monkeypatch):
    from app.services import letterboxd

    monkeypatch.setattr(task_runner, "PROGRESS_FLUSH_SECONDS", 0)
    monkeypatch.setattr(letterboxd, "new_session", lambda: nullcontext(object()))
    monkeypatch.setattr(letterboxd, "fetch_html", lambda *args, **kwargs: "<html></html>")
    films = [{"title": f"Film {index}", "slug": f"film-{index}"} for index in range(3)]
    visited = []
    cancelled_once = False
    task_id = None

    def enrich(*args, **kwargs):
        nonlocal cancelled_once
        entry = args[1]
        entry["tmdb_id"] = len(visited) + 1
        visited.append(entry["slug"])
        if not cancelled_once:
            cancelled_once = True
            with Session(engine) as session:
                row = session.get(SystemTask, task_id)
                row.cancel_requested = True
                session.add(row)
                session.commit()

    monkeypatch.setattr(letterboxd, "enrich_entry", enrich)

    def work(ctx):
        return letterboxd._scrape_paginated(
            base_url="https://letterboxd.com/test/list/demo/",
            mode="test",
            parse_page=lambda soup: [dict(film) for film in films],
            detail_mode=False,
            is_deep=False,
            tmdb_api_key=None,
            max_pages=1,
            no_cache=True,
            progress_callback=ctx.progress,
            ranked_output=False,
        )

    background = BackgroundTasks()
    with Session(engine) as session:
        task, _ = task_runner.submit_task(background, session, "demo", work)
        task_id = task.id
    await background()
    assert visited == ["film-0"]
    assert _status(engine, task_id).progress_data["error"]["code"] == "cancelled"
    assert list(letterboxd.checkpoint_dir().glob("*.json"))
    background = BackgroundTasks()
    with Session(engine) as session:
        resumed, _ = task_runner.submit_task(background, session, "demo", work)
    await background()
    assert visited == ["film-0", "film-1", "film-2"]
    assert _status(engine, resumed.id).status == "completed"
    assert not list(letterboxd.checkpoint_dir().glob("*.json"))


def test_task_migration_preserves_rows_and_has_one_head(config_dir):
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import text

    from app.config import get_settings

    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    assert ScriptDirectory.from_config(config).get_heads() == ["b3c4d5e6f7a8"]
    command.upgrade(config, "a2b3c4d5e6f7")
    db = create_engine(get_settings().database_url)
    with db.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO system_tasks (id, name, status, created_at, updated_at, progress_data) "
                "VALUES ('old', 'demo', 'completed', '2026-01-01', '2026-01-01', '{\"result\":{}}')"
            )
        )
    command.upgrade(config, "head")
    with db.begin() as connection:
        row = connection.execute(
            text(
                "SELECT status, cancel_requested, link, progress_data FROM system_tasks WHERE id='old'"
            )
        ).one()
        assert tuple(row) == ("completed", 0, None, '{"result":{}}')
    command.downgrade(config, "a2b3c4d5e6f7")
    with db.begin() as connection:
        assert (
            connection.execute(text("SELECT status FROM system_tasks WHERE id='old'")).scalar()
            == "completed"
        )
    db.dispose()


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
