"""Facet-backed Tug poles, balance checks, and deterministic draft deals."""

from __future__ import annotations

import random
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from app.facets.query import FacetQuery

TEAM_A = "team_a"
TEAM_B = "team_b"
NEUTRAL = "neutral"


@dataclass(frozen=True)
class BalanceSpec:
    min_pole_rate: float = 0.05
    min_pole_ratio: float = 0.5
    max_pole_ratio: float = 2.0
    min_neutral_rate: float = 0.1
    max_neutral_rate: float = 0.5
    min_bridge_density: float = 0.02
    graph_deadline_seconds: float = 1.5


@dataclass(frozen=True)
class PoleSpec:
    key: str
    label: str
    query: FacetQuery


@dataclass(frozen=True)
class TraversalRule:
    id: str
    label: str
    graph: bool
    link_metadata: str


@dataclass(frozen=True)
class TugPlane:
    id: str
    label: str
    blurb: str
    params_schema: dict[str, Any]
    defaults: dict[str, Any]
    pole_a: PoleSpec
    pole_b: PoleSpec
    allowed_traversals: tuple[str, ...]
    default_traversal: str
    frozen: bool = False

    def territory(self, facts: Mapping[str, Any]) -> str | None:
        a = self.pole_a.query.evaluate(facts)
        b = self.pole_b.query.evaluate(facts)
        if a is None or b is None:
            return None
        if a and not b:
            return TEAM_A
        if b and not a:
            return TEAM_B
        return NEUTRAL

    def unknown(self, facts: Mapping[str, Any]) -> bool:
        return (
            self.pole_a.query.evaluate(facts) is None or self.pole_b.query.evaluate(facts) is None
        )

    def snapshot(self, params: dict[str, Any], traversal: str, balance: dict[str, Any]) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "blurb": self.blurb,
            "params": params,
            "poles": {
                TEAM_A: {"key": self.pole_a.key, "label": self.pole_a.label},
                TEAM_B: {"key": self.pole_b.key, "label": self.pole_b.label},
                NEUTRAL: {"label": "Neutral band"},
            },
            "queries": {
                TEAM_A: self.pole_a.query.model_dump(mode="json", by_alias=True, exclude_none=True),
                TEAM_B: self.pole_b.query.model_dump(mode="json", by_alias=True, exclude_none=True),
            },
            "traversal": traversal,
            "overlap_policy": "contested_is_neutral",
            "balance": balance,
        }


BALANCE = BalanceSpec()


FacetOperator = Literal["eq", "ne", "lt", "le", "gt", "ge", "contains", "has_any", "has_all"]

PLANE_PARAMETERS: dict[str, dict[str, tuple[str, Any, int | float | None, int | float | None]]] = {
    "bipolar_decades": {
        "team_a_before": ("integer", 1970, 1900, 2030),
        "team_b_from": ("integer", 2000, 1900, 2030),
    },
    "country_pair": {
        "team_a_country": ("string", "FR", None, None),
        "team_b_country": ("string", "IT", None, None),
    },
    "language_pair": {
        "team_a_language": ("string", "fr", None, None),
        "team_b_language": ("string", "it", None, None),
    },
    "genre_clusters": {
        "team_a_genres": ("integer[]", [18, 80, 9648], None, None),
        "team_b_genres": ("integer[]", [35, 16, 10751], None, None),
    },
    "runtime_poles": {
        "team_a_under": ("integer", 90, 40, 240),
        "team_b_over": ("integer", 150, 40, 300),
    },
    "setting_eras": {
        "team_a_era": ("string", "ancient", None, None),
        "team_b_era": ("string", "contemporary", None, None),
    },
    "critic_audience": {
        "critic_min": ("integer", 80, 0, 100),
        "audience_min": ("number", 7.0, 0, 10),
    },
}


def _leaf(facet: str, op: FacetOperator, value: Any) -> FacetQuery:
    return FacetQuery(facet=facet, op=op, value=value)


