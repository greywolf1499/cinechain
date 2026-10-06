"""The Genre Pendulum: the required genre swings through a cycle as the run grows.

`rules_config["genre_cycle"]` (default Horror, Thriller, Crime, Comedy) and
`rules_config["swing_frequency"]` (steps per genre, default 2) drive a small state machine over the
number of steps already logged:

    target = genre_cycle[(steps // swing_frequency) % len(genre_cycle)]

A film may follow the last one when it (1) shares at least one genre with it and (2) carries the
current target genre. Any film that satisfies both is legal - shared cast is only an opt-in hybrid
modifier (`require_cast_link`), like the other standalone modes - so this engine extends
`MutatorEngine` (a `CineChainEngine`, hence a `BaseChallengeEngine`).

The step count reaches the rule through `validate_next_step` / `discover_candidates` /
`describe_run_constraint` (all given the run's history); engines are built per request, so it is
kept on the instance for the pair check the Mutator pipeline calls.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar

from sqlalchemy import text
from sqlmodel import select

from app.engines.mutators import (
    POOL_FETCH_ERRORS,
    RULE_POOL_SIZE,
    MutatorEngine,
    candidate_from_row,
)
from app.engines.rulebook import RuleSection
from app.models.cache import CachedMovie
from app.models.run import RunStep
from app.schemas.discovery import DiscoveryCandidate
from app.schemas.engine import (
    ConstraintInfo,
    FilterSpec,
    Suggestion,
    SuggestionFilters,
    ValidationResult,
)
from app.services import cache_repo
from app.services.movie_filters import is_reality_eligible

GENRE_PENDULUM = "genre_pendulum"
DEFAULT_GENRE_CYCLE = ["Horror", "Thriller", "Crime", "Comedy"]
DEFAULT_SWING_FREQUENCY = 2
MAX_CYCLE_LENGTH = 12
MAX_SWING_FREQUENCY = 10
GENRE_CYCLE_KEY = "genre_cycle"
SWING_FREQUENCY_KEY = "swing_frequency"
# Frontier genres a pool query pairs with the target (TMDB `with_genres` ANDs them).
POOL_PAIR_GENRES = 3

# TMDB's fixed movie genre ids: they never change, so no lookup is needed to resolve a name.
TMDB_GENRE_IDS: dict[str, int] = {
    "Action": 28,
    "Adventure": 12,
    "Animation": 16,
    "Comedy": 35,
    "Crime": 80,
    "Documentary": 99,
    "Drama": 18,
    "Family": 10751,
    "Fantasy": 14,
    "History": 36,
    "Horror": 27,
    "Music": 10402,
    "Mystery": 9648,
    "Romance": 10749,
    "Science Fiction": 878,
    "TV Movie": 10770,
    "Thriller": 53,
    "War": 10752,
    "Western": 37,
}
_BY_LOWER = {name.lower(): name for name in TMDB_GENRE_IDS}
_ALIASES = {
    "sci-fi": "Science Fiction",
    "scifi": "Science Fiction",
    "sci fi": "Science Fiction",
    "science-fiction": "Science Fiction",
    "tv": "TV Movie",
    "doc": "Documentary",
}


def canonical_genre(name: object) -> str | None:
    """TMDB's spelling of a genre name (case-insensitive, with a few aliases), else None."""
    if not isinstance(name, str):
        return None
    key = " ".join(name.lower().split())
    return _BY_LOWER.get(key) or _ALIASES.get(key)


@dataclass(frozen=True)
class PendulumState:
    target: str
    next_target: str
    position: int  # 1-based step within the current swing
    frequency: int
    swing: int  # 0-based index of the current swing

    @property
    def target_id(self) -> int:
        return TMDB_GENRE_IDS[self.target]


def pendulum_config(rules: dict | None) -> tuple[list[str], int]:
    rules = rules or {}
    cycle = [g for g in (canonical_genre(n) for n in rules.get(GENRE_CYCLE_KEY) or []) if g]
    frequency = rules.get(SWING_FREQUENCY_KEY)
    if isinstance(frequency, bool) or not isinstance(frequency, int) or frequency < 1:
        frequency = DEFAULT_SWING_FREQUENCY
    return cycle or list(DEFAULT_GENRE_CYCLE), frequency


def pendulum_state(rules: dict | None, steps_logged: int) -> PendulumState:
    """Where the pendulum is for the film that would become step `steps_logged + 1`."""
    cycle, frequency = pendulum_config(rules)
    swing = steps_logged // frequency
    return PendulumState(
        target=cycle[swing % len(cycle)],
        next_target=cycle[(swing + 1) % len(cycle)],
        position=steps_logged % frequency + 1,
        frequency=frequency,
        swing=swing,
    )


