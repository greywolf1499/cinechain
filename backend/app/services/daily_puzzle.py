"""The Daily Bridge: a deterministic, JIT-computed "Cine-Wordle" puzzle.

No cron: the first request of a UTC day derives that day's pair from
`sha256("<date>_<secret key>")`, picks a popular start and target whose shortest route over the
*cached* cast/director graph is 3-5 hops, verifies every hop of that route, and stores the pair
in `daily_puzzles`. Every later request (any user) just reads the row, so the BFS runs once a
day and the pair is stable even though the cache keeps growing.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import logging
import random
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.config import get_settings
from app.models.cache import CachedMovie, CachedMovieCast, CachedMovieDirector
from app.models.daily import (
    ATTEMPT_FORFEITED,
    ATTEMPT_IN_PROGRESS,
    ATTEMPT_SOLVED,
    FINISHED_ATTEMPT_STATUSES,
    DailyPuzzle,
    DailyPuzzleAttempt,
)
from app.models.run import DEFAULT_RULES_CONFIG, Run, RunParticipant, RunStep
from app.schemas.engine import SharedActorConnection
from app.schemas.movies import MovieSummary
from app.schemas.puzzles import AttemptState, PuzzleHop
from app.services import cache_repo, goal_graph, security
from app.services.movie_filters import is_reality_eligible
from app.services.tmdb import TMDBClient, TMDBError
from app.services.tmdb_backoff import DeadlineReached
from app.utils.dates import parse_release_year
from app.utils.ids import utcnow

logger = logging.getLogger(__name__)

DAILY_MIN_POPULARITY = 10.0  # TMDB popularity: both endpoints must be films people know
MIN_PAR_HOPS = 3
MAX_PAR_HOPS = 5
MAX_START_ATTEMPTS = 25
MAX_TARGETS_PER_START = 4
FIRST_PUZZLE_DATE = date(2026, 10, 4)  # puzzle #1
SHARE_FOOTER = "cinechain.local"
GREEN, YELLOW = "green", "yellow"
EMOJI = {GREEN: "🟩", YELLOW: "🟨"}
TARGET_EMOJI = "🎯"
# A failed generation (the cache is too thin) isn't retried more often than this.
RETRY_AFTER_FAILURE_SECONDS = 60.0
_CHUNK = 400

_generation_lock = asyncio.Lock()
_failed_until: dict[str, float] = {}


class PuzzleUnavailable(Exception):
    """No verified pair could be built from what is cached yet."""


class HopError(Exception):
    """A hop couldn't be checked (TMDB trouble or an unknown film)."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


# --- the deterministic pair ---


def today_utc() -> date:
    return datetime.now(UTC).date()


def puzzle_number(day: date) -> int:
    return max(1, (day - FIRST_PUZZLE_DATE).days + 1)


def puzzle_seed(day: date, secret: str) -> int:
    return int(hashlib.sha256(f"{day.isoformat()}_{secret}".encode()).hexdigest(), 16)


@dataclass(frozen=True)
class _Reached:
    hops: int
    parent: int | None  # the previous film on the shortest route
    via: tuple[str, int] | None  # ("actor" | "director", person id) linking parent -> this film


def _batches(items: Sequence[int]) -> Iterable[Sequence[int]]:
    for start in range(0, len(items), _CHUNK):
        yield items[start : start + _CHUNK]


def _people_of(session: Session, movie_ids: Sequence[int], cast_limit: int):
    for batch in _batches(movie_ids):
        for movie_id, actor_id in session.exec(
            select(CachedMovieCast.movie_id, CachedMovieCast.actor_id).where(
                CachedMovieCast.movie_id.in_(batch),  # type: ignore[attr-defined]
                or_(
                    CachedMovieCast.cast_order.is_(None),  # type: ignore[union-attr]
                    CachedMovieCast.cast_order < cast_limit,
                ),
            )
        ):
            yield movie_id, ("actor", actor_id)
        for movie_id, person_id in session.exec(
            select(CachedMovieDirector.movie_id, CachedMovieDirector.person_id).where(
                CachedMovieDirector.movie_id.in_(batch)
            )  # type: ignore[attr-defined]
        ):
            yield movie_id, ("director", person_id)


