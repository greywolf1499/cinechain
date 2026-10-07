"""Validated AST with identical Python and SQLite three-valued semantics."""

from __future__ import annotations

import json
import math
import operator
from collections.abc import Iterable, Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from sqlmodel import Session

from app.facets.registry import CATALOGUE, FAMILY_VERSIONS, FacetValue
from app.facets.store import EMPTY_SET

Scalar = str | int | float | bool
COMPARISONS = {
    "eq": operator.eq,
    "ne": operator.ne,
    "lt": operator.lt,
    "le": operator.le,
    "gt": operator.gt,
    "ge": operator.ge,
}
DEFAULT_UNIVERSE = "SELECT tmdb_id AS movie_id FROM cached_movies"


class FacetQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    all: list[FacetQuery] | None = Field(default=None, max_length=32)
    any: list[FacetQuery] | None = Field(default=None, max_length=32)
    not_: FacetQuery | None = Field(default=None, alias="not")
    facet: str | None = None
    op: Literal["eq", "ne", "lt", "le", "gt", "ge", "contains", "has_any", "has_all"] | None = None
    value: Scalar | list[Scalar] | None = None

    @model_validator(mode="after")
    def validate_query(self) -> FacetQuery:
        if sum(x is not None for x in (self.all, self.any, self.not_, self.facet)) != 1:
            raise ValueError("Exactly one of all, any, not or facet is required")
        if self.facet is None:
            if self.op is not None or self.value is not None:
                raise ValueError("Only leaves accept op/value")
        else:
            definition = CATALOGUE.get(self.facet)
            if definition is None or self.op not in definition.ops:
                raise ValueError("Unknown facet or unsupported operator")
            values = self.value if isinstance(self.value, list) else [self.value]
            if self.op in ("has_any", "has_all"):
                if not isinstance(self.value, list) or len(self.value) > 32:
                    raise ValueError("Set operators require a list of at most 32 values")
            elif isinstance(self.value, list):
                raise ValueError("This operator requires a scalar")
            for value in values:
                kind = definition.kind.removeprefix("set_")
                valid = (
                    isinstance(value, bool)
                    if kind == "bool"
                    else isinstance(value, str) and bool(value) and value != EMPTY_SET
                    if kind == "cat"
                    else isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(value)
                )
                if not valid:
                    raise ValueError(f"Invalid {definition.kind} value")
        if self.size() > 64 or self.depth() > 8:
            raise ValueError("Query is limited to 64 nodes and depth 8")
        return self

    def children(self) -> list[FacetQuery]:
        return (
            self.all
            if self.all is not None
            else self.any
            if self.any is not None
            else [self.not_]
            if self.not_
            else []
        )

    def size(self) -> int:
        return 1 + sum(child.size() for child in self.children())

    def depth(self) -> int:
        return 1 + max((child.depth() for child in self.children()), default=0)

    @property
    def stored(self) -> bool:
        return (
            not CATALOGUE[self.facet].relative
            if self.facet
            else all(child.stored for child in self.children())
        )

    def evaluate(self, facts: Mapping[str, FacetValue]) -> bool | None:
        if self.not_ is not None:
            result = self.not_.evaluate(facts)
            return None if result is None else not result
        if self.all is not None or self.any is not None:
            results = [child.evaluate(facts) for child in self.children()]
            if self.all is not None:
                return False if False in results else None if None in results else True
            return True if True in results else None if None in results else False
        assert self.facet is not None and self.op is not None
        value = facts.get(self.facet)
        if value is None:
            return None
        if self.op == "contains":
            assert isinstance(value, list)
            return self.value in value
        if self.op == "has_any":
            assert isinstance(value, list) and isinstance(self.value, list)
            return any(item in value for item in self.value)
        if self.op == "has_all":
            assert isinstance(value, list) and isinstance(self.value, list)
            return all(item in value for item in self.value)
        return COMPARISONS[self.op](value, self.value)


def evaluate(query: FacetQuery, facts: Mapping[str, FacetValue]) -> bool | None:
    return query.evaluate(facts)


