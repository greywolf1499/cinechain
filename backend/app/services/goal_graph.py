"""Bounded cache-first graph search for goal-driven modes.

The graph is movie -> person -> movie, where "person" edges are selected by a
link policy:
- shared_cast: top-billed cast only
- shared_director: directors only
- shared_any_person: cast, directors and craft crew
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import pairwise

from sqlalchemy import or_
from sqlmodel import Session, select

from app.config import get_settings
from app.models.cache import (
    CachedActor,
    CachedCrewCredit,
    CachedMovie,
    CachedMovieCast,
    CachedMovieDirector,
)
from app.schemas.engine import SharedActorConnection
from app.services.movie_filters import is_reality_eligible

_CHUNK = 400
_ACTOR = "actor"
_DIRECTOR = "director"
_CRAFT = "craft"
_GRAPH_POLICIES = {"shared_cast", "shared_director", "shared_any_person"}


@dataclass(frozen=True)
class GoalGraphResult:
    distance: int | None
    path_movie_ids: list[int]
    connections: list[SharedActorConnection]
    searched_depth: int
    timed_out: bool = False

    @property
    def par(self) -> int | None:
        return self.distance


def _chunks(values: Sequence[int]) -> Iterable[Sequence[int]]:
    for start in range(0, len(values), _CHUNK):
        yield values[start : start + _CHUNK]


def _cast_limit(limit: int | None) -> int:
    return int(limit or get_settings().pathfinder_cast_limit)


def _is_graph_policy(policy: str | None) -> bool:
    return (policy or "shared_cast") in _GRAPH_POLICIES


def _people_of(
    session: Session,
    movie_ids: Sequence[int],
    policy: str,
    cast_limit: int,
) -> list[tuple[int, tuple[str, int]]]:
    rows: list[tuple[int, tuple[str, int]]] = []
    if not movie_ids:
        return rows
    if policy in ("shared_cast", "shared_any_person"):
        for batch in _chunks(movie_ids):
            for movie_id, actor_id in session.exec(
                select(CachedMovieCast.movie_id, CachedMovieCast.actor_id).where(
                    CachedMovieCast.movie_id.in_(batch),  # type: ignore[attr-defined]
                    or_(
                        CachedMovieCast.cast_order.is_(None),  # type: ignore[union-attr]
                        CachedMovieCast.cast_order < cast_limit,
                    ),
                )
            ):
                rows.append((movie_id, (_ACTOR, actor_id)))
    if policy in ("shared_director", "shared_any_person"):
        for batch in _chunks(movie_ids):
            for movie_id, person_id in session.exec(
                select(CachedMovieDirector.movie_id, CachedMovieDirector.person_id).where(
                    CachedMovieDirector.movie_id.in_(batch)  # type: ignore[attr-defined]
                )
            ):
                rows.append((movie_id, (_DIRECTOR, person_id)))
    if policy == "shared_any_person":
        for batch in _chunks(movie_ids):
            for movie_id, person_id in session.exec(
                select(CachedCrewCredit.movie_id, CachedCrewCredit.person_id).where(
                    CachedCrewCredit.movie_id.in_(batch)  # type: ignore[attr-defined]
                )
            ):
                rows.append((movie_id, (_CRAFT, person_id)))
    rows.sort(key=lambda item: (item[1][0], item[1][1], item[0]))
    return rows


def _movies_of(
    session: Session,
    people: Sequence[tuple[str, int]],
    policy: str,
    cast_limit: int,
) -> list[tuple[tuple[str, int], int]]:
    actor_ids = [person_id for kind, person_id in people if kind == _ACTOR]
    director_ids = [person_id for kind, person_id in people if kind == _DIRECTOR]
    crew_ids = [person_id for kind, person_id in people if kind == _CRAFT]
    rows: list[tuple[tuple[str, int], int]] = []
    if policy in ("shared_cast", "shared_any_person") and actor_ids:
        for batch in _chunks(actor_ids):
            for actor_id, movie_id in session.exec(
                select(CachedMovieCast.actor_id, CachedMovieCast.movie_id).where(
                    CachedMovieCast.actor_id.in_(batch),  # type: ignore[attr-defined]
                    or_(
                        CachedMovieCast.cast_order.is_(None),  # type: ignore[union-attr]
                        CachedMovieCast.cast_order < cast_limit,
                    ),
                )
            ):
                rows.append(((_ACTOR, actor_id), movie_id))
    if policy in ("shared_director", "shared_any_person") and director_ids:
        for batch in _chunks(director_ids):
            for person_id, movie_id in session.exec(
                select(CachedMovieDirector.person_id, CachedMovieDirector.movie_id).where(
                    CachedMovieDirector.person_id.in_(batch)  # type: ignore[attr-defined]
                )
            ):
                rows.append(((_DIRECTOR, person_id), movie_id))
    if policy == "shared_any_person" and crew_ids:
        for batch in _chunks(crew_ids):
            for person_id, movie_id in session.exec(
                select(CachedCrewCredit.person_id, CachedCrewCredit.movie_id).where(
                    CachedCrewCredit.person_id.in_(batch)  # type: ignore[attr-defined]
                )
            ):
                rows.append(((_CRAFT, person_id), movie_id))
    rows.sort(key=lambda item: (item[0][0], item[0][1], item[1]))
    return rows


def _eligible(
    session: Session,
    movie_ids: Sequence[int],
    excluded_movie_ids: set[int],
) -> set[int]:
    if not movie_ids:
        return set()
    keep: set[int] = set()
    for batch in _chunks(movie_ids):
        rows = session.exec(
            select(CachedMovie).where(CachedMovie.tmdb_id.in_(batch))  # type: ignore[attr-defined]
        ).all()
        keep.update(
            row.tmdb_id
            for row in rows
            if row.tmdb_id not in excluded_movie_ids and is_reality_eligible(row)
        )
    return keep


def _expand_frontier(
    session: Session,
    frontier: set[int],
    visited: dict[int, int],
    parents: dict[int, tuple[int, tuple[str, int]]],
    policy: str,
    cast_limit: int,
    excluded_movie_ids: set[int],
) -> set[int]:
    origin: dict[tuple[str, int], int] = {}
    for movie_id, person in _people_of(session, sorted(frontier), policy, cast_limit):
        if person not in origin or movie_id < origin[person]:
            origin[person] = movie_id
    best: dict[int, tuple[int, tuple[str, int]]] = {}
    for person, movie_id in _movies_of(session, sorted(origin), policy, cast_limit):
        if movie_id in visited:
            continue
        option = (origin[person], person)
        if movie_id not in best or option < best[movie_id]:
            best[movie_id] = option
    candidates = _eligible(session, sorted(best), excluded_movie_ids)
    expanded: set[int] = set()
    for movie_id in sorted(candidates):
        parent, via = best[movie_id]
        visited[movie_id] = visited[parent] + 1
        parents[movie_id] = (parent, via)
        expanded.add(movie_id)
    return expanded


def _path_to_root(
    node: int,
    parents: dict[int, tuple[int, tuple[str, int]]],
) -> list[int]:
    path = [node]
    while path[-1] in parents:
        path.append(parents[path[-1]][0])
    return path


def _pick_connection(
    session: Session,
    from_movie_id: int,
    to_movie_id: int,
    policy: str,
    cast_limit: int,
) -> SharedActorConnection | None:
    if policy in ("shared_cast", "shared_any_person"):
        shared = session.exec(
            select(CachedMovieCast.actor_id).where(
                CachedMovieCast.movie_id == from_movie_id,
                or_(
                    CachedMovieCast.cast_order.is_(None),  # type: ignore[union-attr]
                    CachedMovieCast.cast_order < cast_limit,
                ),
                CachedMovieCast.actor_id.in_(  # type: ignore[attr-defined]
                    select(CachedMovieCast.actor_id).where(
                        CachedMovieCast.movie_id == to_movie_id,
                        or_(
                            CachedMovieCast.cast_order.is_(None),  # type: ignore[union-attr]
                            CachedMovieCast.cast_order < cast_limit,
                        ),
                    )
                ),
            )
        ).all()
        if shared:
            actor_id = min(shared)
            actor = session.get(CachedActor, actor_id)
            cast_from = session.get(CachedMovieCast, (from_movie_id, actor_id))
            cast_to = session.get(CachedMovieCast, (to_movie_id, actor_id))
            return SharedActorConnection(
                kind=_ACTOR,
                actor_id=actor_id,
                actor_name=actor.name if actor else str(actor_id),
                profile_path=actor.profile_path if actor else None,
                character_in_from=cast_from.character_name if cast_from else None,
                character_in_to=cast_to.character_name if cast_to else None,
            )
    if policy in ("shared_director", "shared_any_person"):
        shared = session.exec(
            select(CachedMovieDirector.person_id).where(
                CachedMovieDirector.movie_id == from_movie_id,
                CachedMovieDirector.person_id.in_(  # type: ignore[attr-defined]
                    select(CachedMovieDirector.person_id).where(
                        CachedMovieDirector.movie_id == to_movie_id
                    )
                ),
            )
        ).all()
        if shared:
            person_id = min(shared)
            director = session.get(CachedMovieDirector, (from_movie_id, person_id)) or session.get(
                CachedMovieDirector, (to_movie_id, person_id)
            )
            return SharedActorConnection(
                kind=_DIRECTOR,
                actor_id=person_id,
                actor_name=director.name if director else str(person_id),
            )
    if policy == "shared_any_person":
        shared = session.exec(
            select(CachedCrewCredit.person_id).where(
                CachedCrewCredit.movie_id == from_movie_id,
                CachedCrewCredit.person_id.in_(  # type: ignore[attr-defined]
                    select(CachedCrewCredit.person_id).where(
                        CachedCrewCredit.movie_id == to_movie_id
                    )
                ),
            )
        ).all()
        if shared:
            person_id = min(shared)
            crew = session.exec(
                select(CachedCrewCredit).where(
                    CachedCrewCredit.person_id == person_id,
                    CachedCrewCredit.movie_id.in_([from_movie_id, to_movie_id]),  # type: ignore[attr-defined]
                )
            ).first()
            return SharedActorConnection(
                kind=_CRAFT,
                actor_id=person_id,
                actor_name=crew.person_name if crew else str(person_id),
            )
    return None


def _connections_for_path(
    session: Session, path_movie_ids: Sequence[int], policy: str, cast_limit: int
) -> list[SharedActorConnection]:
    links: list[SharedActorConnection] = []
    for from_id, to_id in pairwise(path_movie_ids):
        connection = _pick_connection(session, from_id, to_id, policy, cast_limit)
        if connection is None:
            break
        links.append(connection)
    return links


def search(
    session: Session,
    sources: Sequence[int],
    targets: Sequence[int],
    *,
    policy: str = "shared_cast",
    max_depth: int = 6,
    max_seconds: float = 6.0,
    cast_limit: int | None = None,
    excluded_movie_ids: set[int] | None = None,
) -> GoalGraphResult:
    """Shortest path between any source and any target in the cached graph."""
    policy = policy if _is_graph_policy(policy) else "shared_cast"
    cast_limit = _cast_limit(cast_limit)
    excluded = set(excluded_movie_ids or ())
    start = time.monotonic()
    source_set = set(sources)
    target_set = set(targets)
    if not source_set or not target_set:
        return GoalGraphResult(None, [], [], 0)
    overlap = sorted(source_set & target_set)
    if overlap:
        return GoalGraphResult(0, [overlap[0]], [], 0)

    f_visited = {movie_id: 0 for movie_id in sorted(source_set)}
    b_visited = {movie_id: 0 for movie_id in sorted(target_set)}
    f_parents: dict[int, tuple[int, tuple[str, int]]] = {}
    b_parents: dict[int, tuple[int, tuple[str, int]]] = {}
    f_frontier, b_frontier = set(source_set), set(target_set)
    best_meet: tuple[int, int] | None = None
    searched_depth = 0
    timed_out = False

    while f_frontier and b_frontier:
        if time.monotonic() - start >= max_seconds:
            timed_out = True
            break
        if best_meet is not None and searched_depth >= best_meet[0]:
            break
        if len(f_frontier) < len(b_frontier):
            side = "forward"
        elif len(b_frontier) < len(f_frontier):
            side = "backward"
        else:
            side = "forward" if len(f_visited) <= len(b_visited) else "backward"
        if side == "forward":
            f_frontier = _expand_frontier(
                session, f_frontier, f_visited, f_parents, policy, cast_limit, excluded
            )
            if not f_frontier:
                break
            searched_depth = max(
                searched_depth, max(f_visited[movie_id] for movie_id in f_frontier)
            )
            meetings = sorted(movie_id for movie_id in f_frontier if movie_id in b_visited)
        else:
            b_frontier = _expand_frontier(
                session, b_frontier, b_visited, b_parents, policy, cast_limit, excluded
            )
            if not b_frontier:
                break
            searched_depth = max(
                searched_depth, max(b_visited[movie_id] for movie_id in b_frontier)
            )
            meetings = sorted(movie_id for movie_id in b_frontier if movie_id in f_visited)
        for movie_id in meetings:
            distance = f_visited[movie_id] + b_visited[movie_id]
            if distance > max_depth:
                continue
            candidate = (distance, movie_id)
            if best_meet is None or candidate < best_meet:
                best_meet = candidate
        if best_meet is not None and best_meet[0] <= 1:
            break
        if searched_depth >= max_depth:
            break

    if best_meet is None:
        return GoalGraphResult(None, [], [], min(searched_depth, max_depth), timed_out)

    _, meeting = best_meet
    from_source = _path_to_root(meeting, f_parents)[::-1]
    to_target = _path_to_root(meeting, b_parents)
    path_movie_ids = from_source + to_target[1:]
    connections = _connections_for_path(session, path_movie_ids, policy, cast_limit)
    return GoalGraphResult(
        distance=len(path_movie_ids) - 1,
        path_movie_ids=path_movie_ids,
        connections=connections,
        searched_depth=min(best_meet[0], max_depth),
        timed_out=timed_out,
    )


def distance_to_targets(
    session: Session,
    movie_ids: Sequence[int],
    targets: Sequence[int],
    *,
    policy: str = "shared_cast",
    max_depth: int = 6,
    max_seconds: float = 1.5,
    cast_limit: int | None = None,
    excluded_movie_ids: set[int] | None = None,
) -> dict[int, int | None]:
    """Distance from each movie to the nearest target (cache-only, bounded)."""
    policy = policy if _is_graph_policy(policy) else "shared_cast"
    cast_limit = _cast_limit(cast_limit)
    excluded = set(excluded_movie_ids or ())
    wanted = [movie_id for movie_id in dict.fromkeys(movie_ids) if movie_id not in excluded]
    target_set = {movie_id for movie_id in targets if movie_id not in excluded}
    if not wanted:
        return {}
    if not target_set:
        return {movie_id: None for movie_id in wanted}

    start = time.monotonic()
    visited = {movie_id: 0 for movie_id in sorted(target_set)}
    parents: dict[int, tuple[int, tuple[str, int]]] = {}
    frontier = set(target_set)
    remaining = set(wanted)
    distances = {movie_id: (0 if movie_id in target_set else None) for movie_id in wanted}
    remaining -= target_set

    while frontier and remaining:
        if time.monotonic() - start >= max_seconds:
            break
        if max(visited[movie_id] for movie_id in frontier) >= max_depth:
            break
        frontier = _expand_frontier(
            session, frontier, visited, parents, policy, cast_limit, excluded
        )
        for movie_id in sorted(frontier & remaining):
            distances[movie_id] = visited[movie_id]
            remaining.remove(movie_id)
    return distances
