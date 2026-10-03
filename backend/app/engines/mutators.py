"""Graph mutators: CineChain variants whose rules depend on the previous hop.

All three keep the shared-cast chain (Auteur Relay widens it to directors) and
add a rule between *consecutive* films, enforced at logging time
(`validate_next_step`), when offering candidates (`discover_candidates`) and when
solving a bridge (`bridge_constraints` -> `constrained_pathfinder`).

Rule violations are hard blocks (`ValidationResult.blocked`): a wildcard can skip
a missing cast link but never a mode's defining rule.
"""

from __future__ import annotations

import time
from typing import ClassVar

from app.engines.cinechain import CineChainEngine
from app.models.cache import CachedMovie
from app.schemas.discovery import DiscoveryCandidate, DiscoveryConnection
from app.schemas.engine import (
    ConstraintInfo,
    SharedActorConnection,
    Suggestion,
    SuggestionFilters,
    ValidationResult,
)
from app.services import cache_repo, pathfinder
from app.services.bridge_paths import parse_countries
from app.services.graph import PathConstraints
from app.services.movie_filters import is_reality_eligible
from app.services.tmdb_backoff import DeadlineReached, fetch_with_backoff
from app.utils.dates import parse_release_year

# World Passport needs each candidate's country, which search/credit stubs lack:
# hydrate this many per request (most popular first), the rest stay "unverified".
HYDRATE_BUDGET = 30
HYDRATE_SECONDS = 20.0

OPPOSITE_KIND = {"actor": "director", "director": "actor"}


class MutatorEngine(CineChainEngine):
    """Shared plumbing: subclasses define `pair_violation` (and, for the
    alternating Auteur Relay, override the link check)."""

    capabilities: ClassVar[list[str]] = [
        cap for cap in CineChainEngine.capabilities if cap != "bridge_swap"
    ]
    needs_detail: ClassVar[bool] = False  # `pair_violation` needs full film detail (country)

    def pair_violation(self, earlier: CachedMovie, later: CachedMovie) -> str | None:
        """Why `later` may not directly follow `earlier`; None = allowed."""
        return None

    def bridge_constraints(self, start_connection_type: str | None = None) -> PathConstraints:
        return PathConstraints(
            movie_ok=lambda earlier, later: self.pair_violation(earlier, later) is None,
            endpoint_reason=self.pair_violation,
            needs_detail=self.needs_detail,
        )

    async def _load(self, movie_id: int, hydrate: bool = False) -> CachedMovie:
        row = self.session.get(CachedMovie, movie_id)
        if row is None:
            return await cache_repo.get_movie(self.session, self.tmdb, movie_id)
        if hydrate and row.origin_country is None:
            return await cache_repo.get_movie(self.session, self.tmdb, movie_id, refresh=True)
        return row

    async def _validate_link(
        self,
        from_movie_id: int,
        to_movie_id: int,
        cast_limit: int | None,
        rules: dict | None,
        previous_transition: dict | None,
    ) -> ValidationResult:
        return await CineChainEngine.validate_next_step(
            self, from_movie_id, to_movie_id, cast_limit=cast_limit)

    async def validate_next_step(
        self,
        from_movie_id: int,
        to_movie_id: int,
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
    ) -> ValidationResult:
        earlier = await self._load(from_movie_id)
        later = await self._load(to_movie_id, hydrate=self.needs_detail)
        reason = self.pair_violation(earlier, later)
        if reason:
            return ValidationResult(valid=False, blocked=True, reason=reason)
        return await self._validate_link(
            from_movie_id, to_movie_id, cast_limit, rules, previous_transition)

    async def _filter_pool(
        self, frontier: CachedMovie, candidates: list[DiscoveryCandidate]
    ) -> list[DiscoveryCandidate]:
        budget = HYDRATE_BUDGET if self.needs_detail else 0
        deadline = time.monotonic() + HYDRATE_SECONDS
        keep: set[int] = set()
        for candidate in sorted(candidates, key=lambda c: -(c.popularity or 0.0)):
            row = self.session.get(CachedMovie, candidate.movie_id)
            if row is None:
                continue
            if self.needs_detail and row.origin_country is None and budget > 0:
                budget -= 1
                try:
                    fetched = await fetch_with_backoff(
                        lambda movie_id=candidate.movie_id: cache_repo.get_movie(
                            self.session, self.tmdb, movie_id, refresh=True),
                        deadline)
                    row = fetched or row
                except DeadlineReached:
                    budget = 0
                except Exception:  # noqa: BLE001 - leave this film unverified
                    self.session.rollback()
                candidate.origin_country = row.origin_country
            if self.pair_violation(frontier, row):
                continue
            candidate.constraint_unverified = self.needs_detail and row.origin_country is None
            keep.add(candidate.movie_id)
        return [c for c in candidates if c.movie_id in keep]

    async def discover_candidates(
        self,
        frontier_movie_id: int,
        mode: str = "or",
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
    ) -> list[DiscoveryCandidate]:
        candidates = await super().discover_candidates(frontier_movie_id, mode, cast_limit)
        frontier = await self._load(frontier_movie_id)
        return await self._filter_pool(frontier, candidates)

    async def get_suggestions(
        self,
        current_movie_id: int,
        exclude_movie_ids: list[int],
        filters: SuggestionFilters,
        rules: dict | None = None,
    ) -> list[Suggestion]:
        suggestions = await super().get_suggestions(current_movie_id, exclude_movie_ids, filters)
        frontier = await self._load(current_movie_id)
        kept = []
        for suggestion in suggestions:
            row = self.session.get(CachedMovie, suggestion.movie_id)
            if row is not None and not self.pair_violation(frontier, row):
                kept.append(suggestion)
        return kept

    def solve_bridge(
        self,
        from_movie_id: int,
        to_movie_id: int,
        max_depth: int | None = None,
        call_budget: int | None = None,
        cast_limit: int | None = None,
        min_runtime: int | None = None,
        excluded_movie_ids: set[int] | None = None,
        max_duration_seconds: int | None = None,
        min_hops: int | None = None,
        rules: dict | None = None,
        start_connection_type: str | None = None,
    ):
        return pathfinder.solve_bridge_bipartite(
            self.session,
            self.tmdb,
            from_movie_id,
            to_movie_id,
            max_depth=max_depth,
            call_budget=call_budget,
            cast_limit=cast_limit,
            min_runtime=min_runtime,
            excluded_movie_ids=excluded_movie_ids,
            max_duration_seconds=max_duration_seconds,
            min_hops=min_hops,
            constraints=self.bridge_constraints(start_connection_type),
        )