def build_plane(plane_id: str, params: dict[str, Any] | None = None) -> TugPlane:
    if plane_id in ("era_classic", "geo_west_rest"):
        return legacy_plane(plane_id)
    definitions = PLANE_PARAMETERS.get(plane_id)
    if definitions is None:
        raise ValueError(f"Unknown Tug plane: {plane_id}")
    supplied = params or {}
    unknown = set(supplied) - set(definitions)
    if unknown:
        raise ValueError(f"Unknown parameter for {plane_id}: {', '.join(sorted(unknown))}")
    validated: dict[str, Any] = {}
    for key, (kind, default, minimum, maximum) in definitions.items():
        value = supplied.get(key, default)
        if kind == "integer":
            valid = isinstance(value, int) and not isinstance(value, bool)
        elif kind == "number":
            valid = isinstance(value, (int, float)) and not isinstance(value, bool)
        elif kind == "integer[]":
            valid = isinstance(value, list) and all(
                isinstance(item, int) and not isinstance(item, bool) for item in value
            )
        else:
            valid = isinstance(value, str) and bool(value.strip())
        if not valid:
            raise ValueError(f"{key} must be {kind}")
        if minimum is not None and value < minimum:
            raise ValueError(f"{key} must be at least {minimum}")
        if maximum is not None and value > maximum:
            raise ValueError(f"{key} must be at most {maximum}")
        validated[key] = value
    params = validated
    if plane_id == "bipolar_decades":
        cutoff_a = int(params.get("team_a_before", 1970))
        cutoff_b = int(params.get("team_b_from", 2000))
        return TugPlane(
            plane_id,
            "Bipolar decades",
            "Classic films pull for A, recent films for B; years between are neutral.",
            {
                "team_a_before": {"type": "integer", "minimum": 1900, "maximum": 2030},
                "team_b_from": {"type": "integer", "minimum": 1900, "maximum": 2030},
            },
            {"team_a_before": cutoff_a, "team_b_from": cutoff_b},
            PoleSpec(TEAM_A, f"Before {cutoff_a}", _leaf("release_year", "lt", cutoff_a)),
            PoleSpec(TEAM_B, f"{cutoff_b} and later", _leaf("release_year", "ge", cutoff_b)),
            ("shared_cast", "shared_director", "genre_overlap", "draft"),
            "shared_cast",
        )
    if plane_id == "country_pair":
        country_a = str(params.get("team_a_country", "FR")).upper()
        country_b = str(params.get("team_b_country", "IT")).upper()
        return TugPlane(
            plane_id,
            "Country pair",
            "Two production countries pull against one another.",
            {
                "team_a_country": {"type": "string", "default": "FR"},
                "team_b_country": {"type": "string", "default": "IT"},
            },
            {"team_a_country": country_a, "team_b_country": country_b},
            PoleSpec(TEAM_A, country_a, _leaf("origin_country", "has_any", [country_a])),
            PoleSpec(TEAM_B, country_b, _leaf("origin_country", "has_any", [country_b])),
            ("shared_cast", "shared_director", "genre_overlap", "draft"),
            "shared_cast",
        )
    if plane_id == "language_pair":
        lang_a = str(params.get("team_a_language", "fr")).lower()
        lang_b = str(params.get("team_b_language", "it")).lower()
        return TugPlane(
            plane_id,
            "Language pair",
            "Original-language films pull against a contrasting language.",
            {
                "team_a_language": {"type": "string", "default": "fr"},
                "team_b_language": {"type": "string", "default": "it"},
            },
            {"team_a_language": lang_a, "team_b_language": lang_b},
            PoleSpec(TEAM_A, lang_a.upper(), _leaf("original_language", "eq", lang_a)),
            PoleSpec(TEAM_B, lang_b.upper(), _leaf("original_language", "eq", lang_b)),
            ("shared_cast", "shared_director", "genre_overlap", "draft"),
            "shared_cast",
        )
    if plane_id == "genre_clusters":
        genres_a = list(params.get("team_a_genres", [18, 80, 9648]))
        genres_b = list(params.get("team_b_genres", [35, 16, 10751]))
        return TugPlane(
            plane_id,
            "Genre clusters",
            "Dramatic crime and mystery face comedy, animation and family films.",
            {"team_a_genres": {"type": "integer[]"}, "team_b_genres": {"type": "integer[]"}},
            {"team_a_genres": genres_a, "team_b_genres": genres_b},
            PoleSpec(TEAM_A, "Drama · Crime · Mystery", _leaf("genre", "has_any", genres_a)),
            PoleSpec(TEAM_B, "Comedy · Animation · Family", _leaf("genre", "has_any", genres_b)),
            ("shared_cast", "shared_director", "genre_overlap", "decade_adjacent", "draft"),
            "shared_cast",
        )
    if plane_id == "runtime_poles":
        short = int(params.get("team_a_under", 90))
        long = int(params.get("team_b_over", 150))
        return TugPlane(
            plane_id,
            "Runtime poles",
            "Short films face epics, with the middle runtimes neutral.",
            {
                "team_a_under": {"type": "integer", "minimum": 40, "maximum": 240},
                "team_b_over": {"type": "integer", "minimum": 40, "maximum": 300},
            },
            {"team_a_under": short, "team_b_over": long},
            PoleSpec(TEAM_A, f"Under {short} min", _leaf("runtime_verified", "lt", short)),
            PoleSpec(TEAM_B, f"Over {long} min", _leaf("runtime_verified", "gt", long)),
            ("shared_cast", "shared_director", "genre_overlap", "draft"),
            "shared_cast",
        )
    if plane_id == "setting_eras":
        era_a = str(params.get("team_a_era", "ancient"))
        era_b = str(params.get("team_b_era", "contemporary"))
        return TugPlane(
            plane_id,
            "Setting eras",
            "Stories set in distinct historical periods pull against one another.",
            {
                "team_a_era": {"type": "string", "default": "ancient"},
                "team_b_era": {"type": "string", "default": "contemporary"},
            },
            {"team_a_era": era_a, "team_b_era": era_b},
            PoleSpec(TEAM_A, era_a.title(), _leaf("setting_era", "eq", era_a)),
            PoleSpec(TEAM_B, era_b.title(), _leaf("setting_era", "eq", era_b)),
            ("shared_cast", "shared_director", "genre_overlap", "draft"),
            "shared_cast",
        )
    if plane_id == "critic_audience":
        critic = int(params.get("critic_min", 80))
        audience = float(params.get("audience_min", 7))
        return TugPlane(
            plane_id,
            "Critic vs audience",
            "Critic favourites pull against highly rated audience picks.",
            {
                "critic_min": {"type": "integer", "minimum": 0, "maximum": 100},
                "audience_min": {"type": "number", "minimum": 0, "maximum": 10},
            },
            {"critic_min": critic, "audience_min": audience},
            PoleSpec(TEAM_A, f"Critics {critic}+", _leaf("tomatometer", "ge", critic)),
            PoleSpec(TEAM_B, f"Audience {audience}+", _leaf("rating", "ge", audience)),
            ("shared_cast", "shared_director", "genre_overlap", "draft"),
            "shared_cast",
        )
    raise ValueError(f"Unknown Tug plane: {plane_id}")


