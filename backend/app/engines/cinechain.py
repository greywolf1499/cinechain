"""The original Six Degrees rule set: shared credited cast links each step."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import ClassVar

import httpx
from sqlmodel import select

from app.config import get_settings
from app.engines.base import BaseChallengeEngine
from app.engines.reunions import (
    CHARACTER_HOP_KEY,
    GOLDEN_REUNION_KEY,
    CastCredit,
    Person,
    find_character_hop,
    find_golden_reunion,
)
from app.engines.rulebook import RuleSection
from app.models.cache import CachedMovieCast
from app.models.run import Run, RunStep
from app.schemas.discovery import DiscoveryCandidate, DiscoveryConnection
from app.schemas.engine import (
    KeystoneActor,
    Preset,
    RuleField,
    RunStats,
    SharedActorConnection,
    Suggestion,
    SuggestionFilters,
    ValidationResult,
)
from app.services import bounties, cache_repo, feasibility, pathfinder
from app.services.bridge_paths import parse_countries
from app.services.movie_filters import is_reality_eligible, passes_filters
from app.services.tmdb import TMDBError
from app.services.tmdb_backoff import DeadlineReached, fetch_with_backoff
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
            countries.update(parse_countries(step.movie_origin_country))
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


def cast_credits(cast: Sequence[dict]) -> list[CastCredit]:
    return [
        CastCredit(m["actor_id"], m["name"], m["character_name"], m["cast_order"]) for m in cast
    ]


class CineChainEngine(BaseChallengeEngine):
    def coach_line(self, run: Run, steps: Sequence[RunStep]) -> str | None:
        rules = run.rules_config or {}
        if not steps or not bounties.board_enabled(rules):
            return None
        cast = self.session.exec(
            select(CachedMovieCast).where(CachedMovieCast.movie_id == steps[-1].movie_id)
        ).all()
        cast_depth = rules.get("max_cast_order") or get_settings().pathfinder_cast_limit
        actor_ids = [
            member.actor_id
            for member in cast
            if member.cast_order is not None
            and member.cast_order < cast_depth
            and (
                not rules.get("no_consecutive_actor", True)
                or member.actor_id != (steps[-1].transition_metadata or {}).get("actor_id")
            )
        ]
        if not actor_ids:
            return None
        candidate_ids = sorted(
            set(
                self.session.exec(
                    select(CachedMovieCast.movie_id).where(CachedMovieCast.actor_id.in_(actor_ids))
                ).all()
            )
            - {step.movie_id for step in steps}
        )
        movies = feasibility.movies(self.session)
        quests = [bounties.resolve(rules, key) for key in bounties.active_bounties(rules)]
        for movie_id in candidate_ids:
            movie = movies.get(movie_id)
            if (
                not movie
                or not is_reality_eligible(movie)
                or not self.bounty_pool_allowed(movie, rules, steps)
            ):
                continue
            for quest in quests:
                if (
                    quest
                    and quest.predicate
                    and feasibility.verdict(self.session, quest.predicate, movie_id) is True
                ):
                    return f"{movie.title} may earn the {quest.title} bounty. Check its rule chips."
        return None

    rule_fields: ClassVar[list[RuleField]] = [
        *BaseChallengeEngine.rule_fields,
        RuleField(
            key="no_consecutive_actor",
            kind="bool",
            label="No consecutive actor reuse",
            default=True,
            group="advanced",
        ),
        RuleField(
            key="max_cast_order",
            kind="int",
            label="Max cast depth",
            min=1,
            max=30,
            default=15,
            group="advanced",
        ),
    ]
    presets: ClassVar[list[Preset]] = [
        Preset(
            id="standard",
            label="Standard",
            blurb="Balanced cast depth and two wildcards.",
            values={
                "allow_repeats": "strict",
                "no_consecutive_actor": True,
                "max_cast_order": 15,
                "min_runtime": 40,
                "wildcards_budget": 2,
            },
        ),
        Preset(
            id="purist",
            label="Purist",
            blurb="Top-billed cast, no wildcards.",
            values={
                "allow_repeats": "strict",
                "no_consecutive_actor": True,
                "max_cast_order": 5,
                "min_runtime": 60,
                "wildcards_budget": 0,
            },
        ),
        Preset(
            id="casual",
            label="Casual",
            blurb="Wide cast, repeat penalties and unlimited wildcards.",
            values={
                "allow_repeats": "penalty",
                "no_consecutive_actor": False,
                "max_cast_order": 25,
                "min_runtime": 0,
                "wildcards_budget": -1,
            },
        ),
    ]
    default_preset = "standard"
    tagline = "Six Degrees of Kevin Bacon"
    tags: ClassVar[list[str]] = ["Shared cast"]
    rulebook: ClassVar[RuleSection] = RuleSection(
        "Build a connected movie chain.",
        [
            "Link films through a shared actor or the same character played by two actors.",
            "Actor links use the configured cast depth: {cast_depth}.",
        ],
        ["{win_goal}"],
        ["{fail_goal}"],
        [
            "Choose a connector with a broad filmography to keep your next move open.",
            "Save wildcards for scarce links rather than spending them on easy detours.",
        ],
        ["seed", "wildcard"],
    )
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
        "modifiers",
        "bridge_swap",
    ]
    supports_json_rules = True
    modifier_scopes = frozenset({"film", "pair", "sequence"})
    # A Character Hop (same character, different actors) is a valid link on its own.
    character_hop_links: ClassVar[bool] = True

    def link_metadata(self, result: ValidationResult, client_metadata: dict | None) -> dict | None:
        meta = super().link_metadata(result, client_metadata)
        hop = next((c for c in result.connections if c.kind == "character"), None)
        if hop is not None and meta is not None and meta.get("actor_id") is None:
            # A character hop with nothing claimed by the client: record who played the character.
            meta.update(
                actor_id=hop.actor_id,
                actor_name=hop.actor_name,
                character_in_from=hop.character_in_from,
                character_in_to=hop.character_in_to,
            )
        return meta

    async def golden_reunion(
        self,
        from_movie_id: int,
        to_movie_id: int,
        cast_from: Sequence[CastCredit],
        cast_to: Sequence[CastCredit],
    ) -> dict | None:
        """The earlier film's director and one of its top-5 actors back together on the later
        film. A failed TMDB lookup just means no bonus - it never blocks a step."""
        try:
            directors_from = await cache_repo.get_movie_directors(
                self.session, self.tmdb, from_movie_id
            )
            directors_to = await cache_repo.get_movie_directors(
                self.session, self.tmdb, to_movie_id
            )
        except (TMDBError, httpx.HTTPError):
            return None
        return find_golden_reunion(
            [Person(d.person_id, d.name) for d in directors_from],
            cast_from,
            [Person(d.person_id, d.name) for d in directors_to],
            cast_to,
        )

    async def validate_primary(
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
        from_cast = await cache_repo.get_movie_cast(
            self.session, self.tmdb, from_movie_id, cast_limit
        )
        to_cast = await cache_repo.get_movie_cast(self.session, self.tmdb, to_movie_id, cast_limit)
        to_cast_by_actor = {member["actor_id"]: member for member in to_cast}

        connections = [
            SharedActorConnection(
                actor_id=member["actor_id"],
                actor_name=member["name"],
                profile_path=member["profile_path"],
                character_in_from=member["character_name"],
                character_in_to=to_cast_by_actor[member["actor_id"]]["character_name"],
            )
            for member in from_cast
            if member["actor_id"] in to_cast_by_actor
        ]

        credits_from = cast_credits(from_cast)
        credits_to = cast_credits(to_cast)
        hop = find_character_hop(credits_from, credits_to)
        linked = bool(connections) or (hop is not None and self.character_hop_links)
        if not linked:
            return ValidationResult(
                valid=False, reason="No shared credited cast found", connections=[]
            )

        mechanic: dict = {}
        if hop is not None:
            mechanic[CHARACTER_HOP_KEY] = hop.character
        reunion = await self.golden_reunion(from_movie_id, to_movie_id, credits_from, credits_to)
        if reunion is not None:
            mechanic[GOLDEN_REUNION_KEY] = reunion
        if not connections and hop is not None:
            connections = [
                SharedActorConnection(
                    kind="character",
                    actor_id=hop.actor_to.person_id,
                    actor_name=f"{hop.actor_from.name} \u2192 {hop.actor_to.name}",
                    character_in_from=hop.actor_from.character,
                    character_in_to=hop.actor_to.character,
                )
            ]
        return ValidationResult(valid=True, connections=connections, mechanic=mechanic or None)

    async def get_suggestions(
        self,
        current_movie_id: int,
        exclude_movie_ids: list[int],
        filters: SuggestionFilters,
        rules: dict | None = None,
        history: Sequence[RunStep] | None = None,
    ) -> list[Suggestion]:
        cast = await cache_repo.get_movie_cast(
            self.session, self.tmdb, current_movie_id, (rules or {}).get("max_cast_order")
        )
        exclude = set(exclude_movie_ids) | {current_movie_id}
        country_hydrations = 0

        suggestions: dict[int, Suggestion] = {}
        for member in cast:
            credits_ = await cache_repo.get_actor_credits(
                self.session, self.tmdb, member["actor_id"]
            )
            for movie in credits_:
                if movie.tmdb_id in exclude or movie.tmdb_id in suggestions:
                    continue
                if not passes_filters(movie, filters.model_copy(update={"country": None})):
                    continue
                if filters.country and movie.origin_country is None and country_hydrations < 20:
                    country_hydrations += 1
                    movie = await cache_repo.get_movie(
                        self.session, self.tmdb, movie.tmdb_id, refresh=True
                    )
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
        history: Sequence[RunStep] | None = None,
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
                        character_in_frontier=frontier_character_by_actor.get(member["actor_id"]),
                        character_in_candidate=cast_entry["character_name"] if cast_entry else None,
                    )
                )

        results = list(candidates.values())
        if mode == "and":
            results = [c for c in results if len(c.connections) >= 2]
        return results

    async def widen_pool(
        self, frontier: int, rules: dict | None, history: Sequence[RunStep] | None, rung: int
    ) -> list[DiscoveryCandidate]:
        limit = self._discovery_cast_limit or (rules or {}).get("max_cast_order") or 15
        if rung == 3:
            from app.engines import chaos
            from app.facets.query import FacetQuery
            from app.services import feasibility

            repo = cache_repo.CacheRepo(self.session)
            cast = repo.get_cached_cast(frontier, limit) or []
            cached_pool: dict[int, DiscoveryCandidate] = {}
            for member in cast:
                for movie in repo.get_cached_actor_credits(member["actor_id"]) or []:
                    if movie.tmdb_id == frontier or not is_reality_eligible(movie):
                        continue
                    candidate = cached_pool.setdefault(
                        movie.tmdb_id,
                        DiscoveryCandidate(
                            movie_id=movie.tmdb_id,
                            title=movie.title,
                            poster_path=movie.poster_path,
                            release_year=parse_release_year(movie.release_date),
                            genre_ids=movie.genre_ids or [],
                            origin_country=movie.origin_country,
                            popularity=movie.popularity,
                        ),
                    )
                    candidate.connections.append(
                        DiscoveryConnection(
                            actor_id=member["actor_id"],
                            actor_name=member["name"],
                            character_in_frontier=member["character_name"],
                        )
                    )
            handicap = chaos.active(rules)
            query = feasibility.query_of(handicap.predicate) if handicap else FacetQuery(all=[])
            matches = set(feasibility.matching_ids(self.session, query, cached_pool))
            return [
                candidate
                for movie_id, candidate in cached_pool.items()
                if movie_id in matches
                and (self._discovery_mode != "and" or len(candidate.connections) >= 2)
            ]
        if rung == 1:
            cast = await self.tmdb.get_movie_credits(frontier)
            cache_repo.CacheRepo(self.session).upsert_cast(frontier, cast, limit + 10)
        pool = await self.discover_candidates(
            frontier,
            self._discovery_mode,
            limit + 10,
            rules,
            self._discovery_previous,
            history,
            **self._discovery_options,
        )
        # Broader billing is a source of filmographies, not permission to break the run's link rule.
        original = await cache_repo.get_movie_cast(self.session, self.tmdb, frontier, limit)
        allowed = {member["actor_id"] for member in original}
        names = {member["actor_id"]: member for member in original}
        kept = []
        for candidate in pool:
            candidate.connections = [
                connection
                for connection in candidate.connections
                if connection.kind != "actor" or connection.actor_id in allowed
            ]
            if not candidate.connections:
                repo = cache_repo.CacheRepo(self.session)
                candidate_cast = repo.get_cached_cast(candidate.movie_id, limit)
                if candidate_cast is None and (self._hydration_left or 0) > 0:
                    self._hydration_left = (self._hydration_left or 0) - 1
                    try:
                        candidate_cast = await fetch_with_backoff(
                            lambda movie_id=candidate.movie_id: cache_repo.get_movie_cast(
                                self.session,
                                self.tmdb,
                                movie_id,
                                limit,
                            ),
                            self._hydration_deadline,
                        )
                    except DeadlineReached:
                        self._hydration_left = 0
                        self.discovery_reason = (
                            "Some cast links are unverified. Prepare this run's people."
                        )
                for member in candidate_cast or []:
                    actor_id = member["actor_id"]
                    if actor_id in allowed:
                        candidate.connections.append(
                            DiscoveryConnection(
                                actor_id=actor_id,
                                actor_name=names[actor_id]["name"],
                                character_in_frontier=names[actor_id]["character_name"],
                                character_in_candidate=member["character_name"],
                            )
                        )
            if candidate.connections and (
                self._discovery_mode != "and" or len(candidate.connections) >= 2
            ):
                kept.append(candidate)
        return kept

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
