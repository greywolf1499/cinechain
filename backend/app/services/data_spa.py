"""Bounded, restartable cache treatments using the ephemeral task runner."""

from __future__ import annotations

import hashlib
import logging
import threading
from datetime import UTC, timedelta
from functools import partial
from typing import Any

import anyio
import httpx
import numpy as np
from fastapi import BackgroundTasks
from sqlalchemy import case, exists, or_
from sqlmodel import Session, col, func, select

from app.facets import store
from app.facets import tropes as trope_facets
from app.facets.models import MovieFacet, MovieFacetStatus
from app.facets.registry import FAMILY_VERSIONS
from app.integrations.omdb import OMDbClient
from app.models.cache import CachedMovie, CachedMovieRating
from app.models.curated import CanonMovieBadge, LetterboxdWatchlist
from app.models.run import Run, RunStep
from app.models.system import ProviderBudget, SystemTask
from app.services import (
    cache_repo,
    embeddings,
    llm,
    movie_features,
    provider_budgets,
    settings_repo,
    task_runner,
    tvtropes,
)
from app.services.tmdb import TMDBClient, TMDBNotFoundError
from app.utils.ids import utcnow

TREATMENTS = ("details", "people", "ratings", "embeddings", "tropes", "facets")
# Literal task names so the frontend title guard can resolve every submitted name statically.
TASK_NAMES = {
    "details": "spa_details",
    "people": "spa_people",
    "ratings": "spa_ratings",
    "embeddings": "spa_embeddings",
    "tropes": "spa_tropes",
    "facets": "spa_facets",
    "fix_all": "spa_fix_all",
}
COOLDOWN = timedelta(days=30)
EMBED_BATCH_SIZE = 32
_submit_lock = threading.Lock()
logger = logging.getLogger(__name__)


def _version(treatment: str, session: Session) -> int:
    if treatment == "embeddings":
        fingerprint = embeddings.load_config(session).fingerprint
        return int(hashlib.sha256(fingerprint.encode()).hexdigest()[:7], 16)
    if treatment == "tropes":
        return 1
    return 1


def _known(session: Session, movie: CachedMovie, treatment: str) -> bool:
    if treatment == "details":
        return (
            movie.origin_country is not None
            and movie.tagline is not None
            and movie.overview is not None
            and bool(movie.runtime)
        )
    if treatment == "people":
        return all(
            value is not None
            for value in (
                movie.cast_fetched_at,
                movie.directors_fetched_at,
                movie.crew_fetched_at,
            )
        )
    if treatment == "ratings":
        rating = session.get(CachedMovieRating, movie.tmdb_id)
        return bool(rating and any((rating.imdb_rating, rating.rotten_tomatoes, rating.metacritic)))
    if treatment == "embeddings":
        return (
            movie.overview_embedding is not None
            and embeddings.row_fingerprint(movie.overview_embedding_model)
            == embeddings.load_config(session).fingerprint
        )
    if treatment == "tropes":
        status = session.get(MovieFacetStatus, (movie.tmdb_id, "spa:tropes"))
        return bool(status and status.version == 1 and status.status == "ok")
    return all(
        (status := session.get(MovieFacetStatus, (movie.tmdb_id, family))) is not None
        and status.version == version
        and status.status == "ok"
        for family, version in FAMILY_VERSIONS.items()
    )


def _eligible(session: Session, movie: CachedMovie, treatment: str) -> bool:
    if _known(session, movie, treatment):
        return False
    outcome = session.get(MovieFacetStatus, (movie.tmdb_id, f"spa:{treatment}"))
    if outcome is None or outcome.version != _version(treatment, session):
        return True
    timestamp = outcome.computed_at.replace(tzinfo=UTC)
    return outcome.status != "unavailable" or utcnow() - timestamp >= COOLDOWN


