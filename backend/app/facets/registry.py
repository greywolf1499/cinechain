"""Typed catalogue; unknown values are None, known empty sets are []."""

from dataclasses import dataclass
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
        if self.kind.startswith("set_"):
            return ("has_any", "has_all", "contains")
        return ("eq", "ne", "lt", "le", "gt", "ge") if self.kind == "num" else ("eq", "ne")


_FAMILIES: dict[str, dict[Kind, tuple[str, ...]]] = {
    "lexical": {
        "cat": ("title_first_letter", "title_last_letter"),
        "num": ("title_length", "title_word_count"),
        "set_num": ("title_number", "title_year_token"),
        "bool": ("one_word_title", "title_palindrome", "title_has_subtitle", "title_sequel_marker"),
    },
    "production": {
        "num": ("release_year", "release_decade", "runtime", "setting_year", "director_film_index"),
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
CATALOGUE = {
    name: Facet(name, family, kind, name.replace("_", " ").capitalize(), int(name in TIER_ONE))
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

FAMILY_VERSIONS = {
    family: max(f.version for f in CATALOGUE.values() if f.family == family) for family in _FAMILIES
}
