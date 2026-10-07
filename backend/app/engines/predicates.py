"""Shared, three-valued film predicates; missing facts remain unknown."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.facets.query import FacetQuery
from app.facets.registry import named_variants
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
    movie: CachedMovie,
    directors: list[CachedMovieDirector],
    rating: float | None = None,
) -> MovieFacts:
    return MovieFacts(
        runtime=movie.runtime,
        year=parse_release_year(movie.release_date),
        popularity=movie.popularity,
        language=movie.original_language,
        countries=None if movie.origin_country is None else parse_countries(movie.origin_country),
        director_genders=[d.gender for d in directors],
        genre_ids=list(movie.genre_ids or []),
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
class FacetPredicate:
    id: str
    label: str
    emoji: str
    needs: frozenset[str]
    difficulty: int
    params: dict[str, float]

    @property
    def query(self) -> FacetQuery:
        if self.id == "female_director":
            return FacetQuery(facet="female_director", op="eq", value=True)
        if self.id in ("non_english", "non_us_non_english"):
            language = {"facet": "original_language", "op": "ne", "value": "en"}
            return FacetQuery.model_validate(
                language
                if self.id == "non_english"
                else {"facet": "non_us_non_english", "op": "eq", "value": True}
            )
        field_name, comparison = self.id.rsplit("_", 1)
        return FacetQuery.model_validate(
            {
                "facet": "release_year" if field_name == "year" else field_name,
                "op": comparison,
                "value": self.params["value"],
            }
        )

    @property
    def ranges(self) -> dict[str, tuple[float | None, float | None]]:
        if "value" not in self.params:
            return {}
        field_name, comparison = self.id.rsplit("_", 1)
        value = self.params["value"]
        offset = 1 if field_name in ("year", "runtime") else 0
        return {
            field_name: (
                value + (offset if comparison == "gt" else 0)
                if comparison in ("gt", "ge")
                else None,
                value - (offset if comparison == "lt" else 0)
                if comparison in ("lt", "le")
                else None,
            )
        }

    def check(self, movie: CachedMovie | None, facts: MovieFacts) -> bool | None:
        genders = facts.director_genders
        return self.query.evaluate(
            {
                "runtime": facts.runtime,
                "release_year": facts.year,
                "popularity": facts.popularity,
                "original_language": facts.language or None,
                "origin_country": facts.countries,
                "rating": facts.rating,
                "non_us_non_english": None
                if not facts.language
                else False
                if facts.language == "en"
                else None
                if facts.countries is None
                else "US" not in facts.countries,
                "female_director": True
                if 1 in genders
                else None
                if not genders or None in genders
                else False,
            }
        )


_SPECS = {
    "year_lt": ("Released before", "📼", frozenset({"release_date"}), 2),
    "runtime_lt": ("Runtime under", "⏱️", frozenset({"runtime"}), 2),
    "runtime_gt": ("Runtime over", "🏔️", frozenset({"runtime"}), 3),
    "runtime_le": ("Runtime at most", "⚡", frozenset({"runtime"}), 2),
    "runtime_ge": ("Runtime at least", "🏔️", frozenset({"runtime"}), 3),
    "non_english": ("Non-English", "🌍", frozenset({"original_language"}), 2),
    "non_us_non_english": (
        "Non-English, non-US",
        "🌍",
        frozenset({"original_language", "origin_country"}),
        3,
    ),
    "rating_lt": ("Rating under", "🎬", frozenset({"rating"}), 3),
    "popularity_lt": ("Popularity under", "💎", frozenset({"popularity"}), 3),
    "female_director": ("Directed by a woman", "🎥", frozenset({"directors"}), 3),
}


def predicate(predicate_id: str, **params: float) -> Predicate:
    label, emoji, needs, difficulty = _SPECS[predicate_id]
    return FacetPredicate(predicate_id, label, emoji, needs, difficulty, params)


FilmPredicate = FacetPredicate


@dataclass(frozen=True)
class QueryPredicate:
    id: str
    label: str
    query: FacetQuery
    emoji: str = ""
    difficulty: int = 1

    @property
    def params(self) -> dict:
        return {}

    @property
    def needs(self) -> frozenset[str]:
        fields = {
            "release_year": "release_date",
            "original_language": "original_language",
            "genre": "genre_ids",
            "origin_country": "origin_country",
            "region": "origin_country",
            "female_director": "directors",
            "one_word_title": "title",
            "runtime_verified": "runtime",
            "cult_classic": "rating",
            "critic_darling": "rating",
        }

        def leaves(query: FacetQuery) -> list[str]:
            return (
                [query.facet]
                if query.facet is not None
                else [f for child in query.children() for f in leaves(child)]
            )

        return frozenset(fields.get(f, f) for f in leaves(self.query))

    @property
    def ranges(self) -> dict[str, tuple[float | None, float | None]]:
        def ranges(query):
            if query.all is not None:
                result = {}
                for child in query.all:
                    for name, (low, high) in ranges(child).items():
                        old_low, old_high = result.get(name, (None, None))
                        lows = [v for v in (low, old_low) if v is not None]
                        highs = [v for v in (high, old_high) if v is not None]
                        result[name] = (max(lows) if lows else None, min(highs) if highs else None)
                return result
            field = {
                "release_year": "year",
                "runtime": "runtime",
                "runtime_verified": "runtime",
                "rating": "rating",
                "popularity": "popularity",
            }.get(query.facet)
            if field and query.op in ("lt", "le", "gt", "ge", "eq"):
                value = query.value
                assert isinstance(value, (int, float))
                offset = 1 if field in ("year", "runtime") else 0
                return {
                    field: (
                        value + (offset if query.op == "gt" else 0)
                        if query.op in ("gt", "ge", "eq")
                        else None,
                        value - (offset if query.op == "lt" else 0)
                        if query.op in ("lt", "le", "eq")
                        else None,
                    )
                }
            return {}

        return ranges(self.query)

    def check(self, movie: CachedMovie | None, facts: MovieFacts) -> bool | None:
        from app.facets import lexical, production, reception

        values = {}
        if movie is not None:
            values.update(lexical.evaluate(movie.title))
            values.update(production.evaluate(movie))
            values.update(reception.evaluate(movie, None))
        values.update(
            {
                "runtime": facts.runtime or None,
                "release_year": facts.year,
                "runtime_verified": facts.runtime if facts.runtime and facts.runtime > 0 else None,
                "rating": facts.rating,
                "popularity": facts.popularity,
                "original_language": facts.language or None,
                "origin_country": facts.countries,
                "genre": facts.genre_ids or None,
                "text": facts.text or None,
                "female_director": True
                if 1 in facts.director_genders
                else None
                if not facts.director_genders or None in facts.director_genders
                else False,
                "non_us_non_english": None
                if not facts.language
                else False
                if facts.language == "en"
                else None
                if facts.countries is None
                else "US" not in facts.countries,
            }
        )
        return self.query.evaluate(values)


def named_predicate(name: str) -> QueryPredicate:
    variant = named_variants()[name]
    return QueryPredicate(name, variant["label"], FacetQuery.model_validate(variant["query"]))
