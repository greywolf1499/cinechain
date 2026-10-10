"""Connect the Canon: hit three waypoints, leg by leg."""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from itertools import pairwise
from typing import Any, ClassVar

from app.engines.base import RunSetupError
from app.engines.cinechain import CineChainEngine, compute_run_stats
from app.engines.conditions import RunOutcome
from app.engines.rulebook import RuleSection
from app.models.run import RUN_STATUS_COMPLETED, Run, RunStep
from app.schemas.discovery import DiscoveryCandidate
from app.schemas.engine import RunStats, Suggestion, SuggestionFilters, ValidationResult
from app.services import goal_graph

CONNECT_CANON = "connect_canon"


class ConnectCanonEngine(CineChainEngine):
    game_type = CONNECT_CANON
    seed_policy = "none"
    display_name = "Connect the Canon"
    description = (
        "Reach all configured canon waypoints while staying near or under par on each leg."
    )
    tagline = "Three waypoints, one clean route"
    tags: ClassVar[list[str]] = ["Shared cast", "Waypoints", "Par scoring"]
    rulebook: ClassVar[RuleSection] = RuleSection(
        "Start at your first waypoint, then reach the rest in order.",
        [
            "The first waypoint is added as your starting film.",
            "Each leg has a target waypoint and a cache-derived par.",
            "Logging the current waypoint advances the active leg.",
        ],
        ["Reach the final waypoint to complete the run."],
        ["Use clues sparingly; the bridge solver is locked unless assist is enabled."],
        ["Route through broadly connected films before committing to narrow canon picks."],
        ["seed", "wildcard"],
    )

    def prepare_rules_config(self, rules: dict) -> dict:
        prepared = super().prepare_rules_config(rules)
        prepared.setdefault("order", "ordered")
        prepared.setdefault("assist", False)
        prepared.setdefault("link", "shared_cast")
        return prepared

    async def prepare_run(self, rules: dict, user_id: str) -> dict:
        prepared = self.prepare_rules_config(rules)
        waypoints = [int(movie_id) for movie_id in prepared.get("waypoint_movie_ids") or []]
        if len(waypoints) != 3 or len(set(waypoints)) != 3:
            raise RunSetupError(
                "Connect the Canon needs exactly three distinct waypoint movie ids."
            )
        order = prepared.get("order", "ordered")
        if order == "best_order":
            best = None
            for permutation in itertools.permutations(waypoints):
                legs = await self._build_legs(
                    list(permutation), prepared.get("link", "shared_cast")
                )
                score = sum(leg["par"] if isinstance(leg.get("par"), int) else 9 for leg in legs)
                candidate = (score, list(permutation), legs)
                if best is None or candidate < best:
                    best = candidate
            assert best is not None
            waypoints = best[1]
            legs = best[2]
        else:
            legs = await self._build_legs(waypoints, prepared.get("link", "shared_cast"))
        prepared["waypoints"] = waypoints
        prepared["legs"] = legs
        prepared["current_leg"] = 0
        return prepared

    async def _build_legs(self, waypoints: list[int], policy: str) -> list[dict[str, Any]]:
        legs: list[dict[str, Any]] = []
        excluded: set[int] = set()
        for from_id, to_id in pairwise(waypoints):
            result = goal_graph.search(
                self.session,
                [from_id],
                [to_id],
                policy=policy,
                max_depth=6,
                max_seconds=6.0,
                excluded_movie_ids=excluded,
            )
            legs.append(
                {
                    "from": from_id,
                    "to": to_id,
                    "par": result.par,
                    "distance": result.par,
                    "path": result.path_movie_ids,
                    "reached_at_step": None,
                }
            )
            if result.path_movie_ids:
                excluded.update(result.path_movie_ids[1:-1])
        return legs

    def sync_run_state(self, run: Run, steps: Sequence[RunStep]) -> None:
        rules = dict(run.rules_config or {})
        legs = [dict(leg) for leg in (rules.get("legs") or [])]
        if not legs:
            return
        watched = [step for step in steps if step.status == "watched"]
        current_leg = 0
        for step_index, step in enumerate(watched, start=1):
            if current_leg >= len(legs) or step.movie_id != int(legs[current_leg]["to"]):
                continue
            legs[current_leg]["reached_at_step"] = step_index
            current_leg += 1
        for leg in legs[current_leg:]:
            leg["reached_at_step"] = None
        rules["legs"] = legs
        rules["current_leg"] = current_leg
        run.rules_config = rules

    def evaluate_run_outcome(self, run: Run, steps: list[RunStep]) -> RunOutcome | None:
        own = super().evaluate_run_outcome(run, steps)
        if own is not None:
            return own
        rules = run.rules_config or {}
        waypoints = rules.get("waypoints") or []
        if not waypoints:
            return None
        legs = rules.get("legs") or []
        if legs and int(rules.get("current_leg") or 0) >= len(legs):
            return RunOutcome(RUN_STATUS_COMPLETED, "All canon waypoints reached.")
        return None

    async def validate_primary(
        self,
        from_movie_id: int,
        to_movie_id: int,
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
    ) -> ValidationResult:
        result = await super().validate_primary(
            from_movie_id,
            to_movie_id,
            cast_limit=cast_limit,
            rules=rules,
            previous_transition=previous_transition,
        )
        if not result.valid:
            return result
        config = rules or {}
        current_leg = int(config.get("current_leg") or 0)
        legs = config.get("legs") or []
        if current_leg < len(legs) and int(legs[current_leg]["to"]) == to_movie_id:
            result.mechanic = {**(result.mechanic or {}), "waypoint_reached": True}
        return result

    def link_metadata(self, result: ValidationResult, client_metadata: dict | None) -> dict | None:
        metadata = super().link_metadata(result, client_metadata) or {}
        if (result.mechanic or {}).get("waypoint_reached"):
            metadata["waypoint_reached"] = True
        return metadata or None

    async def discover_candidates(
        self,
        frontier_movie_id: int,
        mode: str = "or",
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
        history: Sequence[RunStep] | None = None,
    ) -> list[DiscoveryCandidate]:
        candidates = await super().discover_candidates(
            frontier_movie_id,
            mode,
            cast_limit,
            rules,
            previous_transition,
            history,
        )
        config = rules or {}
        legs = config.get("legs") or []
        current_leg = int(config.get("current_leg") or 0)
        target = int(legs[current_leg]["to"]) if current_leg < len(legs) else None
        if target is None:
            return candidates
        movie_ids = [candidate.movie_id for candidate in candidates]
        distances = goal_graph.distance_to_targets(
            self.session,
            movie_ids,
            [target],
            policy=config.get("link", "shared_cast"),
            max_depth=6,
            max_seconds=1.5,
        )
        for candidate in candidates:
            candidate.target_distance = distances.get(candidate.movie_id)
        candidates.sort(
            key=lambda candidate: (
                candidate.target_distance is None,
                candidate.target_distance if candidate.target_distance is not None else 99,
                -(candidate.popularity or 0.0),
            )
        )
        return candidates

    async def get_suggestions(
        self,
        current_movie_id: int,
        exclude_movie_ids: list[int],
        filters: SuggestionFilters,
        rules: dict | None = None,
        history: Sequence[RunStep] | None = None,
    ) -> list[Suggestion]:
        return []

    async def compute_stats(self, steps: list[RunStep]) -> RunStats:
        return compute_run_stats(steps)

    @staticmethod
    def par_score(run: Run, steps: Sequence[RunStep]) -> int | None:
        rules = run.rules_config or {}
        legs = rules.get("legs") or []
        watched = [step for step in steps if step.status == "watched"]
        if not legs or not watched:
            return None
        step_lookup: dict[int, int] = {}
        for index, step in enumerate(watched, start=1):
            step_lookup.setdefault(step.movie_id, index)
        start_step = step_lookup.get(int(legs[0]["from"]), 0)
        score = 0
        scored_legs = 0
        for leg in legs:
            reached = leg.get("reached_at_step")
            par = leg.get("par")
            if reached is None or par is None:
                continue
            score += int(reached) - start_step - int(par)
            start_step = int(reached)
            scored_legs += 1
        return score if scored_legs else None

    @classmethod
    def bridge_locked(cls, rules: dict | None, from_id: int, to_id: int) -> bool:
        config = rules or {}
        if config.get("assist"):
            return False
        legs = config.get("legs") or []
        current_leg = int(config.get("current_leg") or 0)
        if current_leg >= len(legs):
            return False
        leg = legs[current_leg]
        return {int(leg["from"]), int(leg["to"])} == {from_id, to_id}
