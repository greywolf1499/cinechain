"""The original Six Degrees rule set: shared credited cast links each step."""

from __future__ import annotations

import json
from collections import Counter
from typing import ClassVar

from app.engines.base import BaseChallengeEngine
from app.models.cache import CachedMovie
from app.models.run import RunStep
from app.schemas.engine import (
    KeystoneActor,
    RunStats,
    SharedActorConnection,
    Suggestion,
    SuggestionFilters,
    ValidationResult,
)
from app.services import cache_repo, pathfinder
from app.utils.dates import parse_release_year


def _passes_filters(movie: CachedMovie, filters: SuggestionFilters) -> bool:
    if filters.country is not None:
        countries = json.loads(
            movie.origin_country) if movie.origin_country else []
        if filters.country not in countries:
            return False
    if filters.decade is not None:
        year = parse_release_year(movie.release_date)
        if year is None or (year // 10) * 10 != filters.decade:
            return False
    if filters.genre_id is not None:
        return bool(movie.genre_ids and filters.genre_id in movie.genre_ids)
    # `on_server` is reserved for the Phase 7 Jellyfin integration - no-op for now.
    return True


class CineChainEngine(BaseChallengeEngine):
    game_type = "cinechain"
    display_name = "CineChain"
    description = (
        "Classic Six Degrees: every film must share a credited actor with the previous one."
    )
    capabilities: ClassVar[list[str]] = [
        "validate_next_step",
        "get_suggestions",
        "compute_stats",
        "solve_bridge",
    ]

    async def validate_next_step(self, from_movie_id: int, to_movie_id: int) -> ValidationResult:
        from_cast = await cache_repo.get_movie_cast(self.session, self.tmdb, from_movie_id)
        to_cast_by_actor = {
            member["actor_id"]: member
            for member in await cache_repo.get_movie_cast(self.session, self.tmdb, to_movie_id)
        }

        connections = [
            SharedActorConnection(
                actor_id=member["actor_id"],
                actor_name=member["name"],
                profile_path=member["profile_path"],
                character_in_from=member["character_name"],
                character_in_to=to_cast_by_actor[member["actor_id"]
                                                 ]["character_name"],
            )
            for member in from_cast
            if member["actor_id"] in to_cast_by_actor
        ]

        if connections:
            return ValidationResult(valid=True, connections=connections)
        return ValidationResult(valid=False, reason="No shared credited cast found", connections=[])

    async def get_suggestions(
        self, current_movie_id: int, exclude_movie_ids: list[int], filters: SuggestionFilters
    ) -> list[Suggestion]:
        cast = await cache_repo.get_movie_cast(self.session, self.tmdb, current_movie_id)
        exclude = set(exclude_movie_ids) | {current_movie_id}

        suggestions: dict[int, Suggestion] = {}
        for member in cast:
            credits_ = await cache_repo.get_actor_credits(
                self.session, self.tmdb, member["actor_id"]
            )
            for movie in credits_:
                if movie.tmdb_id in exclude or movie.tmdb_id in suggestions:
                    continue
                if not _passes_filters(movie, filters):
                    continue
                suggestions[movie.tmdb_id] = Suggestion(
                    movie_id=movie.tmdb_id,
                    title=movie.title,
                    poster_path=movie.poster_path,
                    release_year=parse_release_year(movie.release_date),
                    origin_country=movie.origin_country,
                    connecting_actor_id=member["actor_id"],
                    connecting_actor_name=member["name"],
                )
        return list(suggestions.values())

    async def compute_stats(self, steps: list[RunStep]) -> RunStats:
        countries: set[str] = set()
        decades: set[int] = set()
        actor_counts: Counter[int] = Counter()
        actor_names: dict[int, str] = {}

        for step in steps:
            if step.movie_origin_country:
                countries.update(json.loads(step.movie_origin_country))
            if step.movie_release_year:
                decades.add((step.movie_release_year // 10) * 10)
            if step.transition_metadata:
                actor_id = step.transition_metadata.get("actor_id")
                actor_name = step.transition_metadata.get("actor_name")
                if actor_id is not None:
                    actor_counts[actor_id] += 1
                    if actor_name:
                        actor_names[actor_id] = actor_name

        keystone_actors = [
            KeystoneActor(
                actor_id=actor_id, actor_name=actor_names.get(actor_id, "Unknown"), appearances=count
            )
            for actor_id, count in actor_counts.most_common()
        ]

        return RunStats(
            total_hops=max(len(steps) - 1, 0),
            countries=sorted(countries),
            decades=sorted(decades),
            keystone_actors=keystone_actors,
        )

    def solve_bridge(
        self,
        from_movie_id: int,
        to_movie_id: int,
        max_depth: int | None = None,
        call_budget: int | None = None,
    ):
        return pathfinder.solve_bridge_bipartite(
            self.session,
            self.tmdb,
            from_movie_id,
            to_movie_id,
            max_depth=max_depth,
            call_budget=call_budget,
        )
