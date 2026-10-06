"""Opt-in win/fail conditions parsed from a run's `rules_config` JSON (Engine V2).

    {"win_condition":  {"type": "decades_spanned", "count": 3}}
    {"fail_condition": {"type": "max_wildcards_used", "count": 3}}

Each key accepts one condition object or a list of them (any one met triggers
the outcome). Win types fire once a metric *reaches* `count`; `max_*` fail types
fire once a metric *exceeds* `count`. Fail is checked before win, so a step that
both completes the goal and breaks a cap is a loss.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from app.models.run import RUN_STATUS_COMPLETED, RUN_STATUS_FAILED, RunStep

WIN_KEY = "win_condition"
FAIL_KEY = "fail_condition"


@dataclass(frozen=True)
class RunOutcome:
    status: str
    reason: str


def _watched(steps: Sequence[RunStep]) -> list[RunStep]:
    return [s for s in steps if s.status == "watched"]


def _metadata(step: RunStep) -> dict[str, Any]:
    return step.transition_metadata or {}


def _decades_spanned(steps: Sequence[RunStep]) -> int:
    return len({(s.movie_release_year // 10) * 10 for s in _watched(steps) if s.movie_release_year})


def _countries_visited(steps: Sequence[RunStep]) -> int:
    countries: set[str] = set()
    for step in _watched(steps):
        if step.movie_origin_country:
            try:
                countries.update(json.loads(step.movie_origin_country))
            except (TypeError, ValueError):
                continue
    return len(countries)


def _movies_watched(steps: Sequence[RunStep]) -> int:
    return len(_watched(steps))


# Wildcards/penalties are spent when a step is logged (even a planned one).
def _wildcards_used(steps: Sequence[RunStep]) -> int:
    return sum(int(bool(_metadata(s).get("wildcard_used"))) + _metadata(s).get("overlay_wildcard_spent", 0)
               for s in steps)


def _repeats_used(steps: Sequence[RunStep]) -> int:
    return sum(1 for s in steps if _metadata(s).get("repeat_penalty"))


def _max_same_actor_links(steps: Sequence[RunStep]) -> int:
    counts = Counter(
        _metadata(s)["actor_id"] for s in steps if _metadata(s).get("actor_id") is not None
    )
    return max(counts.values(), default=0)


# type -> (metric, human label used in the outcome reason)
WIN_CONDITIONS: dict[str, tuple[Callable[[Sequence[RunStep]], int], str]] = {
    "decades_spanned": (_decades_spanned, "decades spanned"),
    "countries_visited": (_countries_visited, "countries visited"),
    "movies_watched": (_movies_watched, "movies watched"),
}
FAIL_CONDITIONS: dict[str, tuple[Callable[[Sequence[RunStep]], int], str]] = {
    "max_wildcards_used": (_wildcards_used, "wildcards used"),
    "max_repeats_used": (_repeats_used, "repeat penalties incurred"),
    "max_same_actor_links": (_max_same_actor_links, "links through the same actor"),
}


def _as_list(raw: Any) -> list[Any]:
    return raw if isinstance(raw, list) else [raw]


def validate_conditions(rules: dict[str, Any] | None) -> list[str]:
    """Human-readable problems with the win/fail condition blocks (empty = valid)."""
    errors: list[str] = []
    rules = rules or {}
    for key, registry in ((WIN_KEY, WIN_CONDITIONS), (FAIL_KEY, FAIL_CONDITIONS)):
        if rules.get(key) is None:
            continue
        for condition in _as_list(rules[key]):
            if not isinstance(condition, dict):
                errors.append(f"{key} must be an object (or a list of objects)")
                continue
            ctype = condition.get("type")
            if ctype not in registry:
                errors.append(f"{key}.type must be one of {sorted(registry)} (got {ctype!r})")
            count = condition.get("count")
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                errors.append(f"{key}.count must be a positive integer")
    return errors


def evaluate_conditions(
    rules: dict[str, Any] | None, steps: Sequence[RunStep]
) -> RunOutcome | None:
    """The outcome the run's custom conditions dictate for `steps`, if any.

    Malformed conditions are skipped (never raised): a bad rules_config must not
    make a run unplayable.
    """
    rules = rules or {}
    for key, registry, status, exceeds in (
        (FAIL_KEY, FAIL_CONDITIONS, RUN_STATUS_FAILED, True),
        (WIN_KEY, WIN_CONDITIONS, RUN_STATUS_COMPLETED, False),
    ):
        if rules.get(key) is None:
            continue
        for condition in _as_list(rules[key]):
            if not isinstance(condition, dict):
                continue
            entry = registry.get(condition.get("type"))
            count = condition.get("count")
            if entry is None or isinstance(count, bool) or not isinstance(count, int):
                continue
            metric, label = entry
            value = metric(steps)
            if (value > count) if exceeds else (value >= count):
                verb = "more than" if exceeds else "reached"
                prefix = "Failed" if status == RUN_STATUS_FAILED else "Victory"
                return RunOutcome(status, f"{prefix}: {verb} {count} {label} ({value})")
    return None