def _worklist(
    session: Session,
    movie_ids: list[int] | None,
    batch_cap: int,
    treatments: list[str],
) -> list[int]:
    active = exists(
        select(RunStep.id)
        .join(Run, Run.id == RunStep.run_id)
        .where(
            RunStep.movie_id == CachedMovie.tmdb_id,
            Run.status == "active",
            Run.game_type != "import",
        )
    )
    canon = exists(
        select(CanonMovieBadge.id).where(CanonMovieBadge.movie_id == CachedMovie.tmdb_id)
    )
    watchlist = exists(
        select(LetterboxdWatchlist.id).where(LetterboxdWatchlist.movie_id == CachedMovie.tmdb_id)
    )
    coverage = _coverage_conditions(session)
    eligibility = []
    for treatment in treatments:
        cooldown = exists(
            select(MovieFacetStatus.movie_id).where(
                MovieFacetStatus.movie_id == CachedMovie.tmdb_id,
                MovieFacetStatus.family == f"spa:{treatment}",
                MovieFacetStatus.version == _version(treatment, session),
                MovieFacetStatus.status == "unavailable",
                MovieFacetStatus.computed_at > (utcnow() - COOLDOWN).replace(tzinfo=None),
            )
        )
        eligibility.append(~coverage[treatment] & ~cooldown)
    query = (
        select(CachedMovie.tmdb_id)
        .where(or_(*eligibility))
        .order_by(
            case((active, 0), (canon, 1), (watchlist, 2), else_=3),
            col(CachedMovie.popularity).desc(),
            CachedMovie.tmdb_id,
        )
    )
    if movie_ids is not None:
        query = query.where(col(CachedMovie.tmdb_id).in_(movie_ids))
    return list(session.exec(query.limit(batch_cap)).all())


def submit(
    background_tasks: BackgroundTasks,
    session: Session,
    treatment: str,
    user_id: str | None,
    movie_ids: list[int] | None = None,
    run_id: str | None = None,
    batch_cap: int = 200,
) -> SystemTask:
    # This application has one API process, but simultaneous requests use worker threads.
    with _submit_lock:
        return _submit(background_tasks, session, treatment, user_id, movie_ids, run_id, batch_cap)


def _submit(
    background_tasks: BackgroundTasks,
    session: Session,
    treatment: str,
    user_id: str | None,
    movie_ids: list[int] | None,
    run_id: str | None,
    batch_cap: int,
) -> SystemTask:
    if treatment not in TASK_NAMES:
        raise ValueError("Unknown spa treatment")
    if not 1 <= batch_cap <= 2000:
        raise ValueError("batch_cap must be between 1 and 2000")
    if treatment == "tropes":
        batch_cap = min(batch_cap, tvtropes.MAX_PAGES_PER_BATCH)
    scope = f"run:{run_id}:" if run_id else ""
    if movie_ids is not None and run_id is None:
        scope = (
            hashlib.sha256(",".join(map(str, sorted(set(movie_ids)))).encode()).hexdigest()[:16]
            + ":"
        )
    key = f"spa:{scope}{treatment}"
    previous = session.exec(
        select(SystemTask)
        .where(SystemTask.dedupe_key == key)
        .order_by(col(SystemTask.created_at).desc())
    ).first()
    resume = dict(previous.progress_data or {}) if previous else {}
    task, created = task_runner.submit_task(
        background_tasks,
        session,
        TASK_NAMES[treatment],
        _run,
        user_id=user_id,
        dedupe_key=key,
        label=f"Data Spa: {treatment.replace('_', ' ')}",
        link=f"/runs/{run_id}" if run_id else "/settings/data-spa",
    )
    if created:
        continuing = resume.get("stage", 0) < len(resume.get("treatments", []))
        if continuing and "worklist" in resume:
            data = {k: v for k, v in resume.items() if k not in ("error", "result", "paused")}
        else:
            treatments = list(TREATMENTS) if treatment == "fix_all" else [treatment]
            data = {
                "worklist": _worklist(session, movie_ids, batch_cap, treatments),
                "treatments": treatments,
                "stage": 0,
                "cursor": 0,
                "processed": 0,
            }
        task.progress_data = {**(task.progress_data or {}), **data}
        session.add(task)
        session.commit()
        session.refresh(task)
    return task


