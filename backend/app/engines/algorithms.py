"""Algorithm Sandbox engines: standalone modes with a *computed* rule between hops.

- Aesthetic Gradient: consecutive posters must have similar dominant colours.
- Semantic Trope Web: consecutive plot overviews must be semantically close, or the two films
  must share a discrete LLM-extracted trope ("heist", "time-loop").

Both derive a per-film feature lazily (see `movie_features`), persist it on the
movie cache and enforce the rule as a hard block, like the other mutators. A film
whose feature can't be computed (no poster / no overview / model offline) is
"unverified" and never blocks. Any film satisfying the rule may follow; a shared
cast link is an opt-in modifier (`rules_config.require_cast_link`).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

from sqlalchemy import or_
from sqlmodel import col, select

from app.engines.cinechain import CineChainEngine
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
from app.services import cache_repo, embeddings, movie_features
from app.services.aesthetic import color_distance
from app.services.movie_filters import is_reality_eligible
from app.utils.dates import parse_release_year

# Max Euclidean RGB distance (0..~442) between consecutive posters' dominant colours.
COLOR_DISTANCE_THRESHOLD = 100.0
# Min cosine similarity between consecutive overviews.
SEMANTIC_SIMILARITY_THRESHOLD = 0.5
# Pool films judged per request: bounds poster downloads / model work.
POOL_FEATURE_BUDGET = 80
# Pool films whose tropes are extracted per Pick Next request (each costs an LLM call).
POOL_TROPE_BUDGET = 8


class FeatureEngine(MutatorEngine):
    """Shared plumbing: subclasses define `measure`, `violation` and `prepare`."""

    capabilities: ClassVar[list[str]] = [
        cap for cap in MutatorEngine.capabilities if cap != "solve_bridge"
    ]
    optional_cast_link = True

    def measure(self, earlier: CachedMovie, later: CachedMovie) -> float | None:
        """The pair's closeness metric from cached features; None = can't tell."""
        raise NotImplementedError

    def violation(self, earlier: CachedMovie, later: CachedMovie, metric: float) -> str | None:
        raise NotImplementedError

    async def prepare(self, movies: list[CachedMovie]) -> None:
        """Compute and persist any missing features for these films."""
        raise NotImplementedError

    def annotate(
        self, candidate: DiscoveryCandidate, row: CachedMovie, metric: float | None
    ) -> None:
        raise NotImplementedError

    def with_metric(self, result: ValidationResult, metric: float | None) -> ValidationResult:
        raise NotImplementedError

    def pair_violation(
        self, earlier: CachedMovie, later: CachedMovie, rules: dict | None = None
    ) -> str | None:
        metric = self.measure(earlier, later)
        return None if metric is None else self.violation(earlier, later, metric)

    async def validate_candidate(self, movie_id: int, rules: dict) -> ValidationResult:
        # No rule applies to a first film, but it still needs its features for the next hop.
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
        metric = self.measure(earlier, later)
        reason = None if metric is None else self.violation(earlier, later, metric)
        if reason:
            return self.with_metric(
                ValidationResult(valid=False, blocked=True, reason=reason), metric
            )
        if not self.cast_link_required(rules):
            return self.with_metric(ValidationResult(valid=True), metric)
        result = await CineChainEngine.validate_primary(
            self, from_movie_id, to_movie_id, cast_limit=cast_limit
        )
        return self.with_metric(result, metric)

    def link_metadata(self, result: ValidationResult, client_metadata: dict | None) -> dict | None:
        meta = dict(client_metadata or {})
        for key, value in self.metadata_fields(result).items():
            if value is None:
                meta.pop(key, None)
            else:
                meta[key] = value
        return meta or None

    def metadata_fields(self, result: ValidationResult) -> dict:
        return {}

    async def discover_candidates(
        self,
        frontier_movie_id: int,
        mode: str = "or",
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
        history: Sequence[RunStep] | None = None,
    ) -> list[DiscoveryCandidate]:
        if not self.cast_link_required(rules):
            return await super().discover_candidates(
                frontier_movie_id, mode, cast_limit, rules, previous_transition, history
            )
        candidates = await CineChainEngine.discover_candidates(
            self, frontier_movie_id, mode, cast_limit
        )
        frontier = await self._load(frontier_movie_id, hydrate=True)
        return await self._filter_pool(frontier, candidates, rules)

    async def _filter_pool(
        self,
        frontier: CachedMovie,
        candidates: list[DiscoveryCandidate],
        rules: dict | None = None,
    ) -> list[DiscoveryCandidate]:
        rows = await self._hydrate_pool(candidates)
        by_popularity = sorted(candidates, key=lambda c: -(c.popularity or 0.0))[
            :POOL_FEATURE_BUDGET
        ]
        await self.prepare(
            [frontier, *(rows[c.movie_id] for c in by_popularity if c.movie_id in rows)]
        )
        keep: set[int] = set()
        for candidate in candidates:
            row = rows.get(candidate.movie_id)
            if row is None:
                continue
            metric = self.measure(frontier, row)
            if metric is not None and self.violation(frontier, row, metric):
                continue
            self.annotate(candidate, row, metric)
            candidate.constraint_unverified = metric is None
            keep.add(candidate.movie_id)
        return [c for c in candidates if c.movie_id in keep]

    def _scored_candidates(
        self, frontier: CachedMovie, rows: list[CachedMovie], best_first: str
    ) -> list[DiscoveryCandidate]:
        """Rule-satisfying films as annotated candidates, closest match first."""
        scored: list[tuple[float, DiscoveryCandidate]] = []
        for row in rows:
            if row.tmdb_id == frontier.tmdb_id or not is_reality_eligible(row):
                continue
            metric = self.measure(frontier, row)
            if metric is None or self.violation(frontier, row, metric):
                continue
            candidate = candidate_from_row(row)
            self.annotate(candidate, row, metric)
            scored.append((metric if best_first == "low" else -metric, candidate))
        scored.sort(key=lambda pair: (pair[0], -(pair[1].popularity or 0.0)))
        return [candidate for _, candidate in scored[:RULE_POOL_SIZE]]


class AestheticGradientEngine(FeatureEngine):
    """Consecutive films must have visually similar posters (dominant colour)."""

    game_type = "aesthetic_gradient"
    tagline = "Fade poster to poster"
    tags: ClassVar[list[str]] = ["Any film", "Poster colour"]
    rulebook: ClassVar[RuleSection] = RuleSection(
        "Create a visual gradient from movie posters.",
        ["Choose a film with a nearby dominant poster colour; shared cast is optional."],
        ["Colour distance must be at most {color_threshold}. {win_goal}"],
        ["A measured colour gap above the threshold blocks the hop.", "{fail_goal}"],
        [
            "Use intermediate colours instead of jumping across the palette.",
            "Inspect the swatches, not just the artwork's subject.",
        ],
        ["seed"],
    )

    @classmethod
    def rulebook_values(cls, rules: dict | None) -> dict:
        return {**super().rulebook_values(rules), "color_threshold": COLOR_DISTANCE_THRESHOLD}

    display_name = "Aesthetic Gradient"
    description = (
        "Fade from poster to poster: every film's dominant colour must be close to the last "
        "one's. Any film counts - no shared cast needed."
    )

    def measure(self, earlier: CachedMovie, later: CachedMovie) -> float | None:
        if not earlier.dominant_color or not later.dominant_color:
            return None
        return color_distance(earlier.dominant_color, later.dominant_color)

    def violation(self, earlier: CachedMovie, later: CachedMovie, metric: float) -> str | None:
        if metric <= COLOR_DISTANCE_THRESHOLD:
            return None
        return (
            f"Aesthetic Gradient: {later.title}'s poster ({later.dominant_color}) is too far "
            f"from {earlier.title}'s ({earlier.dominant_color}) - colour distance "
            f"{metric:.0f} exceeds {COLOR_DISTANCE_THRESHOLD:.0f}"
        )

    async def prepare(self, movies: list[CachedMovie]) -> None:
        await movie_features.ensure_dominant_colors(self.session, self.tmdb, movies)

    def annotate(
        self, candidate: DiscoveryCandidate, row: CachedMovie, metric: float | None
    ) -> None:
        candidate.dominant_color = row.dominant_color

    def with_metric(self, result: ValidationResult, metric: float | None) -> ValidationResult:
        result.color_distance = None if metric is None else round(metric, 1)
        return result

    def metadata_fields(self, result: ValidationResult) -> dict:
        return {"color_distance": result.color_distance}

    async def discover_rule_candidates(
        self,
        frontier: CachedMovie,
        rules: dict | None,
        history: Sequence[RunStep] | None = None,
    ) -> list[DiscoveryCandidate]:
        await self.prepare([frontier])
        if not frontier.dominant_color:
            return []
        pool: dict[int, CachedMovie] = {}

        # Films whose colour is already known from earlier play.
        for row in self.session.exec(
            select(CachedMovie).where(CachedMovie.dominant_color.is_not(None))  # type: ignore[union-attr]
        ).all():
            pool[row.tmdb_id] = row

        # Plus popular films from the same era and genres, coloured on the fly.
        year = parse_release_year(frontier.release_date)
        params: dict = {"primary_release_date.lte": today_iso()}
        if year is not None:
            params["primary_release_date.gte"] = f"{year - 10:04d}-01-01"
            params["primary_release_date.lte"] = min(f"{year + 10:04d}-12-31", today_iso())
        if frontier.genre_ids:
            params["with_genres"] = "|".join(str(g) for g in frontier.genre_ids[:3])
        try:
            fresh = await cache_repo.discover_movies(self.session, self.tmdb, pages=2, **params)
        except POOL_FETCH_ERRORS:
            fresh = []
        fresh = [r for r in fresh if r.tmdb_id not in pool]
        await self.prepare(
            sorted(fresh, key=lambda r: -(r.popularity or 0.0))[:POOL_FEATURE_BUDGET]
        )
        pool.update({r.tmdb_id: r for r in fresh})
        return self._scored_candidates(frontier, list(pool.values()), best_first="low")

    async def describe_constraint(
        self,
        tail_movie_id: int | None,
        previous_transition: dict | None,
        rules: dict | None = None,
    ) -> ConstraintInfo | None:
        if tail_movie_id is None:
            return ConstraintInfo(
                kind="color",
                title="Any film starts the gradient",
                detail="Every later poster must be a similar colour to the one before it - or share a trope with it.",
            )
        tail = await self._load(tail_movie_id)
        await self.prepare([tail])
        if not tail.dominant_color:
            return ConstraintInfo(
                kind="color",
                title="Next poster must be a similar colour",
                detail="The last poster's colour couldn't be read, so it can't be checked.",
            )
        return ConstraintInfo(
            kind="color",
            title=f"Next poster must be close to {tail.dominant_color}",
            detail=f"Colour distance of at most {COLOR_DISTANCE_THRESHOLD:.0f} (RGB, 0-442).",
        )


def shared_trope(earlier: CachedMovie, later: CachedMovie) -> str | None:
    """A discrete trope both films carry (in the earlier film's order), or None."""
    theirs = set(later.extracted_tropes or [])
    return next((t for t in earlier.extracted_tropes or [] if t in theirs), None)


class SemanticTropeEngine(FeatureEngine):
    """Consecutive films must have semantically similar plots (overview embeddings) or share
    at least one discrete trope (LLM-extracted, kebab-case)."""

    game_type = "semantic_trope"
    tagline = "Follow the plot, not the cast"
    tags: ClassVar[list[str]] = ["Any film", "Plot similarity"]
    rulebook: ClassVar[RuleSection] = RuleSection(
        "Link films with plots or themes in common.",
        ["Pick a shared theme or a plot match above {similarity_threshold}%."],
        ["{win_goal}"],
        ["A measured weak match with no shared trope blocks the hop.", "{fail_goal}"],
        [
            "Broad themes give more onward routes than a single narrow premise.",
            "Shared themes need matching genres and a plot fit of at least {trope_threshold}%.",
            "Check the theme and plot. Suggested tags can be wrong.",
        ],
        ["seed"],
    )

    @classmethod
    def rulebook_values(cls, rules: dict | None) -> dict:
        return {
            **super().rulebook_values(rules),
            "similarity_threshold": _percent(SEMANTIC_SIMILARITY_THRESHOLD),
            "trope_threshold": _percent(movie_features.TROPE_CONFIDENCE_THRESHOLD),
        }

    display_name = "Semantic Trope Web"
    description = (
        "Follow the plot: every film must be a close semantic match to the last, judged by a "
        "small on-device language model - or share a trope with it (heist, time-loop...). "
        "Any film counts - no shared cast needed."
    )
    needs_detail = True  # overviews come from the full TMDB detail

    def _shared_trope(self, earlier: CachedMovie, later: CachedMovie) -> str | None:
        verified = getattr(self, "_verified_tropes", {})
        theirs = set(verified.get(later.tmdb_id, []))
        return next((tag for tag in verified.get(earlier.tmdb_id, []) if tag in theirs), None)

    def _needs_hydration(self, row: CachedMovie, rules: dict | None = None) -> bool:
        return row.overview is None or self._modifiers_need_detail(row, rules)

    def measure(self, earlier: CachedMovie, later: CachedMovie) -> float | None:
        vector_a = embeddings.decode_embedding(earlier.overview_embedding)
        vector_b = embeddings.decode_embedding(later.overview_embedding)
        if vector_a is None or vector_b is None:
            return None
        # Different models (or widths) live in different vector spaces: can't tell.
        fingerprint = embeddings.row_fingerprint(earlier.overview_embedding_model)
        if (
            fingerprint != embeddings.row_fingerprint(later.overview_embedding_model)
            or vector_a.shape != vector_b.shape
        ):
            return None
        return embeddings.normalize_similarity(
            embeddings.cosine_similarity(vector_a, vector_b), fingerprint
        )

    def violation(self, earlier: CachedMovie, later: CachedMovie, metric: float) -> str | None:
        if metric > SEMANTIC_SIMILARITY_THRESHOLD or self._shared_trope(earlier, later):
            return None
        return (
            f"Semantic Trope Web: {later.title} is only a {_percent(metric)}% plot match for "
            f"{earlier.title} and they share no trope - it needs more than "
            f"{_percent(SEMANTIC_SIMILARITY_THRESHOLD)}% or a common trope"
        )

    async def prepare(self, movies: list[CachedMovie]) -> None:
        await movie_features.ensure_embeddings(self.session, movies)
        await self.prepare_tropes([movie for movie in movies if movie.extracted_tropes is not None])

    async def prepare_tropes(self, movies: Sequence[CachedMovie]) -> None:
        """Extract missing tropes (an LLM call each, so only for the films that matter)."""
        if not hasattr(self, "_verified_tropes"):
            self._verified_tropes: dict[int, list[str]] = {}
            self._trope_sources: dict[int, tuple] = {}

        def source(movie: CachedMovie) -> tuple:
            return movie.overview, tuple(movie.genre_ids or []), tuple(movie.extracted_tropes or [])

        pending = [
            movie for movie in movies if self._trope_sources.get(movie.tmdb_id) != source(movie)
        ]
        verified = await movie_features.ensure_tropes(self.session, pending)
        self._verified_tropes.update(verified)
        self._trope_sources.update({movie.tmdb_id: source(movie) for movie in pending})

    async def validate_candidate(self, movie_id: int, rules: dict) -> ValidationResult:
        result = await super().validate_candidate(movie_id, rules)
        await self.prepare_tropes([await self._load(movie_id, hydrate=True)])
        return result

    async def validate_primary(
        self,
        from_movie_id: int,
        to_movie_id: int,
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
    ) -> ValidationResult:
        # A hop is valid on either plot similarity or a shared trope (see `violation`).
        earlier = await self._load(from_movie_id, hydrate=True)
        later = await self._load(to_movie_id, hydrate=True)
        await self.prepare_tropes([earlier, later])
        result = await super().validate_primary(
            from_movie_id,
            to_movie_id,
            cast_limit=cast_limit,
            rules=rules,
            previous_transition=previous_transition,
        )
        if result.valid:
            result.shared_trope = self._shared_trope(earlier, later)
        return result

    def annotate(
        self, candidate: DiscoveryCandidate, row: CachedMovie, metric: float | None
    ) -> None:
        candidate.semantic_score = None if metric is None else max(0.0, round(metric, 4))
        candidate.tropes = list(getattr(self, "_verified_tropes", {}).get(row.tmdb_id, []))

    def with_metric(self, result: ValidationResult, metric: float | None) -> ValidationResult:
        result.similarity = None if metric is None else round(metric, 4)
        return result

    def metadata_fields(self, result: ValidationResult) -> dict:
        return {"semantic_score": result.similarity, "shared_trope": result.shared_trope}

    async def _filter_pool(
        self,
        frontier: CachedMovie,
        candidates: list[DiscoveryCandidate],
        rules: dict | None = None,
    ) -> list[DiscoveryCandidate]:
        await self.prepare_tropes([frontier])
        return await super()._filter_pool(frontier, candidates, rules)

    def _scored_candidates(
        self, frontier: CachedMovie, rows: list[CachedMovie], best_first: str
    ) -> list[DiscoveryCandidate]:
        """Films that match the plot or share a trope, closest plot first (films whose plot
        can't be measured but share a trope still qualify)."""
        scored: list[tuple[float, DiscoveryCandidate]] = []
        for row in rows:
            if row.tmdb_id == frontier.tmdb_id or not is_reality_eligible(row):
                continue
            metric = self.measure(frontier, row)
            linked = self._shared_trope(frontier, row) is not None
            if metric is None and not linked:
                continue
            if metric is not None and self.violation(frontier, row, metric):
                continue
            candidate = candidate_from_row(row)
            self.annotate(candidate, row, metric)
            rank = metric if metric is not None else SEMANTIC_SIMILARITY_THRESHOLD
            scored.append((-rank, candidate))
        scored.sort(key=lambda pair: (pair[0], -(pair[1].popularity or 0.0)))
        return [candidate for _, candidate in scored[:RULE_POOL_SIZE]]

    async def discover_rule_candidates(
        self,
        frontier: CachedMovie,
        rules: dict | None,
        history: Sequence[RunStep] | None = None,
    ) -> list[DiscoveryCandidate]:
        await self.prepare([frontier])
        await self.prepare_tropes([frontier])
        if (
            embeddings.decode_embedding(frontier.overview_embedding) is None
            and not frontier.extracted_tropes
        ):
            return []
        pool: dict[int, CachedMovie] = {}

        # Films already embedded (by the same model as the frontier) from earlier play.
        fingerprint = embeddings.row_fingerprint(frontier.overview_embedding_model)
        statement = select(CachedMovie).where(CachedMovie.overview_embedding.is_not(None))  # type: ignore[union-attr]
        if fingerprint == embeddings.LOCAL_FINGERPRINT:
            statement = statement.where(
                or_(
                    CachedMovie.overview_embedding_model.is_(None),  # type: ignore[union-attr]
                    col(CachedMovie.overview_embedding_model) == fingerprint,
                )
            )
        else:
            statement = statement.where(CachedMovie.overview_embedding_model == fingerprint)
        for row in self.session.exec(statement).all():
            pool[row.tmdb_id] = row

        # Plus TMDB's recommended / similar titles, read and embedded on the fly.
        try:
            related = await cache_repo.get_related_movies(self.session, self.tmdb, frontier.tmdb_id)
        except POOL_FETCH_ERRORS:
            related = []
        stubs = [candidate_from_row(r) for r in related if r.tmdb_id not in pool]
        rows = await self._hydrate_pool(stubs)
        ranked = sorted(rows.values(), key=lambda r: -(r.popularity or 0.0))
        await self.prepare(ranked[:POOL_FEATURE_BUDGET])
        pool.update(rows)

        # Films sharing one of the frontier's tropes qualify even without a close plot match.
        if frontier.extracted_tropes:
            tagged = self.session.exec(
                select(CachedMovie)
                .where(col(CachedMovie.extracted_tropes).is_not(None))
                .order_by(col(CachedMovie.popularity).desc())
                .limit(POOL_FEATURE_BUDGET)
            ).all()
            await self.prepare_tropes(tagged)
            pool.update({r.tmdb_id: r for r in tagged if self._shared_trope(frontier, r)})
            by_popularity = sorted(
                (r for r in pool.values() if r.extracted_tropes is None and r.overview),
                key=lambda r: -(r.popularity or 0.0),
            )
            await self.prepare_tropes(by_popularity[:POOL_TROPE_BUDGET])
        return self._scored_candidates(frontier, list(pool.values()), best_first="high")

    async def describe_constraint(
        self,
        tail_movie_id: int | None,
        previous_transition: dict | None,
        rules: dict | None = None,
    ) -> ConstraintInfo | None:
        floor = f"more than {_percent(SEMANTIC_SIMILARITY_THRESHOLD)}%"
        if tail_movie_id is None:
            return ConstraintInfo(
                kind="semantic",
                title="Any film starts the web",
                detail=f"Every later film's plot must be a semantic match of {floor} to the one before it.",
            )
        return ConstraintInfo(
            kind="semantic",
            title="Next film must match the plot or share a trope",
            detail=f"Plot similarity {floor} to the last film's overview, or at least one trope in common.",
        )


def _percent(similarity: float) -> int:
    return round(max(0.0, similarity) * 100)
