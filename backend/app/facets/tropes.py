"""Curated trope vocabulary and source-aware reads for gameplay."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from sqlmodel import Session, col, delete, select

from app.facets.models import MovieFacet


@dataclass(frozen=True)
class Trope:
    slug: str
    aliases: tuple[str, ...]
    definition: str
    required_genres: frozenset[int] = frozenset()


TAXONOMY = (
    Trope("alien-invasion", ("AlienInvasion",), "Extraterrestrial invaders threaten humanity.", frozenset({878})),
    Trope("cyberpunk", ("Cyberpunk",), "A high-tech dystopia shaped by cybernetics, hackers and oppressive corporations.", frozenset({878})),
    Trope("crime", ("Crime",), "Characters plan, commit or investigate criminal acts."),
    Trope("double-cross", ("DoubleCross",), "An ally betrays a plan or agreement for personal gain."),
    Trope("enemies-to-lovers", ("EnemiesToLovers",), "Adversaries gradually develop a romantic relationship."),
    Trope("found-family", ("FoundFamily",), "Unrelated people form close bonds and become a chosen family."),
    Trope("heist", ("Heist",), "A team plans and carries out a robbery."),
    Trope("love-story", ("LoveStory",), "People fall in love and face obstacles to their relationship."),
    Trope("revenge", ("Revenge",), "A wronged character seeks revenge for a past harm."),
    Trope("romance", ("Romance",), "A romantic relationship is central to the story."),
    Trope("space-opera", ("SpaceOpera",), "Interstellar adventures and conflict span alien worlds.", frozenset({878})),
    Trope("supernatural-horror", ("SupernaturalHorror",), "Supernatural forces threaten or terrorize the characters.", frozenset({27})),
    Trope("time-loop", ("TimeLoop",), "Characters repeatedly relive the same period of time.", frozenset({878, 14})),
    Trope("time-travel", ("TimeTravel",), "Characters travel between different points in time.", frozenset({878, 14})),
    Trope("unreliable-narrator", ("UnreliableNarrator",), "The story is told by a narrator whose account cannot be fully trusted."),
    Trope("never-asked", ("NeverAsked",), "A character is given an unwanted responsibility or role."),
    Trope("zombie-apocalypse", ("ZombieApocalypse",), "Survivors struggle as the undead overrun society.", frozenset({27, 878})),
)


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")


BY_SLUG = {trope.slug: trope for trope in TAXONOMY}
ALIASES = {
    _key(alias): trope.slug
    for trope in TAXONOMY
    for alias in (trope.slug, *trope.aliases)
}
DEFINITIONS = {trope.slug: trope.definition for trope in TAXONOMY}
GENRE_REQUIREMENTS = {trope.slug: trope.required_genres for trope in TAXONOMY if trope.required_genres}


def normalize_trope(value: str) -> str | None:
    return ALIASES.get(_key(value))


def genre_allowed(slug: str, genre_ids: Iterable[int] | None) -> bool:
    required = GENRE_REQUIREMENTS.get(slug)
    return required is None or bool(genre_ids and required.intersection(genre_ids))


def evidence_for(session: Session, movie_ids: Iterable[int]) -> dict[int, dict[str, list[str]]]:
    """Return trusted source unions; unknown TVTropes mappings stay out of gameplay."""
    ids = list(dict.fromkeys(movie_ids))
    if not ids:
        return {}
    rows = session.exec(
        select(MovieFacet).where(
            col(MovieFacet.movie_id).in_(ids),
            MovieFacet.facet_id == "trope",
            col(MovieFacet.confidence).is_not(None),
        )
    ).all()
    result: dict[int, dict[str, set[str]]] = {}
    for row in rows:
        slug = normalize_trope(row.value_text)
        if slug is None or row.source not in {"llm", "tvtropes", "manual"}:
            continue
        result.setdefault(row.movie_id, {}).setdefault(slug, set()).add(row.source)
    return {
        movie_id: {slug: sorted(sources) for slug, sources in movie_tags.items()}
        for movie_id, movie_tags in result.items()
    }


def trusted_tropes(session: Session, movie_ids: Iterable[int]) -> dict[int, list[str]]:
    return {
        movie_id: sorted(by_slug)
        for movie_id, by_slug in evidence_for(session, movie_ids).items()
    }


def replace_source(
    session: Session,
    movie_id: int,
    source: str,
    values: dict[str, float | None],
    source_urls: dict[str, str] | None = None,
) -> None:
    if source not in {"llm", "tvtropes", "manual"}:
        raise ValueError(f"Unsupported trope source: {source}")
    session.exec(
        delete(MovieFacet).where(
            MovieFacet.movie_id == movie_id,
            MovieFacet.facet_id == "trope",
            MovieFacet.source == source,
        )
    )
    for slug, confidence in values.items():
        session.add(
            MovieFacet(
                movie_id=movie_id,
                facet_id="trope",
                value_text=slug,
                source=source,
                confidence=confidence,
                source_url=(source_urls or {}).get(slug),
            )
        )
    session.flush()


def invalidate_movie_evidence(
    session: Session,
    movie_id: int,
    *,
    overview_changed: bool = False,
    work_identity_changed: bool = False,
    genre_ids: Iterable[int] | None = None,
) -> None:
    """Drop source evidence invalidated by changed plot/work facts and reapply the genre gate."""
    stale_sources = set()
    if overview_changed:
        stale_sources.update({"llm", "tvtropes"})
    if work_identity_changed:
        stale_sources.add("tvtropes")
    if stale_sources:
        session.exec(
            delete(MovieFacet).where(
                MovieFacet.movie_id == movie_id,
                MovieFacet.facet_id == "trope",
                col(MovieFacet.source).in_(stale_sources),
            )
        )
    if genre_ids is None:
        return
    rows = session.exec(
        select(MovieFacet).where(
            MovieFacet.movie_id == movie_id,
            MovieFacet.facet_id == "trope",
            col(MovieFacet.confidence).is_not(None),
        )
    ).all()
    for row in rows:
        slug = normalize_trope(row.value_text)
        if slug is not None and not genre_allowed(slug, genre_ids):
            session.delete(row)
