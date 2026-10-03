"""Iterative bidirectional BFS over the JIT movie<->actor bipartite graph.

One movie-hop = two bipartite edges (movie -> actor -> movie). Each round
expands whichever side (forward, from the source movie; backward, from the
target movie) has the smaller frontier, alternating movie/actor levels, and
checks for a meeting node after every round - so a direct 1-hop shared-cast
pair or a 2-hop shared-filmography bridge resolves in one or two rounds.

Cache-resident nodes are expanded for free; nodes with no cached cast/credits
yet cost one unit of `call_budget` each, spent in cache-first order so a
`call_budget` cap never blocks progress that's already free. A single
`asyncio.Semaphore(1)` guards all solves process-wide, since a full-budget
uncached search materializes a lot of frontier state and this is a low-RAM
single-worker service, not a multi-tenant one.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable

import anyio
from sqlmodel import Session, select

from app.config import get_settings
from app.models.cache import CachedActor, CachedMovie, CachedMovieCast
from app.models.curated import CanonMovieBadge
from app.schemas.engine import BridgeNode, SharedActorConnection
from app.services import cache_repo
from app.services.cache_repo import CacheRepo
from app.services.graph import FrontierSide, NodeKey, actor_node, movie_node
from app.services.movie_filters import is_reality_eligible
from app.services.tmdb import TMDBClient, TMDBNotFoundError, TMDBRateLimitError
from app.utils.dates import parse_release_year

logger = logging.getLogger(__name__)

_SEARCH_SEMAPHORE = asyncio.Semaphore(1)
_MAX_RATE_LIMIT_PAUSE_SECONDS = 30.0
_EVENT_FLUSH_SECONDS = 0.25


class _SearchStats:
    __slots__ = ("cache_hits", "pending_events", "rate_limit_pauses", "tmdb_calls")

    def __init__(self) -> None:
        self.tmdb_calls = 0
        self.cache_hits = 0
        self.rate_limit_pauses = 0
        # Events raised mid-round (e.g. a 429 pause) that the solve loop flushes to the stream.
        self.pending_events: list[dict] = []


class _DeadlineReached(Exception):
    """Internal: the solver's time limit expired while waiting on TMDB."""


async def _fetch_with_backoff[T](
    fetch: Callable[[], Awaitable[T]], stats: _SearchStats, deadline: float
) -> T | None:
    """Runs one uncached TMDB fetch. On a 429 the solver pauses (escalating,
    honoring Retry-After) and retries instead of failing the search; it only
    gives up when the admin-defined deadline passes. A 404 (node deleted from
    TMDB) skips that node, returning None."""
    pause = 2.0
    while True:
        if time.monotonic() >= deadline:
            raise _DeadlineReached
        try:
            return await fetch()
        except TMDBRateLimitError as exc:
            stats.rate_limit_pauses += 1
            wait = min(exc.retry_after or pause, _MAX_RATE_LIMIT_PAUSE_SECONDS)
            logger.warning("TMDB rate limit hit; pausing bridge search %.1fs", wait)
            stats.pending_events.append({
                "type": "rate_limited",
                "wait_seconds": round(wait, 1),
                "rate_limit_pauses": stats.rate_limit_pauses,
            })
            await asyncio.sleep(max(0.0, min(wait, deadline - time.monotonic())))
            pause = min(pause * 2, _MAX_RATE_LIMIT_PAUSE_SECONDS)
        except TMDBNotFoundError:
            return None


