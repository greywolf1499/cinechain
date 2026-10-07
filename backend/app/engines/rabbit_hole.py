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

import random
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from typing import ClassVar

from sqlmodel import Session, col, select

from app.engines.base import RunSetupError
from app.engines.cinechain import CineChainEngine
from app.engines.conditions import RunOutcome
from app.engines.predicates import (
    MovieFacts,
    Predicate,
    QueryPredicate,
    facts_of,
    named_predicate,
    predicate,
)
from app.engines.rulebook import RuleSection
from app.facets.query import FacetQuery
from app.facets.registry import CATALOGUE, named_variants
from app.models.cache import CachedMovie, CachedMovieCast, CachedMovieDirector
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
from app.services import feasibility
from app.services.movie_filters import is_reality_eligible, rating_of
from app.utils.dates import parse_release_year
from app.utils.ids import utcnow

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
RH_VERSION_KEY = "rh_rules_version"
RH_SEED_KEY = "rh_seed"
RESOURCE_KEYS = (LIVES_KEY, "relics", "reroll_tokens", TIER_OVERRIDE_KEY, "curse_skip")


@dataclass(frozen=True)
class Tier:
    number: int
    name: str
    rule: str  # short label for badges and warnings
    start_depth: int
    predicate: Predicate | None = None
    curses: tuple[Predicate, ...] = ()
    procedural: bool = False


TIERS = (
    Tier(1, "Freefall", "No extra constraints", 0),
    Tier(
        2,
        "The Retro Lock",
        "Released before 2000",
        5,
        predicate("year_lt", value=RETRO_CUTOFF_YEAR),
    ),
    Tier(3, "Tower of Babel", "Non-English", 10, predicate("non_english")),
    Tier(
        4,
        "The Micro-Clock",
        "Under 100 mins",
        15,
        predicate("runtime_lt", value=MICRO_CLOCK_MINUTES),
    ),
    Tier(
        5, "The B-Movie Abyss", "Rated under 6.0", 20, predicate("rating_lt", value=B_MOVIE_RATING)
    ),
)


def procedural(rules: dict | None) -> bool:
    return (rules or {}).get(RH_VERSION_KEY) in (2, 3)


def daily_seed(day: date) -> int:
    # Keep the shared seed exactly representable by JavaScript.
    return int.from_bytes(sha256(day.isoformat().encode("ascii")).digest()[:6], "big")


def tier_options() -> list[Predicate]:
    return [
        *(predicate("year_lt", value=value) for value in (1980, 1990, 2000)),
        predicate("non_english"),
        *(predicate("runtime_lt", value=value) for value in (90, 100, 110)),
        *(predicate("rating_lt", value=value) for value in (5, 6, 7)),
        *(predicate("popularity_lt", value=value) for value in (15, 30, 50)),
        *(predicate("runtime_ge", value=value) for value in (110, 140, 150)),
        predicate("non_us_non_english"),
    ]


def predicate_data(test: Predicate) -> dict:
    if isinstance(test, QueryPredicate):
        return {
            "query": test.query.model_dump(by_alias=True, exclude_none=True),
            "predicate_id": test.id,
            "params": {},
            "name": test.label,
            "rule": test.label,
            "difficulty": test.difficulty,
        }
    names = {
        "year_lt": "The Retro Lock",
        "non_english": "Tower of Babel",
        "runtime_lt": "The Micro-Clock",
        "rating_lt": "The B-Movie Abyss",
        "popularity_lt": "The Hidden Depths",
        "runtime_ge": "The Long Haul",
        "non_us_non_english": "Beyond the Border",
    }
    value = test.params.get("value")
    rule = f"{test.label} {value:g}" if value is not None else test.label
    if test.id.startswith("runtime"):
        rule += " mins"
    return {
        "predicate_id": test.id,
        "params": test.params,
        "name": names[test.id],
        "rule": rule,
        "difficulty": test.difficulty,
    }


def test_from_data(data: dict) -> Predicate:
    if "query" in data:
        return QueryPredicate(
            data["predicate_id"],
            data["rule"],
            FacetQuery.model_validate(data["query"]),
            difficulty=data.get("difficulty", 1),
        )
    return predicate(data["predicate_id"], **data["params"])


