from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import httpx
import numpy as np
import pytest
import respx
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import BackgroundTasks, FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app.api.deps import get_current_user
from app.api.routes_system import router
from app.config import Settings
from app.db import get_session
from app.facets.models import MovieFacetStatus
from app.facets.registry import FAMILY_VERSIONS
from app.integrations.omdb import OMDbClient
from app.models.cache import CachedMovie
from app.models.curated import CanonMovieBadge, CuratedList, LetterboxdWatchlist
from app.models.run import Run, RunParticipant, RunStep
from app.models.system import ProviderBudget, SystemTask
from app.models.user import User
from app.services import cache_repo, data_spa, embeddings, provider_budgets, task_runner
from app.services.tmdb import (
    TMDBClient,
    TMDBError,
    TMDBNotFoundError,
    TMDBRateLimitError,
)
from app.utils.ids import utcnow


@pytest.fixture
def spa_engine(config_dir):
    from app.db import engine

    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            User(
                id="admin",
                username="admin",
                display_name="Admin",
                password_hash="unused",
                is_admin=True,
            )
        )
        session.commit()
    yield engine
    engine.dispose()


def movie(session, movie_id=1, **kwargs):
    row = CachedMovie(tmdb_id=movie_id, title=kwargs.pop("title", f"Movie {movie_id}"), **kwargs)
    session.add(row)
    session.commit()
    return row


def submit(engine, treatment="details", **kwargs):
    with Session(engine) as session:
        tasks = BackgroundTasks()
        task = data_spa.submit(tasks, session, treatment, "admin", **kwargs)
        return task.id, tasks


def context(engine, task_id):
    with Session(engine) as session:
        return task_runner.TaskContext(
            engine, task_id, session.get(SystemTask, task_id).progress_data
        )


async def execute(engine, task_id):
    await task_runner._execute_async(engine, task_id, data_spa._run, None)
    with Session(engine) as session:
        return session.get(SystemTask, task_id)


def test_atomic_budget_and_day(spa_engine):
    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(
            workers.map(
                lambda _: provider_budgets.reserve(spa_engine, "omdb", limit=7),
                range(30),
            )
        )
    assert sum(results) == 7
    with Session(spa_engine) as session:
        budget = session.get(ProviderBudget, ("omdb", utcnow().date().isoformat()))
        assert budget.used == 7
        session.add(ProviderBudget(provider="omdb", day="2000-01-01", used=900))
        session.commit()
    assert provider_budgets.reserve(spa_engine, "omdb", limit=8)


async def test_real_jit_ratings_budget_reserved_before_http(spa_engine):
    with Session(spa_engine) as session:
        movie(session, imdb_id="tt123")
    with Session(spa_engine) as session, respx.mock:

        def response(_):
            with Session(spa_engine) as probe:
                assert probe.get(ProviderBudget, ("omdb", utcnow().date().isoformat())).used == 1
            return httpx.Response(200, json={"Response": "True", "imdbRating": "7.0"})

        route = respx.get("https://www.omdbapi.com/").mock(side_effect=response)
        async with httpx.AsyncClient() as http:
            omdb = OMDbClient(http, Settings(omdb_api_key="fixture"))
            assert (
                await cache_repo.get_movie_ratings(session, TMDBClient(http), omdb, 1)
            ).imdb_rating == "7.0"
            assert await cache_repo.get_movie_ratings(session, TMDBClient(http), omdb, 1)
            assert route.call_count == 1
            budget = session.get(ProviderBudget, ("omdb", utcnow().date().isoformat()))
            budget.used = 900
            session.add(budget)
            session.commit()
            with pytest.raises(provider_budgets.BudgetExhausted):
                await cache_repo.get_movie_ratings(session, TMDBClient(http), omdb, 1, force=True)
            assert route.call_count == 1


