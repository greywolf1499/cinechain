"""Shared, three-valued film predicates; missing facts remain unknown."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.models.cache import CachedMovie, CachedMovieDirector
from app.utils.countries import parse_countries
from app.utils.dates import parse_release_year


@dataclass(frozen=True)
class MovieFacts:
    runtime: int | None
    year: int | None
    popularity: float | None
    language: str | None
    countries: list[str] | None
    director_genders: list[int | None]
    genre_ids: list[int] = field(default_factory=list)
    text: str = ""
    rating: float | None = None


def facts_of(
    movie: CachedMovie, directors: list[CachedMovieDirector], rating: float | None = None,
) -> MovieFacts:
    return MovieFacts(
        runtime=movie.runtime, year=parse_release_year(movie.release_date),
        popularity=movie.popularity, language=movie.original_language,
        countries=None if movie.origin_country is None else parse_countries(movie.origin_country),
        director_genders=[d.gender for d in directors], genre_ids=list(movie.genre_ids or []),
        text=" ".join(filter(None, (movie.title, movie.tagline, movie.overview))).lower(),
        rating=rating,
    )


class Predicate(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def label(self) -> str: ...
    @property
    def emoji(self) -> str: ...
    @property
    def needs(self) -> frozenset[str]: ...
    @property
    def difficulty(self) -> int: ...
    @property
    def params(self) -> dict[str, float]: ...
    @property
    def ranges(self) -> dict[str, tuple[float | None, float | None]]: ...

    def check(self, movie: CachedMovie | None, facts: MovieFacts) -> bool | None: ...


@dataclass(frozen=True)
class FilmPredicate:
    id: str
    label: str
    emoji: str
    needs: frozenset[str]
    difficulty: int
    params: dict[str, float]

    @property
    def ranges(self) -> dict[str, tuple[float | None, float | None]]:
        if "value" not in self.params:
            return {}
        field_name, comparison = self.id.rsplit("_", 1)
        value = self.params["value"]
        offset = 1 if field_name in ("year", "runtime") else 0
        return {field_name: (
            value + (offset if comparison == "gt" else 0) if comparison in ("gt", "ge") else None,
            value - (offset if comparison == "lt" else 0) if comparison in ("lt", "le") else None,
        )}

    def check(self, movie: CachedMovie | None, facts: MovieFacts) -> bool | None:
        if self.id == "female_director":
            if 1 in facts.director_genders:
                return True
            return None if not facts.director_genders or None in facts.director_genders else False
        if self.id in ("non_english", "non_us_non_english"):
            if not facts.language:
                return None
            if facts.language == "en":
                return False
            if self.id == "non_english":
                return True
            return None if facts.countries is None else "US" not in facts.countries
        field_name, comparison = self.id.rsplit("_", 1)
        value = getattr(facts, field_name)
        if value is None:
            return None
        threshold = self.params["value"]
        if comparison == "lt":
            return value < threshold
        if comparison == "gt":
            return value > threshold
        if comparison == "le":
            return value <= threshold
        return value >= threshold


_SPECS = {
    "year_lt": ("Released before", "📼", frozenset({"release_date"}), 2),
    "runtime_lt": ("Runtime under", "⏱️", frozenset({"runtime"}), 2),
    "runtime_gt": ("Runtime over", "🏔️", frozenset({"runtime"}), 3),
    "runtime_le": ("Runtime at most", "⚡", frozenset({"runtime"}), 2),
    "runtime_ge": ("Runtime at least", "🏔️", frozenset({"runtime"}), 3),
    "non_english": ("Non-English", "🌍", frozenset({"original_language"}), 2),
    "non_us_non_english": (
        "Non-English, non-US", "🌍", frozenset({"original_language", "origin_country"}), 3,
    ),
    "rating_lt": ("Rating under", "🎬", frozenset({"rating"}), 3),
    "popularity_lt": ("Popularity under", "💎", frozenset({"popularity"}), 3),
    "female_director": ("Directed by a woman", "🎥", frozenset({"directors"}), 3),
}


def predicate(predicate_id: str, **params: float) -> Predicate:
    label, emoji, needs, difficulty = _SPECS[predicate_id]
    return FilmPredicate(predicate_id, label, emoji, needs, difficulty, params)