def _movies_of(session: Session, people: Sequence[tuple[str, int]], cast_limit: int):
    actors = [pid for kind, pid in people if kind == "actor"]
    directors = [pid for kind, pid in people if kind == "director"]
    for batch in _batches(actors):
        for actor_id, movie_id in session.exec(
            select(CachedMovieCast.actor_id, CachedMovieCast.movie_id).where(
                CachedMovieCast.actor_id.in_(batch),  # type: ignore[attr-defined]
                or_(
                    CachedMovieCast.cast_order.is_(None),  # type: ignore[union-attr]
                    CachedMovieCast.cast_order < cast_limit,
                ),
            )
        ):
            yield ("actor", actor_id), movie_id
    for batch in _batches(directors):
        for person_id, movie_id in session.exec(
            select(CachedMovieDirector.person_id, CachedMovieDirector.movie_id).where(
                CachedMovieDirector.person_id.in_(batch)
            )  # type: ignore[attr-defined]
        ):
            yield ("director", person_id), movie_id


def _eligible(session: Session, movie_ids: Sequence[int]) -> set[int]:
    keep: set[int] = set()
    for batch in _batches(movie_ids):
        rows = session.exec(select(CachedMovie).where(CachedMovie.tmdb_id.in_(batch))).all()  # type: ignore[attr-defined]
        keep.update(row.tmdb_id for row in rows if is_reality_eligible(row))
    return keep


def explore(
    session: Session, source_id: int, max_hops: int, cast_limit: int
) -> dict[int, _Reached]:
    """Breadth-first over the cached movie <-> actor/director graph. Ties always resolve to the
    smallest ids, so the same cache yields the same shortest routes."""
    reached = {source_id: _Reached(0, None, None)}
    seen_people: set[tuple[str, int]] = set()
    frontier = [source_id]
    for hops in range(1, max_hops + 1):
        if not frontier:
            break
        origin: dict[tuple[str, int], int] = {}
        for movie_id, person in _people_of(session, frontier, cast_limit):
            if person not in seen_people and (person not in origin or movie_id < origin[person]):
                origin[person] = movie_id
        seen_people.update(origin)
        best: dict[int, tuple[int, tuple[str, int]]] = {}
        for person, movie_id in _movies_of(session, sorted(origin), cast_limit):
            if movie_id in reached:
                continue
            option = (origin[person], person)
            if movie_id not in best or option < best[movie_id]:
                best[movie_id] = option
        frontier = sorted(_eligible(session, sorted(best)))
        for movie_id in frontier:
            parent, via = best[movie_id]
            reached[movie_id] = _Reached(hops, parent, via)
    return reached


def route_to(reached: dict[int, _Reached], target_id: int) -> list[int]:
    route = [target_id]
    while reached[route[-1]].parent is not None:
        route.append(reached[route[-1]].parent)  # type: ignore[arg-type]
    return route[::-1]


# --- links between two films (the rule the player is held to) ---


def _connection_to_dict(connection: SharedActorConnection) -> dict:
    return connection.model_dump()


async def find_links(
    session: Session, tmdb: TMDBClient, from_id: int, to_id: int
) -> list[SharedActorConnection]:
    """Every shared top-billed actor and shared director between two films (JIT from TMDB)."""
    limit = get_settings().pathfinder_cast_limit
    try:
        cast_from = await cache_repo.get_movie_cast(session, tmdb, from_id, limit)
        cast_to = await cache_repo.get_movie_cast(session, tmdb, to_id, limit)
        directors_from = await cache_repo.get_movie_directors(session, tmdb, from_id)
        directors_to = await cache_repo.get_movie_directors(session, tmdb, to_id)
    except (TMDBError, DeadlineReached, httpx.HTTPError) as exc:
        status = 404 if getattr(exc, "status_code", None) == 404 else 502
        raise HopError(f"TMDB lookup failed: {exc}", status) from exc
    to_by_actor = {member["actor_id"]: member for member in cast_to}
    links = [
        SharedActorConnection(
            actor_id=member["actor_id"],
            actor_name=member["name"],
            profile_path=member["profile_path"],
            character_in_from=member["character_name"],
            character_in_to=to_by_actor[member["actor_id"]]["character_name"],
        )
        for member in cast_from
        if member["actor_id"] in to_by_actor
    ]
    directing_to = {d.person_id for d in directors_to}
    links.extend(
        SharedActorConnection(kind="director", actor_id=d.person_id, actor_name=d.name)
        for d in directors_from
        if d.person_id in directing_to
    )
    return links


