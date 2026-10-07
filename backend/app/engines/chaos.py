"""The Chaos Button: a one-step random handicap on the next film.

Rolling it stores `rules_config["active_chaos"] = {"id", "label"}`. While set, the next film must
satisfy the handicap - a hard block no wildcard can buy, applied by `validate_next_step` and to the
Pick Next pool - and logging any step clears it (`active_chaos` back to None). Films whose data
can't tell (no runtime / language / rating yet) are never blocked; the engine fetches their detail
first so that is rare.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.engines.predicates import Predicate, facts_of, named_predicate
from app.engines.rulebook import RuleSection
from app.facets.registry import named_variants

RULEBOOK = RuleSection(
    "Play through a one-step Chaos handicap.",
    ["The active handicap is {chaos_label}. Satisfy it on the next logged film."],
    [
        "Log a film to clear the handicap.",
        "If a fact stays unknown after checking, it does not block.",
    ],
    ["Wildcards cannot bypass a known handicap violation."],
    ["Read the handicap before selecting a connector; narrow the pool before committing."],
    ["wildcard"],
)

from sqlmodel import Session

from app.models.cache import CachedMovie

ACTIVE_KEY = "active_chaos"

PRE_YEAR = named_variants()["classic"]["query"]["value"]
B_MOVIE_RATING = named_variants()["campy"]["query"]["value"]
EPIC_RUNTIME = named_variants()["epic"]["query"]["value"]
SHORT_RUNTIME = named_variants()["short"]["query"]["value"]


@dataclass(frozen=True)
class Handicap:
    id: str
    label: str
    # True/False, or None when the film's data can't tell yet.
    check: Callable[[CachedMovie, float | None], bool | None]
    needs: str  # the CachedMovie field whose absence means "fetch the detail": "", "runtime", ...
    predicate: Predicate


def _pre_1970(movie: CachedMovie, rating: float | None) -> bool | None:
    return named_predicate("classic").check(movie, facts_of(movie, []))


def _b_movie(movie: CachedMovie, rating: float | None) -> bool | None:
    return named_predicate("campy").check(movie, facts_of(movie, [], rating))


def _epic_length(movie: CachedMovie, rating: float | None) -> bool | None:
    return None if not movie.runtime else named_predicate("epic").check(movie, facts_of(movie, []))


def _short_flick(movie: CachedMovie, rating: float | None) -> bool | None:
    return None if not movie.runtime else named_predicate("short").check(movie, facts_of(movie, []))


def _foreign_tongue(movie: CachedMovie, rating: float | None) -> bool | None:
    return named_predicate("non_english").check(movie, facts_of(movie, []))


HANDICAPS: dict[str, Handicap] = {
    h.id: h
    for h in (
        Handicap(
            "pre_1970",
            "Time Machine: Pre-1970 only",
            _pre_1970,
            "",
            named_predicate("classic"),
        ),
        Handicap(
            "b_movie",
            "Campy Cinema: Under 6.0 rating",
            _b_movie,
            "vote_average",
            named_predicate("campy"),
        ),
        Handicap(
            "epic_length",
            "The Long Haul: Over 150 mins",
            _epic_length,
            "runtime",
            named_predicate("epic"),
        ),
        Handicap(
            "short_flick",
            "Lightning Fast: Under 90 mins",
            _short_flick,
            "runtime",
            named_predicate("short"),
        ),
        Handicap(
            "foreign_tongue",
            "Passport Punch: Non-English only",
            _foreign_tongue,
            "original_language",
            named_predicate("non_english"),
        ),
    )
}
for _id, _concept, _needs in (
    ("modern_only", "modern", "release_date"),
    ("crowd_pleaser", "crowd_pleaser", "vote_average"),
    ("english_only", "english", "original_language"),
    ("one_word_titles", "one_word", ""),
    ("cult_classics", "cult_classic", "vote_average"),
):
    _test = named_predicate(_concept)
    HANDICAPS[_id] = Handicap(
        _id,
        _test.label,
        lambda movie, rating, test=_test: test.check(movie, facts_of(movie, [], rating)),
        _needs,
        _test,
    )


def roll(rng: random.Random | None = None, feasible: list[str] | None = None) -> dict[str, Any]:
    pool = list(HANDICAPS.values()) if feasible is None else [HANDICAPS[key] for key in feasible]
    if not pool:
        raise ValueError("No feasible Chaos handicap")
    handicap = (rng or random.Random()).choice(pool)
    return {"id": handicap.id, "label": handicap.label}


def active(rules: dict | None) -> Handicap | None:
    chaos = (rules or {}).get(ACTIVE_KEY)
    return HANDICAPS.get(chaos.get("id")) if isinstance(chaos, dict) else None


def needs_detail(movie: CachedMovie, rules: dict | None) -> bool:
    """Does this film still lack the field the active handicap judges on?"""
    handicap = active(rules)
    return bool(handicap and handicap.needs and getattr(movie, handicap.needs) is None)


def violation(session: Session, movie: CachedMovie, rules: dict | None) -> str | None:
    """Why `movie` breaks the active handicap (None = fine, or nothing active, or unknown)."""
    handicap = active(rules)
    if handicap is None:
        return None
    from app.services import feasibility
    from app.services.movie_filters import rating_of

    verdict = (
        feasibility.verdict(session, handicap.predicate, movie.tmdb_id)
        if session.get(CachedMovie, movie.tmdb_id) is not None
        else handicap.check(movie, rating_of(session, movie))
    )
    if verdict is False:
        return f"Chaos Active - {handicap.label}: {movie.title} doesn't qualify (this step only)"
    return None


def clear(rules: dict[str, Any]) -> dict[str, Any]:
    return {**rules, ACTIVE_KEY: None}
