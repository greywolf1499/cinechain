"""Blind Fork: the active player offers 3 films, the partner vetoes 1 and picks from the rest.

The offer lives in `run.rules_config["pending_fork"]`:
`{"offered_by_id", "movie_ids", "offered_at", "links"?, "vetoed_movie_id"?, "vetoed_by_id"?}`.
Vetoing removes the film from `movie_ids`, so an offer with two films left is ready to accept.
"""

from typing import Any

from app.utils.ids import utcnow

BLIND_FORK_KEY = "blind_fork"
PENDING_FORK_KEY = "pending_fork"
OFFER_SIZE = 3
SERVER_OWNED_RULES = (
    PENDING_FORK_KEY, "tug_scores", "tug_players", "tug_momentum", "tug_rules_version",
    "tunnel_hints_remaining", "tunnel_distance", "tier_override", "lives_remaining",
    # Built by the March Madness / Method Actor / Auteur Marathon / Regional Deep Dive engines
    # when the run is created.
    "bracket", "bracket_films", "actor", "filmography", "director", "expedition",
    # Bounty Board state and Rotten Tomatoes Split scoreboard.
    "active_bounties", "completed_bounties", "split_scores", "split_players",
    # The Chaos Button's one-step handicap is only ever rolled by the server.
    "active_chaos",
    # AI March Madness commentary and the Bounty Board's AI-generated bounty definitions.
    "bracket_commentary", "custom_bounties",
)

# `rules_config` keys the server derives; a client-supplied value would be a forged state
# (a pre-seeded offer, Tug scores, or momentum that starts a run close to victory).


def strip_server_rules(rules: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in rules.items() if k not in SERVER_OWNED_RULES}


def pending_fork(rules: dict | None) -> dict[str, Any] | None:
    fork = (rules or {}).get(PENDING_FORK_KEY)
    return fork if isinstance(fork, dict) else None


def with_fork(rules: dict | None, fork: dict[str, Any] | None) -> dict[str, Any]:
    """A copy of `rules` carrying `fork` (None clears it) - always a new dict, so the JSON
    column registers the change."""
    rest = {k: v for k, v in (rules or {}).items() if k != PENDING_FORK_KEY}
    return {**rest, PENDING_FORK_KEY: fork} if fork is not None else rest


def new_offer(
    user_id: str, movie_ids: list[int], links: dict[int, dict[str, Any]]
) -> dict[str, Any]:
    offer: dict[str, Any] = {
        "offered_by_id": user_id,
        "movie_ids": list(movie_ids),
        "offered_at": utcnow().isoformat(),
    }
    if links:
        offer["links"] = {str(movie_id): meta for movie_id, meta in links.items()
                          if movie_id in movie_ids}
    return offer