async def test_budget_clean_stop_resume_without_skipping(spa_engine, monkeypatch):
    monkeypatch.setenv("OMDB_API_KEY", "fixture")
    from app.config import get_settings

    get_settings.cache_clear()
    with Session(spa_engine) as session:
        for i in (1, 2, 3):
            movie(session, i, imdb_id=f"tt{i}")
        session.add(ProviderBudget(provider="omdb", day=utcnow().date().isoformat(), used=899))
        session.commit()
    task_id, _ = submit(spa_engine, "ratings")
    with respx.mock:
        route = respx.get("https://www.omdbapi.com/").mock(
            return_value=httpx.Response(
                200,
                json={"Response": "True", "imdbRating": "7.0"},
            )
        )
        task = await execute(spa_engine, task_id)
        assert task.status == "completed"
        assert task.progress_data["cursor"] == 1
        assert task.progress_data["result"]["paused"] == "omdb_budget"
        assert route.call_count == 1
        with Session(spa_engine) as session:
            assert session.get(MovieFacetStatus, (2, "spa:ratings")) is None
            budget = session.get(ProviderBudget, ("omdb", utcnow().date().isoformat()))
            budget.used = 0
            session.add(budget)
            session.commit()
        resumed_id, _ = submit(spa_engine, "ratings")
        resumed = await execute(spa_engine, resumed_id)
        assert resumed.progress_data["stage"] == 1
        assert [call.request.url.params["i"] for call in route.calls] == ["tt1", "tt2", "tt3"]


async def test_unavailable_cooldown_errors_retry_no_catalogue_poison(spa_engine, monkeypatch):
    with Session(spa_engine) as session:
        movie(session, 1)
        movie(session, 2)
    calls = []

    async def treatment(session, film, *_):
        calls.append(film.tmdb_id)
        return "unavailable" if film.tmdb_id == 1 else "error"

    monkeypatch.setattr(data_spa, "_treat", treatment)
    task_id, _ = submit(spa_engine)
    await execute(spa_engine, task_id)
    task_id, _ = submit(spa_engine)
    await execute(spa_engine, task_id)
    assert calls == [1, 2, 2]
    with Session(spa_engine) as session:
        assert session.get(MovieFacetStatus, (1, "production")) is None
        status = session.get(MovieFacetStatus, (1, "spa:details"))
        status.computed_at = utcnow() - timedelta(days=31)
        session.add(status)
        session.commit()
    task_id, _ = submit(spa_engine)
    await execute(spa_engine, task_id)
    assert calls[-2:] == [1, 2]


def test_priority_and_dedupe(spa_engine):
    with Session(spa_engine) as session:
        for i in range(1, 5):
            movie(session, i, popularity=i * 100)
        run = Run(id="active", name="Active")
        canon = CuratedList(id="canon", title="Canon", url="fixture", badge_prefix="C")
        session.add(run)
        session.add(canon)
        session.commit()
        session.add(RunStep(run_id=run.id, movie_id=1, movie_title="One"))
        session.add(CanonMovieBadge(curated_list_id=canon.id, movie_id=2, badge_label="C"))
        session.add(
            LetterboxdWatchlist(
                user_id="admin", letterboxd_username="fixture", movie_id=3, title="Three"
            )
        )
        session.commit()
    task_id, tasks = submit(spa_engine, batch_cap=4)
    duplicate_id, duplicate_tasks = submit(spa_engine, batch_cap=4)
    assert duplicate_id == task_id
    assert len(tasks.tasks) == 1 and not duplicate_tasks.tasks
    assert context(spa_engine, task_id)._data["worklist"] == [1, 2, 3, 4]


async def test_restart_atomic_outcome_and_cursor(spa_engine, monkeypatch):
    with Session(spa_engine) as session:
        movie(session, 1)
        movie(session, 2)
    calls = []

    async def treatment(session, film, *_):
        calls.append(film.tmdb_id)
        return "unavailable"

    monkeypatch.setattr(data_spa, "_treat", treatment)
    checkpoint = data_spa._checkpoint

    def interrupted_checkpoint(*args):
        if len(calls) == 2:
            raise OSError("simulated process interruption")
        return checkpoint(*args)

    monkeypatch.setattr(data_spa, "_checkpoint", interrupted_checkpoint)
    task_id, _ = submit(spa_engine)
    failed = await execute(spa_engine, task_id)
    assert failed.status == "failed" and failed.progress_data["cursor"] == 1
    with Session(spa_engine) as session:
        assert session.get(MovieFacetStatus, (1, "spa:details")).status == "unavailable"
        assert session.get(MovieFacetStatus, (2, "spa:details")) is None
    resumed_id, _ = submit(spa_engine)
    assert (await execute(spa_engine, resumed_id)).status == "completed"
    assert calls == [1, 2, 2]


