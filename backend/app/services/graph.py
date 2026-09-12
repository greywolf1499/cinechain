"""Bipartite movie<->actor graph primitives for the bridge pathfinder."""

from __future__ import annotations

from dataclasses import dataclass

NodeKey = tuple[str, int]  # ("movie", tmdb_id) | ("actor", actor_id)


def movie_node(movie_id: int) -> NodeKey:
    return ("movie", movie_id)


def actor_node(actor_id: int) -> NodeKey:
    return ("actor", actor_id)


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
