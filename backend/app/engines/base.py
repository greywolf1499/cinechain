"""Strategy-pattern interface for pluggable challenge game rules."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import ClassVar

from sqlmodel import Session

from app.engines.conditions import RunOutcome, evaluate_conditions, validate_conditions
from app.models.run import (
    LEGACY_ENGINE_VERSION,
    RUN_STATUS_ACTIVE,
    Run,
    RunStep,
)
from app.schemas.discovery import DiscoveryCandidate
from app.schemas.engine import (
    ConstraintInfo,
    RunStats,
    Suggestion,
    SuggestionFilters,
    ValidationResult,
)
from app.services.tmdb import TMDBClient


class BaseChallengeEngine(ABC):
    """A challenge's game rules, bound to the current DB session and TMDB client."""

    game_type: str
    display_name: str
    description: str
    capabilities: ClassVar[list[str]]
    # Graph-style engines honour opt-in win/fail conditions in `rules_config`;
    # rigid trackers leave this False and ignore them.
    supports_json_rules: ClassVar[bool] = False

    def __init__(self, session: Session, tmdb: TMDBClient) -> None:
        self.session = session
        self.tmdb = tmdb

    def validate_rules_config(self, rules: dict | None) -> list[str]:
        """Problems with a V2 `rules_config` payload (empty list = valid)."""
        return validate_conditions(rules) if self.supports_json_rules else []

    def evaluate_run_outcome(self, run: Run, steps: list[RunStep]) -> RunOutcome | None:
        """Win/loss check, run after a step is logged. None = keep playing.

        Legacy (engine_version 1) runs never evaluate: the strict V2 rules must
        not retroactively end a run that was started under the old ones.
        """
        if (
            not self.supports_json_rules
            or run.engine_version <= LEGACY_ENGINE_VERSION
            or run.status != RUN_STATUS_ACTIVE
        ):
            return None
        return evaluate_conditions(run.rules_config, steps)

    async def validate_candidate(self, movie_id: int, rules: dict) -> ValidationResult:
        """Run-scoped rules about the film *itself* (e.g. canon-only, decade
        sieve), checked for every step including the first, which has no
        previous film to link from. Violations are returned `blocked`."""
        return ValidationResult(valid=True)

    @abstractmethod
    async def validate_next_step(self, from_movie_id: int, to_movie_id: int) -> ValidationResult:
        """Is `to_movie_id` a legal next film after `from_movie_id`?

        Concrete engines also accept `cast_limit`, `rules` (the run's
        `rules_config`) and `previous_transition` (the previous step's
        `transition_metadata`) keyword arguments; with `rules=None` the
        run-scoped candidate rules are skipped.
        """

    @abstractmethod
    async def get_suggestions(
        self,
        current_movie_id: int,
        exclude_movie_ids: list[int],
        filters: SuggestionFilters,
        rules: dict | None = None,
    ) -> list[Suggestion]:
        """Candidate next films reachable from `current_movie_id`."""

    async def describe_constraint(
        self, tail_movie_id: int | None, previous_transition: dict | None,
        rules: dict | None = None,
    ) -> ConstraintInfo | None:
        """The rule that currently shapes the run's next hop, for the UI. None = unconstrained."""
        return None

    def link_metadata(
        self, result: ValidationResult, client_metadata: dict | None
    ) -> dict | None:
        """Server-authoritative `transition_metadata` for a step, built from the
        engine's own validation result. None = keep whatever the client sent."""
        return None

    @abstractmethod
    async def compute_stats(self, steps: list[RunStep]) -> RunStats:
        """Passport-style stats (countries/decades/keystone actors) for a run."""

    @abstractmethod
    def solve_bridge(
        self, from_movie_id: int, to_movie_id: int, max_depth: int
    ) -> AsyncIterator[dict]:
        """Bidirectional-BFS bridge solve as a stream of events.

        Event shape (progress/partial/result/error/done) is finalized in
        Phase 6 - this signature only reserves the contract.
        """

    async def discover_candidates(
        self,
        frontier_movie_id: int,
        mode: str = "or",
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
    ) -> list[DiscoveryCandidate]:
        """Unified cast-aggregation "Pick Next" pool (Phase 13) - an OPTIONAL
        capability, not part of the required Strategy contract (e.g. a future
        DirectorLadderEngine would aggregate by director, not cast overlap).
        Concrete engines that support it should override this; the route
        layer catches NotImplementedError and reports it as an unsupported
        capability rather than a 500.
        """
        raise NotImplementedError