async def test_cancel_between_items_preserves_resume(spa_engine, monkeypatch):
    with Session(spa_engine) as session:
        movie(session, 1)
        movie(session, 2)
    calls = []

    async def treatment(session, film, *_):
        calls.append(film.tmdb_id)
        with Session(spa_engine) as other:
            task = other.exec(select(SystemTask).where(SystemTask.status == "running")).one()
            task.cancel_requested = True
            other.add(task)
            other.commit()
        return "unavailable"

    monkeypatch.setattr(data_spa, "_treat", treatment)
    task_id, _ = submit(spa_engine)
    cancelled = await execute(spa_engine, task_id)
    assert calls == [1]
    assert cancelled.progress_data["error"]["code"] == "cancelled"
    assert cancelled.progress_data["cursor"] == 0
    with Session(spa_engine) as session:
        assert session.get(MovieFacetStatus, (1, "spa:details")) is None


async def test_embeddings_once_per_32_batch_and_fingerprint(spa_engine, monkeypatch):
    with Session(spa_engine) as session:
        for i in range(1, 66):
            movie(session, i, overview="Plot")
    calls = []

    async def embed(config, texts):
        calls.append(len(texts))
        return embeddings.EmbeddingBatch(
            [np.ones(384, dtype=np.float32) for _ in texts], config.fingerprint
        )

    monkeypatch.setattr(embeddings, "embed_batch", embed)
    task_id, _ = submit(spa_engine, "embeddings")
    assert (await execute(spa_engine, task_id)).status == "completed"
    assert calls == [32, 32, 1]
    with Session(spa_engine) as session:
        assert data_spa.health(session)["coverage"]["embeddings"]["known"] == 65
        session.get(CachedMovie, 1).overview_embedding_model = "other:model"
        session.commit()
        assert data_spa.health(session)["coverage"]["embeddings"]["known"] == 64


async def test_fix_all_one_task_chained(spa_engine, monkeypatch):
    with Session(spa_engine) as session:
        movie(session, 1, overview="Plot")
    calls = []

    async def treatment(session, film, treatment, *_):
        calls.append(treatment)
        return "ok"

    async def embed(config, texts):
        calls.append("embeddings")
        return embeddings.EmbeddingBatch([np.ones(384)], config.fingerprint)

    monkeypatch.setattr(data_spa, "_treat", treatment)
    monkeypatch.setattr(embeddings, "embed_batch", embed)
    task_id, tasks = submit(spa_engine, "fix_all")
    assert len(tasks.tasks) == 1
    assert (await execute(spa_engine, task_id)).status == "completed"
    assert calls == list(data_spa.TREATMENTS)
    with Session(spa_engine) as session:
        assert len(session.exec(select(SystemTask)).all()) == 1
        assert len(session.exec(select(MovieFacetStatus)).all()) == 5


def test_health_and_endpoint_auth_contract(spa_engine):
    app = FastAPI()
    app.include_router(router)

    def session_override():
        with Session(spa_engine) as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    with TestClient(app) as client:
        assert client.get("/system/cache/health").status_code == 401
        assert client.post("/system/spa/details").status_code == 401
        app.dependency_overrides[get_current_user] = lambda: User(
            id="admin", username="admin", display_name="Admin", password_hash="unused"
        )
        assert client.post("/system/spa/details").status_code == 403
        response = client.get("/system/cache/health")
        assert response.status_code == 200
        data = response.json()
        assert data["total_movies"] == 0
        assert set(data["coverage"]) == set(data_spa.TREATMENTS)
        assert data["budgets"][0]["remaining"] == 900
        app.dependency_overrides[get_current_user] = lambda: User(
            id="admin",
            username="admin",
            display_name="Admin",
            password_hash="unused",
            is_admin=True,
        )
        assert client.post("/system/spa/nonsense").status_code == 404
        assert client.post("/system/spa/details", json={"batch_cap": 2001}).status_code == 422
        result = client.post("/system/spa/facets")
        assert result.status_code == 200 and result.json()["name"] == "spa_facets"