async def _verified_route(
    session: Session, tmdb: TMDBClient, route: Sequence[int]
) -> list[SharedActorConnection] | None:
    """The first link of every hop on `route`, or None when any hop doesn't hold up live."""
    links: list[SharedActorConnection] = []
    for earlier, later in itertools.pairwise(route):
        try:
            found = await find_links(session, tmdb, earlier, later)
        except HopError:
            return None
        if not found:
            return None
        links.append(found[0])
    return links


async def _build_pair(session: Session, tmdb: TMDBClient, day: date) -> DailyPuzzle:
    settings = get_settings()
    rng = random.Random(puzzle_seed(day, security._get_secret_key(settings)))
    cast_limit = settings.pathfinder_cast_limit
    pool = sorted(
        row.tmdb_id
        for row in session.exec(
            select(CachedMovie).where(
                CachedMovie.popularity > DAILY_MIN_POPULARITY,  # type: ignore[operator]
                CachedMovie.tmdb_id.in_(  # type: ignore[attr-defined]
                    select(CachedMovieCast.movie_id)
                ),
            ),
        ).all()
        if is_reality_eligible(row)
    )
    if len(pool) < 2:
        raise PuzzleUnavailable("Not enough popular films are cached yet to build a puzzle")
    for start_id in rng.sample(pool, min(MAX_START_ATTEMPTS, len(pool))):
        targets = []
        for target_id in pool:
            if target_id == start_id:
                continue
            result = goal_graph.search(
                session,
                [start_id],
                [target_id],
                policy="shared_cast",
                max_depth=MAX_PAR_HOPS,
                max_seconds=0.6,
                cast_limit=cast_limit,
            )
            if result.distance is not None and MIN_PAR_HOPS <= result.distance <= MAX_PAR_HOPS:
                targets.append(target_id)
        rng.shuffle(targets)
        for target_id in targets[:MAX_TARGETS_PER_START]:
            route_result = goal_graph.search(
                session,
                [start_id],
                [target_id],
                policy="shared_cast",
                max_depth=MAX_PAR_HOPS,
                max_seconds=2.0,
                cast_limit=cast_limit,
            )
            route = route_result.path_movie_ids
            if len(route) < 2:
                continue
            links = await _verified_route(session, tmdb, route)
            if links is None:
                continue
            optimal = [{"movie_id": route[0], "link": None}] + [
                {"movie_id": movie_id, "link": _connection_to_dict(link)}
                for movie_id, link in zip(route[1:], links, strict=True)
            ]
            return DailyPuzzle(
                puzzle_date=day.isoformat(),
                puzzle_number=puzzle_number(day),
                start_movie_id=start_id,
                target_movie_id=target_id,
                par_hops=len(route) - 1,
                optimal_path=optimal,
            )
    raise PuzzleUnavailable(
        f"No {MIN_PAR_HOPS}-{MAX_PAR_HOPS} hop pair of popular films could be verified from the "
        "cached graph yet - browse a few films (or run a Bridge search) and try again"
    )


async def get_or_create_puzzle(
    session: Session, tmdb: TMDBClient, day: date | None = None
) -> DailyPuzzle:
    """Today's puzzle, computed on the first request of the day and read back afterwards."""
    day = day or today_utc()
    key = day.isoformat()
    existing = session.get(DailyPuzzle, key)
    if existing is not None:
        return existing
    if _failed_until.get(key, 0.0) > time.monotonic():
        raise PuzzleUnavailable("Today's puzzle could not be built yet - try again in a minute")
    async with _generation_lock:
        existing = session.get(DailyPuzzle, key)
        if existing is not None:
            return existing
        try:
            puzzle = await _build_pair(session, tmdb, day)
        except PuzzleUnavailable:
            _failed_until[key] = time.monotonic() + RETRY_AFTER_FAILURE_SECONDS
            raise
        session.add(puzzle)
        try:
            session.commit()
        except IntegrityError:  # another worker stored the day's pair first
            session.rollback()
            return session.get(DailyPuzzle, key)  # type: ignore[return-value]
        session.refresh(puzzle)
        return puzzle


def reset_state() -> None:
    _failed_until.clear()


# --- presentation ---


def movie_summary(session: Session, movie_id: int) -> MovieSummary:
    row = session.get(CachedMovie, movie_id)
    if row is None:
        return MovieSummary(tmdb_id=movie_id, title=str(movie_id))
    return MovieSummary(
        tmdb_id=row.tmdb_id,
        title=row.title,
        poster_path=row.poster_path,
        release_year=parse_release_year(row.release_date),
        origin_country=row.origin_country,
    )


