"""Cache-only, three-valued feasibility evidence, scoped to one request."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import text
from sqlmodel import Session, select

from app.engines.predicates import MovieFacts, Predicate, facts_of
from app.facets.query import FacetQuery, compile, count, universe_ids
from app.facets.store import refresh
from app.models.cache import CachedMovie


@dataclass(frozen=True)
class Feasibility:
    ok: bool
    reason: str
    pass_rate: float | None

    @property
    def drawable(self) -> bool:
        return self.ok and (self.pass_rate is None or self.pass_rate <= 0.8)


def invalidate(session: Session) -> None:
    session.info.pop("bounty_feasibility", None)


def query_of(test: Predicate) -> FacetQuery:
    query = getattr(test, "query", None)
    if not isinstance(query, FacetQuery):
        raise TypeError(f"Predicate {test.id} has no facet query")
    return query


def counts(session: Session, test: Predicate, ids: Iterable[int]) -> dict:
    ids = list(ids)
    query = query_of(test)
    key = (query.model_dump_json(by_alias=True, exclude_none=True), tuple(ids))
    cache = session.info.setdefault("bounty_feasibility", {})
    if key not in cache:
        refresh(session, ids)
        cache[key] = count(session, query, ids)
    return cache[key]


def check(
    session: Session, test: Predicate, ids: Iterable[int], *, exact: bool = True
) -> Feasibility:
    ids = list(ids)
    if not ids:
        return Feasibility(
            not exact, "No remaining eligible films" if exact else "Cache has no evidence", None
        )
    result = counts(session, test, ids)
    possible = result["matches"] + result["unknown"]
    observed_rate = None if result["unknown"] else result["pass_rate"]
    return Feasibility(
        possible > 0,
        "No remaining eligible film matches this quest"
        if not possible
        else "Too easy for the eligible pool"
        if observed_rate is not None and observed_rate > 0.8
        else "A matching film is possible",
        observed_rate,
    )


def pass_rate(session: Session, test: Predicate, ids: Iterable[int]) -> float | None:
    return counts(session, test, ids)["pass_rate"]


def verdict(session: Session, test: Predicate, movie_id: int) -> bool | None:
    result = counts(session, test, [movie_id])
    return None if result["unknown"] else bool(result["matches"])


def matching_ids(session: Session, query: FacetQuery, ids: Iterable[int]) -> list[int]:
    ids = list(ids)
    refresh(session, ids)
    universe, params = universe_ids(ids)
    sql, bindings = compile(query, universe)
    return list(
        session.execute(
            text(f"SELECT movie_id FROM ({sql}) WHERE verdict IS TRUE"), {**params, **bindings}
        ).scalars()
    )


def difficulty(rate: float | None) -> int:
    return 1 if rate is None else 6 if rate <= 0 else max(1, min(6, 1 + round(-math.log2(rate))))


def measured_difficulty(session: Session, test: Predicate, ids: Iterable[int]) -> int:
    return difficulty(pass_rate(session, test, ids))


def movies(session: Session) -> dict[int, CachedMovie]:
    return {m.tmdb_id: m for m in session.exec(select(CachedMovie)).all()}


def within_movie(movie: CachedMovie, bounds: dict) -> bool:
    return within(facts_of(movie, []), bounds)


def covered_facets(session: Session, ids: Iterable[int]) -> list[dict]:
    import json

    from app.facets.registry import CATALOGUE, FAMILY_VERSIONS

    ids = list(ids)
    refresh(session, ids)
    rows = session.execute(
        text("""SELECT f.facet_id, COUNT(DISTINCT f.movie_id) AS known
        FROM movie_facets f JOIN movie_facet_status s ON s.movie_id=f.movie_id
        WHERE f.movie_id IN (SELECT value FROM json_each(:ids)) AND s.status='ok'
        GROUP BY f.facet_id"""),
        {"ids": json.dumps(ids)},
    ).mappings()
    coverage = {row["facet_id"]: row["known"] for row in rows}
    return [
        {"facet": f.id, "kind": f.kind, "ops": f.ops, "known": coverage.get(f.id, 0)}
        for f in sorted(CATALOGUE.values(), key=lambda f: (-coverage.get(f.id, 0), f.id))
        if not f.relative and f.version == FAMILY_VERSIONS[f.family]
    ][:25]


def exists(session: Session, test: Predicate, ids: Iterable[int]) -> bool:
    rate = pass_rate(session, test, ids)
    return rate is not None and rate > 0


def cache_pass_rate(session: Session, test: Predicate) -> float | None:
    return pass_rate(session, test, session.exec(select(CachedMovie.tmdb_id)).all())


def ranges_of(test: Predicate) -> dict[str, tuple[float | None, float | None]]:
    return test.ranges


def contradicts(test: Predicate, bounds: dict[str, tuple[float | None, float | None]]) -> bool:
    for field, (low, high) in ranges_of(test).items():
        mode_low, mode_high = bounds.get(field, (None, None))
        lows = [value for value in (low, mode_low) if value is not None]
        highs = [value for value in (high, mode_high) if value is not None]
        if lows and highs and max(lows) > min(highs):
            return True
    return False


def within(facts: MovieFacts, bounds: dict[str, tuple[float | None, float | None]]) -> bool:
    for field, (low, high) in bounds.items():
        value = getattr(facts, field)
        if value is not None and (
            low is not None and value < low or high is not None and value > high
        ):
            return False
    return True