def legacy_plane(plane_id: str) -> TugPlane:
    if plane_id == "era_classic":
        plane = build_plane("bipolar_decades", {"team_a_before": 1975, "team_b_from": 2006})
    elif plane_id == "geo_west_rest":
        western_codes = ["US", "GB", "FR", "DE", "IT", "ES", "CA", "AU"]
        non_western = FacetQuery.model_validate(
            {"not": _leaf("origin_country", "has_any", western_codes)}
        )
        plane = TugPlane(
            plane_id,
            "West vs rest",
            "Legacy West/rest split retained for saved runs.",
            {},
            {},
            PoleSpec(TEAM_A, "Western", _leaf("origin_country", "has_any", western_codes)),
            PoleSpec(TEAM_B, "Rest of world", non_western),
            ("shared_cast",),
            "shared_cast",
            frozen=True,
        )
    else:
        raise ValueError(f"Unknown legacy Tug plane: {plane_id}")
    return TugPlane(
        plane_id,
        plane.label,
        plane.blurb,
        plane.params_schema,
        plane.defaults,
        plane.pole_a,
        plane.pole_b,
        plane.allowed_traversals,
        plane.default_traversal,
        frozen=True,
    )


NEW_PLANE_IDS = (
    "bipolar_decades",
    "country_pair",
    "language_pair",
    "genre_clusters",
    "runtime_poles",
    "setting_eras",
    "critic_audience",
)
LEGACY_PLANE_IDS = ("era_classic", "geo_west_rest")