async def test_cap_moves_past_previously_repaired_films(spa_engine, monkeypatch):
    with Session(spa_engine) as session:
        movie(session, 1)
        movie(session, 2)

    async def treatment(session, film, *_):
        return "unavailable"

    monkeypatch.setattr(data_spa, "_treat", treatment)
    task_id, _ = submit(spa_engine, batch_cap=1)
    await execute(spa_engine, task_id)
    next_id, _ = submit(spa_engine, batch_cap=1)
    assert context(spa_engine, next_id)._data["worklist"] == [2]


def test_concurrent_submission_dedupes_and_scoped_ids(spa_engine):
    with Session(spa_engine) as session:
        for i in range(1, 4):
            movie(session, i)
    with ThreadPoolExecutor(max_workers=4) as workers:
        submitted = list(
            workers.map(
                lambda _: submit(spa_engine, movie_ids=[3, 2], run_id="run-fixture"),
                range(8),
            )
        )
    assert len({task_id for task_id, _ in submitted}) == 1
    assert sum(len(tasks.tasks) for _, tasks in submitted) == 1
    ctx = context(spa_engine, submitted[0][0])
    assert ctx._data["worklist"] == [2, 3]
    with Session(spa_engine) as session:
        task = session.get(SystemTask, ctx.task_id)
        assert task.dedupe_key == "spa:run:run-fixture:details"
        assert task.link == "/runs/run-fixture"


async def test_real_ratings_transient_and_unavailable_outcomes(spa_engine, monkeypatch):
    monkeypatch.setenv("OMDB_API_KEY", "fixture")
    from app.config import get_settings

    get_settings.cache_clear()
    with Session(spa_engine) as session:
        movie(session, 1, imdb_id="tt1")
        movie(session, 2, imdb_id="tt2")
    with respx.mock:
        unavailable = respx.get(
            "https://www.omdbapi.com/", params={"apikey": "fixture", "i": "tt1", "type": "movie"}
        ).mock(
            return_value=httpx.Response(
                200, json={"Response": "False", "Error": "Movie not found!"}
            ),
        )
        transient = respx.get(
            "https://www.omdbapi.com/", params={"apikey": "fixture", "i": "tt2", "type": "movie"}
        ).mock(
            return_value=httpx.Response(503),
        )
        task_id, _ = submit(spa_engine, "ratings")
        await execute(spa_engine, task_id)
        with Session(spa_engine) as session:
            assert session.get(MovieFacetStatus, (1, "spa:ratings")).status == "unavailable"
            assert session.get(MovieFacetStatus, (2, "spa:ratings")).status == "error"
        task_id, _ = submit(spa_engine, "ratings")
        await execute(spa_engine, task_id)
        assert unavailable.call_count == 1 and transient.call_count == 2


async def test_embedding_model_loaded_and_released_once_per_batch(spa_engine, monkeypatch):
    with Session(spa_engine) as session:
        for i in range(1, 34):
            movie(session, i, overview="Plot")
    loads, releases = [], []

    def inference(texts, preset):
        loads.append((len(texts), preset.key))
        try:
            return [np.ones(384, dtype=np.float32) for _ in texts]
        finally:
            releases.append(preset.key)

    monkeypatch.setattr(embeddings, "embed_texts", inference)
    task_id, _ = submit(spa_engine, "embeddings")
    assert (await execute(spa_engine, task_id)).status == "completed"
    assert [size for size, _ in loads] == [32, 1]
    assert len(releases) == 2