@dataclass(frozen=True)
class TierPredicates:
    tests: tuple[Predicate, ...]
    id = "rabbit_tier"
    label = "Rabbit Hole tier and curses"
    emoji = ""
    difficulty = 3

    @property
    def query(self) -> FacetQuery:
        return FacetQuery(all=[feasibility.query_of(test) for test in self.tests])

    @property
    def params(self) -> dict[str, float]:
        return {}

    @property
    def needs(self) -> frozenset[str]:
        return frozenset().union(*(test.needs for test in self.tests))

    @property
    def ranges(self) -> dict[str, tuple[float | None, float | None]]:
        bounds: dict[str, tuple[float | None, float | None]] = {}
        for test in self.tests:
            for field, (low, high) in test.ranges.items():
                old_low, old_high = bounds.get(field, (None, None))
                lows = [value for value in (low, old_low) if value is not None]
                highs = [value for value in (high, old_high) if value is not None]
                bounds[field] = (max(lows) if lows else None, min(highs) if highs else None)
        return bounds

    def check(self, movie: CachedMovie | None, facts: MovieFacts) -> bool | None:
        results = [test.check(movie, facts) for test in self.tests]
        return False if False in results else None if None in results else True


def tier_tests(tier: Tier) -> TierPredicates:
    return TierPredicates(
        tuple(test for test in (tier.predicate, *tier.curses) if test is not None)
    )


def draw_deck(
    session: Session,
    seed: int,
    curses: bool = False,
    eligible_ids: Sequence[int] | None = None,
) -> list[dict]:
    rng = random.Random(seed)
    options = [
        test
        for test in tier_options()
        if (feasibility.cache_pass_rate(session, test) or 0) >= 0.03
        and (
            eligible_ids is None
            or (feasibility.pass_rate(session, test, eligible_ids) or 0) >= 0.03
        )
    ]
    if len(options) < 4:
        raise RunSetupError(
            "Rabbit Hole needs cached movie evidence for at least four feasible tier rules. "
            "Choose a seed film or browse more films first."
        )
    rng.shuffle(options)
    # Prefer different concepts before reusing a concept with another parameter.
    unique: list[Predicate] = []
    repeats: list[Predicate] = []
    for test in options:
        (repeats if any(other.id == test.id for other in unique) else unique).append(test)
    chosen = (unique + repeats)[: rng.randint(4, min(6, len(options)))]
    chosen.sort(key=lambda test: test.difficulty)
    deck = [
        {
            "number": 1,
            "name": "Freefall",
            "rule": "No extra constraints",
            "start_depth": 0,
            "curses": [],
        }
    ]
    for index, test in enumerate(chosen, start=1):
        inherited: list[dict] = []
        if curses and index >= 3:
            previous = deck[-1]
            for curse in [*previous["curses"], {k: previous[k] for k in predicate_data(test)}]:
                combined = TierPredicates(
                    (test, *(test_from_data(entry) for entry in [*inherited, curse]))
                )
                if (feasibility.cache_pass_rate(session, combined) or 0) >= 0.01 and (
                    eligible_ids is None
                    or (feasibility.pass_rate(session, combined, eligible_ids) or 0) >= 0.01
                ):
                    inherited.append(curse)
        deck.append(
            {
                **predicate_data(test),
                "number": index + 1,
                "start_depth": index * 5,
                "curses": inherited,
            }
        )
    return deck


TIER_PASS_BANDS = ((0.20, 0.45), (0.10, 0.30), (0.05, 0.18), (0.025, 0.10))


def facet_options(session: Session) -> list[QueryPredicate]:
    options = [
        named_predicate(name)
        for name in named_variants()
        if name not in ("chaser_trigger", "chaser")
    ]
    movies = feasibility.movies(session)
    from app.facets.store import refresh, stored_values

    refresh(session, movies)
    facts = stored_values(session, movies, ("runtime", "release_year", "rating", "title_length"))
    for movie_id, movie in movies.items():
        facts.setdefault(movie_id, {})["popularity"] = movie.popularity
    for facet in ("runtime", "release_year", "rating", "popularity", "title_length"):
        values = sorted(
            {value for f in facts.values() if isinstance(value := f.get(facet), (int, float))}
        )
        for fraction in (0.03, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9, 0.95, 0.97, 0.99):
            if not values:
                continue
            value = values[min(len(values) - 1, int(len(values) * fraction))]
            for op in ("le", "ge"):
                comparison = "at most" if op == "le" else "at least"
                options.append(
                    QueryPredicate(
                        f"{facet}_{op}_{value}",
                        f"{CATALOGUE[facet].label} {comparison} {value:g}",
                        FacetQuery(facet=facet, op=op, value=value),
                    )
                )
    return options