async def solve_bridge_bipartite(
    session: Session,
    tmdb: TMDBClient,
    from_movie_id: int,
    to_movie_id: int,
    max_depth: int | None = None,
    call_budget: int | None = None,
    cast_limit: int | None = None,
    min_runtime: int | None = None,
    excluded_movie_ids: set[int] | None = None,
    max_duration_seconds: int | None = None,
) -> AsyncIterator[dict]:
    settings = get_settings()
    max_depth = max_depth or settings.pathfinder_max_depth
    # `call_budget` is a hard cap only for callers that explicitly pass one
    # (the fast /engine/bridge pre-check). The streamed solve is bounded by
    # depth and the admin-defined time limit instead.
    # A run's own `max_cast_order` rule overrides the global default depth,
    # same "optional extra kwarg" pattern as validate_next_step's cast_limit.
    cast_limit = cast_limit or settings.pathfinder_cast_limit
    actor_cap = settings.pathfinder_actor_credit_limit
    excluded_movie_ids = excluded_movie_ids or set()
    # `or` would treat an explicit 0 as "unset" - 0 is a legitimate caller
    # value here (e.g. tests forcing an immediate timeout), unlike the other
    # budget knobs above where 0 would never be a meaningful override.
    max_duration_seconds = (
        max_duration_seconds if max_duration_seconds is not None else settings.bridge_max_duration_seconds
    )

    async with _SEARCH_SEMAPHORE:
        start = time.monotonic()
        deadline = start + max_duration_seconds
        repo = CacheRepo(session)
        stats = _SearchStats()

        try:
            forward = FrontierSide.starting_at(movie_node(from_movie_id))
            backward = FrontierSide.starting_at(movie_node(to_movie_id))
            last_expanded_forward = False  # alternates on ties so neither side starves

            if from_movie_id == to_movie_id:
                result = await anyio.to_thread.run_sync(
                    _build_multi_result, session, [[movie_node(from_movie_id)]]
                )
                yield {"type": "result", **result}
                yield {"type": "done"}
                return

            while forward.hops + backward.hops < max_depth:
                if len(forward.frontier) != len(backward.frontier):
                    pick_forward = len(forward.frontier) < len(
                        backward.frontier)
                else:
                    pick_forward = not last_expanded_forward
                side = forward if pick_forward else backward
                last_expanded_forward = pick_forward

                if side.next_type == "movie":
                    expansion = asyncio.ensure_future(_expand_movie_side(
                        session, tmdb, repo, side, cast_limit, stats, call_budget, deadline
                    ))
                else:
                    expansion = asyncio.ensure_future(_expand_actor_side(
                        session,
                        tmdb,
                        repo,
                        side,
                        actor_cap,
                        stats,
                        call_budget,
                        deadline,
                        excluded_movie_ids,
                        min_runtime,
                    ))
                try:
                    # Wake periodically so a 429 pause can be streamed to the client live.
                    while not expansion.done():
                        await asyncio.wait({expansion}, timeout=_EVENT_FLUSH_SECONDS)
                        while stats.pending_events:
                            yield stats.pending_events.pop(0)
                finally:
                    if not expansion.done():
                        expansion.cancel()
                new_nodes = expansion.result()
                if side.next_type == "movie":
                    side.next_type = "actor"
                else:
                    side.next_type = "movie"
                    side.hops += 1

                genuinely_new = {
                    node: parent for node, parent in new_nodes.items() if node not in side.visited
                }
                side.visited.update(genuinely_new)
                side.frontier = set(genuinely_new.keys())

                elapsed_ms = (time.monotonic() - start) * 1000
                yield {
                    "type": "progress",
                    "depth": forward.hops + backward.hops,
                    "frontier_forward": len(forward.frontier),
                    "frontier_backward": len(backward.frontier),
                    "tmdb_calls": stats.tmdb_calls,
                    "cache_hits": stats.cache_hits,
                    "rate_limit_pauses": stats.rate_limit_pauses,
                    "elapsed_ms": elapsed_ms,
                }

                meetings = _find_intersections(
                    forward.visited, backward.visited, limit=3)
                if meetings:
                    candidate_paths = [
                        _reconstruct_path(forward, backward, meeting) for meeting in meetings
                    ]
                    result = await anyio.to_thread.run_sync(
                        _build_multi_result, session, candidate_paths)
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
                    yield {
                        "type": "timeout",
                        "depth_reached": forward.hops + backward.hops,
                        "tmdb_calls": stats.tmdb_calls,
                        "elapsed_ms": (time.monotonic() - start) * 1000,
                        "message": (
                            f"Search exceeded the {max_duration_seconds}s solver timeout after "
                            f"reaching depth {forward.hops + backward.hops}. Try widening the "
                            "cast depth rule or raising the solver timeout in Settings."
                        ),
                    }
                    yield {"type": "done"}
                    return

                if not forward.frontier and not backward.frontier:
                    break  # both sides stalled - nothing left to explore before max_depth

            yield {
                "type": "exhausted",
                "reason": "max_depth_reached",
                "tmdb_calls": stats.tmdb_calls,
                "elapsed_ms": (time.monotonic() - start) * 1000,
            }
            yield {"type": "done"}
        except Exception as exc:  # noqa: BLE001 - surfaced as a client-facing error event, not a 500
            yield {"type": "error", "message": str(exc)}
            yield {"type": "done"}


