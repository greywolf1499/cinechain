"""Letterboxd diary ingestion (CSV export / RSS feed) -> `RunStep` history.

Every imported film becomes a `watched` `RunStep` on one per-user, hidden
`game_type="import"` run, so Passport aggregation reads a single unified table.

Memory: the CSV is streamed row by row (`csv.DictReader` over the file) and
processed in chunks of `CHUNK_SIZE`; only the current chunk, a (title, year) ->
id memo and the set of already-imported (movie, date) pairs are held in RAM.

TMDB: titles are resolved with the async multi-pass resolver, then details and
directors come through the JIT cache. Everything that touches TMDB goes through
`fetch_with_backoff`, so a 429 pauses with `asyncio.sleep` (like the bridge
solver) instead of failing the import; the event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import csv
import itertools
import logging
import re
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import anyio
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.models.run import IMPORT_GAME_TYPE, Run, RunParticipant, RunStep
from app.services import cache_repo, letterboxd
from app.services.task_runner import TaskContext
from app.services.tmdb import TMDBClient, TMDBError
from app.services.tmdb_backoff import DeadlineReached, fetch_with_backoff
from app.services.tmdb_resolver import resolve_movie_id
from app.utils.dates import parse_release_year
from app.utils.ids import utcnow

logger = logging.getLogger(__name__)

IMPORT_RUN_NAME = "Letterboxd Import"
CHUNK_SIZE = 25
RESOLVE_CONCURRENCY = 5
MAX_CONSECUTIVE_STALLS = 3
MAX_UNRESOLVED_REPORTED = 50
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
PER_CALL_DEADLINE_SECONDS = 120.0

_TITLE_HEADERS = ("name", "title", "film", "film title")
_YEAR_HEADERS = ("year", "film year", "release year")
_WATCHED_HEADERS = ("watched date", "watcheddate", "watched_date", "date watched")
_LOGGED_HEADERS = ("date",)  # watched.csv / diary "Date" = when it was logged
_URI_HEADERS = ("letterboxd uri", "letterboxd url", "url")


class ImportAborted(Exception):
    """The import cannot continue (bad TMDB key, persistent rate limiting)."""


@dataclass(frozen=True)
class DiaryEntry:
    title: str
    year: int | None
    watched_on: date | None
    slug: str | None = None
    tmdb_id: int | None = None


# ---------------------------------------------------------------- parsing


def _norm_header(name: str | None) -> str:
    return (name or "").strip().lower()


def _pick(row: dict[str, str], candidates: tuple[str, ...]) -> str:
    for key in candidates:
        value = row.get(key)
        if value and value.strip():
            return value.strip()
    return ""


def parse_date(raw: str | None) -> date | None:
    value = (raw or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value[:10], fmt).replace(tzinfo=UTC).date()
        except ValueError:
            continue
    return None


def _slug_from_uri(uri: str) -> str | None:
    match = re.search(r"/film/([^/]+)", uri)
    return match.group(1) if match else None


def validate_csv_header(path: Path) -> None:
    """Fails fast (before a task exists) when the file is not a Letterboxd diary export."""
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
    if not header:
        raise ValueError("The CSV file is empty.")
    names = {_norm_header(column) for column in header}
    if not names.intersection(_TITLE_HEADERS):
        raise ValueError(
            "The CSV needs a 'Name' (or 'Title') column - use the diary.csv from your Letterboxd export."
        )
    if not names.intersection(_WATCHED_HEADERS + _LOGGED_HEADERS):
        raise ValueError(
            "The CSV needs a 'Watched Date' (or 'Date') column - use the diary.csv from your Letterboxd export."
        )


def iter_csv_entries(path: Path) -> Iterator[DiaryEntry]:
    """Streams entries one row at a time; never reads the whole file."""
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
        for raw_row in csv.DictReader(handle):
            row = {_norm_header(k): v for k, v in raw_row.items() if k}
            title = _pick(row, _TITLE_HEADERS)
            if not title:
                continue
            watched = parse_date(_pick(row, _WATCHED_HEADERS)) or parse_date(
                _pick(row, _LOGGED_HEADERS)
            )
            yield DiaryEntry(
                title=title,
                year=letterboxd.parse_year(_pick(row, _YEAR_HEADERS)),
                watched_on=watched,
                slug=_slug_from_uri(_pick(row, _URI_HEADERS)),
            )


def count_csv_rows(path: Path) -> int:
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def entries_from_rss(feed: dict[str, Any]) -> Iterator[DiaryEntry]:
    """Diary items only: list/review items in the feed carry no watched date."""
    for film in feed.get("films", []):
        if not film.get("watched_at"):
            continue
        yield DiaryEntry(
            title=film["title"],
            year=film.get("year"),
            watched_on=parse_date(film["watched_at"]),
            slug=film.get("slug") or None,
            tmdb_id=film.get("tmdb_id"),
        )


# ---------------------------------------------------------------- storage


def get_or_create_import_run(session: Session, user_id: str) -> Run:
    """The user's single hidden import run; created on first import."""
    run = session.exec(
        select(Run)
        .join(RunParticipant, RunParticipant.run_id == Run.id)
        .where(Run.game_type == IMPORT_GAME_TYPE, RunParticipant.user_id == user_id)
    ).first()
    if run is not None:
        return run
    run = Run(
        name=IMPORT_RUN_NAME, game_type=IMPORT_GAME_TYPE, status="completed", completed_at=utcnow()
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    session.add(RunParticipant(run_id=run.id, user_id=user_id, role="owner"))
    session.commit()
    return run


def _existing_watches(session: Session, run_id: str) -> set[tuple[int, str]]:
    rows = session.exec(
        select(RunStep.movie_id, RunStep.watched_at).where(RunStep.run_id == run_id)
    ).all()
    return {(movie_id, watched.date().isoformat()) for movie_id, watched in rows if watched}


def _persist_steps(session: Session, steps: list[RunStep]) -> None:
    session.add_all(steps)
    session.commit()


# ---------------------------------------------------------------- importer


class DiaryImporter:
    def __init__(self, ctx: TaskContext, tmdb: TMDBClient, user_id: str, source: str) -> None:
        self.ctx = ctx
        self.tmdb = tmdb
        self.user_id = user_id
        self.source = source
        self.total: int | None = None
        self.processed = 0
        self.imported = 0
        self.duplicates = 0
        self.rate_limit_pauses = 0
        self.unresolved: list[dict[str, Any]] = []
        self.unresolved_count = 0
        self._stalls = 0
        self._memo: dict[tuple[str, int | None], int | None] = {}
        self._failed_lookups: dict[tuple[str, int | None], str] = {}
        self._background: set[asyncio.Task[None]] = set()

    # -- progress ---------------------------------------------------------

    def _payload(self, message: str | None = None) -> dict[str, Any]:
        return {
            "stage": "importing",
            "current": self.processed,
            "total": self.total,
            "imported": self.imported,
            "skipped_duplicates": self.duplicates,
            "unresolved": self.unresolved_count,
            "rate_limit_pauses": self.rate_limit_pauses,
            "message": message
            or f"Imported {self.imported}, skipped {self.duplicates} duplicates, {self.unresolved_count} unresolved",
        }

    def _on_pause(self, wait: float) -> None:
        self.rate_limit_pauses += 1
        message = f"TMDB rate limit reached - pausing {wait:.0f}s"
        task = asyncio.ensure_future(self.ctx.aprogress(self._payload(message)))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _skip(self, entry: DiaryEntry, reason: str) -> None:
        self.unresolved_count += 1
        if len(self.unresolved) < MAX_UNRESOLVED_REPORTED:
            self.unresolved.append({"title": entry.title, "year": entry.year, "reason": reason})

    # -- TMDB lookups -----------------------------------------------------

    def _deadline(self) -> float:
        return time.monotonic() + PER_CALL_DEADLINE_SECONDS

    async def _resolve_chunk(self, entries: list[DiaryEntry]) -> None:
        """Resolves unknown (title, year) pairs concurrently; rewatches share one lookup."""
        pending = {
            (entry.title.strip().lower(), entry.year): entry
            for entry in entries
            if entry.tmdb_id is None and (entry.title.strip().lower(), entry.year) not in self._memo
        }
        gate = asyncio.Semaphore(RESOLVE_CONCURRENCY)

        async def resolve(entry: DiaryEntry) -> int | None:
            async with gate:
                return await resolve_movie_id(
                    self.tmdb,
                    entry.title,
                    entry.year,
                    slug=entry.slug,
                    on_pause=self._on_pause,
                    per_call_seconds=PER_CALL_DEADLINE_SECONDS,
                )

        outcomes = await asyncio.gather(
            *(resolve(e) for e in pending.values()), return_exceptions=True
        )
        for key, outcome in zip(pending, outcomes, strict=True):
            if isinstance(outcome, DeadlineReached):
                self._failed_lookups[key] = "rate_limited"
                continue
            if isinstance(outcome, TMDBError) and outcome.status_code in (401, 403):
                raise ImportAborted(
                    "TMDB rejected the API key - check it under Settings > Integrations."
                )
            if isinstance(outcome, BaseException):
                logger.warning("TMDB lookup failed for %s: %s", key, outcome)
                self._failed_lookups[key] = "tmdb_error"
                continue
            self._memo[key] = outcome

    async def _hydrate(self, session: Session, tmdb_id: int) -> Any | None:
        """Cached movie detail (+ directors, best effort) for `tmdb_id`."""
        movie = await fetch_with_backoff(
            lambda: cache_repo.get_movie(session, self.tmdb, tmdb_id),
            self._deadline(),
            self._on_pause,
        )
        if movie is None:
            return None
        try:
            await fetch_with_backoff(
                lambda: cache_repo.get_movie_directors(session, self.tmdb, tmdb_id),
                self._deadline(),
                self._on_pause,
            )
        except (DeadlineReached, TMDBError):
            logger.info("Directors for %s will be backfilled later", tmdb_id)
        return movie

    # -- main loop --------------------------------------------------------

    async def run(self, entries: Iterable[DiaryEntry], total: int | None) -> dict[str, Any]:
        self.total = total
        engine: Engine = self.ctx.engine
        with Session(engine) as session:
            run = await anyio.to_thread.run_sync(get_or_create_import_run, session, self.user_id)
            run_id = run.id
            seen = await anyio.to_thread.run_sync(_existing_watches, session, run_id)

            iterator = iter(entries)
            while chunk := list(itertools.islice(iterator, CHUNK_SIZE)):
                await self._resolve_chunk(chunk)
                steps: list[RunStep] = []
                try:
                    for entry in chunk:
                        self.processed += 1
                        step = await self._entry_to_step(session, entry, run_id, seen)
                        if step is not None:
                            steps.append(step)
                finally:  # an abort mid-chunk must still keep what was resolved so far
                    if steps:
                        await anyio.to_thread.run_sync(_persist_steps, session, steps)
                        self.imported += len(steps)
                    await self.ctx.aprogress(self._payload())

        for task in list(self._background):
            await task
        return {
            "source": self.source,
            "run_id": run_id,
            "total_rows": self.processed,
            "imported": self.imported,
            "skipped_duplicates": self.duplicates,
            "unresolved_count": self.unresolved_count,
            "unresolved": self.unresolved,
            "rate_limit_pauses": self.rate_limit_pauses,
        }

    async def _entry_to_step(
        self, session: Session, entry: DiaryEntry, run_id: str, seen: set[tuple[int, str]]
    ) -> RunStep | None:
        if entry.watched_on is None:
            self._skip(entry, "invalid_date")
            return None
        key = (entry.title.strip().lower(), entry.year)
        tmdb_id = entry.tmdb_id or self._memo.get(key)
        if tmdb_id is None:
            failure = None if entry.tmdb_id else self._failed_lookups.get(key)
            if failure == "rate_limited":
                self._stall(entry)
            else:
                self._skip(entry, failure or "no_tmdb_match")
            return None

        watch_key = (tmdb_id, entry.watched_on.isoformat())
        if watch_key in seen:
            self.duplicates += 1
            return None

        try:
            movie = await self._hydrate(session, tmdb_id)
        except DeadlineReached:
            self._stall(entry)
            return None
        except TMDBError as exc:
            if exc.status_code in (401, 403):
                raise ImportAborted(
                    "TMDB rejected the API key - check it under Settings > Integrations."
                ) from exc
            self._skip(entry, "tmdb_error")
            return None
        if movie is None:
            self._skip(entry, "not_found_on_tmdb")
            return None

        self._stalls = 0
        seen.add(watch_key)
        return RunStep(
            run_id=run_id,
            movie_id=movie.tmdb_id,
            movie_title=movie.title,
            movie_poster_path=movie.poster_path,
            movie_release_year=parse_release_year(movie.release_date),
            movie_origin_country=movie.origin_country,
            status="watched",
            watched_at=datetime.combine(entry.watched_on, datetime.min.time(), tzinfo=UTC),
            logged_by_user_id=self.user_id,
        )

    def _stall(self, entry: DiaryEntry) -> None:
        self._stalls += 1
        self._skip(entry, "rate_limited")
        if self._stalls >= MAX_CONSECUTIVE_STALLS:
            raise ImportAborted(
                "TMDB kept rate-limiting the import. Everything imported so far is saved - "
                "run the import again later to continue."
            )


# ---------------------------------------------------------------- entrypoints


def csv_job(path: Path, tmdb: TMDBClient, user_id: str) -> Callable[[TaskContext], Any]:
    async def work(ctx: TaskContext) -> dict[str, Any]:
        try:
            total = await anyio.to_thread.run_sync(count_csv_rows, path)
            return await DiaryImporter(ctx, tmdb, user_id, "csv").run(iter_csv_entries(path), total)
        finally:
            path.unlink(missing_ok=True)

    return work


def rss_job(username: str, tmdb: TMDBClient, user_id: str) -> Callable[[TaskContext], Any]:
    async def work(ctx: TaskContext) -> dict[str, Any]:
        await ctx.aprogress({"stage": "fetching", "message": f"Fetching {username}'s RSS feed"})
        feed = await anyio.to_thread.run_sync(letterboxd.ingest_rss_diary, username)
        entries = list(entries_from_rss(feed))
        return await DiaryImporter(ctx, tmdb, user_id, "rss").run(entries, len(entries))

    return work