def _checkpoint(
    ctx: task_runner.TaskContext,
    session: Session,
    outcomes: list[tuple[int, str]],
    treatment: str,
    cursor: int,
) -> None:
    for movie_id, status in outcomes:
        session.merge(
            MovieFacetStatus(
                movie_id=movie_id,
                family=f"spa:{treatment}",
                version=_version(treatment, session),
                status=status,
                computed_at=utcnow(),
            )
        )
    data = {
        **ctx._data,
        "cursor": cursor,
        "processed": int(ctx._data.get("processed", 0)) + len(outcomes),
    }
    total = len(ctx._data["worklist"]) * len(ctx._data["treatments"])
    data["progress"] = {
        "current": ctx._data["stage"] * len(ctx._data["worklist"]) + cursor,
        "total": total,
        "message": f"Repairing {treatment}",
    }
    task = session.get(SystemTask, ctx.task_id)
    if task is not None:
        task.progress_data = data
        task.updated_at = utcnow()
        session.add(task)
    session.commit()
    ctx._data = data


async def _treat(
    session: Session,
    movie: CachedMovie,
    treatment: str,
    tmdb: TMDBClient,
    omdb: OMDbClient,
    ctx: task_runner.TaskContext,
    tvtropes_client: tvtropes.TVTropesClient,
) -> str:
    if treatment == "details":
        await cache_repo.get_movie(session, tmdb, movie.tmdb_id, refresh=True)
    elif treatment == "people":
        await cache_repo.get_movie_cast(session, tmdb, movie.tmdb_id)
        ctx.check_cancelled(force=True)
        await cache_repo.get_movie_directors(session, tmdb, movie.tmdb_id)
        ctx.check_cancelled(force=True)
        await cache_repo.get_movie_crew(session, tmdb, movie.tmdb_id)
    elif treatment == "ratings":
        if not omdb.enabled:
            raise RuntimeError("Configure OMDb in Settings to repair ratings")
        if not provider_budgets.reserve(session.get_bind(), "omdb"):
            raise provider_budgets.BudgetExhausted
        lookup = (
            await omdb.lookup_by_imdb_id(movie.imdb_id)
            if movie.imdb_id
            else await omdb.lookup_by_title(
                movie.title, cache_repo.parse_release_year(movie.release_date)
            )
        )
        if lookup.transient:
            return "error"
        cache_repo.CacheRepo(session).upsert_ratings(movie.tmdb_id, lookup.ratings)
    elif treatment == "tropes":
        if tvtropes.enabled(session):
            try:
                scraped = await anyio.to_thread.run_sync(
                    partial(
                        tvtropes_client.sync_movie,
                        movie,
                        enabled=True,
                    )
                )
                ctx.check_cancelled(force=True)
                evidence = await movie_features.validate_tvtropes(
                    session,
                    movie,
                    ((trope.slug, trope.mapped_slug) for trope in scraped.tropes),
                    llm.load_config(session),
                )
                source_urls = {
                    trope.mapped_slug or trope.slug: trope.url for trope in scraped.tropes
                }
                trope_facets.replace_source(
                    session, movie.tmdb_id, "tvtropes", evidence, source_urls
                )
                movie.tvtropes_work_url = scraped.work_url
                session.add(movie)
            except tvtropes.RobotsDisallowed as exc:
                logger.info("TVTropes scrape skipped by robots policy: %s", exc)
            except tvtropes.CloudflareBlock:
                raise
        ctx.check_cancelled(force=True)
        await movie_features.ensure_tropes(session, [movie])
        known = session.exec(
            select(MovieFacet).where(
                MovieFacet.movie_id == movie.tmdb_id,
                MovieFacet.facet_id == "trope",
                col(MovieFacet.confidence).is_not(None),
            )
        ).first()
        return "ok" if known is not None else "unavailable"
    elif treatment == "facets":
        store.refresh(session, [movie.tmdb_id], check_cancelled=ctx.check_cancelled)
    session.refresh(movie)
    return "ok" if _known(session, movie, treatment) else "unavailable"