def draw_facet_deck(
    session: Session, seed: int, eligible_ids: Sequence[int], curses: bool = False
) -> list[dict]:
    rng = random.Random(seed)
    options = facet_options(session)
    rng.shuffle(options)
    candidates = list(options)

    def numeric_value(test: QueryPredicate) -> float:
        assert isinstance(test.query.value, (int, float))
        return test.query.value

    for facet in ("runtime", "release_year", "rating", "popularity", "title_length"):
        lower = sorted(
            (p for p in options if p.query.facet == facet and p.query.op == "le"), key=numeric_value
        )[:3]
        upper = sorted(
            (p for p in options if p.query.facet == facet and p.query.op == "ge"),
            key=numeric_value,
            reverse=True,
        )[:3]
        for left in lower:
            for right in upper:
                candidates.append(
                    QueryPredicate(
                        f"{left.id}|{right.id}",
                        f"{left.label} or {right.label}",
                        FacetQuery(any=[left.query, right.query]),
                    )
                )
    cult, one_word = named_predicate("cult_classic"), named_predicate("one_word")
    candidates.append(
        QueryPredicate(
            "cult_classic+one_word",
            "Cult classic + one-word title",
            FacetQuery(all=[cult.query, one_word.query]),
        )
    )
    for _ in range(300):
        parts = rng.sample(options, rng.randint(2, min(3, len(options))))
        conjunction = rng.choice((True, False))
        candidates.append(
            QueryPredicate(
                ("+" if conjunction else "|").join(p.id for p in parts),
                (" + " if conjunction else " or ").join(p.label for p in parts),
                FacetQuery(all=[p.query for p in parts])
                if conjunction
                else FacetQuery(any=[p.query for p in parts]),
            )
        )

    def leaf_count(query: FacetQuery) -> int:
        return (
            1 if query.facet is not None else sum(leaf_count(child) for child in query.children())
        )

    candidates = [test for test in candidates if 1 <= leaf_count(test.query) <= 3]
    cache_ids = list(feasibility.movies(session))
    deck = [
        {
            "number": 1,
            "name": "Freefall",
            "rule": "No extra constraints",
            "start_depth": 0,
            "curses": [],
        }
    ]
    used = set()
    previous_difficulty = 1
    for index, (low, high) in enumerate(TIER_PASS_BANDS, start=1):
        accepted = []
        for test in candidates:
            if test.id in used:
                continue
            cache_rate = feasibility.pass_rate(session, test, cache_ids)
            pool_rate = feasibility.pass_rate(session, test, eligible_ids)
            if (
                cache_rate is None
                or pool_rate is None
                or not (low <= cache_rate <= high and low <= pool_rate <= high)
            ):
                continue
            measured = feasibility.difficulty(pool_rate)
            if measured >= previous_difficulty:
                accepted.append((test, pool_rate, measured))
        if not accepted:
            raise RunSetupError(
                f"Rabbit Hole needs cached movie evidence and a reachable pool in tier {index + 1}'s {low:.0%}-{high:.0%} pass-rate band. Browse more films or choose a broader seed."
            )
        accepted.sort(key=lambda entry: (entry[2], abs(entry[1] - (low + high) / 2)))
        # Choose among equally difficult rules, favouring a composition when available.
        minimum = accepted[0][2]
        fair = [entry for entry in accepted if entry[2] == minimum]
        compositions = [entry for entry in fair if entry[0].query.all is not None]
        test, rate, measured = rng.choice(compositions or fair)
        used.add(test.id)
        previous_difficulty = measured
        inherited = []
        if curses and index >= 3:
            for entry in [*deck[-1]["curses"], deck[-1]]:
                combined = QueryPredicate(
                    "combined",
                    "Combined",
                    FacetQuery(
                        all=[
                            test.query,
                            feasibility.query_of(test_from_data(entry)),
                            *(feasibility.query_of(test_from_data(c)) for c in inherited),
                        ]
                    ),
                )
                if (
                    combined.query.size() <= 64
                    and (feasibility.pass_rate(session, combined, eligible_ids) or 0) >= 0.01
                    and (feasibility.pass_rate(session, combined, cache_ids) or 0) >= 0.01
                ):
                    inherited.append(
                        {
                            k: v
                            for k, v in entry.items()
                            if k not in ("curses", "number", "start_depth")
                        }
                    )
        deck.append(
            {
                **predicate_data(test),
                "number": index + 1,
                "start_depth": index * 5,
                "difficulty": measured,
                "pass_rate": rate,
                "curses": inherited,
            }
        )
    return deck


