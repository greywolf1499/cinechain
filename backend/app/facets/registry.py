"""Typed catalogue; unknown values are None, known empty sets are []."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

type FacetValue = str | float | int | bool | list[str] | list[int] | None
Kind = Literal["num", "cat", "bool", "set_num", "set_cat"]


@dataclass(frozen=True)
class Facet:
    id: str
    family: str
    kind: Kind
    label: str
    tier: int = 0
    version: int = 1
    emoji: str = ""
    relative: bool = False

    @property
    def ops(self) -> tuple[str, ...]:
        if self.id == "text":
            return ("contains",)
        if self.kind.startswith("set_"):
            return ("has_any", "has_all", "contains")
        return ("eq", "ne", "lt", "le", "gt", "ge") if self.kind == "num" else ("eq", "ne")


_FAMILIES: dict[str, dict[Kind, tuple[str, ...]]] = {
    "lexical": {
        "cat": ("title_first_letter", "title_first_letter_literal", "title_last_letter"),
        "num": (
            "title_length",
            "title_word_count",
            "title_number_count",
            "title_number_min",
            "title_number_max",
            "title_number_count_with_years",
            "title_number_min_with_years",
            "title_number_max_with_years",
        ),
        "set_num": ("title_number", "title_year_token", "title_number_with_years"),
        "bool": ("one_word_title", "title_palindrome", "title_has_subtitle", "title_sequel_marker"),
    },
    "production": {
        "num": (
            "release_year",
            "release_decade",
            "runtime",
            "runtime_verified",
            "setting_year",
            "director_film_index",
            "origin_country_count",
        ),
        "cat": ("runtime_band", "original_language", "setting_era", "collection_id"),
        "set_cat": ("origin_country", "region", "micro_era"),
        "set_num": ("genre",),
        "bool": (
            "in_collection",
            "director_debut",
            "posthumous_release",
            "female_director",
            "non_us_non_english",
        ),
    },
    "reception": {
        "num": ("imdb_rating", "tomatometer", "metacritic", "rating", "critic_audience_gap"),
        "bool": ("cult_classic", "critic_darling", "box_office_bomb", "sleeper_hit"),
    },
}
TIER_ONE = {"director_debut", "director_film_index", "posthumous_release", "female_director"}
FAMILY_VERSIONS = {"lexical": 2, "production": 2, "reception": 1}
CATALOGUE = {
    name: Facet(
        name,
        family,
        kind,
        name.replace("_", " ").capitalize(),
        int(name in TIER_ONE),
        version=FAMILY_VERSIONS[family],
    )
    for family, kinds in _FAMILIES.items()
    for kind, names in kinds.items()
    for name in names
}
_RELATIVE: tuple[tuple[str, Kind], ...] = (
    ("popularity", "num"),
    ("popularity_percentile", "num"),
    ("vote_count_band", "cat"),
)
for _name, _kind in _RELATIVE:
    CATALOGUE[_name] = Facet(
        _name, "reception", _kind, _name.replace("_", " ").capitalize(), relative=True
    )
CATALOGUE["text"] = Facet("text", "lexical", "cat", "Title or plot keyword", relative=True)
CATALOGUE["canon"] = Facet("canon", "production", "bool", "Curated canon", relative=True)


def named_variants() -> dict[str, dict]:
    """One source for named concepts; rolling years are resolved per request."""

    def leaf(facet, op, value):
        return {"facet": facet, "op": op, "value": value}

    return {
        "short": {"label": "Short", "query": leaf("runtime_verified", "lt", 90)},
        "epic": {"label": "Epic", "query": leaf("runtime_verified", "gt", 150)},
        "classic": {"label": "Classic", "query": leaf("release_year", "lt", 1970)},
        "modern": {"label": "Modern only", "query": leaf("release_year", "ge", 1970)},
        "recent": {
            "label": "Recent",
            "query": leaf("release_year", "ge", datetime.now(UTC).year - 5),
        },
        "vintage": {"label": "Time capsule", "query": leaf("release_year", "lt", 1960)},
        "hidden_gem": {"label": "Hidden gem", "query": leaf("popularity", "lt", 12)},
        "campy": {"label": "Campy cinema", "query": leaf("rating", "lt", 6)},
        "crowd_pleaser": {"label": "Crowd-pleaser", "query": leaf("rating", "ge", 6)},
        "non_english": {
            "label": "Non-English only",
            "query": leaf("original_language", "ne", "en"),
        },
        "english": {"label": "English only", "query": leaf("original_language", "eq", "en")},
        "foreign": {"label": "Foreign horizon", "query": leaf("non_us_non_english", "eq", True)},
        "female_director": {
            "label": "Directed by a woman",
            "query": leaf("female_director", "eq", True),
        },
        "one_word": {"label": "One-word titles", "query": leaf("one_word_title", "eq", True)},
        "cult_classic": {"label": "Cult classic", "query": leaf("cult_classic", "eq", True)},
        "asian": {
            "label": "Asian cinema",
            "query": leaf(
                "region",
                "has_any",
                [
                    "central_asia",
                    "eastern_asia",
                    "south_eastern_asia",
                    "southern_asia",
                    "western_asia",
                ],
            ),
        },
        "european": {
            "label": "European cinema",
            "query": leaf(
                "region",
                "has_any",
                ["eastern_europe", "northern_europe", "southern_europe", "western_europe"],
            ),
        },
        "latam_africa": {
            "label": "Latin American or African cinema",
            "query": leaf(
                "region",
                "has_any",
                [
                    "caribbean",
                    "central_america",
                    "south_america",
                    "northern_africa",
                    "eastern_africa",
                    "middle_africa",
                    "southern_africa",
                    "western_africa",
                ],
            ),
        },
        "chaser_trigger": {
            "label": "Heavy film",
            "query": {"any": [leaf("runtime", "ge", 135), leaf("genre", "contains", 18)]},
        },
        "chaser": {
            "label": "Palate cleanser",
            "query": {
                "all": [
                    leaf("runtime", "gt", 0),
                    leaf("runtime", "le", 95),
                    leaf("genre", "has_any", [16, 35]),
                ]
            },
        },
    }


FAMILY_VERSIONS = {
    family: max(f.version for f in CATALOGUE.values() if f.family == family) for family in _FAMILIES
}
