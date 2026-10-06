"""The Rabbit Hole: a rogue-like survival mode. The deeper the chain, the nastier the rule.

Classic CineChain links (a shared credited actor or director) with a tier rule on top that
escalates with the depth, i.e. the number of films already in the run:

    Tier 1 (depth 0-4)   Freefall          no extra constraint
    Tier 2 (depth 5-9)   The Retro Lock    released before 2000
    Tier 3 (depth 10-14) Tower of Babel    not originally in English
    Tier 4 (depth 15-19) The Micro-Clock   runs under 100 minutes
    Tier 5 (depth 20+)   The B-Movie Abyss rated under 6.0 (IMDb, else TMDB's user score)

Breaking the tier rule or the cast link is a *soft* violation: it is only allowed with `force`,
and a forced step costs one life (`rules_config["lives_remaining"]`, 3 of `max_lives`) instead of
a wildcard - see `_enforce_run_rules`. With no lives left a violation is simply refused; a dead
end (or giving up) at zero lives ends the run as `failed`.

The depth reaches the rules through `validate_next_step` / `discover_candidates` /
`describe_run_constraint` (all given the run's history); engines are built per request, so it is
kept on the instance for the checks the shared pipeline calls.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar

from app.engines.cinechain import CineChainEngine
from app.engines.conditions import RunOutcome
from app.engines.rulebook import RuleSection
from app.models.cache import CachedMovie
from app.models.run import RUN_STATUS_COMPLETED, RUN_STATUS_FAILED, Run, RunStep
from app.schemas.discovery import DiscoveryCandidate
from app.schemas.engine import (
    ConstraintInfo,
    FilterSpec,
    Preset,
    RabbitHoleState,
    RuleField,
    ValidationResult,
)
from app.services.movie_filters import rating_of
from app.utils.dates import parse_release_year

RABBIT_HOLE = "rabbit_hole"
LIVES_KEY = "lives_remaining"
MAX_LIVES_KEY = "max_lives"
DEFAULT_LIVES = 3
MAX_LIVES_LIMIT = 9
RETRO_CUTOFF_YEAR = 2000
MICRO_CLOCK_MINUTES = 100
B_MOVIE_RATING = 6.0
WARNING_WINDOW = 2  # a tier boundary 1 or 2 hops ahead is announced
TIER_OVERRIDE_KEY = "tier_override"
ESCAPE_DEPTH_KEY = "escape_depth"
MIN_ESCAPE_DEPTH = 25
MAX_ESCAPE_DEPTH = 60


@dataclass(frozen=True)
class Tier:
    number: int
    name: str
    rule: str  # short label for badges and warnings
    start_depth: int


TIERS = (
    Tier(1, "Freefall", "No extra constraints", 0),
    Tier(2, "The Retro Lock", "Released before 2000", 5),
    Tier(3, "Tower of Babel", "Non-English", 10),
    Tier(4, "The Micro-Clock", "Under 100 mins", 15),
    Tier(5, "The B-Movie Abyss", "Rated under 6.0", 20),
)


def tier_for_depth(depth: int, rules: dict | None = None) -> Tier:
    """The tier governing the film that would become step `depth + 1`."""
    override = (rules or {}).get(TIER_OVERRIDE_KEY)
    if (
        isinstance(override, dict)
        and override.get("depth") == depth
        and not isinstance(override.get("depth"), bool)
        and isinstance(override.get("tier"), int)
        and not isinstance(override.get("tier"), bool)
        and 2 <= override["tier"] <= len(TIERS)
    ):
        return TIERS[override["tier"] - 1]
    return next(t for t in reversed(TIERS) if depth >= t.start_depth)


def next_tier_of(tier: Tier) -> Tier | None:
    return TIERS[tier.number] if tier.number < len(TIERS) else None


def lives_of(rules: dict | None) -> tuple[int, int]:
    """(lives remaining, max lives) with defaults and clamping for legacy/odd configs."""
    rules = rules or {}
    max_lives = rules.get(MAX_LIVES_KEY)
    if isinstance(max_lives, bool) or not isinstance(max_lives, int) or max_lives < 1:
        max_lives = DEFAULT_LIVES
    remaining = rules.get(LIVES_KEY)
    if isinstance(remaining, bool) or not isinstance(remaining, int):
        remaining = max_lives
    return max(0, min(remaining, max_lives)), max_lives


def tier_state(depth: int, rules: dict | None) -> RabbitHoleState:
    tier = tier_for_depth(depth, rules)
    scheduled_tier = tier_for_depth(depth)
    upcoming = next_tier_of(tier)
    if tier.number != scheduled_tier.number:
        upcoming = next_tier_of(scheduled_tier)
    remaining, max_lives = lives_of(rules)
    state = RabbitHoleState(
        depth=depth,
        tier=tier.number,
        tier_name=tier.name,
        tier_rule=tier.rule,
        lives_remaining=remaining,
        max_lives=max_lives,
        tier_override=tier.number if tier.number != scheduled_tier.number else None,
    )
    if upcoming is not None:
        away = upcoming.start_depth - depth
        state.next_tier, state.next_tier_name = upcoming.number, upcoming.name
        state.next_tier_rule, state.steps_until_next = upcoming.rule, away
        if away <= WARNING_WINDOW:
            when = "on the next hop" if away == 1 else f"in {away} hops"
            state.upcoming_tier_warning = (
                f"⚠️ Warning: Tier {upcoming.number} ({upcoming.rule}) begins {when}!"
            )
    return state


def compliance(session, tier: Tier, row: CachedMovie) -> bool | None:
    """Does `row` satisfy the tier's rule? None = the film's data can't tell (yet)."""
    if tier.number == 1:
        return True
    if tier.number == 2:
        year = parse_release_year(row.release_date)
        return None if year is None else year < RETRO_CUTOFF_YEAR
    if tier.number == 3:
        return None if not row.original_language else row.original_language != "en"
    if tier.number == 4:
        return None if row.runtime is None else row.runtime < MICRO_CLOCK_MINUTES
    rating = rating_of(session, row)
    return None if rating is None else rating < B_MOVIE_RATING