def tiers_of(rules: dict | None) -> tuple[Tier, ...]:
    if not procedural(rules):
        return TIERS
    return tuple(
        Tier(
            entry["number"],
            entry["name"],
            entry["rule"],
            entry["start_depth"],
            test_from_data(entry) if entry.get("predicate_id") else None,
            tuple(test_from_data(curse) for curse in entry["curses"]),
            True,
        )
        for entry in (rules or {})["tier_deck"]
    )


def tier_for_depth(depth: int, rules: dict | None = None) -> Tier:
    """The tier governing the film that would become step `depth + 1`."""
    override = (rules or {}).get(TIER_OVERRIDE_KEY)
    tiers = tiers_of(rules)
    scheduled = next(t for t in reversed(tiers) if depth >= t.start_depth)
    if procedural(rules):
        tier = scheduled
        if isinstance(override, dict) and override.get("depth") == depth:
            entry = override["predicate"]
            tier = Tier(
                scheduled.number,
                entry["name"],
                entry["rule"],
                scheduled.start_depth,
                test_from_data(entry),
                scheduled.curses,
                True,
            )
        if (rules or {}).get("curse_skip") == depth:
            tier = Tier(
                tier.number,
                tier.name,
                tier.rule,
                tier.start_depth,
                tier.predicate,
                tier.curses[:-1],
                True,
            )
        return tier
    if (
        isinstance(override, dict)
        and override.get("depth") == depth
        and not isinstance(override.get("depth"), bool)
        and isinstance(override.get("tier"), int)
        and not isinstance(override.get("tier"), bool)
        and 2 <= override["tier"] <= len(TIERS)
    ):
        return TIERS[override["tier"] - 1]
    return scheduled