def catalogue() -> list[dict[str, Any]]:
    from app.engines.traversal import TRAVERSALS

    result = []
    for plane_id in NEW_PLANE_IDS:
        plane = build_plane(plane_id)
        result.append(
            {
                "id": plane.id,
                "label": plane.label,
                "blurb": plane.blurb,
                "params_schema": plane.params_schema,
                "defaults": plane.defaults,
                "poles": {
                    TEAM_A: {
                        "label": plane.pole_a.label,
                        "query": plane.pole_a.query.model_dump(
                            mode="json", by_alias=True, exclude_none=True
                        ),
                    },
                    TEAM_B: {
                        "label": plane.pole_b.label,
                        "query": plane.pole_b.query.model_dump(
                            mode="json", by_alias=True, exclude_none=True
                        ),
                    },
                },
                "allowed_traversals": [
                    {
                        "id": key,
                        "label": TRAVERSALS[key].label,
                        "graph": TRAVERSALS[key].graph,
                    }
                    for key in plane.allowed_traversals
                ],
                "default_traversal": plane.default_traversal,
                "frozen": plane.frozen,
            }
        )
    return result


def _facets(query: FacetQuery) -> set[str]:
    if query.facet:
        return {query.facet}
    return set().union(*(_facets(child) for child in query.children()))


def territory_from_facts(plane: TugPlane, facts: Mapping[str, Any]) -> str | None:
    return plane.territory(facts)


