"""Historical Time-Travel: march through *the years the stories are set in*.

Like Chrono Climb/Descent, but the clock is each film's narrative setting year (see
`app.services.historical_era`), not its release date: a 2000 film set in 180 AD sits at 180.
Every hop must move strictly forward ("climb", the default) or backward ("descent") through
history. Any film that satisfies the rule may follow; shared cast is an opt-in modifier.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar

from sqlmodel import select

from app.engines import modifiers
from app.engines.mutators import (
    POOL_FETCH_ERRORS,
    RULE_POOL_SIZE,
    MutatorEngine,
    candidate_from_row,
    today_iso,
)
from app.engines.rulebook import RuleSection
from app.models.cache import CachedMovie
from app.models.run import RunStep
from app.schemas.discovery import DiscoveryCandidate
from app.schemas.engine import ConstraintInfo, ValidationResult
from app.services import cache_repo, historical_era, llm
from app.services.movie_filters import is_reality_eligible
from app.utils.dates import parse_release_year

HISTORICAL_TIME_TRAVEL = "historical_time_travel"
DIRECTIONS = modifiers.CHRONO_DIRECTIONS
# Films whose era is resolved (a keyword call each) per Pick Next request.
POOL_ERA_BUDGET = 16
# Cached, already-dated films considered per request.
CACHED_POOL_SIZE = 200
# Eras beyond the frontier whose films are fetched by keyword so the pool isn't all "Contemporary".
TARGET_ERAS = 2


def format_year(year: int) -> str:
    """180 -> "180 AD", -400 -> "400 BC"; modern years stay bare ("1945")."""
    if year < 0:
        return f"{-year} BC"
    return f"{year} AD" if year < 1000 else str(year)


class HistoricalTimeTravelEngine(MutatorEngine):
    tagline = "Travel through the eras stories are set in"
    tags: ClassVar[list[str]] = ["Any film", "Setting year", "Forward / Backward"]
    rulebook: ClassVar[RuleSection] = RuleSection(
        "Travel through story settings, not release dates.",
        ["Pick a film set strictly {setting_direction} the current film; inspect or edit uncertain setting years."],
        ["{win_goal}"], ["Equal or wrong-direction settings block the hop.", "{fail_goal}"],
        ["Small era jumps leave more history available for future moves.",
         "Check inferred settings before making a large leap."], ["seed"],
    )

    @classmethod
    def rulebook_values(cls, rules: dict | None) -> dict[str, Any]:
        return {**super().rulebook_values(rules),
                "setting_direction": "before" if cls.direction(rules) == "descent" else "after"}
    game_type = HISTORICAL_TIME_TRAVEL
    display_name = "Historical Time-Travel"
    description = (
        "Travel through history: every film must be set later (Forward) or earlier (Backward) "
        "than the last - by the year its story takes place, not its release date. Any film "
        "counts - no shared cast needed."
    )
    needs_detail = True  # eras are read from the plot overview
    optional_cast_link = True
    default_modifiers: ClassVar[dict[str, Any]] = {}

    @staticmethod
    def direction(rules: dict | None) -> str:
        value = (rules or {}).get("direction")
        return value if value in DIRECTIONS else "climb"

    def validate_rules_config(self, rules: dict | None) -> list[str]:
        problems = super().validate_rules_config(rules)
        direction = (rules or {}).get("direction")
        if direction is not None and direction not in DIRECTIONS:
            problems.append("direction must be 'climb' or 'descent'")
        return problems

    def active_modifiers(self, rules: dict | None) -> dict[str, Any]:
        # `direction` is this mode's own rule; the shared modifier pipeline would otherwise read
        # it as the pre-V3 spelling of `chrono_direction` and add a release-year rule on top.
        return super().active_modifiers(
            {k: v for k, v in (rules or {}).items() if k != modifiers.LEGACY_CHRONO_KEY}
        )

    def _needs_hydration(self, row: CachedMovie, rules: dict | None = None) -> bool:
        return row.overview is None or self._modifiers_need_detail(row, rules)

    async def prepare(self, movies: Sequence[CachedMovie], *, allow_llm: bool = True) -> None:
        """Resolve (and save) the narrative era of these films."""
        config = llm.load_config(self.session)
        for movie in movies:
            await historical_era.ensure_narrative_era(
                self.session, self.tmdb, movie, config, allow_llm=allow_llm
            )

    def _setting(self, movie: CachedMovie) -> tuple[int, str]:
        return historical_era.effective_era(movie)

    def pair_violation(
        self, earlier: CachedMovie, later: CachedMovie, rules: dict | None = None
    ) -> str | None:
        year_a, label_a = self._setting(earlier)
        year_b, label_b = self._setting(later)
        if self.direction(rules) == "descent":
            if year_b < year_a:
                return None
            return (
                f"Historical Time-Travel: {later.title} is set in {format_year(year_b)} "
                f"({label_b}) - the next film must be set before {format_year(year_a)} ({label_a})"
            )
        if year_b > year_a:
            return None
        return (
            f"Historical Time-Travel: {later.title} is set in {format_year(year_b)} "
            f"({label_b}) - the next film must be set after {format_year(year_a)} ({label_a})"
        )

    def mechanic(
        self, earlier: CachedMovie, later: CachedMovie, rules: dict | None = None
    ) -> dict | None:
        year_a, _ = self._setting(earlier)
        year_b, label_b = self._setting(later)
        return {
            "narrative_delta": year_b - year_a,
            "narrative_year": year_b,
            "narrative_era_label": label_b,
            "direction": self.direction(rules),
        }

    async def validate_candidate(self, movie_id: int, rules: dict) -> ValidationResult:
        # No rule applies to a first film, but its era anchors the next hop.
        await self.prepare([await self._load(movie_id, hydrate=True)])
        return ValidationResult(valid=True)

    async def validate_primary(
        self,
        from_movie_id: int,
        to_movie_id: int,
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
    ) -> ValidationResult:
        earlier = await self._load(from_movie_id, hydrate=True)
        later = await self._load(to_movie_id, hydrate=True)
        await self.prepare([earlier, later])
        return await super().validate_primary(
            from_movie_id, to_movie_id, cast_limit, rules, previous_transition
        )

    async def describe_constraint(
        self,
        tail_movie_id: int | None,
        previous_transition: dict | None,
        rules: dict | None = None,
    ) -> ConstraintInfo | None:
        descent = self.direction(rules) == "descent"
        word, name = ("before", "Backward") if descent else ("after", "Forward")
        if tail_movie_id is None:
            return ConstraintInfo(
                kind="year",
                title=f"Any film starts the {name.lower()} journey",
                detail=f"Every later film must be set {word} the one before it.",
            )
        tail = await self._load(tail_movie_id, hydrate=True)
        await self.prepare([tail])
        year, label = self._setting(tail)
        return ConstraintInfo(
            kind="year",
            title=f"Next film must be set {word} {format_year(year)} ({label})",
            detail="The year the story takes place - not the release year. "
            "Strictly " + ("earlier" if descent else "later") + "; the same year doesn't count.",
        )

    def _annotate(
        self, candidate: DiscoveryCandidate, row: CachedMovie, frontier_year: int
    ) -> None:
        year, label = self._setting(row)
        candidate.narrative_year = year
        candidate.narrative_era_label = label
        candidate.narrative_delta = year - frontier_year
        candidate.constraint_unverified = row.narrative_year is None

    async def _era_pool(self, frontier_year: int, descent: bool) -> list[CachedMovie]:
        """Fresh films from the eras just beyond the frontier, found by TMDB keyword."""
        ahead = sorted(
            {
                (year, label, slug)
                for slug, (year, label) in historical_era.TAXONOMY.items()
                if (year < frontier_year if descent else year > frontier_year)
            },
            key=lambda e: -e[0] if descent else e[0],
        )
        seen_labels: set[str] = set()
        rows: list[CachedMovie] = []
        for _, label, slug in ahead:
            if label in seen_labels:
                continue
            seen_labels.add(label)
            if len(seen_labels) > TARGET_ERAS:
                break
            try:
                ids = await self.tmdb.find_keyword_ids(slug.replace("-", " "))
                if not ids:
                    continue
                rows.extend(
                    await cache_repo.discover_movies(
                        self.session,
                        self.tmdb,
                        pages=1,
                        with_keywords="|".join(str(i) for i in ids),
                        **{"primary_release_date.lte": today_iso()},
                    )
                )
            except POOL_FETCH_ERRORS:
                continue
        return rows

    async def discover_rule_candidates(
        self,
        frontier: CachedMovie,
        rules: dict | None,
        history: Sequence[RunStep] | None = None,
    ) -> list[DiscoveryCandidate]:
        await self.prepare([frontier])
        frontier_year, _ = self._setting(frontier)
        descent = self.direction(rules) == "descent"
        pool: dict[int, CachedMovie] = {}

        # Films already dated from earlier play, anywhere in the valid span.
        statement = select(CachedMovie).where(CachedMovie.narrative_year.is_not(None))  # type: ignore[union-attr]
        statement = statement.where(
            CachedMovie.narrative_year < frontier_year
            if descent
            else CachedMovie.narrative_year > frontier_year
        )
        statement = statement.order_by(CachedMovie.popularity.desc()).limit(CACHED_POOL_SIZE)  # type: ignore[union-attr]
        for row in self.session.exec(statement).all():
            pool[row.tmdb_id] = row

        # Plus fresh films: the eras just beyond the frontier, and popular ones from its own time.
        fresh = await self._era_pool(frontier_year, descent)
        release = parse_release_year(frontier.release_date)
        if release is not None:
            try:
                fresh.extend(
                    await cache_repo.discover_movies(
                        self.session,
                        self.tmdb,
                        pages=1,
                        **{
                            "primary_release_date.gte": f"{release - 10:04d}-01-01",
                            "primary_release_date.lte": min(
                                f"{release + 10:04d}-12-31", today_iso()
                            ),
                        },
                    )
                )
            except POOL_FETCH_ERRORS:
                pass
        fresh = sorted(
            {r.tmdb_id: r for r in fresh if r.tmdb_id not in pool}.values(),
            key=lambda r: -(r.popularity or 0.0),
        )[:POOL_ERA_BUDGET]
        await historical_era.ensure_narrative_eras(
            self.session, self.tmdb, fresh, llm.load_config(self.session), allow_llm=False
        )
        pool.update({r.tmdb_id: r for r in fresh})

        candidates: list[DiscoveryCandidate] = []
        for row in pool.values():
            if (
                row.tmdb_id == frontier.tmdb_id
                or not is_reality_eligible(row)
                or self._pair_blocked(frontier, row, rules, history)
            ):
                continue
            candidate = candidate_from_row(row)
            self._annotate(candidate, row, frontier_year)
            candidates.append(candidate)
        # Shortest leap through time first, then most popular.
        candidates.sort(key=lambda c: (abs(c.narrative_delta or 0), -(c.popularity or 0.0)))
        return candidates[:RULE_POOL_SIZE]

    async def _filter_pool(
        self,
        frontier: CachedMovie,
        candidates: list[DiscoveryCandidate],
        rules: dict | None = None,
    ) -> list[DiscoveryCandidate]:
        """Hybrid (cast-linked) pool: dated, kept to the valid direction and annotated."""
        await self.prepare([frontier])
        frontier_year, _ = self._setting(frontier)
        rows = await self._hydrate_pool(candidates, rules)
        await historical_era.ensure_narrative_eras(
            self.session,
            self.tmdb,
            list(rows.values())[:POOL_ERA_BUDGET],
            llm.load_config(self.session),
            allow_llm=False,
        )
        kept = []
        for candidate in candidates:
            row = rows.get(candidate.movie_id)
            if row is None or self.pair_violation(frontier, row, rules):
                continue
            self._annotate(candidate, row, frontier_year)
            kept.append(candidate)
        return kept