class ChronoClimbEngine(MutatorEngine):
    """Every hop must move strictly forward in time."""

    game_type = "chrono_climb"
    display_name = "Chrono Climb"
    description = "Shared-cast chain where every film must be released after the one before it."

    def pair_violation(self, earlier: CachedMovie, later: CachedMovie) -> str | None:
        year_a = parse_release_year(earlier.release_date)
        year_b = parse_release_year(later.release_date)
        if year_a is None or year_b is None:
            return "Chrono Climb needs a release year for both films"
        if year_b <= year_a:
            return (
                f"Chrono Climb: {later.title} ({year_b}) must be released after "
                f"{earlier.title} ({year_a})"
            )
        return None

    async def describe_constraint(
        self, tail_movie_id: int | None, previous_transition: dict | None
    ) -> ConstraintInfo | None:
        if tail_movie_id is None:
            return ConstraintInfo(
                kind="year", title="Any film starts the climb",
                detail="Every later film must be released after the one before it.")
        year = parse_release_year((await self._load(tail_movie_id)).release_date)
        if year is None:
            return None
        return ConstraintInfo(
            kind="year", title=f"Next film must be released after {year}",
            detail="Strictly later - the same year doesn't count.")


def primary_country(movie: CachedMovie) -> str | None:
    countries = parse_countries(movie.origin_country)
    return countries[0] if countries else None


class WorldPassportEngine(MutatorEngine):
    """Every film's primary country must differ from the previous film's."""

    game_type = "world_passport"
    display_name = "World Cinema Passport"
    description = "Shared-cast chain where every film must come from a different country than the last."
    needs_detail = True

    def pair_violation(self, earlier: CachedMovie, later: CachedMovie) -> str | None:
        country_a, country_b = primary_country(earlier), primary_country(later)
        # Unknown country data never blocks: world cinema is often thinly documented.
        if country_a is None or country_b is None or country_a != country_b:
            return None
        return (
            f"World Passport: {later.title} is also from {country_b} - the next stamp "
            f"must be a country other than {country_a}"
        )

    def bridge_constraints(self, start_connection_type: str | None = None) -> PathConstraints:
        return PathConstraints(
            movie_ok=lambda earlier, later: self.pair_violation(earlier, later) is None,
            needs_detail=True,
        )

    async def describe_constraint(
        self, tail_movie_id: int | None, previous_transition: dict | None
    ) -> ConstraintInfo | None:
        if tail_movie_id is None:
            return ConstraintInfo(
                kind="country", title="Any film starts the passport",
                detail="Every later film must be from a different country than the one before it.")
        country = primary_country(await self._load(tail_movie_id, hydrate=True))
        if country is None:
            return ConstraintInfo(
                kind="country", title="Next film must be from a different country",
                detail="The last film's country isn't on record, so it can't be checked.")
        return ConstraintInfo(
            kind="country", title=f"Next film must be from a country other than {country}",
            detail="Compared by primary production country.")