async def _expand_movie_side(
    session: Session,
    tmdb: TMDBClient,
    repo: CacheRepo,
    side: FrontierSide,
    cast_limit: int,
    stats: _SearchStats,
    call_budget: int | None,
    deadline: float,
) -> dict[NodeKey, NodeKey]:
    movie_ids = [node[1] for node in side.frontier]

    cached: list[tuple[int, list]] = []
    uncached: list[int] = []
    for movie_id in movie_ids:
        cast = await anyio.to_thread.run_sync(repo.get_cached_cast, movie_id, cast_limit)
        if cast is not None:
            cached.append((movie_id, cast))
        else:
            uncached.append(movie_id)

    new_nodes: dict[NodeKey, NodeKey] = {}

    for movie_id, cast in cached:
        stats.cache_hits += 1
        parent = movie_node(movie_id)
        for member in cast:
            new_nodes.setdefault(actor_node(member["actor_id"]), parent)

    for movie_id in uncached:
        if call_budget is not None and stats.tmdb_calls >= call_budget:
            break
        if stats.tmdb_calls > 0:
            await asyncio.sleep(tmdb.pacing_delay_seconds)
        try:
            cast = await _fetch_with_backoff(
                lambda movie_id=movie_id: cache_repo.get_movie_cast(
                    session, tmdb, movie_id, cast_limit),
                stats, deadline)
        except _DeadlineReached:
            break  # the main loop reports the timeout
        stats.tmdb_calls += 1
        if cast is None:
            continue
        parent = movie_node(movie_id)
        for member in cast:
            new_nodes.setdefault(actor_node(member["actor_id"]), parent)

    return new_nodes


async def _expand_actor_side(
    session: Session,
    tmdb: TMDBClient,
    repo: CacheRepo,
    side: FrontierSide,
    actor_cap: int,
    stats: _SearchStats,
    call_budget: int | None,
    deadline: float,
    excluded_movie_ids: set[int],
    min_runtime: int | None,
) -> dict[NodeKey, NodeKey]:
    actor_ids = [node[1] for node in side.frontier]

    cached: list[tuple[int, list]] = []
    uncached: list[int] = []
    for actor_id in actor_ids:
        credits_ = await anyio.to_thread.run_sync(repo.get_cached_actor_credits, actor_id)
        if credits_ is not None:
            cached.append((actor_id, credits_))
        else:
            uncached.append(actor_id)

    # Cache-resident actors are expanded first and always fit; the budget-cap
    # only limits how many *uncached* actors get spent on this round.
    ordered: list[tuple[int, list | None]] = [(a, c) for a, c in cached] + [
        (a, None) for a in uncached
    ]
    ordered = ordered[:actor_cap]

    new_nodes: dict[NodeKey, NodeKey] = {}
    for actor_id, credits_ in ordered:
        parent = actor_node(actor_id)
        if credits_ is None:
            if call_budget is not None and stats.tmdb_calls >= call_budget:
                continue
            if stats.tmdb_calls > 0:
                await asyncio.sleep(tmdb.pacing_delay_seconds)
            try:
                credits_ = await _fetch_with_backoff(
                    lambda actor_id=actor_id: cache_repo.get_actor_credits(
                        session, tmdb, actor_id),
                    stats, deadline)
            except _DeadlineReached:
                break  # the main loop reports the timeout
            stats.tmdb_calls += 1
            if credits_ is None:
                continue
        else:
            stats.cache_hits += 1
        for movie in credits_:
            # Reality filter + run-scoped exclusion/min-runtime pruning happen
            # HERE (the only place new movie nodes enter the graph) - roots
            # (from_movie_id/to_movie_id) are added directly and never pass
            # through this filter, so a run's own already-watched frontier
            # movie can still be a valid start/target even though it's also
            # in `excluded_movie_ids`.
            if not is_reality_eligible(movie):
                continue
            if movie.tmdb_id in excluded_movie_ids:
                continue
            if min_runtime and movie.runtime is not None and movie.runtime < min_runtime:
                continue
            new_nodes.setdefault(movie_node(movie.tmdb_id), parent)

    return new_nodes


def _find_intersections(
    forward_visited: dict[NodeKey, NodeKey | None],
    backward_visited: dict[NodeKey, NodeKey | None],
    limit: int = 3,
) -> list[NodeKey]:
    """Collects up to `limit` distinct meeting nodes discovered in THIS round
    (not just the first) so the caller can offer 2-3 alternate bridge paths -
    e.g. two different actors both directly connecting the same two films."""
    small, large = (
        (forward_visited, backward_visited)
        if len(forward_visited) <= len(backward_visited)
        else (backward_visited, forward_visited)
    )
    found = [node for node in small if node in large]
    return found[:limit]


