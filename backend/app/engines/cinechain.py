"""The original Six Degrees rule set: shared credited cast links each step."""

from __future__ import annotations

import json
from collections import Counter
from typing import ClassVar

from app.engines.base import BaseChallengeEngine
from app.models.run import RunStep
from app.schemas.discovery import DiscoveryCandidate, DiscoveryConnection
from app.schemas.engine import (
    KeystoneActor,
    RunStats,
    SharedActorConnection,
    Suggestion,
    SuggestionFilters,
    ValidationResult,
)
from app.services import cache_repo, pathfinder
from app.services.movie_filters import is_reality_eligible, passes_filters
from app.utils.dates import parse_release_year


def compute_run_stats(steps: list[RunStep]) -> RunStats:
    # Planned-but-not-yet-watched steps haven't actually been experienced,
    # so they shouldn't count toward cultural-breadth passport stats.
    watched_steps = [s for s in steps if s.status == "watched"]

    countries: set[str] = set()
    decades: set[int] = set()
    actor_counts: Counter[int] = Counter()
    actor_names: dict[int, str] = {}

    for step in watched_steps:
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
        total_hops=max(len(watched_steps) - 1, 0),
        countries=sorted(countries),
        decades=sorted(decades),
        keystone_actors=keystone_actors,
    )


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
        "discover_candidates",
        "json_rules",
        "bridge_swap",
    ]
    supports_json_rules = True

    async def validate_next_step(
        self,
        from_movie_id: int,
        to_movie_id: int,
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
    ) -> ValidationResult:
        # Note: if a movie's cast was already cached at a lower limit (e.g. the
        # global default of 15), a run requesting a deeper cast_limit (e.g. a
        # Casual preset's 25) won't retroactively discover more billed actors
        # without a fresh TMDB fetch - this only affects the *effective* depth
        # considered, never causes an error.
        from_cast = await cache_repo.get_movie_cast(self.session, self.tmdb, from_movie_id, cast_limit)
        to_cast_by_actor = {
            member["actor_id"]: member
            for member in await cache_repo.get_movie_cast(
                self.session, self.tmdb, to_movie_id, cast_limit
            )
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
        self,
        current_movie_id: int,
        exclude_movie_ids: list[int],
        filters: SuggestionFilters,
        rules: dict | None = None,
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
                if not passes_filters(movie, filters):
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

    async def discover_candidates(
        self,
        frontier_movie_id: int,
        mode: str = "or",
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
    ) -> list[DiscoveryCandidate]:
        """Pools every top-billed cast member's filmography into one set of
        candidates, tracking ALL connecting actors per movie (not just the
        first found) so the frontend can render "2 Shared Actors" pills and
        do its own AND/OR-aware actor-chip filtering. `mode="and"` narrows the
        pool server-side to movies with 2+ connections (true co-star reunions)
        since that requires the full aggregation this method already does.
        """
        cast = await cache_repo.get_movie_cast(
            self.session, self.tmdb, frontier_movie_id, cast_limit
        )
        frontier_character_by_actor = {
            member["actor_id"]: member["character_name"] for member in cast
        }

        candidates: dict[int, DiscoveryCandidate] = {}
        for member in cast:
            credits_ = await cache_repo.get_actor_credits(
                self.session, self.tmdb, member["actor_id"]
            )
            for movie in credits_:
                if movie.tmdb_id == frontier_movie_id:
                    continue
                if not is_reality_eligible(movie):
                    continue
                candidate = candidates.get(movie.tmdb_id)
                if candidate is None:
                    candidate = DiscoveryCandidate(
                        movie_id=movie.tmdb_id,
                        title=movie.title,
                        poster_path=movie.poster_path,
                        release_year=parse_release_year(movie.release_date),
                        origin_country=movie.origin_country,
                        genre_ids=movie.genre_ids or [],
                        popularity=movie.popularity,
                    )
                    candidates[movie.tmdb_id] = candidate

                # Already cached for free - get_actor_credits just upserted this
                # exact (movie, actor) pairing into cached_movie_cast above.
                cast_entry = await cache_repo.get_cast_entry(
                    self.session, movie.tmdb_id, member["actor_id"]
                )
                candidate.connections.append(
                    DiscoveryConnection(
                        actor_id=member["actor_id"],
                        actor_name=member["name"],
                        profile_path=member["profile_path"],
                        character_in_frontier=frontier_character_by_actor.get(
                            member["actor_id"]),
                        character_in_candidate=cast_entry["character_name"] if cast_entry else None,
                    )
                )

        results = list(candidates.values())
        if mode == "and":
            results = [c for c in results if len(c.connections) >= 2]
        return results

    async def compute_stats(self, steps: list[RunStep]) -> RunStats:
        return compute_run_stats(steps)

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
        )
