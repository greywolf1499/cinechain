"""Strategy-pattern interface for pluggable challenge game rules."""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from typing import Any, ClassVar, Literal

import httpx
from sqlmodel import Session

from app.engines import chaos, modifiers
from app.engines.conditions import RunOutcome, evaluate_conditions, validate_conditions
from app.engines.rulebook import RuleSection
from app.models.cache import CachedMovie
from app.models.run import (
    LEGACY_ENGINE_VERSION,
    RUN_STATUS_ACTIVE,
    Run,
    RunStep,
)
from app.schemas.discovery import DiscoveryCandidate, DiscoveryDiagnostics
from app.schemas.engine import (
    ConstraintInfo,
    FilterSpec,
    Preset,
    RuleField,
    RunStats,
    Suggestion,
    SuggestionFilters,
    ValidationResult,
)
from app.services import bounties, cache_repo, feasibility
from app.services.movie_filters import is_reality_eligible
from app.services.tmdb import TMDBClient, TMDBError
from app.services.tmdb_backoff import DeadlineReached, fetch_with_backoff

# Modifier checks need each candidate's country/runtime, which search/credit stubs
# lack: hydrate this many per request (most popular first), the rest stay "unverified".
HYDRATE_BUDGET = 30
HYDRATE_SECONDS = 20.0
DRY_POOL_MIN = 8
DISCOVERY_SECONDS = 12.0
logger = logging.getLogger(__name__)


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
    rulebook: ClassVar[RuleSection]
    tagline: ClassVar[str]
    tags: ClassVar[list[str]]
    capabilities: ClassVar[list[str]]
    requires: ClassVar[list[str]] = []
    seed_policy: ClassVar[Literal["none", "free", "derived", "pair"]] = "free"
    queue_policy: ClassVar[Literal["frontier", "slot", "none"]] = "frontier"
    discovery_filters: ClassVar[list[FilterSpec]] = []
    rule_fields: ClassVar[list[RuleField]] = [
        RuleField(
            key="allow_repeats",
            kind="enum",
            label="Repeat policy",
            options=["strict", "penalty", "allowed"],
            default="strict",
            group="advanced",
        ),
        RuleField(
            key="min_runtime",
            kind="int",
            label="Minimum runtime (minutes)",
            min=0,
            default=0,
            group="advanced",
        ),
        RuleField(
            key="wildcards_budget",
            kind="int",
            label="Wildcards allowance",
            min=-1,
            default=0,
            help="-1 means unlimited.",
            group="advanced",
        ),
    ]
    presets: ClassVar[list[Preset]] = []
    default_preset: ClassVar[str] = "custom"
    # Graph-style engines honour opt-in win/fail conditions in `rules_config`;
    # rigid trackers leave this False and ignore them.
    supports_json_rules: ClassVar[bool] = False
    # Graph-style engines accept the composable pair modifiers (chrono_direction,
    # runtime_staircase, country_cooldown); trackers have no previous film to compare.
    modifier_scopes: ClassVar[frozenset[str]] = frozenset()
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
    bounty_reward: ClassVar[Literal["wildcard", "life", "hint", "star"]] = "wildcard"

    def __init__(self, session: Session, tmdb: TMDBClient) -> None:
        self.session = session
        self.tmdb = tmdb
        # One hydration allowance per engine instance (= per request), shared by the
        # engine's own pool filter and the modifier filter.
        self._hydration_left: int | None = None
        self._hydration_deadline = 0.0
        self.discovery_diagnostics = DiscoveryDiagnostics()
        self.discovery_reason: str | None = None
        self._discovery_options: dict[str, Any] = {}

    @classmethod
    def rulebook_values(cls, rules: dict | None) -> dict[str, Any]:
        from app.models.run import DEFAULT_RULES_CONFIG

        config = {**DEFAULT_RULES_CONFIG, **(rules or {})}
        wins = config.get("win_condition")
        failures = config.get("fail_condition")

        def conditions(raw: Any, verb: str) -> str:
            if not raw:
                return ""
            entries = raw if isinstance(raw, list) else [raw]
            return "; ".join(
                f"{verb} {entry['count']} {entry['type'].removeprefix('max_').replace('_', ' ')}"
                for entry in entries
            )

        return {
            **config,
            "win_goal": conditions(wins, "reach")
            if cls.supports_json_rules and wins
            else "Keep exploring. Finish the run when you are done.",
            "fail_goal": conditions(failures, "exceed")
            if cls.supports_json_rules and failures
            else "Check this mode's rules for ways to lose.",
            "cast_depth": config.get("max_cast_order") or "all credited actors",
        }

    def coach_line(self, run: Run, steps: Sequence[RunStep]) -> str | None:
        return None

    @classmethod
    def modifier_problems(cls, rules: dict | None) -> list[str]:
        problems = modifiers.modifier_problems(rules)
        from app.engines.modifier_registry import registry

        for key in modifiers.modifiers_requested(rules):
            spec = registry().get(key)
            if spec and (reason := spec.compatible(cls)):
                problems.append(reason)
        return problems

    def validate_rules_config(self, rules: dict | None) -> list[str]:
        """Problems with a V2 `rules_config` payload (empty list = valid)."""
        problems = self.rule_field_problems(rules)
        problems += validate_conditions(rules) if self.supports_json_rules else []
        return problems + self.modifier_problems(rules)

    @classmethod
    def rule_field_problems(cls, rules: dict | None) -> list[str]:
        problems: list[str] = []
        rules = rules or {}
        for field in cls.rule_fields:
            if field.key not in rules:
                continue
            value = rules[field.key]
            if field.kind == "int":
                if type(value) is not int:
                    problems.append(f"{field.key} must be a whole number")
                elif (
                    field.min is not None
                    and value < field.min
                    or field.max is not None
                    and value > field.max
                ):
                    problems.append(f"{field.key} is outside its allowed range")
            elif field.kind == "bool":
                if type(value) is not bool:
                    problems.append(f"{field.key} must be a boolean")
            elif not isinstance(value, str) or value not in field.options:
                problems.append(f"{field.key} must be one of: {', '.join(field.options)}")
        return problems

    async def seed_candidates(self, rules: dict) -> list[int] | None:
        """Cache-only seed bounds: None means unrestricted, [] means no legal cached seeds."""
        return None

    def active_modifiers(self, rules: dict | None) -> dict[str, Any]:
        """The pair modifiers in force: this engine's defaults overridden by the run's."""
        if not self.modifier_scopes:
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
        from app.engines.modifier_registry import contexts

        return any(
            getattr(row, field) is None for spec, _ in contexts(active) for field in spec.needs
        )

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
        self,
        candidates: list[DiscoveryCandidate],
        rules: dict | None = None,
        *,
        needs: frozenset[str] = frozenset(),
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
            missing = self._needs_hydration(row, rules) or any(
                getattr(row, field) is None for field in needs
            )
            if missing and self._hydration_left > 0:
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
                    self.discovery_reason = (
                        "Some film details are missing. Prepare this run before searching again."
                    )
                except (TMDBError, httpx.HTTPError) as exc:
                    logger.warning(
                        "Candidate %s detail hydration failed: %s", candidate.movie_id, exc
                    )
                    self.discovery_reason = (
                        "Some film details could not be fetched. Prepare this run or try again."
                    )
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
            from app.engines.modifier_registry import contexts

            verdicts = [
                (spec, spec.check(ctx, row))
                for spec, ctx in contexts(self.active_modifiers(rules), history, frontier)
            ]
            candidate.overlay_ok = {
                spec.key: verdict.ok
                for spec, verdict in verdicts
                if getattr(spec, "overlay", False)
            }
            if any(
                verdict.ok is False for spec, verdict in verdicts if spec.scope != "sequence"
            ) or chaos.violation(self.session, row, rules):
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
        *,
        wider: bool = False,
        **options: Any,
    ) -> list[DiscoveryCandidate]:
        """One bounded ladder; every extra candidate still passes the original rules."""
        from app.engines.mutators import POOL_FETCH_ERRORS

        self.discovery_diagnostics = DiscoveryDiagnostics()
        self.discovery_reason = None
        self._discovery_options = options
        self._discovery_mode = mode
        self._discovery_cast_limit = cast_limit
        self._discovery_previous = previous_transition
        deadline = time.monotonic() + DISCOVERY_SECONDS
        if self._hydration_left is None:
            self._hydration_left = HYDRATE_BUDGET
            self._hydration_deadline = deadline
        else:
            self._hydration_deadline = min(self._hydration_deadline, deadline)
        raw: dict[int, DiscoveryCandidate] = {}
        kept: list[DiscoveryCandidate] = []
        try:
            async with asyncio.timeout(DISCOVERY_SECONDS):
                try:
                    initial = await self.discover_candidates(
                        frontier_movie_id,
                        mode,
                        cast_limit,
                        rules,
                        previous_transition,
                        history,
                        **options,
                    )
                except POOL_FETCH_ERRORS as exc:
                    logger.warning("Discovery failed for %s: %s", self.game_type, exc)
                    self.discovery_reason = (
                        "The movie provider is unavailable. Prepare this run or try again."
                    )
                    initial = []
                raw.update((candidate.movie_id, candidate) for candidate in initial)
                self.discovery_diagnostics.engine_pool = len(raw)
                kept = await self.filter_by_modifiers(
                    frontier_movie_id, list(raw.values()), rules, history
                )
                self.discovery_diagnostics.after_modifiers = len(kept)
                if len(kept) < DRY_POOL_MIN or wider:
                    for rung in (1, 2, 3):
                        if len(kept) >= DRY_POOL_MIN and not wider:
                            break
                        try:
                            extra = await self.widen_pool(frontier_movie_id, rules, history, rung)
                        except POOL_FETCH_ERRORS as exc:
                            logger.warning(
                                "Discovery widening failed for %s: %s", self.game_type, exc
                            )
                            self.discovery_reason = (
                                "The movie provider is unavailable. Prepare this run or try again."
                            )
                            continue
                        added = [candidate for candidate in extra if candidate.movie_id not in raw]
                        raw.update((candidate.movie_id, candidate) for candidate in added)
                        self.discovery_diagnostics.engine_pool = len(raw)
                        if added:
                            self.discovery_diagnostics.widened = True
                            accepted = await self.filter_by_modifiers(
                                frontier_movie_id, added, rules, history
                            )
                            kept.extend(accepted)
                            self.discovery_diagnostics.after_modifiers = len(kept)
        except TimeoutError:
            logger.info("Discovery deadline reached for %s", self.game_type)
            self.discovery_reason = (
                "Search reached its 12-second limit. Prepare this run or search wider."
            )
        self.discovery_diagnostics.after_filters = len(kept)
        self.discovery_diagnostics.reason = self.discovery_reason or (
            "The engine found no films after widening. Prepare this run or choose another frontier."
            if not raw
            else "The run's modifiers hid every film. Review the active rules."
            if not kept
            else None
        )
        return kept

    async def widen_pool(
        self, frontier: int, rules: dict | None, history: Sequence[RunStep] | None, rung: int
    ) -> list[DiscoveryCandidate]:
        """Engine-specific extra sources, then a cache-only facet rung. Never relax rules."""
        return []

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
        from app.engines.modifier_registry import contexts

        progress = [p for spec, ctx in contexts(shown, history, tail) if (p := spec.progress(ctx))]
        notes = [p["label"] for p in progress if "key" not in p]
        overlays = [p for p in progress if "key" in p]
        if not locked and not notes and not overlays:
            return info
        if info is None:
            info = ConstraintInfo(
                kind="country" if locked else "free",
                title="Recently visited countries are locked out"
                if locked
                else "Run modifiers shape the next film",
            )
        return info.model_copy(
            update={
                "cooldown_countries": locked,
                "modifier_notes": notes,
                "overlay_progress": overlays,
            }
        )

    def overlay_checks(
        self, movie: CachedMovie, rules: dict, history: Sequence[RunStep]
    ) -> dict[str, Any]:
        from app.engines.modifier_registry import contexts

        return {
            spec.key: spec.check(ctx, movie)
            for spec, ctx in contexts(self.active_modifiers(rules), history)
            if getattr(spec, "overlay", False)
        }

    def overlay_outcome(self, run: Run, steps: Sequence[RunStep]) -> RunOutcome | None:
        from app.engines.modifier_registry import contexts

        if run.engine_version <= LEGACY_ENGINE_VERSION or run.status != RUN_STATUS_ACTIVE:
            return None
        for spec, ctx in contexts(self.active_modifiers(run.rules_config), steps):
            if outcome := spec.outcome(ctx):
                return outcome
        return None

    def prepare_rules_config(self, rules: dict) -> dict:
        """The `rules_config` a new run is stored with (engines fill in their own defaults)."""
        return rules

    async def prepare_run(self, rules: dict, user_id: str) -> dict:
        """Async, I/O-capable counterpart of `prepare_rules_config`, run once when a run is created
        (e.g. fetching an actor's filmography). Raises `RunSetupError` when the run can't start."""
        return rules

    def prepare_overlays(self, rules: dict, history: Sequence[RunStep] = ()) -> dict:
        from app.engines.modifier_registry import contexts, matching_rows

        overlays = [
            (spec, ctx)
            for spec, ctx in contexts(self.active_modifiers(rules), history)
            if getattr(spec, "overlay", False)
        ]
        if not overlays:
            return rules
        track = rules.get("filmography")
        expedition = rules.get("expedition")
        films = track if track is not None else (expedition or {}).get("films")
        exact = films is not None
        if exact:
            rows = [CachedMovie(tmdb_id=film["movie_id"], title=film["title"]) for film in films]
        else:
            cached = feasibility.movies(self.session)
            bounds = self.bounty_bounds(rules, [])
            rows = [
                row
                for row in cached.values()
                if feasibility.within_movie(row, bounds) and is_reality_eligible(row)
            ]
        for spec, ctx in overlays:
            if spec.scope == "film":
                kept = matching_rows(self.session, spec.query(ctx), rows)
                rows = [row for row in rows if row.tmdb_id in kept]
        if not exact and cached and not rows:
            raise RunSetupError("Title overlays have no qualifying films in the cached mode pool")
        for spec, ctx in overlays:
            if spec.key == "number_in_title":
                if track is not None and len(rows) < 3:
                    raise RunSetupError(
                        "Number in title needs at least 3 qualifying checklist films"
                    )
                if exact and not rows:
                    raise RunSetupError("Number in title has no qualifying checklist films")
            elif spec.scope == "film":
                if exact and not rows:
                    raise RunSetupError(f"{spec.label} has no eligible checklist films")
            elif rows:
                required = spec.requirements(ctx)
                if not exact:
                    ids = [row.tmdb_id for row in rows]
                    missing = [
                        token
                        for token in required
                        if feasibility.pass_rate(self.session, spec.coverage(ctx, token), ids) == 0
                    ]
                    if missing:
                        raise RunSetupError(
                            f"{spec.label} has zero pass-rate in the cached mode pool for: {', '.join(map(str, missing[:12]))}"
                        )
                    continue
                choices: dict[Any, list[int]] = {}
                for token in required:
                    kept = matching_rows(self.session, spec.query(ctx, token), rows)
                    choices[token] = [row.tmdb_id for row in rows if row.tmdb_id in kept]
                # Match distinct films to requirements: a single numbered/digit-leading title
                # cannot prove that a repeat-strict checklist covers the entire sequence.
                assigned: dict[int, Any] = {}
                occupied: dict[Any, int] = {}
                missing = []
                for token in required:
                    queue = [token]
                    parents = {}
                    free = None
                    while queue and free is None:
                        current = queue.pop()
                        for movie_id in choices[current]:
                            if movie_id in parents:
                                continue
                            parents[movie_id] = current
                            if movie_id not in assigned:
                                free = movie_id
                                break
                            queue.append(assigned[movie_id])
                    if free is None:
                        missing.append(token)
                    else:
                        while free is not None:
                            current = parents[free]
                            old = occupied.get(current)
                            assigned[free] = current
                            occupied[current] = free
                            free = old
                if missing:
                    raise RunSetupError(
                        f"{spec.label} is infeasible in the {'checklist' if exact else 'cached mode pool'}: missing {', '.join(map(str, missing[:12]))}"
                    )
            elif exact:
                raise RunSetupError(f"{spec.label} has no eligible checklist films")
        if exact and any(spec.key == "number_in_title" for spec, _ in overlays):
            ids = {row.tmdb_id for row in rows}
            if track is not None:
                rules = {
                    **rules,
                    "filmography": [film for film in track if film["movie_id"] in ids],
                }
            else:
                assert expedition is not None
                rules = {
                    **rules,
                    "expedition": {
                        **expedition,
                        "films": [film for film in films if film["movie_id"] in ids],
                        "movie_ids": [
                            film["movie_id"] for film in films if film["movie_id"] in ids
                        ],
                    },
                }
        return rules

    def award_bounty(
        self,
        rules: dict,
        completed_id: str,
        replacement_id: str | None,
        custom: dict | None = None,
    ) -> dict:
        """Apply a completed Bounty Board reward (wildcard by default)."""
        updated = bounties.award(rules, completed_id, replacement_id, custom)
        if self.bounty_reward in ("hint", "star"):
            key = "tunnel_hints_remaining" if self.bounty_reward == "hint" else "bounty_stars"
            updated["wildcards_budget"] = rules.get("wildcards_budget", 0)
            updated[key] = rules.get(key, 2 if self.bounty_reward == "hint" else 0) + 1
        return updated

    def revoke_bounty(self, rules: dict, completed_id: str, replacement_id: str | None) -> dict:
        updated = bounties.revoke(rules, completed_id, replacement_id)
        if self.bounty_reward != "wildcard":
            updated["wildcards_budget"] = rules.get("wildcards_budget", 0)
        if self.bounty_reward in ("hint", "star"):
            key = "tunnel_hints_remaining" if self.bounty_reward == "hint" else "bounty_stars"
            updated[key] = max(0, rules.get(key, 0) - 1)
        return updated

    def bounty_ids(self, rules: dict, history: Sequence[RunStep]) -> list[int] | None:
        """None means an open universe; a list means an exact finite track."""
        return None

    def bounty_pool_allowed(
        self, movie: CachedMovie, rules: dict, history: Sequence[RunStep]
    ) -> bool:
        if any(
            verdict.ok is False for verdict in self.overlay_checks(movie, rules, history).values()
        ):
            return False
        tail = self.session.get(CachedMovie, history[-1].movie_id) if history else None
        return tail is None or self.modifier_violation(tail, movie, rules, history) is None

    def bounty_bounds(
        self,
        rules: dict,
        history: Sequence[RunStep],
    ) -> dict[str, tuple[float | None, float | None]]:
        bounds: dict[str, tuple[float | None, float | None]] = {
            "runtime": (rules.get("min_runtime", 0), None)
        }
        if history:
            tail = self.session.get(CachedMovie, history[-1].movie_id)
            active = self.active_modifiers(rules)
            from app.utils.dates import parse_release_year

            year = parse_release_year(tail.release_date) if tail else None
            if year is not None and active.get("chrono_direction"):
                bounds["year"] = (
                    (year + 1, None) if active["chrono_direction"] == "climb" else (None, year - 1)
                )
            if tail and tail.runtime is not None and active.get("runtime_staircase"):
                bounds["runtime"] = (
                    (max(rules.get("min_runtime", 0), tail.runtime + 1), None)
                    if active["runtime_staircase"] == "ascending"
                    else (rules.get("min_runtime", 0), tail.runtime - 1)
                )
        return bounds

    def bounty_feasible(
        self, rules: dict, history: Sequence[RunStep], bounty: bounties.Bounty
    ) -> feasibility.Feasibility:
        test = bounty.predicate
        if test is None:
            return feasibility.Feasibility(False, "Quest has no supported predicate", None)
        bounds = self.bounty_bounds(rules, history)
        if feasibility.contradicts(test, bounds):
            return feasibility.Feasibility(
                False, "Quest conflicts with the mode's current bounds", 0
            )
        return feasibility.check(
            self.session,
            test,
            self.bounty_universe(rules, history),
            exact=self.bounty_ids(rules, history) is not None,
        )

    def bounty_universe(self, rules: dict, history: Sequence[RunStep]) -> list[int]:
        bounds = self.bounty_bounds(rules, history)
        movies = feasibility.movies(self.session)
        exact_ids = self.bounty_ids(rules, history)
        watched = {step.movie_id for step in history}
        exclude_watched = exact_ids is not None or rules.get("allow_repeats", "strict") != "allowed"
        ids = [
            movie_id
            for movie_id in (exact_ids if exact_ids is not None else movies)
            if (not exclude_watched or movie_id not in watched)
            and (
                movie_id not in movies
                or (
                    feasibility.within_movie(movies[movie_id], bounds)
                    and (not movies[movie_id].release_date or is_reality_eligible(movies[movie_id]))
                    and self.bounty_pool_allowed(movies[movie_id], rules, history)
                )
            )
        ]
        return ids

    def bounty_difficulty(
        self, rules: dict, history: Sequence[RunStep], bounty: bounties.Bounty
    ) -> int:
        if bounty.predicate is None:
            return 1
        return feasibility.measured_difficulty(
            self.session, bounty.predicate, self.bounty_universe(rules, history)
        )

    def sync_run_state(self, run: Run, steps: Sequence[RunStep]) -> None:
        """Refresh any state the engine derives from the steps and caches on the run (e.g. Tug of
        War's scores). Runs after every step change; the caller commits."""

    def evaluate_run_outcome(self, run: Run, steps: list[RunStep]) -> RunOutcome | None:
        """Win/loss check, run after a step is logged. None = keep playing.

        Legacy (engine_version 1) runs never evaluate: the strict V2 rules must
        not retroactively end a run that was started under the old ones.
        """
        if run.engine_version <= LEGACY_ENGINE_VERSION or run.status != RUN_STATUS_ACTIVE:
            return None
        own = evaluate_conditions(run.rules_config, steps) if self.supports_json_rules else None
        return own or self.overlay_outcome(run, steps)

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
        history: Sequence[RunStep] | None = None,
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

    def annotate_candidates(
        self,
        candidates: list[DiscoveryCandidate],
        rules: dict | None = None,
        history: Sequence[RunStep] | None = None,
    ) -> list[DiscoveryCandidate]:
        """Last pass over the pool the player will actually see, after every hydration and
        pool-shaping step. Engines stamp render-time verdicts here so a card can never show a
        fact and "unverified" at the same time."""
        facets = [
            spec.facet for spec in self.discovery_filters if spec.source == "facet" and spec.facet
        ]
        if facets:
            from app.facets.query import values_for
            from app.facets.store import refresh

            ids = [candidate.movie_id for candidate in candidates]
            refresh(self.session, ids)
            values = values_for(self.session, ids, facets)
            for candidate in candidates:
                candidate.facet_values = values.get(candidate.movie_id, {})
        return candidates
