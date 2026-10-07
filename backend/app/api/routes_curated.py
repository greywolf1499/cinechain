"""Curated canon & Letterboxd ingestion endpoints.

Scraping (`app.services.letterboxd`) is entirely synchronous (curl_cffi) and
MUST run off the event loop. Long scrapes (watchlist, list sync, HQ discovery)
run as `SystemTask`s on FastAPI BackgroundTasks (see `app.services.task_runner`):
the POST returns 202 + the task immediately and clients poll /api/tasks.
"""

from __future__ import annotations

import functools
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlparse

import anyio
from curl_cffi import requests as curl_requests
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, case, col, func, or_, select

from app.api.deps import get_current_admin, get_current_user, get_tmdb_client
from app.api.routes_tasks import TaskOut
from app.config import get_settings
from app.db import get_session
from app.engines.base import RunSetupError
from app.engines.regional_deep_dive import RegionalDeepDiveEngine, _decade_of
from app.models.cache import CachedMovie
from app.models.curated import (
    CanonMovieBadge,
    CuratedList,
    CuratedSourceAccount,
    LetterboxdWatchlist,
)
from app.models.run import RunStep
from app.models.system import SystemTask
from app.models.user import User
from app.services import image_cache, letterboxd, settings_repo, task_runner
from app.services.bridge_paths import parse_countries
from app.services.tmdb import TMDBClient
from app.utils.ids import utcnow

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/curated", tags=["curated"])
slices_router = APIRouter(tags=["curated"])


class CuratedSlices(BaseModel):
    hydrated: int
    total: int
    countries: dict[str, int]
    decades: dict[int, int]
    pairs: dict[str, int]
    indexing: bool = False
    indexing_error: str | None = None


def _canon_ids(session: Session, list_id: str) -> list[int]:
    return list(
        session.exec(
            select(CanonMovieBadge.movie_id)
            .where(CanonMovieBadge.curated_list_id == list_id)
            .distinct()
        ).all()
    )


def _index_outcomes(session: Session, movie_ids: list[int]) -> dict[str, int]:
    """Per-film outcome of a canon indexing pass. A slice needs both a country and a date, so a
    film missing either is counted (and left out of the slices) rather than failing the list."""
    outcomes = {"indexed": 0, "no_country": 0, "undated": 0, "not_found": 0}
    for movie_id in movie_ids:
        movie = session.get(CachedMovie, movie_id)
        if movie is None:
            outcomes["not_found"] += 1
        elif not parse_countries(movie.origin_country):
            outcomes["no_country"] += 1
        elif _decade_of(movie.release_date) is None:
            outcomes["undated"] += 1
        else:
            outcomes["indexed"] += 1
    return outcomes


def _index_message(outcomes: dict[str, int], total: int) -> str:
    skipped = total - outcomes["indexed"]
    message = f"Indexed {outcomes['indexed']}/{total} films"
    if not skipped:
        return f"{message}."
    reasons = [
        (outcomes["no_country"], "without a production country"),
        (outcomes["undated"], "without a release date"),
        (outcomes["not_found"], "not found on TMDB"),
    ]
    detail = ", ".join(f"{count} {label}" for count, label in reasons if count)
    plural = "" if skipped == 1 else "s"
    return f"{message}; {skipped} film{plural} not indexable ({detail})."


def _queue_canon_hydration(
    background_tasks: BackgroundTasks,
    session: Session,
    curated: CuratedList,
    user_id: str,
    tmdb: TMDBClient,
) -> None:
    list_id, title = curated.id, curated.title
    if not curated.is_enabled:
        return

    async def work(ctx: task_runner.TaskContext) -> dict[str, Any]:
        with ctx.session() as db:
            row = db.get(CuratedList, list_id)
            if row is None or not row.is_enabled:
                raise RunSetupError("This canon list is no longer enabled")
            ids = _canon_ids(db, list_id)
            engine = RegionalDeepDiveEngine(db, tmdb)
            await engine._hydrate(ids, None, None, progress=ctx.aprogress, index_all=True)
            outcomes = _index_outcomes(db, ids)
            indexed = outcomes["indexed"]
            # Partial success is success: a film TMDB can't place is reported, not fatal. Only a
            # list nothing could be indexed from (an auth/key failure, an empty answer) fails.
            if ids and not indexed:
                raise RunSetupError(
                    f"Indexed 0/{len(ids)} films; TMDB details are unavailable. "
                    "Sync this list again to retry."
                )
            return {
                "list_id": list_id,
                "hydrated": indexed,
                "total": len(ids),
                **outcomes,
                "message": _index_message(outcomes, len(ids)),
            }

    task_runner.submit_task(
        background_tasks,
        session,
        "canon_hydrate",
        work,
        user_id=user_id,
        dedupe_key=f"canon_hydrate:{list_id}",
        label=f"Indexing {title}",
        link="/lists",
    )