def assess_facts(plane: TugPlane, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    known = 0
    counts = {TEAM_A: 0, TEAM_B: 0, NEUTRAL: 0}
    unknown = 0
    for facts in rows:
        if plane.unknown(facts):
            unknown += 1
            continue
        known += 1
        counts[plane.territory(facts) or NEUTRAL] += 1
    rate_a = counts[TEAM_A] / total if total else 0.0
    rate_b = counts[TEAM_B] / total if total else 0.0
    neutral_rate = counts[NEUTRAL] / known if known else 0.0
    ratio = rate_a / rate_b if rate_b else float("inf") if rate_a else 1.0
    issues: list[str] = []
    if rate_a < BALANCE.min_pole_rate or rate_b < BALANCE.min_pole_rate:
        issues.append("Each pole must cover at least 5% of eligible cached films.")
    if not BALANCE.min_pole_ratio <= ratio <= BALANCE.max_pole_ratio:
        issues.append("Pole pass rates must be within a 0.5–2.0 ratio.")
    if known and not BALANCE.min_neutral_rate <= neutral_rate <= BALANCE.max_neutral_rate:
        issues.append("The neutral/contested band must cover 10–50% of known films.")
    return {
        "eligible": total,
        "known": known,
        "unknown": unknown,
        "team_a": counts[TEAM_A],
        "team_b": counts[TEAM_B],
        "neutral": counts[NEUTRAL],
        "team_a_rate": rate_a,
        "team_b_rate": rate_b,
        "neutral_rate": neutral_rate,
        "pole_ratio": ratio if ratio != float("inf") else None,
        "balanced": not issues,
        "issues": issues,
    }


def check_balance(
    session: Any,
    plane: TugPlane,
    rows: Mapping[int, Any] | None = None,
    *,
    bridge_density: float | None = None,
    traversal: str | None = None,
) -> dict[str, Any]:
    from app.facets.query import values_for
    from app.services import feasibility

    movies = rows if rows is not None else feasibility.movies(session)
    eligible = [
        movie_id
        for movie_id, movie in movies.items()
        if getattr(movie, "status", None) != "Canceled"
        and getattr(movie, "status", None) != "Planned"
    ]
    facets = _facets(plane.pole_a.query) | _facets(plane.pole_b.query)
    facts = values_for(session, eligible, facets)
    result = assess_facts(plane, [facts.get(movie_id, {}) for movie_id in eligible])
    selected = traversal or plane.default_traversal
    explanation = None
    if selected not in plane.allowed_traversals:
        return {
            **result,
            "traversal": selected,
            "traversal_valid": False,
            "explanation": "This traversal is not allowed for the selected plane.",
        }
    from app.engines.traversal import get_policy

    policy = get_policy(selected)
    if policy.graph and bridge_density is not None and bridge_density < BALANCE.min_bridge_density:
        fallback = next(
            (
                candidate
                for candidate in plane.allowed_traversals
                if not get_policy(candidate).graph
            ),
            None,
        )
        if fallback is not None:
            selected = fallback
            explanation = (
                f"Bridge density {bridge_density:.1%} is below 2%; "
                f"using {get_policy(fallback).label} instead."
            )
    return {
        **result,
        "traversal": selected,
        "traversal_valid": True,
        "bridge_density": bridge_density,
        "explanation": explanation,
        "balanced": result["balanced"]
        and (
            not get_policy(selected).graph
            or bridge_density is None
            or bridge_density >= BALANCE.min_bridge_density
        ),
    }


def deal(
    seed: int, movie_ids_by_territory: Mapping[str, Sequence[int]], per_side: int = 3
) -> list[dict]:
    """Return a deterministic set of distinct, balanced offers for both teams and neutral."""
    rng = random.Random(seed)
    output: list[dict] = []
    seen: set[int] = set()
    for territory in (TEAM_A, TEAM_B, NEUTRAL):
        pool = list(dict.fromkeys(movie_ids_by_territory.get(territory, ())))
        rng.shuffle(pool)
        available = [movie_id for movie_id in pool if movie_id not in seen]
        take = min(per_side, len(available))
        selected = available[:take]
        seen.update(selected)
        output.extend({"movie_id": movie_id, "territory": territory} for movie_id in selected)
    return output


def graph_density(
    session: Any,
    eligible_ids: Sequence[int],
    deadline_seconds: float = 1.5,
    traversal: str = "shared_cast",
) -> float:
    """Estimate unique cached graph edges for a traversal without provider calls."""
    from sqlmodel import select

    from app.models.cache import CachedCrewCredit, CachedMovieCast, CachedMovieDirector

    started = time.monotonic()
    eligible = set(eligible_ids)
    if len(eligible) < 2:
        return 0.0
    source_models = (
        (CachedMovieCast,)
        if traversal == "shared_cast"
        else (CachedMovieDirector,)
        if traversal == "shared_director"
        else (CachedMovieCast, CachedMovieDirector, CachedCrewCredit)
    )
    edge_pairs: set[tuple[int, int]] = set()
    for model in source_models:
        if time.monotonic() - started > deadline_seconds:
            return 0.0
        person_column = model.actor_id if model is CachedMovieCast else model.person_id
        rows = session.exec(
            select(person_column, model.movie_id).where(model.movie_id.in_(eligible))
        ).all()
        by_person: dict[int, set[int]] = {}
        for person_id, movie_id in rows:
            by_person.setdefault(person_id, set()).add(movie_id)
        for linked_movies in by_person.values():
            ordered = sorted(linked_movies)
            for index, first in enumerate(ordered):
                edge_pairs.update((first, later) for later in ordered[index + 1 :])
    if time.monotonic() - started > deadline_seconds:
        return 0.0
    possible_pairs = len(eligible) * (len(eligible) - 1) // 2
    return min(1.0, len(edge_pairs) / possible_pairs) if possible_pairs else 0.0
