"""Constrained bridge search for the graph-mutator modes (Phase 21b).

The classic solver (`pathfinder.solve_bridge_bipartite`) searches a movie<->actor
bipartite graph, which collapses "which film did I come from" into the actor
node. That is wrong as soon as a rule depends on the *previous hop*:

  * Chrono Climb   - each film must be released after the one before it
  * World Passport - each film's primary country must differ from the one before
  * Auteur Relay   - hops must alternate actor / director links

So this solver works at the *movie level*: a state is `(movie_id, tag)` where
`tag` is the kind of link used to reach it (only tracked for alternating modes),
and every edge is a real film-to-film link through one actor or director, checked
against the constraints. Same bidirectional BFS, same event stream, same time
limit, cache-first expansion and process-wide semaphore as the classic solver.

Search is heuristic about *completeness* (each round expands at most
`pathfinder_actor_credit_limit` frontier films, cache-resident ones first) but a
returned path always satisfies every constraint.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from itertools import pairwise

import anyio
from sqlmodel import Session

from app.config import get_settings
from app.models.cache import CachedMovie
from app.services import cache_repo, pathfinder
from app.services.bridge_paths import hydrate_movies
from app.services.cache_repo import CacheRepo
from app.services.graph import (
    DIRECTOR_NODE,
    NodeKey,
    PathConstraints,
    actor_node,
    director_node,
    movie_node,
)
from app.services.movie_filters import is_reality_eligible
from app.services.tmdb import TMDBClient

logger = logging.getLogger(__name__)

State = tuple[int, str | None]
MAX_RESULT_PATHS = 3


@dataclass
class _Side:
    forward: bool
    visited: dict[State, tuple[State | None, NodeKey | None]]
    by_movie: dict[int, list[State]]
    frontier: list[State]
    hops: int = 0

    @classmethod
    def starting_at(cls, state: State, forward: bool) -> _Side:
        return cls(forward, {state: (None, None)}, {state[0]: [state]}, [state])

    def add(self, state: State, parent: State | None, via: NodeKey | None) -> None:
        self.visited[state] = (parent, via)
        self.by_movie.setdefault(state[0], []).append(state)

    def drop(self, state: State) -> None:
        self.visited.pop(state, None)
        siblings = self.by_movie.get(state[0], [])
        if state in siblings:
            siblings.remove(state)


@dataclass
class _Ctx:
    session: Session
    tmdb: TMDBClient
    repo: CacheRepo
    constraints: PathConstraints
    cast_limit: int
    min_runtime: int | None
    excluded: set[int]
    call_budget: int | None
    deadline: float
    stats: pathfinder._SearchStats
    protected: set[int] = field(default_factory=set)  # the two roots: never filtered out

    def pair_ok(self, earlier: CachedMovie, later: CachedMovie) -> bool:
        ok = self.constraints.movie_ok
        return True if ok is None else ok(earlier, later)

    def over_budget(self) -> bool:
        return self.call_budget is not None and self.stats.tmdb_calls >= self.call_budget

    async def pace(self) -> None:
        if self.stats.tmdb_calls > 0:
            await asyncio.sleep(self.tmdb.pacing_delay_seconds)

    async def movie(self, movie_id: int, hydrate: bool = False) -> CachedMovie | None:
        row = await anyio.to_thread.run_sync(self.session.get, CachedMovie, movie_id)
        if row is not None and not (hydrate and row.origin_country is None):
            return row
        if self.over_budget():
            return row
        await self.pace()
        fetched = await pathfinder._fetch_with_backoff(
            lambda: cache_repo.get_movie(self.session, self.tmdb, movie_id, refresh=hydrate),
            self.stats,
            self.deadline,
        )
        self.stats.tmdb_calls += 1
        return fetched if fetched is not None else row

    async def actor_neighbours(self, movie_id: int) -> list[tuple[CachedMovie, NodeKey]]:
        cast = await anyio.to_thread.run_sync(self.repo.get_cached_cast, movie_id, self.cast_limit)
        if cast is None:
            if self.over_budget():
                return []
            await self.pace()
            cast = await pathfinder._fetch_with_backoff(
                lambda: cache_repo.get_movie_cast(
                    self.session, self.tmdb, movie_id, self.cast_limit
                ),
                self.stats,
                self.deadline,
            )
            self.stats.tmdb_calls += 1
            if cast is None:
                return []
        else:
            self.stats.cache_hits += 1
        found: list[tuple[CachedMovie, NodeKey]] = []
        for member in cast:
            actor_id = member["actor_id"]
            credits_ = await anyio.to_thread.run_sync(self.repo.get_cached_actor_credits, actor_id)
            if credits_ is None:
                if self.over_budget():
                    continue
                await self.pace()
                credits_ = await pathfinder._fetch_with_backoff(
                    lambda actor_id=actor_id: cache_repo.get_actor_credits(
                        self.session, self.tmdb, actor_id
                    ),
                    self.stats,
                    self.deadline,
                )
                self.stats.tmdb_calls += 1
                if credits_ is None:
                    continue
            else:
                self.stats.cache_hits += 1
            found.extend((film, actor_node(actor_id)) for film in credits_)
        return found

    async def director_neighbours(self, movie_id: int) -> list[tuple[CachedMovie, NodeKey]]:
        directors = await anyio.to_thread.run_sync(self.repo.get_cached_directors, movie_id)
        if directors is None:
            if self.over_budget():
                return []
            await self.pace()
            directors = await pathfinder._fetch_with_backoff(
                lambda: cache_repo.get_movie_directors(self.session, self.tmdb, movie_id),
                self.stats,
                self.deadline,
            )
            self.stats.tmdb_calls += 1
            if directors is None:
                return []
        else:
            self.stats.cache_hits += 1
        found: list[tuple[CachedMovie, NodeKey]] = []
        for director in directors:
            person_id = director.person_id
            films = await anyio.to_thread.run_sync(self.repo.get_cached_director_credits, person_id)
            if films is None:
                if self.over_budget():
                    continue
                await self.pace()
                films = await pathfinder._fetch_with_backoff(
                    lambda person_id=person_id, name=director.name: cache_repo.get_director_credits(
                        self.session, self.tmdb, person_id, name
                    ),
                    self.stats,
                    self.deadline,
                )
                self.stats.tmdb_calls += 1
                if films is None:
                    continue
            else:
                self.stats.cache_hits += 1
            found.extend((film, director_node(person_id)) for film in films)
        return found


def _kinds_from(constraints: PathConstraints, tag: str | None) -> list[str]:
    kinds = ["actor", *([DIRECTOR_NODE] if constraints.use_directors else [])]
    if constraints.alternate_edges and tag is not None:
        kinds = [kind for kind in kinds if kind != tag]
    return kinds


async def _order_frontier(ctx: _Ctx, side: _Side, limit: int) -> list[State]:
    """Cache-resident films first (they expand for free), capped at `limit`."""
    cached: list[State] = []
    uncached: list[State] = []
    for state in side.frontier:
        cast = await anyio.to_thread.run_sync(ctx.repo.get_cached_cast, state[0], ctx.cast_limit)
        (cached if cast is not None else uncached).append(state)
    return (cached + uncached)[:limit]


async def _expand(ctx: _Ctx, side: _Side, limit: int) -> dict[State, tuple[State, NodeKey]]:
    constraints = ctx.constraints
    new_states: dict[State, tuple[State, NodeKey]] = {}
    for state in await _order_frontier(ctx, side, limit):
        movie_id, tag = state
        try:
            movie = await ctx.movie(movie_id, hydrate=constraints.needs_detail)
            if movie is None:
                continue
            parent = side.visited[state][0]
            if parent is not None and movie_id not in ctx.protected:
                parent_movie = await anyio.to_thread.run_sync(
                    ctx.session.get, CachedMovie, parent[0]
                )
                # Deferred edge check: the film's detail wasn't known when it was discovered.
                if parent_movie is not None and not (
                    ctx.pair_ok(parent_movie, movie)
                    if side.forward
                    else ctx.pair_ok(movie, parent_movie)
                ):
                    side.drop(state)
                    continue
            for kind in _kinds_from(constraints, tag):
                neighbours = await (
                    ctx.actor_neighbours(movie_id)
                    if kind == "actor"
                    else ctx.director_neighbours(movie_id)
                )
                new_tag = kind if constraints.alternate_edges else None
                for film, via in neighbours:
                    if film.tmdb_id == movie_id or not is_reality_eligible(film):
                        continue
                    if film.tmdb_id in ctx.excluded and film.tmdb_id not in ctx.protected:
                        continue
                    if (
                        ctx.min_runtime
                        and film.runtime is not None
                        and film.runtime < ctx.min_runtime
                    ):
                        continue
                    if not (ctx.pair_ok(movie, film) if side.forward else ctx.pair_ok(film, movie)):
                        continue
                    new_state: State = (film.tmdb_id, new_tag)
                    if new_state in side.visited or new_state in new_states:
                        continue
                    new_states[new_state] = (state, via)
        except pathfinder._DeadlineReached:
            break  # the main loop reports the timeout
    return new_states


def _chain(side: _Side, state: State) -> list[tuple[State, NodeKey | None]]:
    """`state` back to the side's root, as (state, link used to reach it)."""
    chain: list[tuple[State, NodeKey | None]] = []
    current: State | None = state
    while current is not None:
        parent, via = side.visited[current]
        chain.append((current, via))
        current = parent
    return chain