@slices_router.get("/curated-lists/{list_id}/slices", response_model=CuratedSlices)
@router.get("/lists/{list_id}/slices", response_model=CuratedSlices)
def curated_slices(
    list_id: str,
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    _user: User = Depends(get_current_user),
) -> CuratedSlices:
    curated = session.get(CuratedList, list_id)
    if curated is None:
        raise HTTPException(404, detail="Unknown curated list")
    engine = RegionalDeepDiveEngine(session, tmdb)
    rows = engine._slice_rows(list_id)
    movies = {movie.tmdb_id: movie for _, movie in rows}
    countries = sorted(
        {c for movie in movies.values() for c in parse_countries(movie.origin_country)}
    )
    decades = sorted(
        {
            decade
            for movie in movies.values()
            if (decade := _decade_of(movie.release_date)) is not None
        }
    )
    latest = session.exec(
        select(SystemTask)
        .where(SystemTask.dedupe_key == f"canon_hydrate:{list_id}")
        .order_by(col(SystemTask.created_at).desc())
    ).first()
    syncing = session.exec(
        select(SystemTask).where(
            SystemTask.dedupe_key == f"curated_list_sync:{list_id}",
            col(SystemTask.status).in_(task_runner.ACTIVE_STATUSES),
        )
    ).first()
    return CuratedSlices(
        hydrated=sum(
            bool(parse_countries(movie.origin_country))
            and _decade_of(movie.release_date) is not None
            for movie in movies.values()
        ),
        total=len(_canon_ids(session, list_id)),
        countries={country: len(engine._slice(rows, country, None)) for country in countries},
        decades={decade: len(engine._slice(rows, None, decade)) for decade in decades},
        pairs={
            f"{country}:{decade}": count
            for country in countries
            for decade in decades
            if (count := len(engine._slice(rows, country, decade)))
        },
        indexing=bool(syncing or (latest and latest.status in task_runner.ACTIVE_STATUSES)),
        indexing_error=latest.error if latest and latest.status == task_runner.FAILED else None,
    )


def _error_payload(exc: Exception) -> dict[str, Any]:
    """Structured task error: `message` for display, `code` (and `status`) so the
    frontend can render a specific warning."""
    if isinstance(exc, letterboxd.WatchlistNotFound):
        return {
            "code": "watchlist_not_found",
            "status": 404,
            "message": str(exc),
            "username": exc.username,
        }
    if isinstance(exc, letterboxd.CloudflareBlock):
        return {"code": "cloudflare_block", "status": 503, "message": str(exc)}
    return {"code": "scrape_failed", "message": str(exc)}


def _resolve_tmdb_api_key(session: Session) -> str | None:
    overrides = settings_repo.get_overrides(session)
    key = (overrides.get("tmdb_api_key") or get_settings().tmdb_api_key or "").strip()
    return key or None


SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
PROXY_PATH = "/api/images/proxy"


def _list_image_url(row: CuratedList) -> str | None:
    if row.image_filename:
        version = int(row.image_updated_at.timestamp()) if row.image_updated_at else 0
        return f"/api/curated/lists/{row.id}/image?v={version}"
    return row.image_url


class CuratedListOut(BaseModel):
    id: str
    preset_key: str | None
    title: str
    url: str
    badge_prefix: str
    badge_color: str
    is_ranked: bool
    total_items: int
    is_enabled: bool = True
    film_count: int = 0
    description: str | None = None
    preview_posters: list[str] = []
    source_account_id: str | None = None
    last_synced_at: str | None = None
    last_sync_error: str | None = None
    slug: str | None = None
    badge_emoji: str | None = None
    # Remote logos are returned raw; the frontend routes them through /api/images/proxy.
    image_url: str | None = None
    has_custom_image: bool = False
    account_username: str | None = None
    account_display_name: str | None = None
    # Films from this list already watched in any run (the "popularity" sort).
    watched_count: int = 0

    @classmethod
    def from_model(cls, row: CuratedList) -> CuratedListOut:
        try:
            posters = [str(p) for p in json.loads(row.preview_posters or "[]")]
        except (json.JSONDecodeError, TypeError):
            posters = []
        return cls(
            id=row.id,
            preset_key=row.preset_key,
            title=(row.title or "").strip() or letterboxd.title_from_slug(row.slug or row.url),
            url=row.url,
            badge_prefix=row.badge_prefix,
            badge_color=row.badge_color,
            is_ranked=row.is_ranked,
            total_items=row.total_items,
            is_enabled=row.is_enabled,
            film_count=row.film_count,
            description=row.description,
            preview_posters=posters,
            source_account_id=row.source_account_id,
            last_synced_at=row.last_synced_at.isoformat() if row.last_synced_at else None,
            last_sync_error=row.last_sync_error,
            slug=row.slug,
            badge_emoji=row.badge_emoji,
            image_url=_list_image_url(row),
            has_custom_image=bool(row.image_filename),
        )

    @classmethod
    def from_preset(cls, preset_key: str, preset: dict[str, str]) -> CuratedListOut:
        return cls(
            id=preset_key,
            preset_key=preset_key,
            title=preset["title"],
            url=preset["url"],
            badge_prefix=preset["badge"],
            badge_color="#d9a441",
            is_ranked=False,
            total_items=0,
            slug=preset_key,
        )


@router.get("/lists", response_model=list[CuratedListOut])
def list_curated_lists(
    include_disabled: bool = False,
    session: Session = Depends(get_session),
    _current_user: User = Depends(get_current_user),
) -> list[CuratedListOut]:
    """Presets plus custom/discovered lists; discovered-but-disabled lists are
    only returned with `include_disabled` (they are browsed per account)."""
    all_rows = session.exec(select(CuratedList)).all()
    by_preset = {row.preset_key: row for row in all_rows if row.preset_key}
    other_rows = [
        row for row in all_rows if row.preset_key is None and (include_disabled or row.is_enabled)
    ]

    results = [
        CuratedListOut.from_model(by_preset[preset_key])
        if preset_key in by_preset
        else CuratedListOut.from_preset(preset_key, preset)
        for preset_key, preset in letterboxd.PRESETS.items()
    ]
    results.extend(CuratedListOut.from_model(row) for row in other_rows)
    return results


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:64].strip("-") or "list"


def _unique_slug(session: Session, base: str, exclude_id: str | None = None) -> str:
    base = _slugify(base)
    slug, n = base, 2
    while True:
        clash = session.exec(select(CuratedList.id).where(CuratedList.slug == slug)).first()
        if clash is None or clash == exclude_id:
            return slug
        slug = f"{base[:60]}-{n}"
        n += 1


