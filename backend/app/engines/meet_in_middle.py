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
from dataclasses import dataclass
from typing import ClassVar

from app.engines.cinechain import CineChainEngine
from app.engines.conditions import RunOutcome
from app.models.run import (
    LEGACY_ENGINE_VERSION,
    RUN_STATUS_ACTIVE,
    RUN_STATUS_COMPLETED,
    Run,
    RunStep,
)
from app.services import pathfinder

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


class MeetInTheMiddleEngine(CineChainEngine):
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

    def evaluate_run_outcome(self, run: Run, steps: list[RunStep]) -> RunOutcome | None:
        """Collision Victory: complete the run when a logged step connects both ends."""
        if run.engine_version <= LEGACY_ENGINE_VERSION or run.status != RUN_STATUS_ACTIVE:
            return None
        for step in steps:
            if (step.transition_metadata or {}).get("collision"):
                return RunOutcome(
                    RUN_STATUS_COMPLETED, f"Chains collided at {step.movie_title}!")
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
            frontier.movie_id, movie_id, cast_limit=(rules or {}).get("max_cast_order"),
            rules=rules, previous_transition=frontier.transition_metadata,
            history=list(opposing_steps))
        return result.valid

    async def distance(
        self, head_movie_id: int, tail_movie_id: int, excluded_movie_ids: set[int],
        cast_limit: int | None = None,
    ) -> TunnelDistance:
        """Movie-hops between the two frontiers (1 = they already share cast), via a quick
        bidirectional BFS that avoids every film already in the run."""
        if head_movie_id == tail_movie_id:
            return TunnelDistance(hops=0, searched_depth=0)
        hops: int | None = None
        message: str | None = None
        async for event in pathfinder.solve_bridge_bipartite(
            self.session, self.tmdb, head_movie_id, tail_movie_id,
            max_depth=DISTANCE_MAX_DEPTH, cast_limit=cast_limit,
            excluded_movie_ids=excluded_movie_ids, max_duration_seconds=DISTANCE_MAX_SECONDS,
        ):
            if event["type"] == "result":
                hops = event["hops"]
            elif event["type"] == "timeout":
                message = "The quick search timed out - the ends may still be close."
            elif event["type"] == "exhausted":
                message = f"No route within {DISTANCE_MAX_DEPTH} hops yet."
        return TunnelDistance(
            hops=hops, searched_depth=DISTANCE_MAX_DEPTH, message=None if hops else message)