def _assemble(forward: _Side, backward: _Side, f_state: State, b_state: State) -> list[NodeKey]:
    path: list[NodeKey] = []
    for state, via in reversed(_chain(forward, f_state)):  # root -> meeting
        if via is not None:
            path.append(via)
        path.append(movie_node(state[0]))
    for state, _via in _chain(backward, b_state)[:-1]:  # meeting -> ... (each step's own link)
        parent, via = backward.visited[state]
        if parent is None or via is None:
            continue
        path.append(via)
        path.append(movie_node(parent[0]))
    return path


def _tags_compatible(
    constraints: PathConstraints, forward_tag: str | None, back_tag: str | None
) -> bool:
    if not constraints.alternate_edges:
        return True
    return forward_tag is None or back_tag is None or forward_tag != back_tag


async def _meeting_paths(
    ctx: _Ctx,
    forward: _Side,
    backward: _Side,
    fresh_side: _Side,
    fresh: list[State],
    min_hops: int | None,
    rejected: set[tuple[State, State]],
) -> list[list[NodeKey]]:
    other = backward if fresh_side is forward else forward
    candidates: dict[tuple[int, ...], list[NodeKey]] = {}
    for state in fresh:
        for other_state in other.by_movie.get(state[0], []):
            f_state, b_state = (
                (state, other_state) if fresh_side is forward else (other_state, state)
            )
            if (f_state, b_state) in rejected:
                continue
            if not _tags_compatible(ctx.constraints, f_state[1], b_state[1]):
                continue
            path = _assemble(forward, backward, f_state, b_state)
            movie_ids = tuple(node[1] for node in path[0::2])
            if len(set(movie_ids)) != len(movie_ids):
                continue  # doubles back through a film
            if min_hops and pathfinder._path_hops(path) < min_hops:
                continue
            candidates.setdefault(movie_ids, path)
            if ctx.constraints.needs_detail:
                # Films discovered on the final hop were never expanded; verify them now.
                await hydrate_movies(
                    ctx.session, ctx.tmdb, list(movie_ids), deadline=ctx.deadline + 3
                )
                if not await _path_satisfies(ctx, movie_ids):
                    rejected.add((f_state, b_state))
                    candidates.pop(movie_ids, None)
    return pathfinder.prefer_disjoint_paths(sorted(candidates.values(), key=len))[:MAX_RESULT_PATHS]