def _owner_username(url: str) -> str | None:
    """`https://letterboxd.com/<user>/list/<slug>/` -> `<user>`."""
    segments = [part for part in urlparse(url).path.split("/") if part]
    if len(segments) >= 2 and segments[1] == "list":
        return segments[0].lower()
    return None


def _link_to_account(session: Session, row: CuratedList) -> None:
    """Attaches a list to the curator profile that owns its Letterboxd URL (HQ
    accounts included), so imported custom URLs show up under that curator."""
    if row.source_account_id is not None:
        return
    username = _owner_username(row.url)
    if username is None:
        return
    account = _get_account(session, username)
    if account is not None:
        row.source_account_id = account.id


def _prepare_new_list(session: Session, row: CuratedList, slug_hint: str | None = None) -> None:
    _ensure_seed_accounts(session)
    row.slug = _unique_slug(
        session, slug_hint or row.preset_key or _list_slug_hint(row.url, row.title)
    )
    _link_to_account(session, row)


def _list_slug_hint(url: str, title: str) -> str:
    segments = [part for part in urlparse(url).path.split("/") if part]
    return segments[-1] if segments else title


def _get_or_create_list(session: Session, list_id: str) -> CuratedList:
    existing = session.get(CuratedList, list_id)
    if existing is not None:
        return existing
    by_slug = session.exec(select(CuratedList).where(CuratedList.slug == list_id)).first()
    if by_slug is not None:
        return by_slug
    # `list_id` may be a preset_key rather than a real row id (first-ever
    # sync of a preset) - check for an already-synced row under that preset
    # before creating a duplicate CuratedList on every re-sync.
    existing_by_preset = session.exec(
        select(CuratedList).where(CuratedList.preset_key == list_id)
    ).first()
    if existing_by_preset is not None:
        return existing_by_preset
    preset = letterboxd.PRESETS.get(list_id)
    if preset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown curated list")
    curated_list = CuratedList(
        preset_key=list_id, title=preset["title"], url=preset["url"], badge_prefix=preset["badge"]
    )
    _prepare_new_list(session, curated_list)
    session.add(curated_list)
    session.commit()
    session.refresh(curated_list)
    return curated_list


def _badge_label(curated_list: CuratedList, rank: int | None) -> str:
    return f"{curated_list.badge_prefix} #{rank}" if rank else curated_list.badge_prefix


def _persist_sync_result(
    session: Session, curated_list: CuratedList, result: dict
) -> dict[str, int]:
    """Replaces the list's badges in a single transaction: surviving badges are updated, the
    ones no longer on the list are deleted and the list row is stamped, all committed once. A
    failed scrape never reaches this function, so the previous snapshot survives untouched."""
    films = result["films"]
    tv_titles = sum(1 for film in films if film.get("tmdb_type") == "tv")
    wanted: dict[int, int | None] = {}
    for film in films:
        # A `tv` TMDB id is a series id, never a movie id: it must not become a movie badge.
        if not film.get("tmdb_id") or film.get("tmdb_type") == "tv":
            continue
        wanted.setdefault(film["tmdb_id"], film.get("rank") if result["is_ranked"] else None)

    kept: set[int] = set()
    for badge in session.exec(
        select(CanonMovieBadge).where(CanonMovieBadge.curated_list_id == curated_list.id)
    ).all():
        if badge.movie_id in wanted and badge.movie_id not in kept:
            kept.add(badge.movie_id)
            badge.rank = wanted[badge.movie_id]
            badge.badge_label = _badge_label(curated_list, badge.rank)
            session.add(badge)
        else:
            session.delete(badge)
    for movie_id, rank in wanted.items():
        if movie_id in kept:
            continue
        session.add(
            CanonMovieBadge(
                curated_list_id=curated_list.id,
                movie_id=movie_id,
                badge_label=_badge_label(curated_list, rank),
                rank=rank,
            )
        )

    curated_list.is_ranked = result["is_ranked"]
    curated_list.total_items = len(wanted)
    curated_list.is_enabled = True
    curated_list.last_synced_at = utcnow()
    curated_list.last_sync_error = None
    session.add(curated_list)
    session.commit()
    return {"matched": len(wanted), "total": len(films), "tv_titles": tv_titles}