def _reconstruct_path(forward: FrontierSide, backward: FrontierSide, meeting: NodeKey) -> list[NodeKey]:
    forward_chain: list[NodeKey] = []
    node: NodeKey | None = meeting
    while node is not None:
        forward_chain.append(node)
        node = forward.visited[node]
    forward_chain.reverse()  # root_from -> ... -> meeting

    backward_chain: list[NodeKey] = []
    node = backward.visited[meeting]
    while node is not None:
        backward_chain.append(node)  # already meeting -> ... -> root_to order
        node = backward.visited[node]

    return forward_chain + backward_chain


def _build_result(session: Session, combined_path: list[NodeKey]) -> dict:
    """Sync DB reads only - every node on the path was already cached during the search."""
    movie_ids = [node[1] for node in combined_path[0::2]]
    actor_ids = [node[1] for node in combined_path[1::2]]

    bridge_nodes = []
    for movie_id in movie_ids:
        movie = session.get(CachedMovie, movie_id)
        bridge_nodes.append(
            BridgeNode(
                movie_id=movie_id,
                title=movie.title if movie else str(movie_id),
                poster_path=movie.poster_path if movie else None,
                release_year=parse_release_year(
                    movie.release_date) if movie else None,
                popularity=movie.popularity if movie else None,
            )
        )

    connections = []
    for i, actor_id in enumerate(actor_ids):
        from_id, to_id = movie_ids[i], movie_ids[i + 1]
        actor = session.get(CachedActor, actor_id)
        cast_from = session.get(CachedMovieCast, (from_id, actor_id))
        cast_to = session.get(CachedMovieCast, (to_id, actor_id))
        connections.append(
            SharedActorConnection(
                actor_id=actor_id,
                actor_name=actor.name if actor else str(actor_id),
                profile_path=actor.profile_path if actor else None,
                character_in_from=cast_from.character_name if cast_from else None,
                character_in_to=cast_to.character_name if cast_to else None,
            )
        )

    return {
        "path": bridge_nodes,
        "hops": len(movie_ids) - 1,
        "connections": connections,
    }


def _build_multi_result(session: Session, candidate_paths: list[list[NodeKey]]) -> dict:
    """Builds every candidate collision-layer path and picks up to 3 distinct
    ones (shortest first) to return as `alternate_paths`, so the frontend can
    offer 'Path 1 (Shortest)' / 'Path 2 (Alternative Cast Link)' style tabs.
    """
    built = [_build_result(session, path) for path in candidate_paths]
    built.sort(key=lambda entry: entry["hops"])
    labels = _label_paths(session, built)

    primary = built[0]
    alternates = [
        {"label": label, **entry} for label, entry in zip(labels[1:], built[1:], strict=True)
    ]
    return {
        "path": primary["path"],
        "hops": primary["hops"],
        "connections": primary["connections"],
        "label": labels[0],
        "alternate_paths": alternates,
    }


def _label_paths(session: Session, built: list[dict]) -> list[str]:
    """Shortest first; same-length siblings are 'Alternative Cast Link' (a
    different connecting actor/bridge film at the same hop count), UNLESS
    an alternate's intermediate films are notably less mainstream (lower
    average TMDB popularity) than the primary's - then it's flagged as the
    'Underdog / International Pick', celebrating world/indie cinema rather
    than penalizing it. An alternate that traverses a Curated Canon badged
    film (Sight & Sound, Letterboxd Top 250, etc) is surfaced as 'The
    Cinephile Route' instead, taking priority over both other labels."""
    if len(built) == 1:
        return ["Shortest"]

    def avg_intermediate_popularity(entry: dict) -> float:
        intermediates = entry["path"][1:-1]
        pops = [
            node.popularity for node in intermediates if node.popularity is not None]
        return sum(pops) / len(pops) if pops else 0.0

    def has_canon_badge(entry: dict) -> bool:
        movie_ids = [node.movie_id for node in entry["path"][1:-1]]
        if not movie_ids:
            return False
        return (
            session.exec(
                select(CanonMovieBadge.id).where(
                    CanonMovieBadge.movie_id.in_(movie_ids)).limit(1)
            ).first()
            is not None
        )

    primary_hops = built[0]["hops"]
    primary_popularity = avg_intermediate_popularity(built[0])
    rest = built[1:]
    rest_popularity = [avg_intermediate_popularity(entry) for entry in rest]
    underdog_index = rest_popularity.index(
        min(rest_popularity)) if rest_popularity else None

    labels = ["Shortest"]
    for i, entry in enumerate(rest):
        if has_canon_badge(entry):
            labels.append("The Cinephile Route")
        elif entry["hops"] > primary_hops:
            labels.append("Alternative Path")
        elif i == underdog_index and rest_popularity[i] < primary_popularity:
            labels.append("Underdog / International Pick")
        else:
            labels.append("Alternative Cast Link")
    return labels