class AuteurRelayEngine(MutatorEngine):
    """Hops alternate between a shared actor and a shared director.

    The first hop is free; whichever kind it uses, the next must be the other.
    The kind is stored on each step as `transition_metadata.connection_type`.
    """

    game_type = "auteur_relay"
    display_name = "Auteur Relay"
    description = "Chain films by alternating links: a shared actor, then a shared director, then an actor..."

    def bridge_constraints(self, start_connection_type: str | None = None) -> PathConstraints:
        return PathConstraints(
            alternate_edges=True, use_directors=True, start_tag=start_connection_type)

    async def _validate_link(
        self,
        from_movie_id: int,
        to_movie_id: int,
        cast_limit: int | None,
        rules: dict | None,
        previous_transition: dict | None,
    ) -> ValidationResult:
        shared_actors = await CineChainEngine.validate_next_step(
            self, from_movie_id, to_movie_id, cast_limit=cast_limit)
        actors = shared_actors.connections if shared_actors.valid else []

        directors_from = await cache_repo.get_movie_directors(self.session, self.tmdb, from_movie_id)
        directors_to = {
            d.person_id for d in await cache_repo.get_movie_directors(
                self.session, self.tmdb, to_movie_id)
        }
        directors = [
            SharedActorConnection(kind="director", actor_id=d.person_id, actor_name=d.name)
            for d in directors_from if d.person_id in directors_to
        ]

        required = OPPOSITE_KIND.get((previous_transition or {}).get("connection_type") or "")
        if not actors and not directors:
            return ValidationResult(
                valid=False, reason="No shared credited cast or director found",
                connections=[], connection_type=required)

        available = {"actor": actors, "director": directors}
        if required is None:
            return ValidationResult(valid=True, connections=[*actors, *directors])
        if available[required]:
            return ValidationResult(
                valid=True, connections=available[required], connection_type=required)
        other = OPPOSITE_KIND[required]
        return ValidationResult(
            valid=False, blocked=True, connection_type=required,
            reason=(
                f"Auteur Relay: this hop must connect through a {required}, "
                f"but these films only share a {other}"
            ))

    def link_metadata(
        self, result: ValidationResult, client_metadata: dict | None
    ) -> dict | None:
        meta = dict(client_metadata or {})
        kinds = {c.kind for c in result.connections}
        kind = result.connection_type
        if kind is None:
            hint = meta.get("connection_type")
            kind = hint if hint in kinds else (
                "actor" if "actor" in kinds else "director" if "director" in kinds else None)
        if kind is None:
            return meta or None
        meta["connection_type"] = kind
        chosen = [c for c in result.connections if c.kind == kind]
        if kind == "director":
            for key in ("actor_id", "actor_name", "profile_path", "character_in_from", "character_in_to"):
                meta.pop(key, None)
            if chosen:
                preferred = next((c for c in chosen if c.actor_id == meta.get("director_id")), chosen[0])
                meta["director_id"] = preferred.actor_id
                meta["director_name"] = preferred.actor_name
        else:
            for key in ("director_id", "director_name"):
                meta.pop(key, None)
            if chosen and meta.get("actor_id") is None:
                meta.update(actor_id=chosen[0].actor_id, actor_name=chosen[0].actor_name,
                            profile_path=chosen[0].profile_path)
        return meta

    async def discover_candidates(
        self,
        frontier_movie_id: int,
        mode: str = "or",
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
    ) -> list[DiscoveryCandidate]:
        required = OPPOSITE_KIND.get((previous_transition or {}).get("connection_type") or "")
        pool: dict[int, DiscoveryCandidate] = {}
        if required != "director":
            for candidate in await CineChainEngine.discover_candidates(
                self, frontier_movie_id, "or", cast_limit
            ):
                pool[candidate.movie_id] = candidate
        if required != "actor":
            directors = await cache_repo.get_movie_directors(
                self.session, self.tmdb, frontier_movie_id)
            for director in directors:
                films = await cache_repo.get_director_credits(
                    self.session, self.tmdb, director.person_id, director.name)
                for film in films:
                    if film.tmdb_id == frontier_movie_id or not is_reality_eligible(film):
                        continue
                    candidate = pool.get(film.tmdb_id)
                    if candidate is None:
                        candidate = DiscoveryCandidate(
                            movie_id=film.tmdb_id,
                            title=film.title,
                            poster_path=film.poster_path,
                            release_year=parse_release_year(film.release_date),
                            origin_country=film.origin_country,
                            genre_ids=film.genre_ids or [],
                            popularity=film.popularity,
                        )
                        pool[film.tmdb_id] = candidate
                    candidate.connections.append(DiscoveryConnection(
                        kind="director", actor_id=director.person_id,
                        actor_name=director.name))
        results = list(pool.values())
        if mode == "and":
            results = [c for c in results if len(c.connections) >= 2]
        return results

    async def describe_constraint(
        self, tail_movie_id: int | None, previous_transition: dict | None
    ) -> ConstraintInfo | None:
        if tail_movie_id is None:
            return ConstraintInfo(
                kind="free", title="Any film starts the relay",
                detail="After that, hops must alternate between an actor and a director.")
        previous = previous_transition or {}
        last = previous.get("connection_type")
        required = OPPOSITE_KIND.get(last or "")
        if required is None:
            return ConstraintInfo(
                kind="free", title="Next hop is free: connect via an actor or a director",
                detail="Whichever you pick, the hop after it must be the other kind.")
        who = previous.get("director_name") if last == "director" else previous.get("actor_name")
        return ConstraintInfo(
            kind=required, title=f"Next hop must be {'a Director' if required == 'director' else 'an Actor'}",
            detail=f"The last hop was through {'a director' if last == 'director' else 'an actor'}"
                   + (f" ({who})." if who else "."))