def _hops_out(session: Session, entries: Iterable[dict]) -> list[PuzzleHop]:
    return [
        PuzzleHop(
            movie=movie_summary(session, entry["movie_id"]),
            link=SharedActorConnection(**entry["link"]) if entry.get("link") else None,
        )
        for entry in entries
    ]


def optimal_hops(session: Session, puzzle: DailyPuzzle) -> list[PuzzleHop]:
    return _hops_out(session, puzzle.optimal_path)


def get_attempt(session: Session, day_key: str, user_id: str) -> DailyPuzzleAttempt | None:
    return session.get(DailyPuzzleAttempt, (day_key, user_id))


def share_text(session: Session, puzzle: DailyPuzzle, attempt: DailyPuzzleAttempt) -> str | None:
    if attempt.status != ATTEMPT_SOLVED or not attempt.grades:
        return None
    start = movie_summary(session, puzzle.start_movie_id).title
    target = movie_summary(session, puzzle.target_movie_id).title
    grid = " ".join([*(EMOJI[g] for g in attempt.grades[:-1]), TARGET_EMOJI])
    hops = len(attempt.chain)
    return (
        f"CineChain Daily #{puzzle.puzzle_number} 🎬\n{start} ──► {target}\n"
        f"{grid} ({hops} Hop{'' if hops == 1 else 's'})\n{SHARE_FOOTER}"
    )


def attempt_state(
    session: Session, puzzle: DailyPuzzle, attempt: DailyPuzzleAttempt | None
) -> AttemptState:
    if attempt is None:
        return AttemptState()
    return AttemptState(
        status=attempt.status,
        chain=_hops_out(session, attempt.chain),
        hops=len(attempt.chain),
        grades=attempt.grades,
        share_text=share_text(session, puzzle, attempt),
        run_id=attempt.run_id,
    )


def is_locked(session: Session, user_id: str, from_id: int, to_id: int) -> bool:
    """Anti-cheat: is this exact pair today's puzzle, still unsolved and unforfeited by the user?
    Never generates a puzzle: no stored pair means nobody can be playing it yet."""
    key = today_utc().isoformat()
    puzzle = session.get(DailyPuzzle, key)
    if puzzle is None or {from_id, to_id} != {puzzle.start_movie_id, puzzle.target_movie_id}:
        return False
    attempt = get_attempt(session, key, user_id)
    return attempt is None or attempt.status not in FINISHED_ATTEMPT_STATUSES


# --- attempts ---


def start_attempt(session: Session, puzzle: DailyPuzzle, user_id: str) -> DailyPuzzleAttempt:
    attempt = get_attempt(session, puzzle.puzzle_date, user_id)
    if attempt is None:
        attempt = DailyPuzzleAttempt(puzzle_date=puzzle.puzzle_date, user_id=user_id)
        session.add(attempt)
        session.commit()
        session.refresh(attempt)
    return attempt


def _grade_chain(session: Session, puzzle: DailyPuzzle, chain: Sequence[dict]) -> list[str]:
    """Green when a hop got closer to the target (by the cached graph), yellow when it didn't."""
    cast_limit = get_settings().pathfinder_cast_limit
    distance = [puzzle.par_hops]
    grades: list[str] = []
    for index, entry in enumerate(chain):
        movie_id = entry["movie_id"]
        result = goal_graph.search(
            session,
            [movie_id],
            [puzzle.target_movie_id],
            policy="shared_cast",
            max_depth=len(chain) + puzzle.par_hops,
            max_seconds=0.3,
            cast_limit=cast_limit,
        )
        step_distance = result.distance if result.distance is not None else 10_000
        last = index == len(chain) - 1
        grades.append(GREEN if last or step_distance < distance[-1] else YELLOW)
        distance.append(step_distance)
    return grades