def next_tier_of(tier: Tier, rules: dict | None = None) -> Tier | None:
    tiers = tiers_of(rules)
    return tiers[tier.number] if tier.number < len(tiers) else None


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
    scheduled_tier = tier_for_depth(
        depth,
        {k: v for k, v in (rules or {}).items() if k not in (TIER_OVERRIDE_KEY, "curse_skip")},
    )
    upcoming = next_tier_of(scheduled_tier, rules)
    if tier.number != scheduled_tier.number:
        upcoming = next_tier_of(scheduled_tier, rules)
    remaining, max_lives = lives_of(rules)
    state = RabbitHoleState(
        depth=depth,
        tier=tier.number,
        tier_name=tier.name,
        tier_rule=tier.rule,
        lives_remaining=remaining,
        max_lives=max_lives,
        tier_override=tier.number
        if tier != scheduled_tier and (rules or {}).get(TIER_OVERRIDE_KEY)
        else None,
        curses=[predicate_data(test) for test in tier.curses],
        curse_skipped=(rules or {}).get("curse_skip") == depth,
        reroll_tokens=(rules or {}).get("reroll_tokens", 0),
        relics=(rules or {}).get("relics", {}),
        daily=(rules or {}).get("daily", False),
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
    tests = tier_tests(tier)
    if session.get(CachedMovie, row.tmdb_id) is None:
        return tests.check(row, facts_of(row, [], rating_of(session, row)))
    return feasibility.verdict(session, tests, row.tmdb_id)


def violation_reason(session, tier: Tier, row: CachedMovie) -> str:
    prefix = f"Tier {tier.number} ({tier.name}): "
    if tier.procedural:
        failed = [
            predicate_data(test)["rule"]
            for test in tier_tests(tier).tests
            if feasibility.verdict(session, test, row.tmdb_id) is False
        ]
        return f"{prefix}{row.title} must satisfy: {', '.join(failed)}"
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
    bounty_reward = "life"

    def bounty_bounds(self, rules: dict, history: Sequence[RunStep]) -> dict:
        from app.services.feasibility import ranges_of

        bounds = super().bounty_bounds(rules, history)
        tier = tier_for_depth(len(history), rules)
        if tier.predicate:
            for field, (low, high) in ranges_of(tier_tests(tier)).items():
                previous_low, previous_high = bounds.get(field, (None, None))
                lows = [v for v in (low, previous_low) if v is not None]
                highs = [v for v in (high, previous_high) if v is not None]
                bounds[field] = (max(lows) if lows else None, min(highs) if highs else None)
        return bounds

    def bounty_pool_allowed(
        self, movie: CachedMovie, rules: dict, history: Sequence[RunStep]
    ) -> bool:
        return (
            super().bounty_pool_allowed(movie, rules, history)
            and compliance(self.session, tier_for_depth(len(history), rules), movie) is not False
        )

    rule_fields: ClassVar[list[RuleField]] = [
        *(field for field in CineChainEngine.rule_fields if field.key != "wildcards_budget"),
        RuleField(
            key="max_lives",
            kind="int",
            label="Starting lives",
            min=1,
            max=MAX_LIVES_LIMIT,
            default=3,
        ),
        RuleField(key="allow_reroll", kind="bool", label="Allow tier re-rolls", default=True),
        RuleField(key="curses", kind="bool", label="Hard mode: persistent curses", default=False),
        RuleField(
            key="daily",
            kind="bool",
            label="Daily Dive",
            default=False,
            help="Use today's shared UTC seed; feasible tiers still depend on your movie cache.",
        ),
    ]
    presets: ClassVar[list[Preset]] = [
        Preset(
            id="tourist",
            label="Tourist",
            blurb="Five lives and tier re-rolls.",
            values={"max_lives": 5, "allow_reroll": True},
        ),
        Preset(
            id="spelunker",
            label="Spelunker",
            blurb="Three lives for a balanced descent.",
            values={"max_lives": 3, "allow_reroll": True},
        ),
        Preset(
            id="ironman",
            label="Ironman",
            blurb="One life, persistent curses. No tier re-rolls.",
            values={"max_lives": 1, "allow_reroll": False, "curses": True},
        ),
    ]
    default_preset = "spelunker"
    discovery_filters: ClassVar[list[FilterSpec]] = [
        FilterSpec(
            key="include_off_tier",
            kind="toggle",
            label="Include off-tier films",
            source="tier_compliant",
            default=False,
            server_param="include_off_tier",
            help="Off-tier films cost one life when logged. Other rules still apply.",
        ),
    ]
    tagline = "Descend. Survive. Don't blink."
    tags: ClassVar[list[str]] = ["Shared cast", "3 lives", "Rogue-like"]
    rulebook: ClassVar[RuleSection] = RuleSection(
        "Survive the dive; {escape_goal}",
        [
            "Link a film that meets the current tier's rule.",
            "Spend one life to skip a missing link or a tier rule.",
            "Check the tier card before you pick.",
        ],
        [
            "Start with {max_lives} lives; {lives_remaining} remain. Legal moves cost no life.",
            "{tier_schedule}",
            "{procedural_rules}",
            "{win_goal}",
        ],
        ["At zero lives, a dead end or surrender ends the run as failed.", "{fail_goal}"],
        [
            "Your current pick should leave a filmography suited to the next tier.",
            "Spend a life to escape a trap, not merely to avoid comparing legal choices.",
        ],
        ["seed", "life", "tier", "bounty"],
    )

    @classmethod
    def rulebook_values(cls, rules: dict | None) -> dict:
        remaining, maximum = lives_of(rules)
        escape = (rules or {}).get(ESCAPE_DEPTH_KEY)
        return {
            **super().rulebook_values(rules),
            "max_lives": maximum,
            "lives_remaining": remaining,
            "escape_goal": f"escape at depth {escape}."
            if escape
            else "no escape depth is configured.",
            "tier_schedule": ("This run's tier rules: ")
            + "; ".join(f"depth {tier.start_depth}: {tier.rule}" for tier in tiers_of(rules)),
            "procedural_rules": (
                "Tiers change every five films. Hard-mode curses last while they can be met. "
                "A new tier grants a life or a relic. Lives cannot exceed your cap. "
                "Re-roll uses a token first, then a life. Skip curse lifts the newest curse for one hop. "
                "Undo gives back what that step spent."
                if procedural(rules)
                else ("Follow this run's five-tier schedule. This run has no relics.")
            ),
        }

    def coach_line(self, run: Run, steps: Sequence[RunStep]) -> str | None:
        state = tier_state(len(steps), run.rules_config)
        if state.next_tier_rule and state.steps_until_next == 1:
            return f"Tier {state.next_tier} starts next hop: {state.next_tier_rule}"
        return f"Next film: {state.tier_rule}"

    game_type = RABBIT_HOLE
    supports_bounty_board = True
    display_name = "The Rabbit Hole"
    description = (
        "Survive a seeded deck of feasible tier rules, earn relics every five films, and opt "
        "into persistent curses or a shared Daily Dive. Breaking a rule costs a life."
    )
    uses_lives: ClassVar[bool] = True

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._depth = 0
        self.setup_seed_movie_id: int | None = None

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

    async def prepare_run(self, rules: dict, user_id: str) -> dict:
        seed = daily_seed(utcnow().date()) if rules.get("daily") else secrets.randbits(48)
        movies = feasibility.movies(self.session)
        bounds = super().bounty_bounds(rules, [])
        eligible = [
            movie_id
            for movie_id, row in movies.items()
            if is_reality_eligible(row)
            and feasibility.within_movie(row, bounds)
            and super(RabbitHoleEngine, self).bounty_pool_allowed(row, rules, [])
        ]
        if self.setup_seed_movie_id is not None:
            from app.services import cache_repo

            cast = await cache_repo.get_movie_cast(
                self.session, self.tmdb, self.setup_seed_movie_id
            )
            directors = await cache_repo.get_movie_directors(
                self.session, self.tmdb, self.setup_seed_movie_id
            )
            actor_ids = [
                p["actor_id"] for p in cast if p["cast_order"] < rules.get("max_cast_order", 10)
            ]
            director_ids = [p.person_id for p in directors]
            reachable = set(
                self.session.exec(
                    select(CachedMovieCast.movie_id).where(
                        col(CachedMovieCast.actor_id).in_(actor_ids)
                    )
                ).all()
            )
            reachable.update(
                self.session.exec(
                    select(CachedMovieDirector.movie_id).where(
                        col(CachedMovieDirector.person_id).in_(director_ids)
                    )
                ).all()
            )
            eligible = [i for i in eligible if i in reachable and i != self.setup_seed_movie_id]
        return {
            **rules,
            RH_VERSION_KEY: 3,
            RH_SEED_KEY: seed,
            "tier_deck": draw_facet_deck(self.session, seed, eligible, rules.get("curses", False)),
            "relics": {"skip_curse": 0},
            "reroll_tokens": 0,
        }

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
        if procedural(rules):
            if rules.get("curse_skip", len(steps)) < len(steps):
                rules.pop("curse_skip", None)
            if (
                steps
                and "rh_resources_before" in (steps[-1].transition_metadata or {})
                and len(steps) in {tier.start_depth for tier in tiers_of(rules)[1:]}
            ):
                step = steps[-1]
                if not (step.transition_metadata or {}).get("relic_awarded"):
                    rewards = ["life"]
                    if rules.get("allow_reroll", True):
                        rewards.append("reroll")
                    if rules.get("curses"):
                        rewards.append("skip_curse")
                    kind = random.Random(f"{rules[RH_SEED_KEY]}:relic:{len(steps)}").choice(rewards)
                    lives, maximum = lives_of(rules)
                    amount = 1
                    if kind == "life":
                        amount = int(lives < maximum)
                        rules[LIVES_KEY] = min(maximum, lives + 1)
                    elif kind == "reroll":
                        rules["reroll_tokens"] = rules.get("reroll_tokens", 0) + 1
                    else:
                        rules["relics"] = {
                            **rules["relics"],
                            "skip_curse": rules["relics"].get("skip_curse", 0) + 1,
                        }
                    step.transition_metadata = {
                        **(step.transition_metadata or {}),
                        "relic_awarded": {"kind": kind, "amount": amount, "depth": len(steps)},
                    }
                    self.session.add(step)
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
            frontier_movie_id,
            mode,
            cast_limit,
            rules,
            previous_transition,
            history,
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
        be checked yet stay; `annotate_candidates` stamps the verdict once the pool is final)."""
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
                if compliance(self.session, tier, row) is False and not include_off_tier:
                    continue
            candidate.upcoming_tier_warning = state.upcoming_tier_warning
            kept.append(candidate)
        return kept

    def annotate_candidates(
        self,
        candidates: list[DiscoveryCandidate],
        rules: dict | None = None,
        history: Sequence[RunStep] | None = None,
    ) -> list[DiscoveryCandidate]:
        """Stamps the tier verdict on the final pool. Running after every hydration pass and
        after `shape_pool` means a film whose facts arrived late is judged on those facts, so a
        card can never carry both a cached runtime and "Rule unverified"."""
        candidates = super().annotate_candidates(candidates, rules, history)
        depth = len(history) if history is not None else self._depth
        tier = tier_for_depth(depth, rules)
        for candidate in candidates:
            if tier.number == 1:
                candidate.tier_compliant = True
                continue
            row = self.session.get(CachedMovie, candidate.movie_id)
            verdict = compliance(self.session, tier, row) if row is not None else None
            candidate.tier_compliant = verdict
            candidate.constraint_unverified = verdict is None or (
                row is not None and self._modifiers_need_detail(row, rules)
            )
        return candidates
