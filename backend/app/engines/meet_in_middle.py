"""Meet in the Middle: a two-way tunnel for two players.

Partner A starts at one film (the *head*), Partner B at another (the *tail*). Every step
extends one of the two ends with a film that shares credited cast with that end's current
frontier. The moment a new film *also* connects to the opposite frontier the two chains have
collided and the run is won.

The chain is not linear, so each step records the end it extends in
`transition_metadata["tunnel_side"]` ("head" | "tail"); `split_sides` rebuilds the two tracks
from the run's steps. The cast link itself is the classic CineChain rule, so this engine
extends `CineChainEngine` (itself a `BaseChallengeEngine`) and only adds the tunnel state,
collision detection and the distance estimate between the two frontiers. Run modifiers
(chrono, runtime, cooldown) are ordering rules that make no sense on a chain that grows from
both ends, so they are off.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import ClassVar

from app.engines.cinechain import CineChainEngine
from app.engines.conditions import RunOutcome
from app.engines.rulebook import RuleSection
from app.models.run import (
    LEGACY_ENGINE_VERSION,
    RUN_STATUS_ACTIVE,
    RUN_STATUS_COMPLETED,
    Run,
    RunStep,
)
from app.schemas.engine import BridgeNode, SharedActorConnection
from app.services import cache_repo, pathfinder

MEET_IN_THE_MIDDLE = "meet_in_the_middle"
SIDE_HEAD = "head"
SIDE_TAIL = "tail"
TUNNEL_SIDES = (SIDE_HEAD, SIDE_TAIL)

# "Fast" frontier-to-frontier search: shallow and time-boxed, never the full solver budget.
DISTANCE_MAX_DEPTH = 5
DISTANCE_MAX_SECONDS = 8


def opposite(side: str) -> str:
    return SIDE_TAIL if side == SIDE_HEAD else SIDE_HEAD


def step_side(step: RunStep) -> str:
    side = (step.transition_metadata or {}).get("tunnel_side")
    return side if side in TUNNEL_SIDES else SIDE_HEAD


def split_sides(steps: Sequence[RunStep]) -> tuple[list[RunStep], list[RunStep]]:
    """(head steps, tail steps), each in the order they were logged."""
    head: list[RunStep] = []
    tail: list[RunStep] = []
    for step in steps:
        (tail if step_side(step) == SIDE_TAIL else head).append(step)
    return head, tail


@dataclass(frozen=True)
class TunnelDistance:
    hops: int | None  # None = no route found within the search limits
    searched_depth: int
    message: str | None = None
    path_movie_ids: list[int] = field(default_factory=list)
    connections: list[SharedActorConnection] = field(default_factory=list)


class MeetInTheMiddleEngine(CineChainEngine):
    tagline = "Two partners, one tunnel"
    tags: ClassVar[list[str]] = ["Shared cast", "Co-op", "Two seeds"]
    rulebook: ClassVar[RuleSection] = RuleSection(
        "Connect two seed films until the chains collide.",
        ["Choose which end to extend and link a film to that frontier.",
         "Use a hint to inspect the route between the frontiers; {hints_remaining} hints remain."],
        ["A valid film that connects both ends wins immediately.", "{win_goal}"],
        ["{fail_goal}"],
        ["Work towards actors appearing near both frontiers, not just your own.",
         "Save hints for when the gap is hard to estimate."], ["seed", "wildcard"],
    )

    @classmethod
    def rulebook_values(cls, rules: dict | None) -> dict:
        config = rules or {}
        return {**super().rulebook_values(rules),
                "hints_remaining": config.get("tunnel_hints_remaining", config.get("tunnel_hints", 2))}
    seed_policy = "pair"
    game_type = MEET_IN_THE_MIDDLE
    display_name = "Meet in the Middle"
    description = (
        "A co-op tunnel: each partner starts from their own film and you extend your chains "
        "towards each other. The first film that connects to both ends makes them collide."
    )
    capabilities: ClassVar[list[str]] = [
        *(cap for cap in CineChainEngine.capabilities if cap not in ("modifiers", "bridge_swap")),
        "tunnel",
    ]
    supports_modifiers = False

    def prepare_rules_config(self, rules: dict) -> dict:
        hints = rules.get("tunnel_hints", 2)
        try:
            hints = int(hints)
        except (TypeError, ValueError):
            hints = 2
        return {**rules, "tunnel_hints_remaining": max(0, min(hints, 5))}

    def evaluate_run_outcome(self, run: Run, steps: list[RunStep]) -> RunOutcome | None:
        """Collision Victory: complete the run when a logged step connects both ends."""
        if run.engine_version <= LEGACY_ENGINE_VERSION or run.status != RUN_STATUS_ACTIVE:
            return None
        for step in steps:
            if (step.transition_metadata or {}).get("collision"):
                return RunOutcome(RUN_STATUS_COMPLETED, f"Chains collided at {step.movie_title}!")
        return super().evaluate_run_outcome(run, steps)

    async def collides(
        self,
        movie_id: int,
        opposing_steps: Sequence[RunStep],
        rules: dict | None,
    ) -> bool:
        """Does `movie_id` also connect validly to the opposite end's frontier? Being that very
        frontier film counts: extending one end with the other's frontier closes the tunnel."""
        if not opposing_steps:
            return False
        frontier = opposing_steps[-1]
        if movie_id == frontier.movie_id:
            return True
        result = await self.validate_next_step(
            frontier.movie_id,
            movie_id,
            cast_limit=(rules or {}).get("max_cast_order"),
            rules=rules,
            previous_transition=frontier.transition_metadata,
            history=list(opposing_steps),
        )
        return result.valid

    async def near_miss(
        self,
        movie_id: int,
        opposing_steps: Sequence[RunStep],
        rules: dict | None,
    ) -> RunStep | None:
        """Return the nearest earlier opposite-side film sharing cast with this pick.

        The current opposing frontier is excluded: a valid cast link to that film
        would have completed the run as a collision, not a near miss.
        """
        if len(opposing_steps) < 2:
            return None
        cast_limit = (rules or {}).get("max_cast_order")
        candidate_cast = await cache_repo.get_movie_cast(
            self.session, self.tmdb, movie_id, cast_limit
        )
        candidate_ids = {member["actor_id"] for member in candidate_cast}
        if not candidate_ids:
            return None
        for step in reversed(opposing_steps[:-1]):
            previous_cast = await cache_repo.get_movie_cast(
                self.session, self.tmdb, step.movie_id, cast_limit
            )
            if candidate_ids.intersection(member["actor_id"] for member in previous_cast):
                return step
        return None

    async def distance(
        self,
        head_movie_id: int,
        tail_movie_id: int,
        excluded_movie_ids: set[int],
        cast_limit: int | None = None,
        max_depth: int = DISTANCE_MAX_DEPTH,
        max_seconds: int = DISTANCE_MAX_SECONDS,
    ) -> TunnelDistance:
        """Movie-hops between the two frontiers (1 = they already share cast), via a quick
        bidirectional BFS that avoids every film already in the run."""
        if head_movie_id == tail_movie_id:
            return TunnelDistance(hops=0, searched_depth=0)
        hops: int | None = None
        path_movie_ids: list[int] = []
        connections: list[SharedActorConnection] = []
        searched_depth = 0
        message: str | None = None
        async for event in pathfinder.solve_bridge_bipartite(
            self.session,
            self.tmdb,
            head_movie_id,
            tail_movie_id,
            max_depth=max_depth,
            cast_limit=cast_limit,
            excluded_movie_ids=excluded_movie_ids,
            max_duration_seconds=max_seconds,
        ):
            if event["type"] == "result":
                hops = event["hops"]
                path_movie_ids = [
                    node.movie_id if isinstance(node, BridgeNode) else node["movie_id"]
                    for node in event["path"]
                ]
                connections = [
                    connection
                    if isinstance(connection, SharedActorConnection)
                    else SharedActorConnection.model_validate(connection)
                    for connection in event["connections"]
                ]
                searched_depth = hops
            elif event["type"] == "timeout":
                searched_depth = event.get("depth_reached", searched_depth)
                message = (
                    event.get("message") or "The search timed out - the ends may still be close."
                )
            elif event["type"] == "exhausted":
                searched_depth = event.get("depth_reached", max_depth)
                message = f"No route within {searched_depth} hops yet."
            elif event["type"] == "error":
                message = event.get("message", "The search failed.")
        return TunnelDistance(
            hops=hops,
            searched_depth=searched_depth,
            message=message,
            path_movie_ids=path_movie_ids,
            connections=connections,
        )
