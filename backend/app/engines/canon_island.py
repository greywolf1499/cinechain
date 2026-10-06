"""Canon-Only Island: a purist CineChain where every film must be in one curated list."""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

from sqlmodel import col, select

from app.engines.cinechain import CineChainEngine
from app.engines.rulebook import RuleSection
from app.models.curated import CanonMovieBadge, CuratedList
from app.models.run import RunStep
from app.schemas.discovery import DiscoveryCandidate
from app.schemas.engine import Suggestion, SuggestionFilters, ValidationResult

ALLOWED_LIST_KEY = "allowed_curated_list_id"
_SQLITE_CHUNK = 500


class CanonIslandEngine(CineChainEngine):
    tagline = "Stay on the canon"
    tags: ClassVar[list[str]] = ["Shared cast", "One curated list"]
    rulebook: ClassVar[RuleSection] = RuleSection(
        "Explore a shared-cast chain entirely inside your chosen canon list.",
        ["Pick a list member linked to the current film; even the seed must belong to the list."],
        ["{win_goal}"], ["Films outside the list are blocked, even with a wildcard.", "{fail_goal}"],
        ["Look for actors appearing in several list entries before committing to a rare film.",
         "Use the list as your search boundary, not the whole catalogue."], ["seed", "wildcard"],
    )
    """The classic shared-cast chain, but the engine blocks any film that isn't
    on the run's `allowed_curated_list_id` (a `CuratedList` id). Candidate pools
    are pre-filtered to the list so the UI never offers a film it would reject."""

    game_type = "canon_island"
    seed_policy = "derived"
    display_name = "Canon-Only Island"
    description = (
        "Shared-cast chain with a purist twist: every film must belong to one curated canon list."
    )
    # No Bridge Solver: its intermediate films ignore the canon constraint.
    capabilities: ClassVar[list[str]] = [
        cap for cap in CineChainEngine.capabilities if cap not in ("solve_bridge", "bridge_swap")
    ]

    def validate_rules_config(self, rules: dict | None) -> list[str]:
        problems = super().validate_rules_config(rules)
        list_id = (rules or {}).get(ALLOWED_LIST_KEY)
        if not isinstance(list_id, str) or not list_id:
            problems.append(f"{ALLOWED_LIST_KEY} is required (pick a curated list)")
        elif self.session.get(CuratedList, list_id) is None:
            problems.append(f"{ALLOWED_LIST_KEY}: no curated list with id {list_id!r}")
        return problems

    def _allowed_subset(self, list_id: str, movie_ids: list[int]) -> set[int]:
        allowed: set[int] = set()
        for start in range(0, len(movie_ids), _SQLITE_CHUNK):
            chunk = movie_ids[start : start + _SQLITE_CHUNK]
            allowed.update(
                self.session.exec(
                    select(CanonMovieBadge.movie_id).where(
                        CanonMovieBadge.curated_list_id == list_id,
                        col(CanonMovieBadge.movie_id).in_(chunk),
                    )
                ).all()
            )
        return allowed

    async def seed_candidates(self, rules: dict) -> list[int]:
        return list(
            self.session.exec(
                select(CanonMovieBadge.movie_id)
                .where(CanonMovieBadge.curated_list_id == rules[ALLOWED_LIST_KEY])
                .distinct()
            ).all()
        )

    async def validate_candidate(self, movie_id: int, rules: dict) -> ValidationResult:
        list_id = (rules or {}).get(ALLOWED_LIST_KEY)
        curated = self.session.get(CuratedList, list_id) if list_id else None
        if curated is None:
            return ValidationResult(
                valid=False, blocked=True, reason="This run has no valid canon list configured"
            )
        if movie_id in self._allowed_subset(curated.id, [movie_id]):
            return ValidationResult(valid=True)
        return ValidationResult(
            valid=False,
            blocked=True,
            reason=f"Not on the island: this film isn't in {curated.title}",
        )

    async def validate_primary(
        self,
        from_movie_id: int,
        to_movie_id: int,
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
    ) -> ValidationResult:
        if rules is not None:
            blocked = await self.validate_candidate(to_movie_id, rules)
            if not blocked.valid:
                return blocked
        return await super().validate_primary(from_movie_id, to_movie_id, cast_limit=cast_limit)

    async def get_suggestions(
        self,
        current_movie_id: int,
        exclude_movie_ids: list[int],
        filters: SuggestionFilters,
        rules: dict | None = None,
        history: Sequence[RunStep] | None = None,
    ) -> list[Suggestion]:
        suggestions = await super().get_suggestions(
            current_movie_id, exclude_movie_ids, filters, rules, history
        )
        list_id = (rules or {}).get(ALLOWED_LIST_KEY)
        if not list_id:
            return suggestions
        allowed = self._allowed_subset(list_id, [s.movie_id for s in suggestions])
        return [s for s in suggestions if s.movie_id in allowed]

    async def discover_candidates(
        self,
        frontier_movie_id: int,
        mode: str = "or",
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
        history: Sequence[RunStep] | None = None,
    ) -> list[DiscoveryCandidate]:
        candidates = await super().discover_candidates(frontier_movie_id, mode, cast_limit)
        list_id = (rules or {}).get(ALLOWED_LIST_KEY)
        if not list_id:
            return candidates
        allowed = self._allowed_subset(list_id, [c.movie_id for c in candidates])
        return [c for c in candidates if c.movie_id in allowed]
