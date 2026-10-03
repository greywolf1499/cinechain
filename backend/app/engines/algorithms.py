"""Algorithm Sandbox engines: shared-cast chains with a *computed* rule between hops.

- Aesthetic Gradient: consecutive posters must have similar dominant colours.
- Semantic Trope Web: consecutive plot overviews must be semantically close.

Both derive a per-film feature lazily (see `movie_features`), persist it on the
movie cache and enforce the rule as a hard block, like the other mutators. A film
whose feature can't be computed (no poster / no overview / model offline) is
"unverified" and never blocks.
"""

from __future__ import annotations

from typing import ClassVar

from app.engines.cinechain import CineChainEngine
from app.engines.mutators import MutatorEngine
from app.models.cache import CachedMovie
from app.schemas.discovery import DiscoveryCandidate
from app.schemas.engine import ConstraintInfo, ValidationResult
from app.services import embeddings, movie_features
from app.services.aesthetic import color_distance

# Max Euclidean RGB distance (0..~442) between consecutive posters' dominant colours.
COLOR_DISTANCE_THRESHOLD = 100.0
# Min cosine similarity between consecutive overviews.
SEMANTIC_SIMILARITY_THRESHOLD = 0.5
# Pool films judged per request: bounds poster downloads / model work.
POOL_FEATURE_BUDGET = 80


class FeatureEngine(MutatorEngine):
    """Shared plumbing: subclasses define `measure`, `violation` and `prepare`."""

    capabilities: ClassVar[list[str]] = [
        cap for cap in MutatorEngine.capabilities if cap != "solve_bridge"
    ]

    def measure(self, earlier: CachedMovie, later: CachedMovie) -> float | None:
        """The pair's closeness metric from cached features; None = can't tell."""
        raise NotImplementedError

    def violation(self, earlier: CachedMovie, later: CachedMovie, metric: float) -> str | None:
        raise NotImplementedError

    async def prepare(self, movies: list[CachedMovie]) -> None:
        """Compute and persist any missing features for these films."""
        raise NotImplementedError

    def annotate(self, candidate: DiscoveryCandidate, row: CachedMovie, metric: float | None) -> None:
        raise NotImplementedError

    def with_metric(self, result: ValidationResult, metric: float | None) -> ValidationResult:
        raise NotImplementedError

    def pair_violation(self, earlier: CachedMovie, later: CachedMovie) -> str | None:
        metric = self.measure(earlier, later)
        return None if metric is None else self.violation(earlier, later, metric)

    async def validate_candidate(self, movie_id: int, rules: dict) -> ValidationResult:
        # No rule applies to a first film, but it still needs its features for the next hop.
        await self.prepare([await self._load(movie_id, hydrate=True)])
        return ValidationResult(valid=True)

    async def validate_next_step(
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
                ValidationResult(valid=False, blocked=True, reason=reason), metric)
        result = await CineChainEngine.validate_next_step(
            self, from_movie_id, to_movie_id, cast_limit=cast_limit)
        return self.with_metric(result, metric)

    def link_metadata(
        self, result: ValidationResult, client_metadata: dict | None
    ) -> dict | None:
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
    ) -> list[DiscoveryCandidate]:
        candidates = await CineChainEngine.discover_candidates(
            self, frontier_movie_id, mode, cast_limit)
        frontier = await self._load(frontier_movie_id, hydrate=True)
        return await self._filter_pool(frontier, candidates)

    async def _filter_pool(
        self, frontier: CachedMovie, candidates: list[DiscoveryCandidate]
    ) -> list[DiscoveryCandidate]:
        rows = await self._hydrate_pool(candidates)
        by_popularity = sorted(
            candidates, key=lambda c: -(c.popularity or 0.0))[:POOL_FEATURE_BUDGET]
        await self.prepare([frontier, *(
            rows[c.movie_id] for c in by_popularity if c.movie_id in rows)])
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


