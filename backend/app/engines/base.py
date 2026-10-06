"""Strategy-pattern interface for pluggable challenge game rules."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from typing import Any, ClassVar, Literal

from sqlmodel import Session

from app.engines import chaos, modifiers
from app.engines.conditions import RunOutcome, evaluate_conditions, validate_conditions
from app.models.cache import CachedMovie
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
from app.services import bounties, cache_repo
from app.services.tmdb import TMDBClient
from app.services.tmdb_backoff import DeadlineReached, fetch_with_backoff

# Modifier checks need each candidate's country/runtime, which search/credit stubs
# lack: hydrate this many per request (most popular first), the rest stay "unverified".
HYDRATE_BUDGET = 30
HYDRATE_SECONDS = 20.0


class RunSetupError(Exception):
    """A run can't be created from the given rules (e.g. an unknown actor, a thin watchlist)."""

    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


class BaseChallengeEngine(ABC):
    """A challenge's game rules, bound to the current DB session and TMDB client."""

    game_type: str
    display_name: str
    description: str
    capabilities: ClassVar[list[str]]
    requires: ClassVar[list[str]] = []
    seed_policy: ClassVar[Literal["none", "free", "derived", "pair"]] = "free"
    # Graph-style engines honour opt-in win/fail conditions in `rules_config`;
    # rigid trackers leave this False and ignore them.
    supports_json_rules: ClassVar[bool] = False
    # Graph-style engines accept the composable pair modifiers (chrono_direction,
    # runtime_staircase, country_cooldown); trackers have no previous film to compare.
    supports_modifiers: ClassVar[bool] = False
    # Modifiers this engine always applies unless the run overrides them (e.g. Chrono Climb
    # is just "any film" + chrono_direction="climb").
    default_modifiers: ClassVar[dict[str, Any]] = {}
    # The engine's own pair rule needs full film detail (country, overview) to be judged.
    needs_detail: ClassVar[bool] = False
    # Survival modes: a forced (rule-breaking) step costs one of `rules_config["lives_remaining"]`
    # instead of a wildcard.
    uses_lives: ClassVar[bool] = False
    # May the Bounty Board (earn wildcards by completing bounties) run on top of this mode? Not when
    # steps are logged outside the normal route or wildcards don't exist.
    supports_bounty_board: ClassVar[bool] = True

    def __init__(self, session: Session, tmdb: TMDBClient) -> None:
        self.session = session
        self.tmdb = tmdb
        # One hydration allowance per engine instance (= per request), shared by the
        # engine's own pool filter and the modifier filter.
        self._hydration_left: int | None = None
        self._hydration_deadline = 0.0

    @classmethod
    def modifier_problems(cls, rules: dict | None) -> list[str]:
        problems = modifiers.modifier_problems(rules)
        if not cls.supports_modifiers:
            problems += [
                f"{key} isn't supported by {cls.display_name}"
                for key in modifiers.modifiers_requested(rules)
            ]
        return problems

    def validate_rules_config(self, rules: dict | None) -> list[str]:
        """Problems with a V2 `rules_config` payload (empty list = valid)."""
        problems = validate_conditions(rules) if self.supports_json_rules else []
        return problems + self.modifier_problems(rules)

    async def seed_candidates(self, rules: dict) -> list[int] | None:
        """Cache-only seed bounds: None means unrestricted, [] means no legal cached seeds."""
        return None

    def active_modifiers(self, rules: dict | None) -> dict[str, Any]:
        """The pair modifiers in force: this engine's defaults overridden by the run's."""
        if not self.supports_modifiers:
            return {}
        return modifiers.merge_modifiers(self.default_modifiers, rules)

    def modifier_violation(
        self,
        earlier: CachedMovie,
        later: CachedMovie,
        rules: dict | None,
        history: Sequence[RunStep] | None = None,
    ) -> str | None:
        return modifiers.pair_modifier_violation(
            self.active_modifiers(rules), earlier, later, history
        ) or chaos.violation(self.session, later, rules)

    def cooldown_countries(
        self,
        rules: dict | None,
        history: Sequence[RunStep] | None,
        earlier: CachedMovie | None = None,
    ) -> list[str]:
        return modifiers.cooldown_countries(self.active_modifiers(rules), history, earlier)

    def _modifiers_need_detail(self, row: CachedMovie, rules: dict | None) -> bool:
        active = self.active_modifiers(rules)
        if chaos.needs_detail(row, rules):
            return True
        if active.get(modifiers.COOLDOWN_KEY) and row.origin_country is None:
            return True
        return bool(active.get(modifiers.STAIRCASE_KEY)) and row.runtime is None

    def _needs_hydration(self, row: CachedMovie, rules: dict | None = None) -> bool:
        """Does this film still need its full TMDB detail before it can be judged?"""
        if self.needs_detail and row.origin_country is None:
            return True
        return self._modifiers_need_detail(row, rules)

    async def _load(
        self, movie_id: int, hydrate: bool = False, rules: dict | None = None
    ) -> CachedMovie:
        row = self.session.get(CachedMovie, movie_id)
        if row is None:
            return await cache_repo.get_movie(self.session, self.tmdb, movie_id)
        if hydrate and self._needs_hydration(row, rules):
            return await cache_repo.get_movie(self.session, self.tmdb, movie_id, refresh=True)
        return row

    async def _hydrate_pool(
        self, candidates: list[DiscoveryCandidate], rules: dict | None = None
    ) -> dict[int, CachedMovie]:
        """Cached rows for the pool, fetching full detail for the most popular films
        that need it (bounded by HYDRATE_BUDGET / HYDRATE_SECONDS)."""
        if self._hydration_left is None:
            self._hydration_left = HYDRATE_BUDGET
            self._hydration_deadline = time.monotonic() + HYDRATE_SECONDS
        rows: dict[int, CachedMovie] = {}
        for candidate in sorted(candidates, key=lambda c: -(c.popularity or 0.0)):
            row = self.session.get(CachedMovie, candidate.movie_id)
            if row is None:
                continue
            if self._needs_hydration(row, rules) and self._hydration_left > 0:
                self._hydration_left -= 1
                try:
                    fetched = await fetch_with_backoff(
                        lambda movie_id=candidate.movie_id: cache_repo.get_movie(
                            self.session, self.tmdb, movie_id, refresh=True
                        ),
                        self._hydration_deadline,
                    )
                    row = fetched or row
                except DeadlineReached:
                    self._hydration_left = 0
                except Exception:  # noqa: BLE001 - leave this film unverified
                    self.session.rollback()
                candidate.origin_country = row.origin_country
            rows[candidate.movie_id] = row
        return rows

    async def filter_by_modifiers(
        self,
        frontier_movie_id: int,
        candidates: list[DiscoveryCandidate],
        rules: dict | None,
        history: Sequence[RunStep] | None = None,
    ) -> list[DiscoveryCandidate]:
        """Drop pool films that break an active modifier, so the UI never offers a pick
        it would reject. Films whose detail couldn't be fetched stay (flagged unverified)."""
        if (not self.active_modifiers(rules) and chaos.active(rules) is None) or not candidates:
            return candidates
        frontier = await self._load(frontier_movie_id, hydrate=True, rules=rules)
        rows = await self._hydrate_pool(candidates, rules)
        kept: list[DiscoveryCandidate] = []
        for candidate in candidates:
            row = rows.get(candidate.movie_id)
            if row is None:
                continue
            if self.modifier_violation(frontier, row, rules, history):
                continue
            if self._modifiers_need_detail(row, rules):
                candidate.constraint_unverified = True
            kept.append(candidate)
        return kept

    async def discover_with_modifiers(
        self,
        frontier_movie_id: int,
        mode: str = "or",
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
        history: Sequence[RunStep] | None = None,
    ) -> list[DiscoveryCandidate]:
        """The primary candidate generator's pool, narrowed by the run's active modifiers."""
        pool = await self.discover_candidates(
            frontier_movie_id, mode, cast_limit, rules, previous_transition, history
        )
        return await self.filter_by_modifiers(frontier_movie_id, pool, rules, history)

    async def describe_run_constraint(
        self,
        tail_movie_id: int | None,
        previous_transition: dict | None,
        rules: dict | None,
        history: Sequence[RunStep] | None = None,
    ) -> ConstraintInfo | None:
        """`describe_constraint` plus the active modifiers (cooldown chips, notes)."""
        info = await self.describe_constraint(tail_movie_id, previous_transition, rules)
        active = self.active_modifiers(rules)
        if not active:
            return info
        tail = await self._load(tail_movie_id) if tail_movie_id is not None else None
        locked = modifiers.cooldown_countries(active, history, tail)
        shown = {key: value for key, value in active.items() if key not in self.default_modifiers}
        notes = modifiers.modifier_notes(shown, tail)
        if not locked and not notes:
            return info
        if info is None:
            info = ConstraintInfo(
                kind="country" if locked else "free",
                title="Recently visited countries are locked out"
                if locked
                else "Run modifiers shape the next film",
            )
        return info.model_copy(update={"cooldown_countries": locked, "modifier_notes": notes})

    def prepare_rules_config(self, rules: dict) -> dict:
        """The `rules_config` a new run is stored with (engines fill in their own defaults)."""
        return rules

    async def prepare_run(self, rules: dict, user_id: str) -> dict:
        """Async, I/O-capable counterpart of `prepare_rules_config`, run once when a run is created
        (e.g. fetching an actor's filmography). Raises `RunSetupError` when the run can't start."""
        return rules

    def award_bounty(
        self,
        rules: dict,
        completed_id: str,
        replacement_id: str | None,
        custom: dict | None = None,
    ) -> dict:
        """Apply a completed Bounty Board reward (wildcard by default)."""
        return bounties.award(rules, completed_id, replacement_id, custom)

    def sync_run_state(self, run: Run, steps: Sequence[RunStep]) -> None:
        """Refresh any state the engine derives from the steps and caches on the run (e.g. Tug of
        War's scores). Runs after every step change; the caller commits."""

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

    @classmethod
    def forfeit_outcome(cls, run: Run, steps: Sequence[RunStep]) -> RunOutcome | None:
        """The terminal outcome when a participant gives up, if the engine wants to override the
        plain "forfeited" (e.g. the Rabbit Hole counts a surrender at zero lives as a defeat)."""
        return None

    async def validate_candidate(self, movie_id: int, rules: dict) -> ValidationResult:
        """Run-scoped rules about the film *itself* (e.g. canon-only, decade
        sieve), checked for every step including the first, which has no
        previous film to link from. Violations are returned `blocked`."""
        return ValidationResult(valid=True)

    @abstractmethod
    async def validate_primary(
        self,
        from_movie_id: int,
        to_movie_id: int,
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
    ) -> ValidationResult:
        """The engine's own rule for a hop (its primary candidate generator's link: shared
        cast, a different country, a plot match...), *without* the run's modifiers."""

    async def validate_next_step(
        self,
        from_movie_id: int,
        to_movie_id: int,
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
        history: Sequence[RunStep] | None = None,
    ) -> ValidationResult:
        """Is `to_movie_id` a legal next film after `from_movie_id`?

        A composable pipeline: the engine's primary rule (`validate_primary`) first, then every
        active modifier (`rules_config`, see `app.engines.modifiers`). A modifier violation is
        always a hard block. `rules` is the run's `rules_config` (None skips run-scoped rules),
        `previous_transition` the previous step's `transition_metadata` and `history` the
        run's steps logged so far, oldest first (needed by `country_cooldown`).
        """
        result = await self.validate_primary(
            from_movie_id,
            to_movie_id,
            cast_limit=cast_limit,
            rules=rules,
            previous_transition=previous_transition,
        )
        active = self.active_modifiers(rules)
        if result.blocked or (not active and chaos.active(rules) is None):
            return result
        earlier = await self._load(from_movie_id, hydrate=True, rules=rules)
        later = await self._load(to_movie_id, hydrate=True, rules=rules)
        reason = self.modifier_violation(earlier, later, rules, history)
        mechanic = modifiers.modifier_mechanic(active, earlier, later)
        if mechanic:
            result.mechanic = {**(result.mechanic or {}), **mechanic}
        if reason:
            return result.model_copy(update={"valid": False, "blocked": True, "reason": reason})
        return result

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
        self,
        tail_movie_id: int | None,
        previous_transition: dict | None,
        rules: dict | None = None,
    ) -> ConstraintInfo | None:
        """The rule that currently shapes the run's next hop, for the UI. None = unconstrained."""
        return None

    def link_metadata(self, result: ValidationResult, client_metadata: dict | None) -> dict | None:
        """Server-authoritative `transition_metadata` for a step, built from the
        engine's own validation result. None = keep whatever the client sent."""
        if not result.mechanic:
            return None
        return {**(client_metadata or {}), **result.mechanic}

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
        history: Sequence[RunStep] | None = None,
    ) -> list[DiscoveryCandidate]:
        """Unified cast-aggregation "Pick Next" pool (Phase 13) - an OPTIONAL
        capability, not part of the required Strategy contract (e.g. a future
        DirectorLadderEngine would aggregate by director, not cast overlap).
        Concrete engines that support it should override this; the route
        layer catches NotImplementedError and reports it as an unsupported
        capability rather than a 500.
        """
        raise NotImplementedError
