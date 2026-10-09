"""Shared link policies used by graph and attribute-driven game modes."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from app.engines.cinechain import CineChainEngine
from app.engines.crew_craft import CrewCraftEngine
from app.models.run import RunStep
from app.schemas.engine import SharedActorConnection, ValidationResult
from app.services import cache_repo


@runtime_checkable
class LinkPolicy(Protocol):
    key: str
    label: str
    graph: bool
    link_metadata: str

    async def validate(
        self,
        engine: Any,
        from_movie_id: int,
        to_movie_id: int,
        cast_limit: int | None,
        rules: dict | None,
        previous_transition: dict | None,
        history: Sequence[RunStep] | None,
    ) -> ValidationResult: ...


class TraversalPolicy:
    def __init__(
        self,
        key: str,
        label: str,
        *,
        graph: bool,
        link_metadata: str,
    ) -> None:
        self.key = key
        self.label = label
        self.graph = graph
        self.link_metadata = link_metadata

    async def validate(
        self,
        engine: Any,
        from_movie_id: int,
        to_movie_id: int,
        cast_limit: int | None,
        rules: dict | None,
        previous_transition: dict | None,
        history: Sequence[RunStep] | None,
    ) -> ValidationResult:
        if self.key == "shared_cast":
            return await CineChainEngine.validate_primary(
                engine, from_movie_id, to_movie_id, cast_limit, rules, previous_transition
            )
        if self.key == "shared_director":
            from_rows = await cache_repo.get_movie_directors(
                engine.session, engine.tmdb, from_movie_id
            )
            to_rows = await cache_repo.get_movie_directors(engine.session, engine.tmdb, to_movie_id)
            to_ids = {director.person_id for director in to_rows}
            connections = [
                SharedActorConnection(
                    kind="director",
                    actor_id=director.person_id,
                    actor_name=director.name,
                )
                for director in from_rows
                if director.person_id in to_ids
            ]
            return ValidationResult(
                valid=bool(connections),
                connections=connections,
                reason=None if connections else "No shared director found",
            )
        if self.key == "shared_any_person":
            return await CrewCraftEngine.validate_primary(
                engine, from_movie_id, to_movie_id, cast_limit, rules, previous_transition
            )
        if self.key == "draft":
            return ValidationResult(valid=True, reason="Seeded draft deal")
        return await engine.validate_attribute_link(self.key, from_movie_id, to_movie_id, rules)


TRAVERSALS: dict[str, LinkPolicy] = {
    policy.key: policy
    for policy in (
        TraversalPolicy("shared_cast", "Shared cast", graph=True, link_metadata="actor"),
        TraversalPolicy("shared_director", "Shared director", graph=True, link_metadata="director"),
        TraversalPolicy(
            "shared_any_person", "Shared cast or crew", graph=True, link_metadata="person"
        ),
        TraversalPolicy("genre_overlap", "Genre overlap", graph=False, link_metadata="genre"),
        TraversalPolicy("decade_adjacent", "Adjacent decades", graph=False, link_metadata="decade"),
        TraversalPolicy("language", "Same language", graph=False, link_metadata="language"),
        TraversalPolicy("shared_trope", "Shared trope", graph=False, link_metadata="trope"),
        TraversalPolicy("draft", "Seeded draft", graph=False, link_metadata="draft"),
    )
}


def get_policy(key: str | None) -> LinkPolicy:
    selected = key or "shared_cast"
    try:
        return TRAVERSALS[selected]
    except KeyError as exc:
        raise ValueError(f"Unknown Tug traversal: {selected}") from exc


def public_catalogue() -> list[dict[str, Any]]:
    return [
        {
            "id": policy.key,
            "label": policy.label,
            "graph": policy.graph,
            "link_metadata": policy.link_metadata,
        }
        for policy in TRAVERSALS.values()
    ]
