"""Bipartite movie<->actor graph primitives for the bridge pathfinder."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.models.cache import CachedMovie

NodeKey = tuple[str, int]  # ("movie", tmdb_id) | ("actor", actor_id)


def movie_node(movie_id: int) -> NodeKey:
    return ("movie", movie_id)


def actor_node(actor_id: int) -> NodeKey:
    return ("actor", actor_id)


DIRECTOR_NODE = "director"


def director_node(person_id: int) -> NodeKey:
    return (DIRECTOR_NODE, person_id)


@dataclass(frozen=True)
class PathConstraints:
    """What a graph-mutator mode requires of a bridge path (built by
    `engine.bridge_constraints`), solved by `constrained_pathfinder`."""

    # (earlier film, later film) -> may the later one directly follow the earlier one?
    movie_ok: Callable[[CachedMovie, CachedMovie], bool] | None = None
    # Why (from, to) can never be bridged at all, if that's knowable up front.
    endpoint_reason: Callable[[CachedMovie, CachedMovie], str | None] | None = None
    # `movie_ok` needs fully fetched film detail (e.g. origin country).
    needs_detail: bool = False
    # Consecutive hops must use different link kinds (actor / director).
    alternate_edges: bool = False
    use_directors: bool = False
    # Link kind that led INTO the start film (the run's last hop), for alternation.
    start_tag: str | None = None


@dataclass
class FrontierSide:
    """One side (forward or backward) of the bidirectional search.

    `visited` maps every discovered node to the node that discovered it
    (None for the root), used to reconstruct the path once the two sides meet.
    `frontier` holds only the nodes discovered in the *most recent* round -
    the ones due to be expanded next.
    """

    visited: dict[NodeKey, NodeKey | None]
    frontier: set[NodeKey]
    next_type: str = "movie"  # "movie" | "actor" - the kind of node `frontier` holds
    hops: int = 0  # completed movie-hops (movie -> actor -> movie) so far

    @classmethod
    def starting_at(cls, root: NodeKey) -> FrontierSide:
        return cls(visited={root: None}, frontier={root})