def movie_genres(row: CachedMovie) -> set[int]:
    return set(row.genre_ids or [])


class GenrePendulumEngine(MutatorEngine):
    discovery_filters: ClassVar[list[FilterSpec]] = [
        FilterSpec(
            key="target_genre",
            kind="toggle",
            label="Target genre only",
            source="genre_ids",
            default=True,
            help="The current swing's genre; unknown tags stay visible.",
        ),
    ]
    tagline = "The genre swings as you go"
    tags: ClassVar[list[str]] = ["Any film", "Genre cycle", "Genre overlap"]
    rulebook: ClassVar[RuleSection] = RuleSection(
        "Follow the genre cycle while keeping an overlap between consecutive films.",
        [
            "Carry the target genre and share a genre with the previous film.",
            "The cycle is {genre_cycle}; the target changes every {swing_frequency} steps.",
        ],
        ["{win_goal}"],
        ["Missing overlap or the target genre blocks the hop.", "{fail_goal}"],
        [
            "Choose multi-genre films that bridge the current target to the next swing.",
            "Look ahead at the cycle before exhausting a narrow genre.",
        ],
        ["seed"],
    )

    @classmethod
    def rulebook_values(cls, rules: dict | None) -> dict:
        cycle, frequency = pendulum_config(rules)
        return {
            **super().rulebook_values(rules),
            "genre_cycle": " -> ".join(cycle),
            "swing_frequency": frequency,
        }

    game_type = GENRE_PENDULUM
    display_name = "The Genre Pendulum"
    description = (
        "The genre swings as you go: every film must share a genre with the last one AND carry "
        "the current target genre (Horror, then Thriller, then Crime...). Any film counts - no "
        "shared cast needed."
    )
    capabilities: ClassVar[list[str]] = [
        cap for cap in MutatorEngine.capabilities if cap != "solve_bridge"
    ]
    optional_cast_link = True
    needs_detail = True  # genres of a search/credits stub may be missing until the full fetch

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._steps_logged = 0

    # --- rules ---

    def validate_rules_config(self, rules: dict | None) -> list[str]:
        problems = super().validate_rules_config(rules)
        rules = rules or {}
        cycle = rules.get(GENRE_CYCLE_KEY)
        if cycle is not None:
            if not isinstance(cycle, list) or not 1 <= len(cycle) <= MAX_CYCLE_LENGTH:
                problems.append(f"{GENRE_CYCLE_KEY} must list 1 to {MAX_CYCLE_LENGTH} genres")
            else:
                unknown = [n for n in cycle if canonical_genre(n) is None]
                if unknown:
                    problems.append(
                        f"Unknown genre(s) in {GENRE_CYCLE_KEY}: {', '.join(map(str, unknown))}. "
                        f"Use TMDB genres: {', '.join(TMDB_GENRE_IDS)}"
                    )
        frequency = rules.get(SWING_FREQUENCY_KEY)
        if frequency is not None and (
            isinstance(frequency, bool)
            or not isinstance(frequency, int)
            or not 1 <= frequency <= MAX_SWING_FREQUENCY
        ):
            problems.append(
                f"{SWING_FREQUENCY_KEY} must be a whole number from 1 to {MAX_SWING_FREQUENCY}"
            )
        return problems

    def prepare_rules_config(self, rules: dict) -> dict:
        cycle, frequency = pendulum_config(rules)
        return {**rules, GENRE_CYCLE_KEY: cycle, SWING_FREQUENCY_KEY: frequency}

    # --- the pendulum ---

    def pendulum_violation(
        self, earlier: CachedMovie | None, later: CachedMovie, rules: dict | None, steps_logged: int
    ) -> str | None:
        state = pendulum_state(rules, steps_logged)
        if earlier is not None and not movie_genres(earlier) & movie_genres(later):
            return (
                f"Genre Pendulum: {later.title} shares no genre with {earlier.title} - "
                "each film must overlap the last in at least one genre"
            )
        if state.target_id not in movie_genres(later):
            return (
                f"Genre Pendulum: this swing needs a {state.target} film "
                f"(step {state.position} of {state.frequency}), but {later.title} isn't tagged {state.target}"
            )
        return None

    def pair_violation(
        self, earlier: CachedMovie, later: CachedMovie, rules: dict | None = None
    ) -> str | None:
        return self.pendulum_violation(earlier, later, rules, self._steps_logged)

    def mechanic(
        self, earlier: CachedMovie, later: CachedMovie, rules: dict | None = None
    ) -> dict | None:
        state = pendulum_state(rules, self._steps_logged)
        return {"pendulum_genre": state.target, "pendulum_step": state.position}

    def _needs_hydration(self, row: CachedMovie, rules: dict | None = None) -> bool:
        no_genres = not row.genre_ids and row.origin_country is None
        return no_genres or self._modifiers_need_detail(row, rules)

    async def validate_candidate(self, movie_id: int, rules: dict) -> ValidationResult:
        """The first film has no predecessor to overlap, but must carry the opening genre."""
        movie = await self._load(movie_id, hydrate=True, rules=rules)
        reason = self.pendulum_violation(None, movie, rules, 0)
        if reason:
            return ValidationResult(valid=False, blocked=True, reason=reason)
        return ValidationResult(valid=True)

    async def validate_next_step(
        self,
        from_movie_id: int,
        to_movie_id: int,
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
        history: Sequence[RunStep] | None = None,
    ) -> ValidationResult:
        self._steps_logged = len(history or [])
        return await super().validate_next_step(
            from_movie_id,
            to_movie_id,
            cast_limit=cast_limit,
            rules=rules,
            previous_transition=previous_transition,
            history=history,
        )

    async def describe_run_constraint(
        self,
        tail_movie_id: int | None,
        previous_transition: dict | None,
        rules: dict | None,
        history: Sequence[RunStep] | None = None,
    ) -> ConstraintInfo | None:
        self._steps_logged = len(history or [])
        return await super().describe_run_constraint(
            tail_movie_id, previous_transition, rules, history
        )

    async def describe_constraint(
        self,
        tail_movie_id: int | None,
        previous_transition: dict | None,
        rules: dict | None = None,
    ) -> ConstraintInfo | None:
        state = pendulum_state(rules, self._steps_logged)
        overlap = " and share a genre with the last film" if tail_movie_id is not None else ""
        return ConstraintInfo(
            kind="genre",
            title=f"Current swing: {state.target} (step {state.position} of {state.frequency})",
            detail=f"The next film must be {state.target}{overlap}. Next swing: {state.next_target}.",
        )

    # --- Pick Next ---

    async def get_suggestions(
        self,
        current_movie_id: int,
        exclude_movie_ids: list[int],
        filters: SuggestionFilters,
        rules: dict | None = None,
        history: Sequence[RunStep] | None = None,
    ) -> list[Suggestion]:
        self._steps_logged = len(history or [])
        return await super().get_suggestions(
            current_movie_id, exclude_movie_ids, filters, rules, history
        )

    async def discover_candidates(
        self,
        frontier_movie_id: int,
        mode: str = "or",
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
        history: Sequence[RunStep] | None = None,
    ) -> list[DiscoveryCandidate]:
        self._steps_logged = len(history or [])
        return await super().discover_candidates(
            frontier_movie_id, mode, cast_limit, rules, previous_transition, history
        )

    async def discover_rule_candidates(
        self,
        frontier: CachedMovie,
        rules: dict | None,
        history: Sequence[RunStep] | None = None,
    ) -> list[DiscoveryCandidate]:
        """Films carrying the target genre that overlap the frontier's genres: cached ones first,
        plus TMDB's most popular for "target + one of the frontier's genres"."""
        state = pendulum_state(rules, len(history or []))
        rows: dict[int, CachedMovie] = {}
        statement = (
            select(CachedMovie)
            .where(
                text(
                    "EXISTS (SELECT 1 FROM json_each(cached_movies.genre_ids) AS g WHERE g.value = :genre)"
                ).bindparams(genre=state.target_id)
            )
            .order_by(CachedMovie.popularity.desc())  # type: ignore[union-attr]
            .limit(RULE_POOL_SIZE * 2)
        )
        for row in self.session.exec(statement).all():
            rows[row.tmdb_id] = row

        pairs = [g for g in sorted(movie_genres(frontier)) if g != state.target_id]
        queries = [str(state.target_id)] if state.target_id in movie_genres(frontier) else []
        queries += [f"{state.target_id},{g}" for g in pairs[:POOL_PAIR_GENRES]]
        for with_genres in queries or [str(state.target_id)]:
            try:
                for row in await cache_repo.discover_movies(
                    self.session, self.tmdb, pages=1, with_genres=with_genres
                ):
                    rows.setdefault(row.tmdb_id, row)
            except POOL_FETCH_ERRORS:
                break

        pool = [
            candidate_from_row(row)
            for row in rows.values()
            if row.tmdb_id != frontier.tmdb_id and is_reality_eligible(row)
        ]
        pool = await self._filter_pool(frontier, pool, rules)
        pool.sort(key=lambda c: -(c.popularity or 0.0))
        return pool[:RULE_POOL_SIZE]