async def _embed(
    session: Session,
    movies: list[CachedMovie],
) -> list[tuple[int, str]]:
    pending = [movie for movie in movies if (movie.overview or "").strip()]
    outcomes = [(movie.tmdb_id, "unavailable") for movie in movies if movie not in pending]
    if not pending:
        return outcomes
    config = embeddings.load_config(session)
    try:
        result = await embeddings.embed_batch(config, [movie.overview for movie in pending])
        if (
            len(result.vectors) != len(pending)
            or result.fingerprint not in (config.fingerprint, config.local_fingerprint)
            or any(
                vector.ndim != 1
                or not vector.size
                or not np.all(np.isfinite(vector))
                or np.linalg.norm(vector) == 0
                for vector in result.vectors
            )
        ):
            raise embeddings.EmbeddingUnavailable("Invalid embedding batch")
    except (embeddings.EmbeddingUnavailable, httpx.HTTPError, OSError):
        return outcomes + [(movie.tmdb_id, "error") for movie in pending]
    for movie, vector in zip(pending, result.vectors, strict=True):
        movie.overview_embedding = vector.astype("<f4").tobytes()
        movie.overview_embedding_model = result.fingerprint
        session.add(movie)
        outcomes.append((movie.tmdb_id, "ok"))
    return outcomes


async def _run(ctx: task_runner.TaskContext) -> dict[str, Any]:
    # A request's clients cannot outlive its scope; use the same cache clients/DB overrides
    # with an ephemeral HTTP transport for this task only.
    async with httpx.AsyncClient() as http:
        tmdb, omdb = TMDBClient(http), OMDbClient(http)
        with ctx.session() as session:
            overrides = settings_repo.get_overrides(session)
        tmdb.set_overrides(overrides)
        omdb.set_overrides(overrides)
        trope_client = tvtropes.TVTropesClient(
            check_cancelled=lambda: ctx.check_cancelled(force=True)
        )
        worklist = ctx._data["worklist"]
        treatments = ctx._data["treatments"]
        while ctx._data["stage"] < len(treatments):
            treatment = treatments[ctx._data["stage"]]
            while ctx._data["cursor"] < len(worklist):
                ctx.check_cancelled(force=True)
                cursor = ctx._data["cursor"]
                size = EMBED_BATCH_SIZE if treatment == "embeddings" else 1
                ids = worklist[cursor : cursor + size]
                with ctx.session() as session:
                    movies = [session.get(CachedMovie, movie_id) for movie_id in ids]
                    pending = [
                        movie for movie in movies if movie and _eligible(session, movie, treatment)
                    ]
                    try:
                        if treatment == "embeddings":
                            outcomes = await _embed(session, pending)
                        else:
                            outcomes = []
                            for movie in pending:
                                try:
                                    status = await _treat(
                                        session, movie, treatment, tmdb, omdb, ctx, trope_client
                                    )
                                except TMDBNotFoundError:
                                    session.rollback()
                                    status = "unavailable"
                                except (
                                    task_runner.TaskCancelled,
                                    provider_budgets.BudgetExhausted,
                                    tvtropes.CloudflareBlock,
                                    tvtropes.BatchPageLimit,
                                ):
                                    raise
                                except Exception as exc:  # noqa: BLE001 - persist retryable per-film failures
                                    session.rollback()
                                    status = "error"
                                    ctx._data["last_error"] = str(exc)
                                outcomes.append((movie.tmdb_id, status))
                    except provider_budgets.BudgetExhausted:
                        ctx.set("paused", "omdb_budget")
                        ctx.flush()
                        return {"paused": "omdb_budget", "cursor": cursor}
                    ctx.check_cancelled(force=True)
                    _checkpoint(ctx, session, outcomes, treatment, cursor + len(ids))
            ctx.set("stage", ctx._data["stage"] + 1)
            ctx.set("cursor", 0)
            ctx.flush()
    return {"processed": ctx._data["processed"]}