def violation_reason(session, tier: Tier, row: CachedMovie) -> str:
    prefix = f"Tier {tier.number} ({tier.name}): "
    if tier.number == 2:
        return f"{prefix}{row.title} was released in {parse_release_year(row.release_date)} - it must be before {RETRO_CUTOFF_YEAR}"
    if tier.number == 3:
        return f"{prefix}{row.title} is an English-language film - it must be non-English"
    if tier.number == 4:
        return (
            f"{prefix}{row.title} runs {row.runtime} min - it must be under {MICRO_CLOCK_MINUTES}"
        )
    rating = rating_of(session, row)
    return f"{prefix}{row.title} is rated {rating:.1f} - it must be under {B_MOVIE_RATING:.1f}"


class RabbitHoleEngine(CineChainEngine):
    rule_fields: ClassVar[list[RuleField]] = [
        *(field for field in CineChainEngine.rule_fields if field.key != "wildcards_budget"),
        RuleField(key="max_lives", kind="int", label="Starting lives",
                  min=1, max=MAX_LIVES_LIMIT, default=3),
        RuleField(key="allow_reroll", kind="bool", label="Allow tier re-rolls", default=True),
    ]
    presets: ClassVar[list[Preset]] = [
        Preset(id="tourist", label="Tourist", blurb="Five lives and tier re-rolls.",
               values={"max_lives": 5, "allow_reroll": True}),
        Preset(id="spelunker", label="Spelunker", blurb="Three lives for a balanced descent.",
               values={"max_lives": 3, "allow_reroll": True}),
        Preset(id="ironman", label="Ironman", blurb="One life. No tier re-rolls.",
               values={"max_lives": 1, "allow_reroll": False}),
    ]
    default_preset = "spelunker"
    discovery_filters: ClassVar[list[FilterSpec]] = [
        FilterSpec(key="include_off_tier", kind="toggle", label="Include off-tier films",
                   source="tier_compliant", default=False, server_param="include_off_tier",
                   help="Off-tier films cost one life when logged. Other rules still apply."),
    ]
    tagline = "Descend. Survive. Don't blink."
    tags: ClassVar[list[str]] = ["Shared cast", "3 lives", "Rogue-like"]
    rulebook: ClassVar[RuleSection] = RuleSection(
        "Survive deeper into the chain; {escape_goal}",
        ["Link a film and satisfy the current tier; scheduled restrictions are {tier_schedule}.",
         "Forcing a soft link or tier violation costs one life; inspect the HUD for any active tier re-roll."],
        ["Start with {max_lives} lives; {lives_remaining} remain. Legal moves cost no life.", "{win_goal}"],
        ["At zero lives, a dead end or surrender ends the run as failed.", "{fail_goal}"],
        ["Your current pick should leave a filmography suited to the next tier.",
         "Spend a life to escape a trap, not merely to avoid comparing legal choices."], ["seed", "life", "tier", "bounty"],
    )

    @classmethod
    def rulebook_values(cls, rules: dict | None) -> dict:
        remaining, maximum = lives_of(rules)
        escape = (rules or {}).get(ESCAPE_DEPTH_KEY)
        return {
            **super().rulebook_values(rules), "max_lives": maximum, "lives_remaining": remaining,
            "escape_goal": f"escape at depth {escape}." if escape else "no escape depth is configured.",
            "tier_schedule": "; ".join(f"depth {tier.start_depth}: {tier.rule}" for tier in TIERS),
        }
    game_type = RABBIT_HOLE
    supports_bounty_board = True
    display_name = "The Rabbit Hole"
    description = (
        "Survive the descent: classic cast links, but every five films a nastier rule kicks in "
        "(pre-2000, non-English, under 100 minutes, B-movies). Break a rule and it costs a life - "
        "you have three."
    )
    uses_lives: ClassVar[bool] = True

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._depth = 0

    # --- rules ---

    def validate_rules_config(self, rules: dict | None) -> list[str]:
        problems = super().validate_rules_config(rules)
        max_lives = (rules or {}).get(MAX_LIVES_KEY)
        if max_lives is not None and (
            isinstance(max_lives, bool)
            or not isinstance(max_lives, int)
            or not 1 <= max_lives <= MAX_LIVES_LIMIT
        ):
            problems.append(f"{MAX_LIVES_KEY} must be a whole number from 1 to {MAX_LIVES_LIMIT}")
        escape_depth = (rules or {}).get(ESCAPE_DEPTH_KEY)
        if escape_depth is not None and (
            isinstance(escape_depth, bool)
            or not isinstance(escape_depth, int)
            or not MIN_ESCAPE_DEPTH <= escape_depth <= MAX_ESCAPE_DEPTH
        ):
            problems.append(
                f"{ESCAPE_DEPTH_KEY} must be a whole number from "
                f"{MIN_ESCAPE_DEPTH} to {MAX_ESCAPE_DEPTH}"
            )
        return problems

    def prepare_rules_config(self, rules: dict) -> dict:
        _, max_lives = lives_of(rules)
        # A new run always starts on full lives, whatever the client sent.
        return {**rules, MAX_LIVES_KEY: max_lives, LIVES_KEY: max_lives}

    def award_bounty(
        self,
        rules: dict,
        completed_id: str,
        replacement_id: str | None,
        custom: dict | None = None,
    ) -> dict:
        updated = super().award_bounty(rules, completed_id, replacement_id, custom)
        lives, max_lives = lives_of(rules)
        return {
            **updated,
            "wildcards_budget": rules.get("wildcards_budget", 0),
            LIVES_KEY: min(max_lives, lives + 1),
        }

    def sync_run_state(self, run: Run, steps: Sequence[RunStep]) -> None:
        rules = dict(run.rules_config or {})
        override = rules.get(TIER_OVERRIDE_KEY)
        depth = override.get("depth") if isinstance(override, dict) else None
        if isinstance(depth, int) and not isinstance(depth, bool) and len(steps) > depth:
            rules.pop(TIER_OVERRIDE_KEY, None)
            run.rules_config = rules

    def evaluate_run_outcome(self, run: Run, steps: list[RunStep]) -> RunOutcome | None:
        outcome = super().evaluate_run_outcome(run, steps)
        escape_depth = (run.rules_config or {}).get(ESCAPE_DEPTH_KEY)
        if (
            outcome is None
            and run.engine_version > 1
            and run.status == "active"
            and isinstance(escape_depth, int)
            and not isinstance(escape_depth, bool)
            and len(steps) >= escape_depth
        ):
            remaining, _ = lives_of(run.rules_config)
            return RunOutcome(
                RUN_STATUS_COMPLETED,
                f"Escaped the Rabbit Hole at Depth {len(steps)} with ❤️×{remaining}",
            )
        return outcome

    @classmethod
    def forfeit_outcome(cls, run: Run, steps: Sequence[RunStep]) -> RunOutcome | None:
        """Giving up with no lives left is a defeat, not a retirement."""
        remaining, _ = lives_of(run.rules_config)
        if remaining > 0:
            return None
        depth = len(steps)
        return RunOutcome(
            RUN_STATUS_FAILED,
            f"Succumbed to the Rabbit Hole at Depth {depth} "
            f"({tier_for_depth(depth, run.rules_config).name})",
        )

    def _needs_hydration(self, row: CachedMovie, rules: dict | None = None) -> bool:
        tier = tier_for_depth(self._depth, rules)
        needs = tier.number > 1 and compliance(self.session, tier, row) is None
        return needs or super()._needs_hydration(row, rules)

    # --- validation ---

    async def validate_next_step(
        self,
        from_movie_id: int,
        to_movie_id: int,
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
        history: Sequence[RunStep] | None = None,
    ) -> ValidationResult:
        self._depth = len(history or [])
        return await super().validate_next_step(
            from_movie_id,
            to_movie_id,
            cast_limit=cast_limit,
            rules=rules,
            previous_transition=previous_transition,
            history=history,
        )

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
        tier = tier_for_depth(self._depth, rules)
        if tier.number == 1:
            return result
        later = await self._load(to_movie_id, hydrate=True, rules=rules)
        if compliance(self.session, tier, later) is not False:  # unknown data never costs a life
            return result
        reason = violation_reason(self.session, tier, later)
        if not result.valid and result.reason:
            reason = f"{result.reason}; {reason}"
        # Soft: `force` may still log it, at the price of a life.
        return result.model_copy(update={"valid": False, "blocked": False, "reason": reason})

    # --- the HUD's rule ---

    async def describe_run_constraint(
        self,
        tail_movie_id: int | None,
        previous_transition: dict | None,
        rules: dict | None,
        history: Sequence[RunStep] | None = None,
    ) -> ConstraintInfo | None:
        self._depth = len(history or [])
        return await super().describe_run_constraint(
            tail_movie_id, previous_transition, rules, history
        )

    async def describe_constraint(
        self,
        tail_movie_id: int | None,
        previous_transition: dict | None,
        rules: dict | None = None,
    ) -> ConstraintInfo | None:
        state = tier_state(self._depth, rules)
        detail = (
            "Any film that shares a credited actor or director."
            if state.tier == 1
            else f"{state.tier_rule}, and a shared credited actor or director."
        )
        if state.upcoming_tier_warning:
            detail = f"{detail} {state.upcoming_tier_warning}"
        return ConstraintInfo(
            kind="tier",
            title=f"Tier {state.tier}: {state.tier_name}",
            detail=detail,
            rabbit_hole=state,
        )

    # --- Pick Next ---

    async def discover_with_modifiers(
        self,
        frontier_movie_id: int,
        mode: str = "or",
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
        history: Sequence[RunStep] | None = None,
        *,
        include_off_tier: bool = False,
    ) -> list[DiscoveryCandidate]:
        pool = await self.discover_candidates(
            frontier_movie_id, mode, cast_limit, rules, previous_transition, history,
            include_off_tier=include_off_tier,
        )
        return await self.filter_by_modifiers(frontier_movie_id, pool, rules, history)

    async def discover_candidates(
        self,
        frontier_movie_id: int,
        mode: str = "or",
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
        history: Sequence[RunStep] | None = None,
        *,
        include_off_tier: bool = False,
    ) -> list[DiscoveryCandidate]:
        """Cast-linked films that already satisfy the active tier's rule (films whose data can't
        be checked yet stay, flagged unverified)."""
        self._depth = len(history or [])
        state = tier_state(self._depth, rules)
        tier = tier_for_depth(self._depth, rules)
        pool = await super().discover_candidates(
            frontier_movie_id, mode, cast_limit, rules, previous_transition, history
        )
        rows = await self._hydrate_pool(pool, rules) if tier.number > 1 else {}
        kept: list[DiscoveryCandidate] = []
        for candidate in pool:
            if tier.number > 1:
                row = rows.get(candidate.movie_id)
                if row is None:
                    continue
                verdict = compliance(self.session, tier, row)
                if verdict is False and not include_off_tier:
                    continue
                candidate.tier_compliant = verdict
                candidate.constraint_unverified = verdict is None
            else:
                candidate.tier_compliant = True
            candidate.upcoming_tier_warning = state.upcoming_tier_warning
            kept.append(candidate)
        return kept