def test_migration_single_head_and_preservation_roundtrip(config_dir):
    from app.db import engine

    config = Config("alembic.ini")
    assert ScriptDirectory.from_config(config).get_heads() == ["e6f7a8b9c0d1"]
    command.upgrade(config, "d5e6f7a8b9c0")
    with Session(engine) as session:
        movie(session, 99, title="Preserved", overview="A saved plot")
        session.add(MovieFacetStatus(movie_id=99, family="lexical", version=1, status="ok"))
        session.add(SystemTask(id="preserved-task", name="fixture", progress_data={"cursor": 9}))
        session.commit()
    command.upgrade(config, "head")
    assert provider_budgets.reserve(engine, "omdb")
    with Session(engine) as session:
        assert session.get(CachedMovie, 99).overview == "A saved plot"
        assert session.get(SystemTask, "preserved-task").progress_data["cursor"] == 9
        assert session.get(MovieFacetStatus, (99, "lexical")).status == "ok"
    command.downgrade(config, "d5e6f7a8b9c0")
    command.upgrade(config, "head")
    with Session(engine) as session:
        assert session.get(CachedMovie, 99).title == "Preserved"
        assert session.get(SystemTask, "preserved-task").progress_data["cursor"] == 9
        assert session.get(MovieFacetStatus, (99, "lexical")).status == "ok"
    engine.dispose()


def test_checkpoint_commit_failure_never_advances_cursor(spa_engine, monkeypatch):
    with Session(spa_engine) as session:
        movie(session)
    task_id, _ = submit(spa_engine)
    ctx = context(spa_engine, task_id)
    with Session(spa_engine) as session:

        def fail():
            raise OSError("transaction interrupted")

        monkeypatch.setattr(session, "commit", fail)
        with pytest.raises(OSError):
            data_spa._checkpoint(ctx, session, [(1, "unavailable")], "details", 1)
        session.rollback()
    assert ctx._data["cursor"] == 0
    with Session(spa_engine) as session:
        assert session.get(MovieFacetStatus, (1, "spa:details")) is None
        assert session.get(SystemTask, task_id).progress_data["cursor"] == 0


async def test_actual_facets_health_current_versions_only(spa_engine):
    with Session(spa_engine) as session:
        movie(session, 1, runtime=100, origin_country="[]", tagline="", overview="")
        movie(session, 2)
    task_id, _ = submit(spa_engine, "facets")
    assert (await execute(spa_engine, task_id)).status == "completed"
    with Session(spa_engine) as session:
        report = data_spa.health(session)
        assert report["coverage"]["details"] == {"known": 1, "total": 2}
        assert report["coverage"]["facets"] == {"known": 2, "total": 2}
        family = next(iter(FAMILY_VERSIONS))
        status = session.get(MovieFacetStatus, (1, family))
        status.version += 1
        session.add(status)
        session.commit()
        report = data_spa.health(session)
        assert report["coverage"]["facets"] == {"known": 1, "total": 2}
        assert report["families"][family] == {"known": 1, "total": 2}


async def test_embedding_failure_retry_and_missing_overview_cooldown(spa_engine, monkeypatch):
    with Session(spa_engine) as session:
        movie(session, 1, overview="Plot")
        movie(session, 2)
    calls = []

    async def unavailable(config, texts):
        calls.append(texts)
        raise embeddings.EmbeddingUnavailable("Fixture model offline")

    monkeypatch.setattr(embeddings, "embed_batch", unavailable)
    task_id, _ = submit(spa_engine, "embeddings")
    await execute(spa_engine, task_id)
    with Session(spa_engine) as session:
        assert session.get(MovieFacetStatus, (1, "spa:embeddings")).status == "error"
        assert session.get(MovieFacetStatus, (2, "spa:embeddings")).status == "unavailable"
    next_id, _ = submit(spa_engine, "embeddings")
    assert context(spa_engine, next_id)._data["worklist"] == [1]
    await execute(spa_engine, next_id)
    assert calls == [["Plot"], ["Plot"]]