@router.post("/sync/{list_id}", status_code=status.HTTP_202_ACCEPTED, response_model=TaskOut)
def sync_curated_list(
    list_id: str,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    admin: User = Depends(get_current_admin),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> TaskOut:
    """Starts a background sync (a `SystemTask`) and returns its id immediately;
    poll `GET /api/tasks/{id}` or stream `GET /api/tasks/stream` for progress."""
    curated_list = _get_or_create_list(session, list_id)
    tmdb_api_key = _resolve_tmdb_api_key(session)
    row_id, url, title = curated_list.id, curated_list.url, curated_list.title
    user_id = admin.id

    def work(ctx: task_runner.TaskContext) -> dict[str, Any]:
        try:
            result = letterboxd.scrape_letterboxd_list(
                url, tmdb_api_key=tmdb_api_key, progress_callback=ctx.progress
            )
        except Exception as exc:
            with ctx.session() as db:
                failed = db.get(CuratedList, row_id)
                if failed is not None:
                    failed.last_sync_error = str(exc)
                    db.add(failed)
                    db.commit()
            raise
        ctx.check_cancelled(force=True)
        with ctx.session() as db:
            row = db.get(CuratedList, row_id)
            if row is None:
                raise RunSetupError("This canon list no longer exists")
            counts = _persist_sync_result(db, row, result)
            _queue_canon_hydration(background_tasks, db, row, user_id, tmdb)
        return {
            "matched": counts["matched"],
            "total_films": counts["total"],
            "tv_titles": counts["tv_titles"],
            "is_ranked": result["is_ranked"],
        }

    task, _ = task_runner.submit_task(
        background_tasks,
        session,
        "curated_list_sync",
        work,
        user_id=user_id,
        dedupe_key=f"curated_list_sync:{row_id}",
        label=f"Syncing {title}",
        link="/lists",
        describe_error=_error_payload,
    )
    return TaskOut.from_model(task)


class CustomListRequest(BaseModel):
    url: str
    title: str | None = None
    badge_prefix: str | None = None
    badge_color: str = "#d9a441"


@router.post("/custom", response_model=CuratedListOut, status_code=status.HTTP_201_CREATED)
def create_custom_list(
    payload: CustomListRequest,
    session: Session = Depends(get_session),
    _admin: User = Depends(get_current_admin),
) -> CuratedListOut:
    badge_prefix = letterboxd.derive_badge_prefix(payload.url, payload.badge_prefix)
    existing = _find_list_by_url(session, payload.url)
    if existing is not None:
        existing.badge_prefix = badge_prefix
        existing.badge_color = payload.badge_color
        existing.is_enabled = True
        _ensure_seed_accounts(session)
        _link_to_account(session, existing)
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return _list_out(session, existing)
    curated_list = CuratedList(
        title=(payload.title or "").strip() or letterboxd.title_from_slug(payload.url),
        url=payload.url,
        badge_prefix=badge_prefix,
        badge_color=payload.badge_color,
    )
    _prepare_new_list(session, curated_list)
    session.add(curated_list)
    session.commit()
    session.refresh(curated_list)
    return _list_out(session, curated_list)


class WatchlistSyncRequest(BaseModel):
    letterboxd_username: str


class WatchlistStatus(BaseModel):
    letterboxd_username: str | None
    synced_at: datetime | None
    total_items: int
    last_error: str | None = None


@router.get("/watchlist/status", response_model=WatchlistStatus)
def get_watchlist_status(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> WatchlistStatus:
    """Returns the current user's last successful watchlist sync metadata."""
    total_items, aggregate_synced_at = session.exec(
        select(
            func.count(LetterboxdWatchlist.id),
            func.max(LetterboxdWatchlist.synced_at),
        ).where(LetterboxdWatchlist.user_id == current_user.id)
    ).one()
    username = current_user.letterboxd_username
    synced_at = current_user.watchlist_synced_at
    if total_items:
        aggregate_username = session.exec(
            select(LetterboxdWatchlist.letterboxd_username)
            .where(LetterboxdWatchlist.user_id == current_user.id)
            .order_by(col(LetterboxdWatchlist.synced_at).desc())
            .limit(1)
        ).first()
        username = username or aggregate_username
        synced_at = synced_at or aggregate_synced_at

    return WatchlistStatus(
        letterboxd_username=username,
        synced_at=synced_at,
        total_items=total_items,
        last_error=current_user.watchlist_sync_error,
    )


@router.post("/watchlist/sync", status_code=status.HTTP_202_ACCEPTED, response_model=TaskOut)
def sync_watchlist(
    payload: WatchlistSyncRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> TaskOut:
    """Starts a background watchlist sync and returns the `SystemTask` immediately,
    so a huge watchlist can't hit an HTTP timeout. Errors (e.g. a private or
    deleted account) land on the task as `progress_data.error.code`."""
    tmdb_api_key = _resolve_tmdb_api_key(session)
    user_id = current_user.id
    try:
        username = letterboxd.clean_username(payload.letterboxd_username)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    def work(ctx: task_runner.TaskContext) -> dict[str, Any]:
        try:
            result = letterboxd.scrape_letterboxd_watchlist(
                username, tmdb_api_key=tmdb_api_key, progress_callback=ctx.progress
            )
        except letterboxd.WatchlistNotFound as exc:
            try:
                with ctx.session() as db:
                    user = db.get(User, user_id)
                    if user is None:
                        raise RuntimeError("Watchlist owner no longer exists.")
                    user.watchlist_sync_error = str(exc)
                    db.add(user)
                    db.commit()
            except Exception as persist_error:
                logger.exception("Could not persist watchlist sync warning for %s", username)
                raise RuntimeError(
                    f"Failed to save watchlist sync warning: {persist_error.__class__.__name__}"
                ) from persist_error
            raise
        ctx.check_cancelled(force=True)
        try:
            with ctx.session() as db:
                matched = _persist_watchlist(db, user_id, username, result["films"])
        except Exception as exc:
            logger.exception("Watchlist persistence failed for %s", username)
            raise RuntimeError(f"Failed to save watchlist: {exc.__class__.__name__}") from exc
        return {"matched": matched, "total_films": len(result["films"])}

    task, _ = task_runner.submit_task(
        background_tasks,
        session,
        "watchlist_sync",
        work,
        user_id=user_id,
        dedupe_key=f"watchlist_sync:{user_id}:{username}",
        label=f"Letterboxd watchlist ({username})",
        link="/settings/integrations#watchlist",
        describe_error=_error_payload,
    )
    return TaskOut.from_model(task)


def _persist_watchlist(
    session: Session, user_id: str, username: str, films: list[dict[str, Any]]
) -> int:
    """Replaces the user's stored watchlist atomically; tolerates scraped rows
    with a missing title/year and duplicate TMDB ids."""
    rows: list[LetterboxdWatchlist] = []
    seen: set[int] = set()
    for film in films:
        try:
            movie_id = int(film.get("tmdb_id") or 0)
        except (TypeError, ValueError):
            continue
        if not movie_id or movie_id in seen:
            continue
        seen.add(movie_id)
        raw_year = film.get("year")
        rows.append(
            LetterboxdWatchlist(
                user_id=user_id,
                letterboxd_username=username,
                movie_id=movie_id,
                title=str(film.get("title") or film.get("slug") or movie_id),
                year=raw_year if isinstance(raw_year, int) else None,
            )
        )

    for old in session.exec(
        select(LetterboxdWatchlist).where(LetterboxdWatchlist.user_id == user_id)
    ).all():
        session.delete(old)
    user = session.get(User, user_id)
    if user is None:
        raise RuntimeError("Watchlist owner no longer exists.")
    user.letterboxd_username = username
    user.watchlist_synced_at = utcnow()
    user.watchlist_sync_error = None
    session.add(user)
    session.add_all(rows)
    session.commit()
    return len(rows)


def _norm_url(url: str) -> str:
    return url.strip().rstrip("/").lower()


def _find_list_by_url(session: Session, url: str) -> CuratedList | None:
    target = _norm_url(url)
    for row in session.exec(select(CuratedList)).all():
        if _norm_url(row.url) == target:
            return row
    return None


def _list_out(session: Session, row: CuratedList) -> CuratedListOut:
    out = CuratedListOut.from_model(row)
    account = (
        session.get(CuratedSourceAccount, row.source_account_id) if row.source_account_id else None
    )
    if account is not None:
        out.account_username = account.username
        out.account_display_name = account.display_name
    return out


def _unwrap_proxy_url(url: str) -> str:
    """Accepts either a raw Letterboxd image URL or one already routed through our proxy."""
    parsed = urlparse(url.strip())
    if parsed.path == PROXY_PATH:
        inner = parse_qs(parsed.query).get("url")
        if inner:
            return inner[0]
    return url.strip()


def list_images_dir() -> Path:
    path = get_settings().config_dir / "list_images"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _delete_uploaded_image(row: CuratedList) -> None:
    if row.image_filename:
        (list_images_dir() / Path(row.image_filename).name).unlink(missing_ok=True)
    row.image_filename = None
    row.image_updated_at = None


class ListUpdate(BaseModel):
    is_enabled: bool | None = None
    badge_prefix: str | None = None
    badge_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    badge_emoji: str | None = Field(default=None, max_length=16)  # "" clears it
    slug: str | None = Field(default=None, min_length=1, max_length=64, pattern=SLUG_PATTERN)
    image_url: str | None = Field(default=None, max_length=2048)  # "" clears it
    clear_image: bool = False


@router.patch("/lists/{list_id}", response_model=CuratedListOut)
def update_curated_list(
    list_id: str,
    payload: ListUpdate,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    _admin: User = Depends(get_current_admin),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> CuratedListOut:
    """Tier 2 toggle plus unified customization (slug, emoji badge, logo) for
    every list, preset or custom. Enabling lists it for syncing; disabling drops its badges."""
    curated_list = _get_or_create_list(session, list_id)
    enabling = payload.is_enabled is True and not curated_list.is_enabled
    if payload.slug is not None and payload.slug != curated_list.slug:
        if session.exec(select(CuratedList.id).where(CuratedList.slug == payload.slug)).first():
            raise HTTPException(
                status.HTTP_409_CONFLICT, detail=f"Slug '{payload.slug}' is already in use"
            )
        curated_list.slug = payload.slug
    if payload.badge_prefix is not None:
        curated_list.badge_prefix = letterboxd.derive_badge_prefix(
            curated_list.url, payload.badge_prefix
        )
    if payload.badge_color is not None:
        curated_list.badge_color = payload.badge_color
    if payload.badge_emoji is not None:
        curated_list.badge_emoji = payload.badge_emoji.strip() or None
    if payload.clear_image:
        _delete_uploaded_image(curated_list)
        curated_list.image_url = None
    elif payload.image_url is not None:
        if payload.image_url.strip():
            try:
                chosen = image_cache.validate_image_url(_unwrap_proxy_url(payload.image_url))
            except image_cache.ImageProxyError as exc:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.detail
                ) from exc
            _delete_uploaded_image(curated_list)
            curated_list.image_url = chosen
        else:
            curated_list.image_url = None
    if payload.is_enabled is not None:
        curated_list.is_enabled = payload.is_enabled
        if not payload.is_enabled:
            for badge in session.exec(
                select(CanonMovieBadge).where(CanonMovieBadge.curated_list_id == curated_list.id)
            ).all():
                session.delete(badge)
            curated_list.total_items = 0
            curated_list.last_synced_at = None
    session.add(curated_list)
    session.commit()
    session.refresh(curated_list)
    if enabling:
        _queue_canon_hydration(background_tasks, session, curated_list, _admin.id, tmdb)
    return _list_out(session, curated_list)


MAX_UPLOAD_BYTES = 2 * 1024 * 1024


def _sniff_image_type(data: bytes) -> tuple[str, str] | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if data[:4] == b"GIF8":
        return "gif", "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


@router.put("/lists/{list_id}/image", response_model=CuratedListOut)
async def upload_list_image(
    list_id: str,
    request: Request,
    session: Session = Depends(get_session),
    _admin: User = Depends(get_current_admin),
) -> CuratedListOut:
    """Raw-body upload (PNG/JPEG/GIF/WebP, max 2MB) - the type is sniffed from
    the bytes, never trusted from the client's Content-Type."""
    curated_list = _get_or_create_list(session, list_id)
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, detail="Image must be 2MB or smaller")
    sniffed = _sniff_image_type(bytes(data))
    if sniffed is None:
        raise HTTPException(415, detail="Upload a PNG, JPEG, GIF or WebP image")
    extension, _ = sniffed
    _delete_uploaded_image(curated_list)
    filename = f"{curated_list.id}.{extension}"
    (list_images_dir() / filename).write_bytes(bytes(data))
    curated_list.image_filename = filename
    curated_list.image_updated_at = utcnow()
    curated_list.image_url = None
    session.add(curated_list)
    session.commit()
    session.refresh(curated_list)
    return _list_out(session, curated_list)


@router.get("/lists/{list_id}/image")
def get_list_image(
    list_id: str,
    session: Session = Depends(get_session),
    _current_user: User = Depends(get_current_user),
) -> FileResponse:
    curated_list = session.get(CuratedList, list_id)
    if curated_list is None or not curated_list.image_filename:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No custom image")
    path = list_images_dir() / Path(curated_list.image_filename).name
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No custom image")
    media_type = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "gif": "image/gif",
        "webp": "image/webp",
    }.get(path.suffix.lstrip("."), "application/octet-stream")
    return FileResponse(
        path, media_type=media_type, headers={"Cache-Control": "private, max-age=86400"}
    )


class ListPage(BaseModel):
    items: list[CuratedListOut]
    total: int
    page: int
    page_size: int
    pages: int


def _ensure_preset_rows(session: Session) -> None:
    """Materializes the preset lists as real rows so they can be searched,
    sorted and paginated in SQL like every other list."""
    existing = set(
        session.exec(
            select(CuratedList.preset_key).where(col(CuratedList.preset_key).is_not(None))
        ).all()
    )
    missing = [key for key in letterboxd.PRESETS if key not in existing]
    for key in missing:
        preset = letterboxd.PRESETS[key]
        row = CuratedList(
            preset_key=key, title=preset["title"], url=preset["url"], badge_prefix=preset["badge"]
        )
        _prepare_new_list(session, row)
        session.add(row)
    if missing:
        session.commit()


def _like(column: Any, needle: str) -> Any:
    return func.lower(column).contains(needle.lower(), autoescape=True)


@router.get("/lists/browse", response_model=ListPage)
def browse_curated_lists(
    q: str = Query(default="", max_length=100),
    sort: Literal["name", "film_count", "popularity", "synced"] = "film_count",
    order: Literal["asc", "desc"] | None = None,
    state: Literal["all", "enabled", "disabled"] = "all",
    account: str | None = Query(default=None, max_length=64),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_session),
    _current_user: User = Depends(get_current_user),
) -> ListPage:
    """Server-side search / sort / filter / pagination over every list. `popularity`
    ranks lists by how many of their films are already watched in runs."""
    _ensure_seed_accounts(session)
    _ensure_preset_rows(session)

    watched = (
        select(
            CanonMovieBadge.curated_list_id.label("list_id"),
            func.count(func.distinct(CanonMovieBadge.movie_id)).label("n"),
        )
        .where(
            col(CanonMovieBadge.movie_id).in_(
                select(RunStep.movie_id).where(RunStep.status == "watched")
            )
        )
        .group_by(CanonMovieBadge.curated_list_id)
        .subquery()
    )
    watched_n = func.coalesce(watched.c.n, 0)
    stmt = (
        select(
            CuratedList, watched_n, CuratedSourceAccount.username, CuratedSourceAccount.display_name
        )
        .outerjoin(watched, watched.c.list_id == CuratedList.id)
        .outerjoin(CuratedSourceAccount, CuratedSourceAccount.id == CuratedList.source_account_id)
    )
    needle = q.strip()
    if needle:
        stmt = stmt.where(
            or_(
                _like(CuratedList.title, needle),
                _like(CuratedList.description, needle),
                _like(CuratedList.badge_prefix, needle),
                _like(CuratedSourceAccount.username, needle),
                _like(CuratedSourceAccount.display_name, needle),
            )
        )
    if state == "enabled":
        stmt = stmt.where(CuratedList.is_enabled == True)
    elif state == "disabled":
        stmt = stmt.where(CuratedList.is_enabled == False)
    if account:
        stmt = stmt.where(CuratedSourceAccount.username == account.strip().lower())

    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()

    direction = order or ("asc" if sort == "name" else "desc")
    sort_column = {
        "name": func.lower(CuratedList.title),
        "film_count": CuratedList.film_count,
        "popularity": watched_n,
        "synced": CuratedList.last_synced_at,
    }[sort]
    ordering = sort_column.asc() if direction == "asc" else sort_column.desc()
    rows = session.exec(
        stmt.order_by(ordering, func.lower(CuratedList.title), CuratedList.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    items = []
    for row, watched_count, username, display_name in rows:
        out = CuratedListOut.from_model(row)
        out.watched_count = int(watched_count)
        out.account_username = username
        out.account_display_name = display_name
        items.append(out)
    return ListPage(
        items=items,
        total=int(total),
        page=page,
        page_size=page_size,
        pages=max(1, -(-int(total) // page_size)),
    )


# ---------------------------------------------------------
# Tier 0: curator accounts / Tier 1: their published lists
# ---------------------------------------------------------
class AccountOut(BaseModel):
    username: str
    display_name: str | None
    avatar_url: str | None
    bio: str | None
    is_hq: bool
    account_tier: str | None
    total_public_lists: int
    last_inspected_at: str | None
    lists_discovered_at: str | None
    discovered_lists: int
    enabled_lists: int


class AccountListsOut(BaseModel):
    account: AccountOut
    discovered: bool
    partial: bool = False
    error: str | None = None
    lists: list[CuratedListOut]


def _normalize_username(username: str) -> str:
    try:
        return letterboxd.clean_username(username).lower()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _ensure_seed_accounts(session: Session) -> None:
    existing = set(session.exec(select(CuratedSourceAccount.username)).all())
    added = False
    for seed in letterboxd.SEED_ACCOUNTS:
        if seed["username"] not in existing:
            session.add(
                CuratedSourceAccount(
                    username=seed["username"], display_name=seed["display_name"], is_hq=True
                )
            )
            added = True
    if added:
        session.commit()


def _get_account(session: Session, username: str) -> CuratedSourceAccount | None:
    return session.exec(
        select(CuratedSourceAccount).where(CuratedSourceAccount.username == username)
    ).first()


def _upsert_account(
    session: Session, username: str, data: dict[str, Any]
) -> tuple[CuratedSourceAccount, bool]:
    account = _get_account(session, username)
    created = account is None
    if account is None:
        account = CuratedSourceAccount(username=username)
    for field in ("display_name", "avatar_url", "bio", "account_tier", "total_public_lists"):
        if data.get(field) is not None:
            setattr(account, field, data[field])
    if data.get("is_hq"):
        account.is_hq = True
    session.add(account)
    session.commit()
    session.refresh(account)
    return account, created


# SUM over a Boolean column is coerced back to a bool by SQLAlchemy (2 -> True), so count via an int CASE.
_enabled_flag = case((col(CuratedList.is_enabled).is_(True), 1), else_=0)


def _account_out(
    session: Session,
    account: CuratedSourceAccount,
    discovered: int | None = None,
    enabled: int | None = None,
) -> AccountOut:
    if discovered is None or enabled is None:
        counts = session.exec(
            select(func.count(), func.coalesce(func.sum(_enabled_flag), 0)).where(
                CuratedList.source_account_id == account.id
            )
        ).one()
        discovered, enabled = int(counts[0]), int(counts[1])
    return AccountOut(
        username=account.username,
        display_name=account.display_name,
        avatar_url=account.avatar_url,
        bio=account.bio,
        is_hq=account.is_hq,
        account_tier=account.account_tier,
        total_public_lists=account.total_public_lists,
        last_inspected_at=account.last_inspected_at.isoformat()
        if account.last_inspected_at
        else None,
        lists_discovered_at=account.lists_discovered_at.isoformat()
        if account.lists_discovered_at
        else None,
        discovered_lists=discovered,
        enabled_lists=enabled,
    )


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(
    session: Session = Depends(get_session),
    _current_user: User = Depends(get_current_user),
) -> list[AccountOut]:
    _ensure_seed_accounts(session)
    accounts = session.exec(
        select(CuratedSourceAccount).order_by(CuratedSourceAccount.created_at)
    ).all()
    return [_account_out(session, account) for account in accounts]


class AccountPage(BaseModel):
    items: list[AccountOut]
    total: int
    page: int
    page_size: int
    pages: int


@router.get("/accounts/browse", response_model=AccountPage)
def browse_accounts(
    q: str = Query(default="", max_length=100),
    sort: Literal["name", "lists", "enabled"] = "name",
    kind: Literal["all", "hq", "other"] = "all",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=24, ge=1, le=100),
    session: Session = Depends(get_session),
    _current_user: User = Depends(get_current_user),
) -> AccountPage:
    """Paginated curator directory (hundreds of discovered HQ accounts)."""
    _ensure_seed_accounts(session)
    counts = (
        select(
            CuratedList.source_account_id.label("account_id"),
            func.count().label("lists"),
            func.coalesce(func.sum(_enabled_flag), 0).label("enabled"),
        )
        .group_by(CuratedList.source_account_id)
        .subquery()
    )
    lists_n = func.coalesce(counts.c.lists, 0)
    enabled_n = func.coalesce(counts.c.enabled, 0)
    name = func.lower(
        func.coalesce(CuratedSourceAccount.display_name, CuratedSourceAccount.username)
    )
    stmt = select(CuratedSourceAccount, lists_n, enabled_n).outerjoin(
        counts, counts.c.account_id == CuratedSourceAccount.id
    )
    needle = q.strip()
    if needle:
        stmt = stmt.where(
            or_(
                _like(CuratedSourceAccount.username, needle),
                _like(CuratedSourceAccount.display_name, needle),
                _like(CuratedSourceAccount.bio, needle),
            )
        )
    if kind == "hq":
        stmt = stmt.where(CuratedSourceAccount.is_hq == True)
    elif kind == "other":
        stmt = stmt.where(CuratedSourceAccount.is_hq == False)

    total = int(session.exec(select(func.count()).select_from(stmt.subquery())).one())
    ordering = {
        "name": [name.asc()],
        "lists": [lists_n.desc(), name.asc()],
        "enabled": [enabled_n.desc(), name.asc()],
    }[sort]
    rows = session.exec(
        stmt.order_by(*ordering, CuratedSourceAccount.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items = [
        _account_out(session, account, int(lists), int(enabled)) for account, lists, enabled in rows
    ]
    return AccountPage(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=max(1, -(-total // page_size)),
    )


@router.post("/accounts/discover-hq", status_code=status.HTTP_202_ACCEPTED, response_model=TaskOut)
def discover_hq(
    background_tasks: BackgroundTasks,
    target: str | None = None,
    max_pages: int = Query(default=5, ge=1, le=50),
    session: Session = Depends(get_session),
    admin: User = Depends(get_current_admin),
) -> TaskOut:
    """Starts a background crawl of the Letterboxd HQ directory (or `target`'s
    followed HQ accounts) that tracks every account found. The task result is
    `{discovered, new, partial, error}`."""
    if target is not None:
        target = _normalize_username(target)
    _ensure_seed_accounts(session)

    def work(ctx: task_runner.TaskContext) -> dict[str, Any]:
        found = letterboxd.discover_hq_accounts(
            target, max_pages=max_pages, progress_callback=ctx.progress
        )
        new_accounts = 0
        with ctx.session() as db:
            for account in found["accounts"]:
                ctx.check_cancelled()
                _, created = _upsert_account(db, account["username"].lower(), account)
                new_accounts += int(created)
        return {
            "discovered": len(found["accounts"]),
            "new": new_accounts,
            "partial": found["partial"],
            "error": found["error"],
        }

    task, _ = task_runner.submit_task(
        background_tasks,
        session,
        "discover_hq",
        work,
        user_id=admin.id,
        dedupe_key=f"discover_hq:{target or 'directory'}",
        label="Discovering HQ accounts" + (f" (via {target})" if target else ""),
        link="/curators",
        describe_error=_error_payload,
    )
    return TaskOut.from_model(task)


def _scrape_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, letterboxd.CloudflareBlock):
        return HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail=f"Letterboxd blocked the request: {exc}"
        )
    if letterboxd.is_not_found(exc):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail="Letterboxd account not found")
    return HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Letterboxd request failed: {exc}")


@router.post("/accounts/{username}/inspect", response_model=AccountOut)
async def inspect_account(
    username: str,
    session: Session = Depends(get_session),
    _admin: User = Depends(get_current_admin),
) -> AccountOut:
    username = _normalize_username(username)
    try:
        data = await anyio.to_thread.run_sync(
            functools.partial(letterboxd.inspect_account, username)
        )
    except (letterboxd.CloudflareBlock, curl_requests.exceptions.RequestException) as exc:
        raise _scrape_http_error(exc) from exc

    account, _ = _upsert_account(session, username, data)
    account.last_inspected_at = utcnow()
    session.add(account)
    session.commit()
    session.refresh(account)
    return _account_out(session, account)


def _persist_discovered_lists(
    session: Session, account: CuratedSourceAccount, discovered: list[dict[str, Any]]
) -> None:
    rows = session.exec(select(CuratedList)).all()
    by_url = {_norm_url(row.url): row for row in rows}
    by_preset = {row.preset_key: row for row in rows if row.preset_key}
    preset_by_url = {_norm_url(p["url"]): (key, p) for key, p in letterboxd.PRESETS.items()}

    for entry in discovered:
        url_key = _norm_url(entry["url"])
        preset_match = preset_by_url.get(url_key)
        row = by_url.get(url_key) or (by_preset.get(preset_match[0]) if preset_match else None)
        if row is None:
            if preset_match:
                key, preset = preset_match
                row = CuratedList(
                    preset_key=key,
                    title=preset["title"],
                    url=entry["url"],
                    badge_prefix=preset["badge"],
                    is_enabled=True,
                )
            else:
                row = CuratedList(
                    title=entry["title"],
                    url=entry["url"],
                    badge_prefix=letterboxd.derive_badge_prefix(entry["url"]),
                    is_enabled=False,
                )
            _prepare_new_list(session, row, slug_hint=_list_slug_hint(entry["url"], entry["title"]))
        elif entry["title"] and (
            (row.preset_key is None and row.last_synced_at is None) or not (row.title or "").strip()
        ):
            row.title = entry["title"]
        row.source_account_id = account.id
        if row.slug is None:
            _prepare_new_list(session, row)
        row.film_count = entry["total_films"]
        row.description = entry["description"]
        row.preview_posters = json.dumps(entry["preview_posters"])
        session.add(row)
        by_url[url_key] = row

    account.lists_discovered_at = utcnow()
    account.total_public_lists = max(account.total_public_lists, len(discovered))
    session.add(account)
    session.commit()


@router.get("/accounts/{username}/lists", response_model=AccountListsOut)
async def get_account_lists(
    username: str,
    refresh: bool = False,
    max_pages: int = Query(default=10, ge=1, le=50),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AccountListsOut:
    """Browse a curator's published lists. Admins trigger the Letterboxd crawl
    (first visit or `refresh`); everyone else reads the stored catalog."""
    username = _normalize_username(username)
    _ensure_seed_accounts(session)
    account = _get_account(session, username)
    needs_scrape = refresh or account is None or account.lists_discovered_at is None
    partial, error = False, None

    if needs_scrape and current_user.is_admin:
        try:
            result = await anyio.to_thread.run_sync(
                functools.partial(letterboxd.discover_user_lists, username, max_pages=max_pages)
            )
        except (letterboxd.CloudflareBlock, curl_requests.exceptions.RequestException) as exc:
            raise _scrape_http_error(exc) from exc
        if account is None:
            account, _ = _upsert_account(session, username, {})
        _persist_discovered_lists(session, account, result["lists"])
        partial, error = result["partial"], result["error"]
        session.refresh(account)
    elif account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown curator account")

    rows = session.exec(
        select(CuratedList)
        .where(CuratedList.source_account_id == account.id)
        # type: ignore[attr-defined]
        .order_by(CuratedList.film_count.desc(), CuratedList.title)
    ).all()
    return AccountListsOut(
        account=_account_out(session, account),
        discovered=account.lists_discovered_at is not None,
        partial=partial,
        error=error,
        lists=[_list_out(session, row) for row in rows],
    )


class BadgesBulkRequest(BaseModel):
    movie_ids: list[int]


class BadgeOut(BaseModel):
    badge_label: str
    badge_color: str
    badge_emoji: str | None = None


@router.post("/badges/bulk", response_model=dict[str, list[BadgeOut]])
def get_badges_bulk(
    payload: BadgesBulkRequest,
    session: Session = Depends(get_session),
    _current_user: User = Depends(get_current_user),
) -> dict[str, list[BadgeOut]]:
    if not payload.movie_ids:
        return {}
    rows = session.exec(
        select(CanonMovieBadge, CuratedList)
        .join(CuratedList, CanonMovieBadge.curated_list_id == CuratedList.id)
        .where(CanonMovieBadge.movie_id.in_(payload.movie_ids))
    ).all()
    result: dict[str, list[BadgeOut]] = {}
    for badge, curated_list in rows:
        result.setdefault(str(badge.movie_id), []).append(
            BadgeOut(
                badge_label=badge.badge_label,
                badge_color=curated_list.badge_color,
                badge_emoji=curated_list.badge_emoji,
            )
        )
    return result