async def _path_satisfies(ctx: _Ctx, movie_ids: tuple[int, ...]) -> bool:
    movies = [
        await anyio.to_thread.run_sync(ctx.session.get, CachedMovie, mid) for mid in movie_ids
    ]
    if any(movie is None for movie in movies):
        return True  # can't judge - the constraint treats unknown data as allowed
    return all(ctx.pair_ok(a, b) for a, b in pairwise(movies))


async def solve_constrained(
    session: Session,
    tmdb: TMDBClient,
    from_movie_id: int,
    to_movie_id: int,
    constraints: PathConstraints,
    max_depth: int | None = None,
    call_budget: int | None = None,
    cast_limit: int | None = None,
    min_runtime: int | None = None,
    excluded_movie_ids: set[int] | None = None,
    max_duration_seconds: int | None = None,
    min_hops: int | None = None,
) -> AsyncIterator[dict]:
    settings = get_settings()
    max_depth = max_depth or settings.pathfinder_max_depth
    if min_hops:
        max_depth = max(max_depth, min_hops)
    cast_limit = cast_limit or settings.pathfinder_cast_limit
    frontier_cap = settings.pathfinder_actor_credit_limit
    max_duration_seconds = (
        max_duration_seconds
        if max_duration_seconds is not None
        else settings.bridge_max_duration_seconds
    )

    async with pathfinder._SEARCH_SEMAPHORE:
        start = time.monotonic()
        deadline = start + max_duration_seconds
        stats = pathfinder._SearchStats()
        ctx = _Ctx(
            session=session,
            tmdb=tmdb,
            repo=CacheRepo(session),
            constraints=constraints,
            cast_limit=cast_limit,
            min_runtime=min_runtime,
            excluded=excluded_movie_ids or set(),
            call_budget=call_budget,
            deadline=deadline,
            stats=stats,
            protected={from_movie_id, to_movie_id},
        )
        try:
            if from_movie_id == to_movie_id:
                result = await pathfinder._finish_result(
                    session, tmdb, [[movie_node(from_movie_id)]], deadline
                )
                yield {"type": "result", **result}
                yield {"type": "done"}
                return

            if constraints.endpoint_reason is not None:
                source = await ctx.movie(from_movie_id)
                target = await ctx.movie(to_movie_id)
                reason = (
                    constraints.endpoint_reason(source, target)
                    if source is not None and target is not None
                    else None
                )
                if reason:
                    yield {
                        "type": "exhausted",
                        "reason": "constraint_impossible",
                        "message": reason,
                        "tmdb_calls": stats.tmdb_calls,
                        "elapsed_ms": (time.monotonic() - start) * 1000,
                    }
                    yield {"type": "done"}
                    return

            forward = _Side.starting_at((from_movie_id, constraints.start_tag), forward=True)
            backward = _Side.starting_at((to_movie_id, None), forward=False)
            rejected: set[tuple[State, State]] = set()
            last_forward = False

            while forward.hops + backward.hops < max_depth:
                live = [side for side in (forward, backward) if side.frontier]
                if not live:
                    break  # both sides stalled
                if len(live) == 2 and len(forward.frontier) == len(backward.frontier):
                    side = backward if last_forward else forward
                else:
                    side = min(live, key=lambda s: len(s.frontier))
                last_forward = side is forward

                expansion = asyncio.ensure_future(_expand(ctx, side, frontier_cap))
                try:
                    while not expansion.done():
                        await asyncio.wait({expansion}, timeout=pathfinder._EVENT_FLUSH_SECONDS)
                        while stats.pending_events:
                            yield stats.pending_events.pop(0)
                finally:
                    if not expansion.done():
                        expansion.cancel()
                new_states = expansion.result()
                side.hops += 1
                for state, (parent, via) in new_states.items():
                    side.add(state, parent, via)
                side.frontier = list(new_states)

                yield {
                    "type": "progress",
                    "depth": forward.hops + backward.hops,
                    "frontier_forward": len(forward.frontier),
                    "frontier_backward": len(backward.frontier),
                    "tmdb_calls": stats.tmdb_calls,
                    "cache_hits": stats.cache_hits,
                    "rate_limit_pauses": stats.rate_limit_pauses,
                    "elapsed_ms": (time.monotonic() - start) * 1000,
                }

                paths = await _meeting_paths(
                    ctx, forward, backward, side, side.frontier, min_hops, rejected
                )
                if paths:
                    result = await pathfinder._finish_result(session, tmdb, paths, deadline)
                    yield {"type": "result", **result}
                    yield {"type": "done"}
                    return

                if call_budget is not None and stats.tmdb_calls >= call_budget:
                    yield {
                        "type": "exhausted",
                        "reason": "budget_exceeded",
                        "tmdb_calls": stats.tmdb_calls,
                        "elapsed_ms": (time.monotonic() - start) * 1000,
                    }
                    yield {"type": "done"}
                    return

                if time.monotonic() - start >= max_duration_seconds:
                    depth = forward.hops + backward.hops
                    yield {
                        "type": "timeout",
                        "depth_reached": depth,
                        "tmdb_calls": stats.tmdb_calls,
                        "elapsed_ms": (time.monotonic() - start) * 1000,
                        "message": (
                            f"Search exceeded the {max_duration_seconds}s solver timeout after "
                            f"reaching depth {depth}. Try widening the cast depth rule or "
                            "raising the solver timeout in Settings."
                        ),
                    }
                    yield {"type": "done"}
                    return

            yield {
                "type": "exhausted",
                "reason": "max_depth_reached",
                "tmdb_calls": stats.tmdb_calls,
                "elapsed_ms": (time.monotonic() - start) * 1000,
            }
            yield {"type": "done"}
        except Exception as exc:
            logger.exception("constrained bridge solve failed")
            yield {"type": "error", "message": str(exc)}
            yield {"type": "done"}