@pytest.mark.parametrize(
    "error",
    [
        TMDBError("server down", status_code=500),
        TMDBRateLimitError("slow down", retry_after=60),
        httpx.ConnectError("offline"),
    ],
)
@pytest.mark.parametrize("treatment", ["details", "people"])
async def test_details_people_provider_failures_retry_not_cooldown(
    spa_engine, monkeypatch, treatment, error
):
    with Session(spa_engine) as session:
        movie(session, 1)
    calls = []

    async def fail(self, movie_id, *args, **kwargs):
        calls.append(movie_id)
        raise error

    monkeypatch.setattr(TMDBClient, "get_movie", fail)
    monkeypatch.setattr(TMDBClient, "get_movie_credits", fail)
    for _ in range(2):
        task_id, _ = submit(spa_engine, treatment)
        assert (await execute(spa_engine, task_id)).status == "completed"
        with Session(spa_engine) as session:
            assert session.get(MovieFacetStatus, (1, f"spa:{treatment}")).status == "error"
    assert calls == [1, 1]


@pytest.mark.parametrize("treatment", ["details", "people"])
async def test_details_people_tmdb_not_found_is_definitive_cooldown(
    spa_engine, monkeypatch, treatment
):
    with Session(spa_engine) as session:
        movie(session, 1)
    calls = []

    async def missing(self, movie_id, *args, **kwargs):
        calls.append(movie_id)
        raise TMDBNotFoundError("missing", status_code=404)

    monkeypatch.setattr(TMDBClient, "get_movie", missing)
    monkeypatch.setattr(TMDBClient, "get_movie_credits", missing)
    task_id, _ = submit(spa_engine, treatment)
    await execute(spa_engine, task_id)
    task_id, _ = submit(spa_engine, treatment)
    await execute(spa_engine, task_id)
    assert calls == [1]
    with Session(spa_engine) as session:
        assert session.get(MovieFacetStatus, (1, f"spa:{treatment}")).status == "unavailable"


def _prepare_client(spa_engine, monkeypatch, candidate_ids):
    from app.api import routes_runs
    from app.api.deps import get_tmdb_client
    from app.schemas.discovery import DiscoveryCandidate

    calls = []

    class StaticEngine:
        async def discover_with_modifiers(self, frontier_movie_id, **kwargs):
            return [
                DiscoveryCandidate(movie_id=movie_id, title=f"Movie {movie_id}")
                for movie_id in candidate_ids
            ]

    original_submit = data_spa.submit

    def spy(background_tasks, session, treatment, user_id, **kwargs):
        calls.append({"treatment": treatment, "user_id": user_id, **kwargs})
        # Keep the real dedupe/worklist but never schedule provider work in the test client.
        return original_submit(BackgroundTasks(), session, treatment, user_id, **kwargs)

    monkeypatch.setattr(routes_runs, "get_engine", lambda *args: StaticEngine())
    monkeypatch.setattr(data_spa, "submit", spy)
    app = FastAPI()
    app.include_router(routes_runs.router)

    def session_override():
        with Session(spa_engine) as session:
            yield session

    # conftest re-imports app.db per test; override whichever get_session the routes captured.
    from app.api import deps

    for dependency in {get_session, deps.get_session, routes_runs.get_session}:
        app.dependency_overrides[dependency] = session_override
    app.dependency_overrides[get_tmdb_client] = lambda: None
    return app, calls


def _as_user(app, user_id):
    app.dependency_overrides[get_current_user] = lambda: User(
        id=user_id, username=user_id, display_name=user_id, password_hash="unused"
    )