def _expression(query: FacetQuery) -> tuple[str, dict]:
    params: dict = {}

    def bind(value: Scalar) -> str:
        key = f"f{len(params)}"
        params[key] = value
        return f":{key}"

    def visit(node: FacetQuery) -> str:
        if node.not_:
            return f"(NOT {visit(node.not_)})"
        if node.all is not None or node.any is not None:
            expressions = [visit(child) for child in node.children()]
            return (
                "(" + (" AND " if node.all is not None else " OR ").join(expressions) + ")"
                if expressions
                else "1"
                if node.all is not None
                else "0"
            )
        assert node.facet is not None and node.op is not None
        definition = CATALOGUE[node.facet]
        if definition.relative:
            assert node.value is not None and not isinstance(node.value, list)
            if definition.id == "popularity":
                value = "(SELECT popularity FROM cached_movies WHERE tmdb_id=u.movie_id)"
            else:
                column, buckets = (
                    ("popularity", 100)
                    if definition.id == "popularity_percentile"
                    else ("vote_count", 4)
                )
                value = f"(SELECT bucket FROM (SELECT tmdb_id, NTILE({buckets}) OVER (ORDER BY {column},tmdb_id) AS bucket FROM cached_movies WHERE {column} IS NOT NULL) WHERE tmdb_id=u.movie_id)"
                if definition.id == "vote_count_band":
                    value = f"(CASE {value} WHEN 1 THEN 'low' WHEN 2 THEN 'medium' WHEN 3 THEN 'high' WHEN 4 THEN 'very_high' END)"
            op = {"eq": "=", "ne": "!=", "lt": "<", "le": "<=", "gt": ">", "ge": ">="}[node.op]
            return f"({value}{op}{bind(node.value)})"
        facet = bind(definition.id)
        family = bind(definition.family)
        version = bind(FAMILY_VERSIONS[definition.family])
        base = f"FROM movie_facets AS f WHERE f.facet_id={facet} AND f.movie_id=u.movie_id"
        known = f"""EXISTS (SELECT 1 {base}) AND EXISTS (
            SELECT 1 FROM movie_facet_status AS s WHERE s.movie_id=u.movie_id
            AND s.family={family} AND s.version={version} AND s.status='ok')"""
        column = "f.value_text" if definition.kind in ("cat", "set_cat") else "f.value_num"
        if node.op in ("has_any", "has_all"):
            assert isinstance(node.value, list)
            values = list(dict.fromkeys(node.value))
            if not values:
                matched = "1" if node.op == "has_all" else "0"
            else:
                comparison = ",".join(bind(item) for item in values)
                having = (
                    f" GROUP BY f.movie_id HAVING COUNT(DISTINCT {column})={len(values)}"
                    if node.op == "has_all"
                    else ""
                )
                matched = f"EXISTS (SELECT 1 {base} AND f.value_text!={bind(EMPTY_SET)} AND {column} IN ({comparison}){having})"
        else:
            assert node.value is not None and not isinstance(node.value, list)
            op = {
                "eq": "=",
                "ne": "!=",
                "lt": "<",
                "le": "<=",
                "gt": ">",
                "ge": ">=",
                "contains": "=",
            }[node.op]
            present = (
                "f.value_text=''"
                if definition.kind in ("num", "bool", "set_num")
                else f"f.value_text!={bind(EMPTY_SET)}"
            )
            matched = f"EXISTS (SELECT 1 {base} AND {present} AND {column}{op}{bind(node.value)})"
        return f"(CASE WHEN {known} THEN {matched} ELSE NULL END)"

    return visit(query), params


def compile(query: FacetQuery, universe_sql: str = DEFAULT_UNIVERSE) -> tuple[str, dict]:
    """universe_sql is trusted server SQL exposing movie_id, never request text."""
    expression, params = _expression(query)
    return f"SELECT u.movie_id, {expression} AS verdict FROM ({universe_sql}) AS u", params


def universe_ids(ids: Iterable[int]) -> tuple[str, dict]:
    return "SELECT CAST(value AS INTEGER) AS movie_id FROM json_each(:universe_ids)", {
        "universe_ids": json.dumps(list(ids))
    }


def count(session: Session, query: FacetQuery, universe: Iterable[int] | str | None = None) -> dict:
    sql, extra = (
        (DEFAULT_UNIVERSE, {})
        if universe is None
        else (universe, {})
        if isinstance(universe, str)
        else universe_ids(universe)
    )
    compiled, params = compile(query, sql)
    result = session.execute(
        text(f"""SELECT COUNT(*) AS total,
        COALESCE(SUM(verdict IS TRUE),0) AS matches,
        COALESCE(SUM(verdict IS NULL),0) AS unknown FROM ({compiled})"""),
        {**params, **extra},
    ).one()
    return {
        "matches": result.matches,
        "unknown": result.unknown,
        "pass_rate": (result.matches + result.unknown) / result.total if result.total else None,
    }