async def attempt_hop(
    session: Session,
    tmdb: TMDBClient,
    puzzle: DailyPuzzle,
    user_id: str,
    current_id: int,
    next_id: int,
) -> tuple[list[SharedActorConnection], str | None, bool, DailyPuzzleAttempt | None]:
    """(links, rejection reason, recorded, attempt). A hop is *recorded* only when it continues
    the user's chain from its tip while the attempt is still open."""
    attempt = get_attempt(session, puzzle.puzzle_date, user_id)
    taken = {
        puzzle.start_movie_id,
        *(entry["movie_id"] for entry in (attempt.chain if attempt else [])),
    }
    if current_id == next_id:
        return [], "A film can't link to itself", False, attempt
    links = await find_links(session, tmdb, current_id, next_id)
    if not links:
        return [], "No shared credited cast or director between these films", False, attempt
    open_attempt = attempt is None or attempt.status == ATTEMPT_IN_PROGRESS
    tip = attempt.chain[-1]["movie_id"] if attempt and attempt.chain else puzzle.start_movie_id
    if not open_attempt or current_id != tip:
        return links, None, False, attempt
    if next_id in taken:
        return [], "That film is already in your chain", False, attempt
    attempt = start_attempt(session, puzzle, user_id)
    attempt.chain = [*attempt.chain, {"movie_id": next_id, "link": _connection_to_dict(links[0])}]
    if next_id == puzzle.target_movie_id:
        attempt.status = ATTEMPT_SOLVED
        attempt.finished_at = utcnow()
        attempt.grades = _grade_chain(session, puzzle, attempt.chain)
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    return links, None, True, attempt


def undo_hop(session: Session, puzzle: DailyPuzzle, user_id: str) -> DailyPuzzleAttempt | None:
    attempt = get_attempt(session, puzzle.puzzle_date, user_id)
    if attempt is None or attempt.status != ATTEMPT_IN_PROGRESS or not attempt.chain:
        return attempt
    attempt.chain = attempt.chain[:-1]
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    return attempt


def forfeit(session: Session, puzzle: DailyPuzzle, user_id: str) -> DailyPuzzleAttempt:
    attempt = start_attempt(session, puzzle, user_id)
    if attempt.status == ATTEMPT_IN_PROGRESS:
        attempt.status = ATTEMPT_FORFEITED
        attempt.finished_at = utcnow()
        session.add(attempt)
        session.commit()
        session.refresh(attempt)
    return attempt


def _link_metadata(link: dict | None) -> dict | None:
    if not link:
        return None
    if link.get("kind") == "director":
        return {
            "connection_type": "director",
            "director_id": link["actor_id"],
            "director_name": link["actor_name"],
        }
    return {
        "connection_type": "actor",
        "actor_id": link["actor_id"],
        "actor_name": link["actor_name"],
        "profile_path": link.get("profile_path"),
        "character_in_from": link.get("character_in_from"),
        "character_in_to": link.get("character_in_to"),
    }


def convert_to_run(
    session: Session, puzzle: DailyPuzzle, attempt: DailyPuzzleAttempt, user_id: str
) -> tuple[Run, int]:
    """A planned, unwatched Cinechain run over the user's solved chain (or, after a forfeit, the
    optimal route) so the puzzle can be watched as a marathon. Idempotent per attempt."""
    if attempt.run_id:
        existing = session.get(Run, attempt.run_id)
        if existing is not None:
            count = len(session.exec(select(RunStep).where(RunStep.run_id == existing.id)).all())
            return existing, count
    if attempt.status == ATTEMPT_SOLVED:
        entries = [{"movie_id": puzzle.start_movie_id, "link": None}, *attempt.chain]
    else:
        entries = list(puzzle.optimal_path)
    run = Run(
        name=f"Daily Bridge #{puzzle.puzzle_number}",
        game_type="cinechain",
        rules_config=dict(DEFAULT_RULES_CONFIG),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    session.add(RunParticipant(run_id=run.id, user_id=user_id, role="owner"))
    base = utcnow()
    for index, entry in enumerate(entries):
        movie = session.get(CachedMovie, entry["movie_id"])
        if movie is None:
            continue
        session.add(
            RunStep(
                run_id=run.id,
                movie_id=movie.tmdb_id,
                movie_title=movie.title,
                movie_poster_path=movie.poster_path,
                movie_release_year=parse_release_year(movie.release_date),
                movie_origin_country=movie.origin_country,
                transition_metadata=_link_metadata(entry.get("link")),
                status="planned",
                watched_at=None,
                logged_by_user_id=user_id,
                # Distinct, ordered timestamps: runs read their steps back by `logged_at`.
                logged_at=base + timedelta(milliseconds=index),
            )
        )
    attempt.run_id = run.id
    session.add(attempt)
    session.commit()
    return run, len(entries)