def test_run_prepare_endpoint_contract(spa_engine, monkeypatch):
    candidates = [10, *range(1000, 1250)]
    with Session(spa_engine) as session:
        for user_id in ("player", "outsider"):
            session.add(User(id=user_id, username=user_id, display_name=user_id, password_hash="x"))
        session.add(Run(id="run", name="Run"))
        session.add(Run(id="empty", name="Empty"))
        session.commit()
        for run_id in ("run", "empty"):
            session.add(RunParticipant(run_id=run_id, user_id="player"))
        session.add(RunStep(run_id="run", movie_id=10, movie_title="Frontier"))
        session.commit()
        movie(session, 10)
        for movie_id in range(1000, 1250):
            movie(session, movie_id)
        movie(session, 99, popularity=10_000)
    app, calls = _prepare_client(spa_engine, monkeypatch, candidates)
    with TestClient(app) as client:
        _as_user(app, "outsider")
        assert client.post("/runs/run/prepare").status_code == 404
        assert not calls
        _as_user(app, "player")
        assert client.post("/runs/run/prepare", json={"treatment": "nonsense"}).status_code == 422
        assert client.post("/runs/empty/prepare").status_code == 422
        assert not calls
        response = client.post("/runs/run/prepare")
        assert response.status_code == 200, response.text
        data = response.json()
        duplicate = client.post("/runs/run/prepare", json={"treatment": "fix_all"})
        details = client.post("/runs/run/prepare", json={"treatment": "details"})
    expected = [10, *range(1000, 1199)]
    assert calls[0] == {
        "treatment": "fix_all",
        "user_id": "player",
        "movie_ids": expected,
        "run_id": "run",
        "batch_cap": 200,
    }
    assert data["name"] == "spa_fix_all" and data["user_id"] == "player"
    assert data["dedupe_key"] == "spa:run:run:fix_all" and data["link"] == "/runs/run"
    assert duplicate.status_code == 200 and duplicate.json()["id"] == data["id"]
    assert details.json()["dedupe_key"] == "spa:run:run:details"
    assert details.json()["id"] != data["id"]
    worklist = context(spa_engine, data["id"])._data["worklist"]
    assert len(worklist) == 200 and set(worklist) == set(expected) and 99 not in worklist


async def test_run_prepare_details_worker_fills_scoped_runtimes(spa_engine, monkeypatch):
    monkeypatch.setenv("TMDB_API_KEY", "fixture")
    from app.config import get_settings

    get_settings.cache_clear()
    with Session(spa_engine) as session:
        session.add(User(id="player", username="player", display_name="Player", password_hash="x"))
        session.add(Run(id="run", name="Run"))
        session.commit()
        session.add(RunParticipant(run_id="run", user_id="player"))
        session.add(RunStep(run_id="run", movie_id=10, movie_title="Frontier"))
        session.commit()
        for movie_id in (10, 11, 12):
            movie(session, movie_id)
        movie(session, 99, popularity=10_000)
    app, calls = _prepare_client(spa_engine, monkeypatch, [11, 12])
    _as_user(app, "player")
    with TestClient(app) as client:
        response = client.post("/runs/run/prepare", json={"treatment": "details"})
    assert response.status_code == 200, response.text
    assert calls[0]["movie_ids"] == [10, 11, 12]
    task_id = response.json()["id"]
    assert sorted(context(spa_engine, task_id)._data["worklist"]) == [10, 11, 12]

    def detail(request, movie_id):
        movie_id = int(movie_id)
        return httpx.Response(
            200,
            json={
                "id": movie_id,
                "title": f"Movie {movie_id}",
                "overview": "A plot.",
                "tagline": "A tagline.",
                "origin_country": ["US"],
                "runtime": 90 + movie_id,
            },
        )

    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(url__regex=r"https://api\.themoviedb\.org/3/movie/(?P<movie_id>\d+)$")
        route.side_effect = detail
        task = await execute(spa_engine, task_id)
    requested = sorted(int(call.request.url.path.rsplit("/", 1)[1]) for call in route.calls)
    assert requested == [10, 11, 12]
    assert task.status == "completed"
    assert task.progress_data["progress"] == {
        "current": 3,
        "total": 3,
        "message": "Repairing details",
    }
    with Session(spa_engine) as session:
        assert [session.get(CachedMovie, i).runtime for i in (10, 11, 12)] == [100, 101, 102]
        assert session.get(CachedMovie, 99).runtime is None
        assert session.get(MovieFacetStatus, (99, "spa:details")) is None
        for movie_id in (10, 11, 12):
            assert session.get(MovieFacetStatus, (movie_id, "spa:details")).status == "ok"