def _coverage_conditions(session: Session) -> dict[str, Any]:
    ratings = exists(
        select(CachedMovieRating.movie_id).where(
            CachedMovieRating.movie_id == CachedMovie.tmdb_id,
            (
                (func.coalesce(CachedMovieRating.imdb_rating, "") != "")
                | (func.coalesce(CachedMovieRating.rotten_tomatoes, "") != "")
                | (func.coalesce(CachedMovieRating.metacritic, "") != "")
            ),
        )
    )
    fingerprint = embeddings.load_config(session).fingerprint
    conditions = {
        "details": (func.coalesce(CachedMovie.runtime, 0) > 0)
        & col(CachedMovie.origin_country).is_not(None)
        & col(CachedMovie.tagline).is_not(None)
        & col(CachedMovie.overview).is_not(None),
        "people": col(CachedMovie.cast_fetched_at).is_not(None)
        & col(CachedMovie.directors_fetched_at).is_not(None)
        & col(CachedMovie.crew_fetched_at).is_not(None),
        "ratings": ratings,
        "embeddings": col(CachedMovie.overview_embedding).is_not(None)
        & (
            func.coalesce(CachedMovie.overview_embedding_model, embeddings.LOCAL_FINGERPRINT)
            == fingerprint
        ),
    }
    conditions["tropes"] = exists(
        select(MovieFacetStatus.movie_id).where(
            MovieFacetStatus.movie_id == CachedMovie.tmdb_id,
            MovieFacetStatus.family == "spa:tropes",
            MovieFacetStatus.version == 1,
            MovieFacetStatus.status == "ok",
        )
    )
    # All current catalogue families must be covered, not merely a historical spa job.
    conditions["facets"] = select(func.count()).select_from(MovieFacetStatus).where(
        MovieFacetStatus.movie_id == CachedMovie.tmdb_id,
        col(MovieFacetStatus.family).in_(FAMILY_VERSIONS),
        MovieFacetStatus.status == "ok",
        col(MovieFacetStatus.version)
        == case(
            *[
                (MovieFacetStatus.family == family, version)
                for family, version in FAMILY_VERSIONS.items()
            ],
            else_=-1,
        ),
    ).correlate(CachedMovie).scalar_subquery() == len(FAMILY_VERSIONS)
    return conditions


def health(session: Session) -> dict[str, Any]:
    """Coverage reads use SQL only; never hydrate movies or invoke inference."""
    total = session.exec(select(func.count()).select_from(CachedMovie)).one()

    def count(condition) -> int:
        return session.exec(select(func.count()).select_from(CachedMovie).where(condition)).one()

    families = {}
    for family, version in FAMILY_VERSIONS.items():
        condition = exists(
            select(MovieFacetStatus.movie_id).where(
                MovieFacetStatus.movie_id == CachedMovie.tmdb_id,
                MovieFacetStatus.family == family,
                MovieFacetStatus.version == version,
                MovieFacetStatus.status == "ok",
            )
        )
        families[family] = {"known": count(condition), "total": total}
    conditions = _coverage_conditions(session)
    day = utcnow().date().isoformat()
    budgets = []
    for provider, limit in provider_budgets.LIMITS.items():
        row = session.get(ProviderBudget, (provider, day))
        used = row.used if row else 0
        budgets.append(
            {
                "provider": provider,
                "day": day,
                "used": used,
                "limit": limit,
                "remaining": max(0, limit - used),
            }
        )
    return {
        "total_movies": total,
        "coverage": {
            key: {"known": count(value), "total": total} for key, value in conditions.items()
        },
        "families": families,
        "budgets": budgets,
    }
