"""SQL-based tracker modes: no graph traversal, just rules over the local cache."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar

from sqlalchemy import Float, cast, func, or_, text
from sqlmodel import col, select

from app.engines.base import BaseChallengeEngine
from app.engines.cinechain import compute_run_stats
from app.engines.rulebook import RuleSection
from app.models.cache import CachedMovie, CachedMovieRating
from app.models.run import RunStep
from app.schemas.engine import RunStats, Suggestion, SuggestionFilters, ValidationResult
from app.services import cache_repo
from app.services.movie_filters import UNRELEASED_STATUSES
from app.utils.dates import parse_release_year

TARGET_DECADE_KEY = "target_decade"
MIN_DECADE = 1880


class TrackerEngine(BaseChallengeEngine):
    """Base for modes where films aren't linked by shared cast: a step is legal
    if the film itself satisfies the run's rules. No suggestions, no Bridge
    Solver - the Pick Next hub is replaced by the mode's own UI."""

    supports_json_rules = True
    capabilities: ClassVar[list[str]] = ["validate_next_step", "compute_stats"]

    def bounty_ids(self, rules: dict, history: Sequence[RunStep]) -> list[int] | None:
        track = rules.get("filmography")
        if track is None:
            return super().bounty_ids(rules, history)
        from app.engines.method_actor import marathon_skip

        positions = {film["movie_id"]: index for index, film in enumerate(track)}
        previous = positions.get(history[-1].movie_id, -1) if history else -1
        return [movie_id for movie_id, index in positions.items()
                if marathon_skip(rules) is None or index > previous]

    async def validate_primary(
        self,
        from_movie_id: int,
        to_movie_id: int,
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
    ) -> ValidationResult:
        if rules is None:
            return ValidationResult(valid=True)
        return await self.validate_candidate(to_movie_id, rules)

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

    async def solve_bridge(self, *args, **kwargs) -> AsyncIterator[dict]:
        yield {"type": "error", "message": f"{self.display_name} has no Bridge Solver."}
        yield {"type": "done"}


class DecadeSieveEngine(TrackerEngine):
    """Every film must be released in the run's `target_decade` (e.g. 1970)."""

    game_type = "decade_sieve"
    bounty_reward = "star"

    def bounty_bounds(self, rules: dict, history: Sequence[RunStep]) -> dict:
        decade = rules["target_decade"]
        return {**super().bounty_bounds(rules, history), "year": (decade, decade + 9)}
    tagline = "One decade, no escape"
    tags: ClassVar[list[str]] = ["Any film", "One decade"]
    rulebook: ClassVar[RuleSection] = RuleSection(
        "Explore films released in the {target_decade}s.",
        ["Log any film from the selected decade; no cast connection is required."],
        ["Each watched film adds to your exploration; complete the run manually."],
        ["Films outside the decade cannot be logged."],
        ["Mix genres and countries to make one decade feel expansive.",
         "Check release dates on remakes and reissues."], ["seed"],
    )

    @classmethod
    def rulebook_values(cls, rules: dict | None) -> dict:
        return {**super().rulebook_values(rules), "target_decade": (rules or {}).get("target_decade", 1970)}
    seed_policy = "derived"
    display_name = "Decade Sieve"
    description = (
        "Work through one decade of cinema: only films released in the target decade count."
    )

    def validate_rules_config(self, rules: dict | None) -> list[str]:
        problems = super().validate_rules_config(rules)
        decade = (rules or {}).get(TARGET_DECADE_KEY)
        latest = datetime.now(UTC).year // 10 * 10
        if isinstance(decade, bool) or not isinstance(decade, int):
            problems.append(f"{TARGET_DECADE_KEY} is required (e.g. 1970)")
        elif decade % 10 != 0 or not MIN_DECADE <= decade <= latest:
            problems.append(
                f"{TARGET_DECADE_KEY} must be a decade start between {MIN_DECADE} and {latest}"
            )
        return problems

    async def validate_candidate(self, movie_id: int, rules: dict) -> ValidationResult:
        decade = (rules or {}).get(TARGET_DECADE_KEY)
        if isinstance(decade, bool) or not isinstance(decade, int):
            return ValidationResult(
                valid=False, blocked=True, reason="This run has no target decade configured"
            )
        movie = self.session.get(CachedMovie, movie_id)
        if movie is None:
            movie = await cache_repo.get_movie(self.session, self.tmdb, movie_id)
        year = parse_release_year(movie.release_date)
        if year is not None and decade <= year < decade + 10:
            return ValidationResult(valid=True)
        released = str(year) if year is not None else "no release year"
        return ValidationResult(
            valid=False,
            blocked=True,
            reason=f"Outside the sieve: {movie.title} ({released}) isn't from the {decade}s",
        )

    async def seed_candidates(self, rules: dict) -> list[int]:
        prefix = str(rules[TARGET_DECADE_KEY])[:3]
        return list(
            self.session.exec(
                select(CachedMovie.tmdb_id).where(col(CachedMovie.release_date).like(f"{prefix}%"))
            ).all()
        )


