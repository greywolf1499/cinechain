"""The Crew & Craft Trail: films connect through ANY shared person across cast or key crafts.

A hop is legal when the two films share a person who was, on each film, an actor, the composer,
the director of photography, a writer (Screenplay/Writer) or the director - and not necessarily the
same thing on both: Jordan Peele directs *Get Out* and acts in *Keanu*, which is a valid link. The
rule is the classic CineChain cast link widened with `cached_crew_credits`, so the engine extends
`CineChainEngine` (itself a `BaseChallengeEngine`) and keeps its modifiers, win/fail conditions,
Pick Next hub and (cast-only) Bridge Solver.

The connecting person is recorded on the step in `transition_metadata` as `person_id`,
`person_name`, `role` (what they were on the *new* film) and `from_role` (on the previous film),
rebuilt from the engine's own validation so a client can't claim a link that isn't there.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import ClassVar

from app.engines.cinechain import CineChainEngine
from app.engines.reunions import (
    CHARACTER_HOP_KEY,
    GOLDEN_REUNION_KEY,
    CastCredit,
    Person,
    find_character_hop,
    find_golden_reunion,
)
from app.models.run import RunStep
from app.schemas.discovery import DiscoveryCandidate, DiscoveryConnection
from app.schemas.engine import (
    ConstraintInfo,
    KeystoneActor,
    RunStats,
    SharedActorConnection,
    ValidationResult,
)
from app.services import cache_repo
from app.services.crew_roles import (
    ROLE_ACTOR,
    ROLE_DIRECTOR,
    ROLE_LABELS,
    ROLE_PRIORITY,
    role_for_job,
)
from app.services.movie_filters import is_reality_eligible
from app.utils.dates import parse_release_year

CREW_CRAFT = "crew_craft"

# `transition_metadata` keys the engine rebuilds from its own validation.
LINK_KEYS = ("person_id", "person_name", "role", "from_role", "profile_path",
             "character_in_from", "character_in_to")


@dataclass
class PersonCredits:
    """What one person was on one film: role -> character (actors) / job (crafts)."""

    person_id: int
    name: str
    profile_path: str | None = None
    roles: dict[str, str | None] = field(default_factory=dict)
    cast_order: int | None = None  # billing position, when they are in the cast


def actors_of(people: dict[int, PersonCredits]) -> list[CastCredit]:
    return [
        CastCredit(p.person_id, p.name, p.roles[ROLE_ACTOR], p.cast_order)
        for p in people.values() if ROLE_ACTOR in p.roles]


def directors_of(people: dict[int, PersonCredits]) -> list[Person]:
    return [Person(p.person_id, p.name) for p in people.values() if ROLE_DIRECTOR in p.roles]


def role_pairs(roles_a: set[str], roles_b: set[str]) -> list[tuple[str, str]]:
    """The (role on A, role on B) links one shared person makes: every role they filled on both
    films, or - when none match - their most specific role on each (the cross-role link)."""
    common = [role for role in ROLE_PRIORITY if role in roles_a and role in roles_b]
    if common:
        return [(role, role) for role in common]
    first_a = next(role for role in ROLE_PRIORITY if role in roles_a)
    first_b = next(role for role in ROLE_PRIORITY if role in roles_b)
    return [(first_a, first_b)]


def describe_link(role_from: str | None, role_to: str | None, name: str) -> str:
    if role_from and role_to and role_from != role_to:
        return f"{ROLE_LABELS[role_from]} -> {ROLE_LABELS[role_to]}: {name}"
    return f"{ROLE_LABELS.get(role_to or role_from or ROLE_ACTOR, 'Actor')}: {name}"


class CrewCraftEngine(CineChainEngine):
    game_type = CREW_CRAFT
    display_name = "Crew & Craft Trail"
    description = (
        "Chain films through anyone who worked on them: a shared actor, composer, cinematographer, "
        "writer or director - even across roles (a director who also acts)."
    )
    capabilities: ClassVar[list[str]] = [
        *(cap for cap in CineChainEngine.capabilities if cap != "bridge_swap"),
        "crew_craft",
    ]

    async def credits_of(
        self, movie_id: int, cast_limit: int | None = None
    ) -> dict[int, PersonCredits]:
        """Everyone who counts on a film: its top-billed cast plus its key crafts."""
        people: dict[int, PersonCredits] = {}
        for member in await cache_repo.get_movie_cast(self.session, self.tmdb, movie_id, cast_limit):
            person = people.setdefault(member["actor_id"], PersonCredits(
                member["actor_id"], member["name"], member["profile_path"]))
            person.roles[ROLE_ACTOR] = member["character_name"]
            person.cast_order = member["cast_order"]
        for credit in await cache_repo.get_movie_crew(self.session, self.tmdb, movie_id):
            role = role_for_job(credit.job)
            if role is None:
                continue
            person = people.setdefault(credit.person_id, PersonCredits(
                credit.person_id, credit.person_name, credit.profile_path))
            person.profile_path = person.profile_path or credit.profile_path
            person.roles.setdefault(role, credit.job)
        return people

    async def validate_primary(
        self,
        from_movie_id: int,
        to_movie_id: int,
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
    ) -> ValidationResult:
        earlier = await self.credits_of(from_movie_id, cast_limit)
        later = await self.credits_of(to_movie_id, cast_limit)
        connections: list[SharedActorConnection] = []
        for person_id, a in earlier.items():
            b = later.get(person_id)
            if b is None:
                continue
            for role_from, role_to in role_pairs(set(a.roles), set(b.roles)):
                connections.append(SharedActorConnection(
                    kind="craft", actor_id=person_id, actor_name=a.name,
                    profile_path=a.profile_path or b.profile_path,
                    character_in_from=a.roles.get(role_from) if role_from == ROLE_ACTOR else None,
                    character_in_to=b.roles.get(role_to) if role_to == ROLE_ACTOR else None,
                    role_in_from=role_from, role_in_to=role_to))
        hop = find_character_hop(actors_of(earlier), actors_of(later))
        if hop is not None and not connections and self.character_hop_links:
            connections.append(SharedActorConnection(
                kind="craft", actor_id=hop.actor_to.person_id,
                actor_name=f"{hop.actor_from.name} \u2192 {hop.actor_to.name}",
                character_in_from=hop.actor_from.character,
                character_in_to=hop.actor_to.character,
                role_in_from=ROLE_ACTOR, role_in_to=ROLE_ACTOR))
        if not connections:
            return ValidationResult(
                valid=False, connections=[],
                reason="No shared cast or crew (composer, cinematographer, writer, director) found")
        mechanic: dict = {}
        if hop is not None:
            mechanic[CHARACTER_HOP_KEY] = hop.character
        reunion = find_golden_reunion(
            directors_of(earlier), actors_of(earlier), directors_of(later), actors_of(later))
        if reunion is not None:
            mechanic[GOLDEN_REUNION_KEY] = reunion
        connections.sort(key=lambda c: (
            c.role_in_from != c.role_in_to, ROLE_PRIORITY.index(c.role_in_to or ROLE_ACTOR),
            c.actor_name))
        return ValidationResult(valid=True, connections=connections, mechanic=mechanic or None)

    def link_metadata(
        self, result: ValidationResult, client_metadata: dict | None
    ) -> dict | None:
        if not result.connections:
            return None
        claimed = client_metadata or {}
        chosen = next(
            (c for c in result.connections
             if c.actor_id == claimed.get("person_id")
             and claimed.get("role") in (None, c.role_in_to)),
            result.connections[0])
        kept = {k: v for k, v in claimed.items()
                if k not in LINK_KEYS and k not in ("actor_id", "actor_name")}
        metadata = {
            **kept, "person_id": chosen.actor_id, "person_name": chosen.actor_name,
            "role": chosen.role_in_to, "from_role": chosen.role_in_from,
            "profile_path": chosen.profile_path,
        }
        if chosen.character_in_from:
            metadata["character_in_from"] = chosen.character_in_from
        if chosen.character_in_to:
            metadata["character_in_to"] = chosen.character_in_to
        return metadata

    async def discover_candidates(
        self,
        frontier_movie_id: int,
        mode: str = "or",
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
        history: Sequence[RunStep] | None = None,
    ) -> list[DiscoveryCandidate]:
        """Pools the filmography of every frontier cast member and key craft person, tracking
        every connecting person (and the roles) per film. `mode="and"` keeps films with 2+."""
        frontier = await self.credits_of(frontier_movie_id, cast_limit)
        candidates: dict[int, DiscoveryCandidate] = {}
        roles_by_candidate: dict[int, dict[int, set[str]]] = {}
        character_by_candidate: dict[tuple[int, int], str] = {}
        for person in frontier.values():
            films = await cache_repo.get_person_filmography(
                self.session, self.tmdb, person.person_id, person.name)
            for film in films:
                movie = film.movie
                if movie.tmdb_id == frontier_movie_id or not is_reality_eligible(movie):
                    continue
                candidates.setdefault(movie.tmdb_id, DiscoveryCandidate(
                    movie_id=movie.tmdb_id, title=movie.title, poster_path=movie.poster_path,
                    release_year=parse_release_year(movie.release_date),
                    origin_country=movie.origin_country, genre_ids=movie.genre_ids or [],
                    popularity=movie.popularity))
                roles = roles_by_candidate.setdefault(movie.tmdb_id, {}).setdefault(
                    person.person_id, set())
                roles.add(film.role)
                if film.role == ROLE_ACTOR and film.character:
                    character_by_candidate.setdefault((movie.tmdb_id, person.person_id), film.character)
        for movie_id, candidate in candidates.items():
            for person in frontier.values():
                candidate_roles = roles_by_candidate[movie_id].get(person.person_id)
                if not candidate_roles:
                    continue
                for role_from, role_to in role_pairs(set(person.roles), candidate_roles):
                    candidate.connections.append(DiscoveryConnection(
                        kind="craft", actor_id=person.person_id, actor_name=person.name,
                        profile_path=person.profile_path,
                        character_in_frontier=(
                            person.roles.get(role_from) if role_from == ROLE_ACTOR else None),
                        character_in_candidate=(
                            character_by_candidate.get((movie_id, person.person_id))
                            if role_to == ROLE_ACTOR else None),
                        role_in_frontier=role_from, role_in_candidate=role_to))
        results = list(candidates.values())
        if mode == "and":
            results = [c for c in results if len(c.connections) >= 2]
        return results

    async def describe_constraint(
        self, tail_movie_id: int | None, previous_transition: dict | None,
        rules: dict | None = None,
    ) -> ConstraintInfo | None:
        return ConstraintInfo(
            kind="craft", title="Link through any shared cast or craft",
            detail="Actor, composer, cinematographer, writer or director - the same person "
                   "counts even when they held a different role on each film.")

    async def compute_stats(self, steps: list[RunStep]) -> RunStats:
        stats = await super().compute_stats(steps)
        people: Counter[int] = Counter()
        names: dict[int, str] = {}
        for step in steps:
            meta = step.transition_metadata or {}
            if step.status == "watched" and meta.get("person_id") is not None:
                people[meta["person_id"]] += 1
                names[meta["person_id"]] = meta.get("person_name") or "Unknown"
        if not people:
            return stats
        return stats.model_copy(update={"keystone_actors": [
            KeystoneActor(actor_id=pid, actor_name=names[pid], appearances=count)
            for pid, count in people.most_common()]})
