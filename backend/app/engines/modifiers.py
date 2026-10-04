"""Composable rule modifiers (Engine V3).

An engine is split in two: a *primary candidate generator* (how it finds films
and links them - shared cast, a country, a plot...) and a set of *active
modifiers* read from the run's `rules_config`. Modifiers are pure pair rules
that any compatible engine can layer on top of its own:

    {"chrono_direction": "climb" | "descent" | null}
    {"runtime_staircase": "ascending" | "descending" | null}
    {"country_cooldown": 0}   # may not revisit a country seen in the last N steps

(`require_cast_link` is the fourth modifier; it is a link rule rather than a pair
rule, so `MutatorEngine` evaluates it itself.) A `null` value means "unset": the
engine's own default modifier applies. Violations are hard blocks - a wildcard
can skip a missing cast link but never an active modifier.

Unknown data never blocks (a film with no runtime/country is "unverified"),
except for Chrono, whose rule is meaningless without a release year.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from app.models.cache import CachedMovie
from app.models.run import RunStep
from app.services.bridge_paths import parse_countries
from app.utils.dates import parse_release_year

CHRONO_DIRECTIONS = ("climb", "descent")
STAIRCASE_DIRECTIONS = ("ascending", "descending")
MAX_COUNTRY_COOLDOWN = 20

CHRONO_KEY = "chrono_direction"
STAIRCASE_KEY = "runtime_staircase"
COOLDOWN_KEY = "country_cooldown"
CAST_LINK_KEY = "require_cast_link"
# Pre-V3 spelling of `chrono_direction`, still honoured on Chrono Climb runs.
LEGACY_CHRONO_KEY = "direction"

PAIR_MODIFIER_KEYS = (CHRONO_KEY, STAIRCASE_KEY, COOLDOWN_KEY)


def merge_modifiers(defaults: dict[str, Any], rules: dict | None) -> dict[str, Any]:
    """The modifiers actually in force: engine defaults overridden by non-null run values."""
    rules = rules or {}
    active = dict(defaults)
    if LEGACY_CHRONO_KEY in rules and rules[LEGACY_CHRONO_KEY] is not None:
        active[CHRONO_KEY] = rules[LEGACY_CHRONO_KEY]
    for key in PAIR_MODIFIER_KEYS:
        if rules.get(key) is not None:
            active[key] = rules[key]
    return {
        key: value for key, value in active.items()
        if key in PAIR_MODIFIER_KEYS and (key != COOLDOWN_KEY or value)
    }


def modifier_problems(rules: dict | None) -> list[str]:
    """Human-readable problems with the modifier values (empty = valid)."""
    rules = rules or {}
    problems: list[str] = []
    for key, allowed in ((CHRONO_KEY, CHRONO_DIRECTIONS), (STAIRCASE_KEY, STAIRCASE_DIRECTIONS)):
        value = rules.get(key)
        if value is not None and value not in allowed:
            problems.append(f"{key} must be one of {list(allowed)} or null")
    cooldown = rules.get(COOLDOWN_KEY)
    if cooldown is not None and (
        isinstance(cooldown, bool) or not isinstance(cooldown, int)
        or not 0 <= cooldown <= MAX_COUNTRY_COOLDOWN
    ):
        problems.append(f"{COOLDOWN_KEY} must be an integer between 0 and {MAX_COUNTRY_COOLDOWN}")
    return problems


def modifiers_requested(rules: dict | None) -> list[str]:
    """Pair-modifier keys the run explicitly turns on (used to reject them on trackers)."""
    return [key for key in PAIR_MODIFIER_KEYS if (rules or {}).get(key)]


def primary_country(movie: CachedMovie) -> str | None:
    countries = parse_countries(movie.origin_country)
    return countries[0] if countries else None


def step_country(step: RunStep) -> str | None:
    if not step.movie_origin_country:
        return None
    try:
        countries = json.loads(step.movie_origin_country)
    except (TypeError, ValueError):
        return None
    return countries[0] if isinstance(countries, list) and countries else None


def cooldown_countries(
    active: dict[str, Any], history: Sequence[RunStep] | None, earlier: CachedMovie | None = None
) -> list[str]:
    """Countries locked out right now: the primary countries of the last N logged steps
    (most recent first). Without a run history only `earlier` can be considered."""
    window = int(active.get(COOLDOWN_KEY) or 0)
    if window <= 0:
        return []
    if history:
        countries = [step_country(step) for step in reversed(history[-window:])]
    else:
        countries = [primary_country(earlier)] if earlier is not None else []
    locked: list[str] = []
    for country in countries:
        if country and country not in locked:
            locked.append(country)
    return locked


def _chrono_violation(direction: str, earlier: CachedMovie, later: CachedMovie) -> str | None:
    year_a = parse_release_year(earlier.release_date)
    year_b = parse_release_year(later.release_date)
    if year_a is None or year_b is None:
        return "Chrono needs a release year for both films"
    if direction == "descent":
        if year_b >= year_a:
            return (
                f"Chrono Descent: {later.title} ({year_b}) must be released before "
                f"{earlier.title} ({year_a})"
            )
    elif year_b <= year_a:
        return (
            f"Chrono Climb: {later.title} ({year_b}) must be released after "
            f"{earlier.title} ({year_a})"
        )
    return None


def _staircase_violation(direction: str, earlier: CachedMovie, later: CachedMovie) -> str | None:
    # Unknown runtimes never block (TMDB reports 0 or null for many films).
    if not earlier.runtime or not later.runtime:
        return None
    if direction == "descending":
        if later.runtime >= earlier.runtime:
            return (
                f"Runtime Staircase: {later.title} ({later.runtime} min) must be shorter than "
                f"{earlier.title} ({earlier.runtime} min)"
            )
    elif later.runtime <= earlier.runtime:
        return (
            f"Runtime Staircase: {later.title} ({later.runtime} min) must be longer than "
            f"{earlier.title} ({earlier.runtime} min)"
        )
    return None


def _cooldown_violation(
    active: dict[str, Any], earlier: CachedMovie, later: CachedMovie,
    history: Sequence[RunStep] | None,
) -> str | None:
    country = primary_country(later)
    if country is None:
        return None
    locked = cooldown_countries(active, history, earlier)
    if country not in locked:
        return None
    return (
        f"Country Cooldown: {later.title} is from {country}, which was visited in the last "
        f"{active[COOLDOWN_KEY]} step(s) - pick a country other than {', '.join(locked)}"
    )


def pair_modifier_violation(
    active: dict[str, Any], earlier: CachedMovie, later: CachedMovie,
    history: Sequence[RunStep] | None = None,
) -> str | None:
    """The first active modifier `later` breaks relative to `earlier`; None = allowed."""
    if active.get(CHRONO_KEY):
        reason = _chrono_violation(active[CHRONO_KEY], earlier, later)
        if reason:
            return reason
    if active.get(STAIRCASE_KEY):
        reason = _staircase_violation(active[STAIRCASE_KEY], earlier, later)
        if reason:
            return reason
    if active.get(COOLDOWN_KEY):
        return _cooldown_violation(active, earlier, later, history)
    return None


def modifier_mechanic(
    active: dict[str, Any], earlier: CachedMovie, later: CachedMovie
) -> dict[str, Any] | None:
    """Rule evidence for a hop (year delta, runtime delta), stored on the step."""
    mechanic: dict[str, Any] = {}
    if active.get(CHRONO_KEY):
        year_a = parse_release_year(earlier.release_date)
        year_b = parse_release_year(later.release_date)
        if year_a is not None and year_b is not None:
            mechanic.update(year_delta=year_b - year_a, direction=active[CHRONO_KEY])
    if active.get(STAIRCASE_KEY) and earlier.runtime and later.runtime:
        mechanic.update(
            runtime_delta=later.runtime - earlier.runtime, runtime_staircase=active[STAIRCASE_KEY])
    return mechanic or None


def modifier_notes(active: dict[str, Any], tail: CachedMovie | None) -> list[str]:
    """Short player-facing descriptions of the active modifiers, for the Pick Next banner."""
    notes: list[str] = []
    if active.get(CHRONO_KEY):
        word = "before" if active[CHRONO_KEY] == "descent" else "after"
        year = parse_release_year(tail.release_date) if tail is not None else None
        notes.append(
            f"Chrono {active[CHRONO_KEY]}: released {word} " + (str(year) if year else "the last film"))
    if active.get(STAIRCASE_KEY):
        word = "shorter" if active[STAIRCASE_KEY] == "descending" else "longer"
        runtime = tail.runtime if tail is not None and tail.runtime else None
        notes.append(
            f"Runtime staircase: {word} than " + (f"{runtime} min" if runtime else "the last film"))
    return notes
