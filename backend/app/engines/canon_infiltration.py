"""Canon Infiltration: reach any film from a target canon list within a hop budget."""

from __future__ import annotations

import random
from collections.abc import Sequence
from time import monotonic
from typing import ClassVar

from sqlmodel import select

from app.engines.base import RunSetupError
from app.engines.cinechain import CineChainEngine, compute_run_stats
from app.engines.conditions import RunOutcome
from app.engines.rulebook import RuleSection
from app.facets.query import FacetQuery
from app.models.cache import CachedMovie
from app.models.curated import CanonMovieBadge
from app.models.run import RUN_STATUS_COMPLETED, RUN_STATUS_FAILED, Run, RunStep
from app.schemas.discovery import DiscoveryCandidate
from app.schemas.engine import RuleField, RunStats, Suggestion, SuggestionFilters
from app.services import feasibility, goal_graph

CANON_INFILTRATION = "canon_infiltration"
DEFAULT_HOP_LIMIT = 4
DEFAULT_TARGET_LIST = "sight_and_sound_2022"


class CanonInfiltrationEngine(CineChainEngine):
    game_type = CANON_INFILTRATION
    display_name = "Canon Infiltration"
    description = (
        "Start from a B-movie seed and infiltrate a canon list before the hop limit runs out."
    )
    tagline = "Break into the canon in limited hops"
    tags: ClassVar[list[str]] = ["Shared cast", "Target set", "Hop budget"]
    rulebook: ClassVar[RuleSection] = RuleSection(
        "Infiltrate a canon list in as few hops as possible.",
        [
            "Start from a seed film outside the target canon list.",
            "Any logged film in the target list wins instantly.",
        ],
        ["Reach the target list before your hop budget is exhausted."],
        ["Exceeding the configured hop limit fails the run."],
        ["Prioritize films with lower target-distance chips in Pick Next."],
        ["seed", "wildcard"],
    )
    rule_fields: ClassVar[list[RuleField]] = [
        *CineChainEngine.rule_fields,
        RuleField(
            key="hop_limit",
            kind="int",
            label="Hop limit",
            min=3,
            max=6,
            default=DEFAULT_HOP_LIMIT,
        ),
        RuleField(
            key="fog",
            kind="bool",
            label="Hide target distances",
            default=False,
        ),
    ]

    def __init__(self, session, tmdb) -> None:
        super().__init__(session, tmdb)
        self.setup_seed_movie_id: int | None = None

    def prepare_rules_config(self, rules: dict) -> dict:
        prepared = super().prepare_rules_config(rules)
        prepared.setdefault("target_list_id", DEFAULT_TARGET_LIST)
        prepared.setdefault("hop_limit", DEFAULT_HOP_LIMIT)
        prepared.setdefault("link", "shared_cast")
        return prepared

    async def seed_candidates(self, rules: dict) -> list[int]:
        low_rated = self.session.exec(
            select(CachedMovie.tmdb_id).where(
                CachedMovie.vote_average <= 5.5,
                CachedMovie.vote_count >= 50,
            )
        ).all()
        universe_ids = self.session.exec(select(CachedMovie.tmdb_id)).all()
        box_office_bombs = feasibility.matching_ids(
            self.session,
            FacetQuery(facet="box_office_bomb", op="eq", value=True),
            universe_ids,
        )
        return sorted(set(low_rated) | set(box_office_bombs))

    async def prepare_run(self, rules: dict, user_id: str) -> dict:
        prepared = self.prepare_rules_config(rules)
        seed_id = self.setup_seed_movie_id
        if seed_id is None:
            raise RunSetupError("Canon Infiltration needs a B-movie seed.")
        target_ids = self._target_ids(prepared)
        if not target_ids:
            raise RunSetupError("The selected canon list has no cached movie badges yet.")
        result = goal_graph.search(
            self.session,
            [seed_id],
            target_ids,
            policy=prepared.get("link", "shared_cast"),
            max_depth=int(prepared.get("hop_limit", DEFAULT_HOP_LIMIT)),
            max_seconds=6.0,
        )
        hop_limit = int(prepared.get("hop_limit", DEFAULT_HOP_LIMIT))
        distance = result.distance
        if distance is None or distance < 2 or distance > hop_limit:
            distance = await self._live_distance(seed_id, target_ids, hop_limit)
        if distance is None or distance < 2 or distance > hop_limit:
            raise RunSetupError(
                f"Seed must be between 2 and {hop_limit} hops from the target canon list."
            )
        prepared["infiltration_par"] = distance
        return prepared

    async def _live_distance(
        self, seed_id: int, target_ids: list[int], hop_limit: int
    ) -> int | None:
        if self.tmdb is None:
            return None
        deadline = monotonic() + 3.0
        targets = sorted(target_ids)
        random.Random(seed_id).shuffle(targets)
        for target_id in targets[:3]:
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            search = self.solve_bridge(
                seed_id,
                target_id,
                max_depth=hop_limit,
                call_budget=12,
                max_duration_seconds=min(1.0, remaining),
            )
            try:
                async for event in search:
                    if event.get("type") == "result":
                        hops = event.get("hops")
                        if isinstance(hops, int) and 2 <= hops <= hop_limit:
                            return hops
                    if event.get("type") in {"exhausted", "timeout", "error"}:
                        break
            finally:
                await search.aclose()
        return None

    def _target_ids(self, rules: dict | None) -> list[int]:
        target_list_id = (rules or {}).get("target_list_id")
        if not target_list_id:
            return []
        return list(
            self.session.exec(
                select(CanonMovieBadge.movie_id).where(
                    CanonMovieBadge.curated_list_id == target_list_id
                )
            ).all()
        )

    def evaluate_run_outcome(self, run: Run, steps: list[RunStep]) -> RunOutcome | None:
        own = super().evaluate_run_outcome(run, steps)
        if own is not None:
            return own
        rules = run.rules_config or {}
        target_ids = set(self._target_ids(rules))
        watched = [step for step in steps if step.status == "watched"]
        if any(step.movie_id in target_ids for step in watched):
            return RunOutcome(RUN_STATUS_COMPLETED, "Canon infiltrated within the hop limit!")
        hop_limit = int(rules.get("hop_limit", DEFAULT_HOP_LIMIT))
        if len(watched) - 1 > hop_limit:
            return RunOutcome(
                RUN_STATUS_FAILED,
                f"Failed: exceeded {hop_limit} hops before reaching the canon list.",
            )
        return None

    async def validate_primary(
        self,
        from_movie_id: int,
        to_movie_id: int,
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
    ):
        result = await super().validate_primary(
            from_movie_id,
            to_movie_id,
            cast_limit=cast_limit,
            rules=rules,
            previous_transition=previous_transition,
        )
        if result.valid and to_movie_id in set(self._target_ids(rules)):
            result.mechanic = {**(result.mechanic or {}), "infiltrated": True}
        return result

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
            frontier_movie_id, mode, cast_limit, rules, previous_transition, history
        )
        target_ids = self._target_ids(rules)
        if not target_ids or (rules or {}).get("fog"):
            return candidates
        distances = goal_graph.distance_to_targets(
            self.session,
            [candidate.movie_id for candidate in candidates],
            target_ids,
            policy=(rules or {}).get("link", "shared_cast"),
            max_depth=int((rules or {}).get("hop_limit", DEFAULT_HOP_LIMIT)),
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