@dataclass
class SpinFilters:
    max_runtime: int | None = None
    min_runtime: int | None = None
    min_rating: float | None = None  # IMDb, from cached OMDb ratings
    max_rating: float | None = None
    genre_id: int | None = None  # legacy single genre; merged into `genre_ids`
    genre_ids: Sequence[int] = ()
    genre_operator: str = "OR"  # AND = every selected genre, OR = any of them
    exclude_movie_ids: Sequence[int] = ()


class RouletteEngine(TrackerEngine):
    """Movie Night Roulette: spin for one random film from the local cache that
    matches your filters. Any film may be logged - the spin is the "pick"."""

    game_type = "roulette"
    bounty_reward = "star"
    tagline = "Let the wheel decide"
    tags: ClassVar[list[str]] = ["Random pick"]
    rulebook: ClassVar[RuleSection] = RuleSection(
        "Let a random draw choose movie night.",
        ["Set filters, spin from the cached matching pool, and log your pick; any film may be logged."],
        ["Watched films record your discoveries; complete the run manually."],
        ["An empty pool needs wider filters, not a wildcard."],
        ["Widen one filter at a time if the wheel runs dry.",
         "Use a blind draft when you want a choice without browsing endlessly."], ["seed"],
    )
    seed_policy = "none"
    display_name = "Movie Night Roulette"
    description = "Can't decide? Set a few filters and spin for a random film from your cache."
    capabilities: ClassVar[list[str]] = [*TrackerEngine.capabilities, "roulette_spin"]

    def _pool(self, filters: SpinFilters):
        today = datetime.now(UTC).date().isoformat()
        statement = select(CachedMovie).where(
            CachedMovie.release_date.is_not(None),  # type: ignore[union-attr]
            CachedMovie.release_date != "",
            CachedMovie.release_date <= today,
            or_(
                CachedMovie.status.is_(None),  # type: ignore[union-attr]
                CachedMovie.status.not_in(UNRELEASED_STATUSES),
            ),  # type: ignore[union-attr]
        )
        if filters.max_runtime is not None:
            statement = statement.where(
                CachedMovie.runtime.is_not(None), CachedMovie.runtime <= filters.max_runtime
            )  # type: ignore[union-attr]
        if filters.min_runtime is not None:
            statement = statement.where(
                CachedMovie.runtime.is_not(None), CachedMovie.runtime >= filters.min_runtime
            )  # type: ignore[union-attr]
        if filters.min_rating is not None or filters.max_rating is not None:
            rating = cast(CachedMovieRating.imdb_rating, Float)
            statement = statement.join(
                CachedMovieRating, CachedMovieRating.movie_id == CachedMovie.tmdb_id
            ).where(
                CachedMovieRating.imdb_rating.is_not(None),  # type: ignore[union-attr]
                CachedMovieRating.imdb_rating != "N/A",
            )
            if filters.min_rating is not None:
                statement = statement.where(rating >= filters.min_rating)
            if filters.max_rating is not None:
                statement = statement.where(rating <= filters.max_rating)
        genre_ids = list(
            dict.fromkeys(
                [*filters.genre_ids, *([filters.genre_id] if filters.genre_id is not None else [])]
            )
        )
        if genre_ids:
            params = {f"genre_{i}": genre for i, genre in enumerate(genre_ids)}
            if filters.genre_operator.upper() == "AND":
                clauses = [
                    text(
                        f"EXISTS (SELECT 1 FROM json_each(cached_movies.genre_ids) AS g "
                        f"WHERE g.value = :{name})"
                    ).bindparams(**{name: value})
                    for name, value in params.items()
                ]
                statement = statement.where(*clauses)
            else:
                placeholders = ", ".join(f":{name}" for name in params)
                statement = statement.where(
                    text(
                        "EXISTS (SELECT 1 FROM json_each(cached_movies.genre_ids) AS g "
                        f"WHERE g.value IN ({placeholders}))"
                    ).bindparams(**params)
                )
        if filters.exclude_movie_ids:
            statement = statement.where(CachedMovie.tmdb_id.not_in(list(filters.exclude_movie_ids)))  # type: ignore[union-attr]
        return statement

    def draw(
        self, filters: SpinFilters, count: int = 1
    ) -> tuple[list[tuple[CachedMovie, str | None]], int] | None:
        """Up to `count` distinct random films as (movie, imdb rating), plus the matching
        pool size; None if nothing matches. `count > 1` powers the Blind Draft."""
        pool = self._pool(filters)
        size = self.session.exec(select(func.count()).select_from(pool.subquery())).one()
        if size == 0:
            return None
        movies = self.session.exec(pool.order_by(func.random()).limit(count)).all()
        if not movies:
            return None
        picks = []
        for movie in movies:
            rating = self.session.get(CachedMovieRating, movie.tmdb_id)
            picks.append((movie, rating.imdb_rating if rating else None))
        return picks, size

    def spin(self, filters: SpinFilters) -> tuple[CachedMovie, str | None, int] | None:
        """(movie, imdb rating, matching pool size), or None if nothing matches."""
        drawn = self.draw(filters, 1)
        if drawn is None:
            return None
        (movie, rating), size = drawn[0][0], drawn[1]
        return movie, rating, size
