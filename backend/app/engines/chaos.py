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

from sqlmodel import Session

from app.models.cache import CachedMovie
from app.services.movie_filters import rating_of
from app.utils.dates import parse_release_year

ACTIVE_KEY = "active_chaos"

PRE_YEAR = 1970
B_MOVIE_RATING = 6.0
EPIC_RUNTIME = 150
SHORT_RUNTIME = 85


@dataclass(frozen=True)
class Handicap:
    id: str
    label: str
    # True/False, or None when the film's data can't tell yet.
    check: Callable[[CachedMovie, float | None], bool | None]
    needs: str  # the CachedMovie field whose absence means "fetch the detail": "", "runtime", ...


def _pre_1970(movie: CachedMovie, rating: float | None) -> bool | None:
    year = parse_release_year(movie.release_date)
    return None if year is None else year < PRE_YEAR


def _b_movie(movie: CachedMovie, rating: float | None) -> bool | None:
    return None if rating is None else rating < B_MOVIE_RATING


def _epic_length(movie: CachedMovie, rating: float | None) -> bool | None:
    return None if not movie.runtime else movie.runtime >= EPIC_RUNTIME


def _short_flick(movie: CachedMovie, rating: float | None) -> bool | None:
    return None if not movie.runtime else movie.runtime <= SHORT_RUNTIME


def _foreign_tongue(movie: CachedMovie, rating: float | None) -> bool | None:
    return None if not movie.original_language else movie.original_language != "en"


HANDICAPS: dict[str, Handicap] = {
    h.id: h
    for h in (
        Handicap("pre_1970", "Time Machine: Pre-1970 only", _pre_1970, ""),
        Handicap("b_movie", "Campy Cinema: Under 6.0 rating", _b_movie, "vote_average"),
        Handicap("epic_length", "The Long Haul: Over 150 mins", _epic_length, "runtime"),
        Handicap("short_flick", "Lightning Fast: Under 85 mins", _short_flick, "runtime"),
        Handicap(
            "foreign_tongue",
            "Passport Punch: Non-English only",
            _foreign_tongue,
            "original_language",
        ),
    )
}


def roll(rng: random.Random | None = None) -> dict[str, str]:
    handicap = (rng or random.Random()).choice(list(HANDICAPS.values()))
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
    rating = rating_of(session, movie) if handicap.id == "b_movie" else None
    if handicap.check(movie, rating) is False:
        return f"Chaos Active - {handicap.label}: {movie.title} doesn't qualify (this step only)"
    return None


def clear(rules: dict[str, Any]) -> dict[str, Any]:
    return {**rules, ACTIVE_KEY: None}