class AestheticGradientEngine(FeatureEngine):
    """Consecutive films must have visually similar posters (dominant colour)."""

    game_type = "aesthetic_gradient"
    display_name = "Aesthetic Gradient"
    description = (
        "Shared-cast chain where every poster's dominant colour must be close to the last one's, "
        "so the run fades smoothly through a colour gradient."
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

    def annotate(self, candidate: DiscoveryCandidate, row: CachedMovie, metric: float | None) -> None:
        candidate.dominant_color = row.dominant_color

    def with_metric(self, result: ValidationResult, metric: float | None) -> ValidationResult:
        result.color_distance = None if metric is None else round(metric, 1)
        return result

    def metadata_fields(self, result: ValidationResult) -> dict:
        return {"color_distance": result.color_distance}

    async def describe_constraint(
        self, tail_movie_id: int | None, previous_transition: dict | None
    ) -> ConstraintInfo | None:
        if tail_movie_id is None:
            return ConstraintInfo(
                kind="color", title="Any film starts the gradient",
                detail="Every later poster must be a similar colour to the one before it.")
        tail = await self._load(tail_movie_id)
        await self.prepare([tail])
        if not tail.dominant_color:
            return ConstraintInfo(
                kind="color", title="Next poster must be a similar colour",
                detail="The last poster's colour couldn't be read, so it can't be checked.")
        return ConstraintInfo(
            kind="color", title=f"Next poster must be close to {tail.dominant_color}",
            detail=f"Colour distance of at most {COLOR_DISTANCE_THRESHOLD:.0f} (RGB, 0-442).")


class SemanticTropeEngine(FeatureEngine):
    """Consecutive films must have semantically similar plots (overview embeddings)."""

    game_type = "semantic_trope"
    display_name = "Semantic Trope Web"
    description = (
        "Shared-cast chain where every film's plot must be a close semantic match to the last "
        "film's, judged by a small on-device language model."
    )
    needs_detail = True  # overviews come from the full TMDB detail

    def _needs_hydration(self, row: CachedMovie) -> bool:
        return row.overview is None

    def measure(self, earlier: CachedMovie, later: CachedMovie) -> float | None:
        vector_a = embeddings.decode_embedding(earlier.overview_embedding)
        vector_b = embeddings.decode_embedding(later.overview_embedding)
        if vector_a is None or vector_b is None:
            return None
        return embeddings.cosine_similarity(vector_a, vector_b)

    def violation(self, earlier: CachedMovie, later: CachedMovie, metric: float) -> str | None:
        if metric > SEMANTIC_SIMILARITY_THRESHOLD:
            return None
        return (
            f"Semantic Trope Web: {later.title} is only a {_percent(metric)}% plot match for "
            f"{earlier.title} - it needs more than {_percent(SEMANTIC_SIMILARITY_THRESHOLD)}%"
        )

    async def prepare(self, movies: list[CachedMovie]) -> None:
        await movie_features.ensure_embeddings(self.session, movies)

    def annotate(self, candidate: DiscoveryCandidate, row: CachedMovie, metric: float | None) -> None:
        candidate.semantic_score = None if metric is None else max(0.0, round(metric, 4))

    def with_metric(self, result: ValidationResult, metric: float | None) -> ValidationResult:
        result.similarity = None if metric is None else round(metric, 4)
        return result

    def metadata_fields(self, result: ValidationResult) -> dict:
        return {"semantic_score": result.similarity}

    async def describe_constraint(
        self, tail_movie_id: int | None, previous_transition: dict | None
    ) -> ConstraintInfo | None:
        floor = f"more than {_percent(SEMANTIC_SIMILARITY_THRESHOLD)}%"
        if tail_movie_id is None:
            return ConstraintInfo(
                kind="semantic", title="Any film starts the web",
                detail=f"Every later film's plot must be a semantic match of {floor} to the one before it.")
        return ConstraintInfo(
            kind="semantic", title="Next film's plot must be a close semantic match",
            detail=f"Plot similarity {floor} to the last film's overview.")


def _percent(similarity: float) -> int:
    return round(max(0.0, similarity) * 100)
