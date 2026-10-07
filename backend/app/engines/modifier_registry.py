"""Declarative, engine-scoped modifiers; legacy checks keep their original semantics."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.engines.conditions import RunOutcome
from app.engines.predicates import MovieFacts
from app.engines.rulebook import RuleSection
from app.models.cache import CachedMovie
from app.models.run import RunStep

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.engines.base import BaseChallengeEngine
    from app.facets.query import FacetQuery


class Params(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class ChronoParams(Params):
    direction: Literal["climb", "descent"] = "climb"


class RuntimeParams(Params):
    direction: Literal["ascending", "descending"] = "ascending"


class CooldownParams(Params):
    steps: int = Field(default=3, ge=0, le=20)


class CastParams(Params):
    enabled: bool = True


@dataclass(frozen=True)
class ModCtx:
    params: BaseModel
    history: Sequence[RunStep] = ()
    earlier: CachedMovie | None = None
    facts: Mapping[int, Mapping[str, Any]] | None = None


@dataclass(frozen=True)
class Verdict:
    ok: bool | None = True
    reason: str | None = None


class ModifierSpec(Protocol):
    key: str
    label: str
    emoji: str
    blurb: str
    scope: Literal["film", "pair", "sequence"]
    needs: frozenset[str]
    params: type[BaseModel]
    rulebook: RuleSection

    def check(self, ctx: ModCtx, film: CachedMovie) -> Verdict: ...
    def progress(self, ctx: ModCtx) -> dict[str, Any] | None: ...
    def outcome(self, ctx: ModCtx) -> RunOutcome | None: ...
    def compatible(self, engine: type[BaseChallengeEngine]) -> str | None: ...


@dataclass
class LegacyModifier:
    key: str
    label: str
    emoji: str
    blurb: str
    scope: Literal["film", "pair", "sequence"]
    needs: frozenset[str]
    params: type[BaseModel]
    rulebook: RuleSection

    def compatible(self, engine: type[BaseChallengeEngine]) -> str | None:
        if self.scope not in engine.modifier_scopes:
            return f"{self.key} isn't supported by {engine.display_name}"
        if self.key == "require_cast_link" and not getattr(engine, "optional_cast_link", False):
            return "This mode already owns its link rule"
        return None

    def check(self, ctx: ModCtx, film: CachedMovie) -> Verdict:
        from app.engines import modifiers

        if ctx.earlier is None:
            return Verdict()
        values = ctx.params.model_dump()
        reason = None
        unknown = False
        if self.key == modifiers.CHRONO_KEY:
            reason = modifiers._chrono_violation(values["direction"], ctx.earlier, film)
        elif self.key == modifiers.STAIRCASE_KEY:
            unknown = not ctx.earlier.runtime or not film.runtime
            reason = modifiers._staircase_violation(values["direction"], ctx.earlier, film)
        elif self.key == modifiers.COOLDOWN_KEY:
            unknown = modifiers.primary_country(film) is None
            reason = modifiers._cooldown_violation(
                {self.key: values["steps"]},
                ctx.earlier,
                film,
                ctx.history,
            )
        # Cast links are asynchronous and remain in the engine's primary-link path.
        return Verdict(False if reason else None if unknown else True, reason)

    def progress(self, ctx: ModCtx) -> dict[str, Any] | None:
        from app.engines import modifiers

        values = ctx.params.model_dump()
        value = values.get("direction", values.get("steps", values.get("enabled")))
        notes = modifiers.legacy_modifier_notes({self.key: value}, ctx.earlier)
        return {"label": notes[0]} if notes else None

    def outcome(self, ctx: ModCtx) -> RunOutcome | None:
        return None


def legacy_entries() -> dict[str, ModifierSpec]:
    from app.engines.modifiers import RULEBOOK

    entries = [
        LegacyModifier(
            "chrono_direction",
            "Chrono direction",
            "↕",
            "Move strictly through release years.",
            "pair",
            frozenset(),
            ChronoParams,
            RULEBOOK["chrono_direction"],
        ),
        LegacyModifier(
            "runtime_staircase",
            "Runtime staircase",
            "↗",
            "Each film must be longer or shorter.",
            "pair",
            frozenset({"runtime"}),
            RuntimeParams,
            RULEBOOK["runtime_staircase"],
        ),
        LegacyModifier(
            "country_cooldown",
            "Country cooldown",
            "🌍",
            "Avoid recently visited countries.",
            "pair",
            frozenset({"origin_country"}),
            CooldownParams,
            RULEBOOK["country_cooldown"],
        ),
        LegacyModifier(
            "require_cast_link",
            "Require shared cast",
            "🔗",
            "Add shared cast to a standalone rule.",
            "pair",
            frozenset(),
            CastParams,
            RULEBOOK["require_cast_link"],
        ),
    ]
    return {entry.key: entry for entry in entries}


def registry() -> dict[str, ModifierSpec]:
    return {
        **legacy_entries(),
        **{spec.key: spec for spec in legacy_sequence_entries()},
        **{spec.key: spec for spec in TITLE_MODIFIERS},
    }


def param_values(key: str, value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    field = (
        "steps"
        if key == "country_cooldown"
        else "enabled"
        if key == "require_cast_link"
        else "direction"
    )
    return {field: value}


def contexts(
    active: dict[str, Any],
    history: Sequence[RunStep] | None = None,
    earlier: CachedMovie | None = None,
    facts: Mapping[int, Mapping[str, Any]] | None = None,
) -> list[tuple[ModifierSpec, ModCtx]]:
    specs = registry()
    return [
        (
            specs[key],
            ModCtx(
                specs[key].params.model_validate(param_values(key, value)),
                history or (),
                earlier,
                facts,
            ),
        )
        for key in specs
        if (value := active.get(key)) is not None
    ]


class AlphabetParams(Params):
    direction: Literal["az", "za"] = "az"
    ignore_articles: bool = True
    wild_letters: list[Literal["Q", "X", "Z"]] = Field(default_factory=lambda: ["Q", "X", "Z"])
    strict: bool = True
    seed_sets_start: bool = False


class NumberParams(Params):
    allow_years: bool = False


class AscendingParams(NumberParams):
    mode: Literal["increasing", "count_up"] = "count_up"
    start: int = Field(default=1, ge=0, le=1_000_000)
    target: int = Field(default=10, ge=0, le=1_000_000)

    @model_validator(mode="after")
    def ordered(self) -> AscendingParams:
        if self.target < self.start:
            raise ValueError("target must be at least start")
        if self.mode == "count_up" and self.target - self.start > 1000:
            raise ValueError("count_up supports at most 1001 numbers")
        return self


class CountDownParams(NumberParams):
    mode: Literal["count_down", "decreasing"] = "count_down"
    start: int = Field(default=10, ge=0, le=1_000_000)
    target: int = Field(default=1, ge=0, le=1_000_000)

    @model_validator(mode="after")
    def ordered(self) -> CountDownParams:
        if self.target > self.start:
            raise ValueError("target must be at most start")
        if self.mode == "count_down" and self.start - self.target > 1000:
            raise ValueError("count_down supports at most 1001 numbers")
        return self


class OrderParams(Params):
    direction: Literal["asc", "desc"] = "asc"
    strict: bool = True
    seed_sets_start: bool = False


class ObscurityParams(OrderParams):
    direction: Literal["asc", "desc"] = "desc"


class ChainParams(Params):
    direction: Literal["last_first", "first_last"] = "last_first"
    ignore_articles: bool = True
    digits_wild: bool = True


LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_NEXT: Any = object()
_CACHE_LIMIT = 4096


@dataclass(frozen=True)
class Order:
    """A modifier's params normalised onto one facet, direction, mode and bounds."""

    facet: str
    kind: Literal["letters", "numbers", "scalar"]
    mode: Literal["count", "increasing"]
    descending: bool
    strict: bool = True
    start: int = 0
    target: int = 0
    wild: frozenset[str] = frozenset()
    seed_sets_start: bool = False

    @property
    def letters(self) -> str:
        return LETTERS[::-1] if self.descending else LETTERS

    def beyond(self, value: float, bound: float) -> bool:
        if self.descending:
            return value < bound if self.strict else value <= bound
        return value > bound if self.strict else value >= bound

    def next_number(self, position: int) -> int:
        return position - 1 if self.descending else position + 1


def number_facet(allow_years: bool, suffix: str = "") -> str:
    return f"title_number{suffix}{'_with_years' if allow_years else ''}"


def letter_facet(ignore_articles: bool) -> str:
    return "title_first_letter" if ignore_articles else "title_first_letter_literal"


def film_values(movie: CachedMovie) -> dict[str, Any]:
    """Live facet values for one film, matching the stored lexical/production/reception facets."""
    from sqlalchemy.orm import object_session

    from app.facets import lexical, production, reception
    from app.models.cache import CachedMovieRating

    values: dict[str, Any] = dict(lexical.evaluate(movie.title or ""))
    values.update(production.evaluate(movie))
    session = object_session(movie)
    rated = session.get(CachedMovieRating, movie.tmdb_id) if session is not None else None
    values["rating"] = reception.evaluate(movie, rated)["rating"]
    values["popularity"] = movie.popularity
    return values


def _snapshot_values(step: RunStep) -> dict[str, Any]:
    values = film_values(CachedMovie(tmdb_id=step.movie_id, title=step.movie_title or ""))
    values["release_year"] = step.movie_release_year
    for facet in ("runtime", "rating", "popularity"):
        values[facet] = None
    return values


def history_values(ctx: ModCtx, facet: str) -> list[Any]:
    """One facet value per history step: supplied facts, stored facets, cached row, snapshot."""
    from sqlalchemy.orm import object_session

    from app.facets.registry import CATALOGUE
    from app.facets.store import stored_values

    steps = list(ctx.history)
    found: dict[int, Any] = {}
    for movie_id, facts in (ctx.facts or {}).items():
        if facet in facts:
            found[movie_id] = facts[facet]
    session = next((s for step in steps if (s := object_session(step)) is not None), None)
    if session is not None:
        cache: dict[tuple[int, str], Any] = session.info.setdefault("sequence_values", {})
        if len(cache) > _CACHE_LIMIT:
            cache.clear()
        wanted = [
            step.movie_id
            for step in steps
            if step.movie_id not in found and (step.movie_id, facet) not in cache
        ]
        if wanted:
            definition = CATALOGUE.get(facet)
            stored = (
                stored_values(session, wanted, [facet])
                if definition is not None and not definition.relative
                else {}
            )
            for movie_id in dict.fromkeys(wanted):
                if facet in stored.get(movie_id, {}):
                    cache[(movie_id, facet)] = stored[movie_id][facet]
                elif (row := session.get(CachedMovie, movie_id)) is not None:
                    cache[(movie_id, facet)] = film_values(row).get(facet)
        for step in steps:
            if step.movie_id not in found and (step.movie_id, facet) in cache:
                found[step.movie_id] = cache[(step.movie_id, facet)]
    return [
        found[step.movie_id] if step.movie_id in found else _snapshot_values(step).get(facet)
        for step in steps
    ]


def _known(facet: str) -> dict[str, Any]:
    return {
        "any": [{"facet": facet, "op": "ge", "value": 0}, {"facet": facet, "op": "lt", "value": 0}]
    }


@dataclass
class SequenceModifier(LegacyModifier):
    """Ordered facet rules: sequence folds over watched history, or pair checks on the frontier."""

    facet: str = ""

    @property
    def overlay(self) -> bool:
        return self.scope != "pair"

    def compatible(self, engine: type[BaseChallengeEngine]) -> str | None:
        if self.key in ("chrono_direction", "runtime_staircase"):
            return super().compatible(engine)
        if self.scope not in engine.modifier_scopes:
            return f"{self.label} isn't supported by {engine.display_name}: incompatible ordering or no film choice"
        return None

    def order(self, ctx: ModCtx) -> Order:
        p = ctx.params.model_dump()
        if self.key == "alphabet_run":
            return Order(
                letter_facet(p["ignore_articles"]),
                "letters",
                "count",
                p["direction"] == "za",
                p["strict"],
                wild=frozenset(p["wild_letters"]),
                seed_sets_start=p["seed_sets_start"],
            )
        if self.key in ("ascending_numbers", "count_down", "number_in_title"):
            return Order(
                number_facet(p["allow_years"]),
                "numbers",
                "count" if p.get("mode") in ("count_up", "count_down") else "increasing",
                self.key == "count_down",
                start=p.get("start", 1),
                target=p.get("target", 10),
            )
        return Order(
            self.facet,
            "scalar",
            "increasing",
            p["direction"] == "desc",
            p["strict"],
            seed_sets_start=p["seed_sets_start"],
        )

    def state(self, ctx: ModCtx) -> tuple[Any, int]:
        order = self.order(ctx)
        if order.kind == "letters":
            position: Any = 0
        elif order.kind == "numbers":
            position = order.start + 1 if order.descending else order.start - 1
        else:
            position = None
        count = 0
        letters = order.letters
        for step, value in zip(ctx.history, history_values(ctx, order.facet), strict=True):
            if step.status != "watched":
                continue
            metadata = step.transition_metadata or {}
            if metadata.get("seed"):
                if order.seed_sets_start and value is not None:
                    if order.kind == "letters" and value in letters:
                        position = letters.index(value) + 1
                    elif order.kind == "scalar":
                        position = value
                continue
            skipped = self.key in (metadata.get("overlay_skips") or [])
            if order.kind == "letters":
                if position >= 26:
                    break
                if skipped or letters[position] in order.wild or value == "#":
                    position += 1
                    count += 1
                elif value and value in letters:
                    index = letters.index(value)
                    if index == position or (not order.strict and index >= max(0, position - 1)):
                        position = index + 1
                        count += 1
            elif order.kind == "numbers":
                numbers = value or []
                eligible = (
                    [n for n in numbers if n == order.next_number(position)]
                    if order.mode == "count"
                    else [n for n in numbers if order.beyond(n, position)]
                )
                if skipped or eligible:
                    position = (
                        order.next_number(position)
                        if skipped
                        else (max if order.descending else min)(eligible)
                    )
                    count += 1
            elif skipped:
                position = value if value is not None else position
                count += 1
            elif value is not None and (position is None or order.beyond(value, position)):
                position = value
                count += 1
        return position, count

    def _pair_values(self, ctx: ModCtx, film: CachedMovie) -> tuple[Any, Any]:
        assert ctx.earlier is not None
        p = ctx.params.model_dump()
        first = letter_facet(p["ignore_articles"])
        earlier, later = film_values(ctx.earlier), film_values(film)
        if p["direction"] == "last_first":
            return earlier["title_last_letter"], later[first]
        return earlier[first], later["title_last_letter"]

    def check(self, ctx: ModCtx, film: CachedMovie) -> Verdict:
        if self.scope == "pair" and self.key != "last_letter_chain":
            return super().check(ctx, film)
        if self.key == "last_letter_chain":
            if ctx.earlier is None:
                return Verdict()
            need, have = self._pair_values(ctx, film)
            if need is None or have is None:
                return Verdict(None)
            ok = need == have or (ctx.params.model_dump()["digits_wild"] and "#" in (need, have))
            side = "start" if ctx.params.model_dump()["direction"] == "last_first" else "end"
            return Verdict(
                ok,
                None
                if ok
                else f"Letter chain: the title must {side} with {need}; {film.title} has {have}",
            )
        order = self.order(ctx)
        values = film_values(film)
        if self.key == "number_in_title":
            ok = bool(values[order.facet])
            return Verdict(ok, None if ok else "Number in title: this title has no eligible number")
        position, _ = self.state(ctx)
        value = values.get(order.facet)
        if order.kind == "letters":
            if position >= 26:
                return Verdict()
            letters = order.letters
            expected = letters[position]
            index = letters.find(value) if value else -1
            ok = (
                expected in order.wild
                or value == "#"
                or index == position
                or (not order.strict and index >= max(0, position - 1))
            )
            return Verdict(
                ok,
                None
                if ok
                else f"A-Z: Next: {expected}; {film.title} starts with {value or 'no letter'}",
            )
        if order.kind == "numbers":
            numbers = value or []
            ok = (
                order.next_number(position) in numbers
                if order.mode == "count"
                else any(order.beyond(n, position) for n in numbers)
            )
            name = "Count down" if order.descending else "Ascending numbers"
            return Verdict(
                ok,
                None
                if ok
                else f"{name}: Next: {order.next_number(position)}; no matching number in {film.title}",
            )
        if value is None:
            return Verdict(None)
        ok = position is None or order.beyond(value, position)
        return Verdict(
            ok,
            None
            if ok
            else f"{self.label}: {self._goal(order, position)}; {film.title} is {_fmt(value)}",
        )

    @staticmethod
    def _goal(order: Order, position: Any) -> str:
        if position is None:
            return "any film"
        word = (
            ("below" if order.strict else "at most")
            if order.descending
            else ("above" if order.strict else "at least")
        )
        return f"{word} {_fmt(position)}"

    def progress(self, ctx: ModCtx) -> dict[str, Any] | None:
        if self.scope == "pair" and self.key != "last_letter_chain":
            return super().progress(ctx)
        if self.key == "last_letter_chain":
            if ctx.earlier is None:
                return None
            p = ctx.params.model_dump()
            values = film_values(ctx.earlier)
            if p["direction"] == "last_first":
                letter = values["title_last_letter"]
                return {"label": f"🔗 Letter chain: start with {letter or 'any letter'}"}
            letter = values[letter_facet(p["ignore_articles"])]
            return {"label": f"🔗 Letter chain: end with {letter or 'any letter'}"}
        if self.key == "number_in_title":
            return {"key": self.key, "label": "🔢 Number in title", "next": None}
        order = self.order(ctx)
        position, count = self.state(ctx)
        if order.kind == "letters":
            next_token = order.letters[position] if position < 26 else None
            label = f"🔤 Next: {next_token or 'Complete'} · {position}/26"
        elif order.kind == "numbers":
            next_token = str(order.next_number(position))
            label = f"{self.emoji} Next: {next_token} · {count} films"
        else:
            next_token = None if position is None else _fmt(position)
            label = f"{self.emoji} {self.label}: {self._goal(order, position)} · {count} films"
        return {"key": self.key, "label": label, "next": next_token}

    def outcome(self, ctx: ModCtx) -> RunOutcome | None:
        if self.scope != "sequence":
            return None
        order = self.order(ctx)
        if order.mode != "count":
            return None
        position, count = self.state(ctx)
        if order.kind == "letters" and position >= 26:
            return RunOutcome(
                "completed",
                f"{'Z to A' if order.descending else 'A to Z'} conquered in {count} films",
            )
        if order.kind == "numbers":
            if order.descending and position <= order.target:
                return RunOutcome(
                    "completed",
                    f"Count down from {order.start} to {order.target} conquered in {count} films",
                )
            if not order.descending and position >= order.target:
                return RunOutcome(
                    "completed", f"Count to {order.target} conquered in {count} films"
                )
        return None

    def requirements(self, ctx: ModCtx) -> list[Any]:
        """Distinct films still needed, one token each; checklists must cover every token."""
        if self.scope != "sequence":
            return []
        order = self.order(ctx)
        position, _ = self.state(ctx)
        if order.kind == "letters":
            if position >= 26:
                return []
            return list(order.letters[position:]) if order.strict else [order.letters[-1]]
        if order.kind == "numbers":
            first = order.next_number(position)
            if order.mode != "count":
                return [first]
            step = -1 if order.descending else 1
            return list(range(first, order.target + step, step))
        return [position]

    def query(self, ctx: ModCtx, token: Any = _NEXT) -> FacetQuery:
        from app.facets.query import FacetQuery

        def leaf(facet: str, op: str, value: Any) -> dict[str, Any]:
            return {"facet": facet, "op": op, "value": value}

        if self.scope == "pair":
            if ctx.earlier is None:
                return FacetQuery.model_validate({"all": []})
            values = film_values(ctx.earlier)
            if self.key == "last_letter_chain":
                p = ctx.params.model_dump()
                first = letter_facet(p["ignore_articles"])
                need, facet = (
                    (values["title_last_letter"], first)
                    if p["direction"] == "last_first"
                    else (values[first], "title_last_letter")
                )
                if need is None or (p["digits_wild"] and need == "#"):
                    return FacetQuery.model_validate({"all": []})
                options = [leaf(facet, "eq", need)]
                if p["digits_wild"]:
                    options.append(leaf(facet, "eq", "#"))
                return FacetQuery.model_validate({"any": options})
            direction = ctx.params.model_dump()["direction"]
            bound = values[self.facet] or None
            if bound is None:
                return FacetQuery.model_validate(_known(self.facet))
            op = "lt" if direction in ("descent", "descending") else "gt"
            return FacetQuery.model_validate(leaf(self.facet, op, bound))
        order = self.order(ctx)
        if self.key == "number_in_title":
            return FacetQuery.model_validate(
                leaf(number_facet(ctx.params.model_dump()["allow_years"], "_count"), "gt", 0)
            )
        position, _ = self.state(ctx) if token is _NEXT or order.kind == "letters" else (None, 0)
        if order.kind == "letters":
            if token is _NEXT:
                if position >= 26:
                    return FacetQuery.model_validate({"all": []})
                letters = order.letters
                expected = letters[position]
                tokens = [expected] if order.strict else list(letters[max(0, position - 1) :])
            else:
                expected = token
                tokens = [token]
            if expected in order.wild:
                return FacetQuery.model_validate({"all": []})
            return FacetQuery.model_validate(
                {"any": [leaf(order.facet, "eq", letter) for letter in [*tokens, "#"]]}
            )
        if order.kind == "numbers":
            if token is _NEXT:
                token = order.next_number(position)
            if order.mode == "count":
                return FacetQuery.model_validate(leaf(order.facet, "contains", token))
            years = ctx.params.model_dump()["allow_years"]
            extreme = number_facet(years, "_min" if order.descending else "_max")
            return FacetQuery.model_validate(
                {
                    "all": [
                        leaf(number_facet(years, "_count"), "gt", 0),
                        leaf(extreme, "le" if order.descending else "ge", token),
                    ]
                }
            )
        bound = position if token is _NEXT else token
        if bound is None:
            return FacetQuery.model_validate(_known(order.facet))
        op = (
            ("lt" if order.strict else "le")
            if order.descending
            else ("gt" if order.strict else "ge")
        )
        return FacetQuery.model_validate(leaf(order.facet, op, bound))

    def coverage(self, ctx: ModCtx, token: Any = _NEXT) -> SequenceCoverage:
        return SequenceCoverage(self, ctx, token)


def _fmt(value: Any) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


TitleModifier = SequenceModifier


def _section(
    goal: str,
    turn: list[str],
    scoring: list[str],
    lose: list[str],
    tips: list[str],
    glossary: list[str] | None = None,
) -> RuleSection:
    return RuleSection(
        goal, turn, scoring, lose, tips, ["wildcard"] if glossary is None else glossary
    )


def legacy_sequence_entries() -> list[SequenceModifier]:
    from app.engines.modifiers import RULEBOOK

    return [
        SequenceModifier(
            "chrono_direction",
            "Chrono direction",
            "↕",
            "Move strictly through release years.",
            "pair",
            frozenset(),
            ChronoParams,
            RULEBOOK["chrono_direction"],
            "release_year",
        ),
        SequenceModifier(
            "runtime_staircase",
            "Runtime staircase",
            "↗",
            "Each film must be longer or shorter.",
            "pair",
            frozenset({"runtime"}),
            RuntimeParams,
            RULEBOOK["runtime_staircase"],
            "runtime",
        ),
    ]


TITLE_MODIFIERS = [
    SequenceModifier(
        "alphabet_run",
        "A-Z",
        "🔤",
        "Watch titles in alphabet order; Q, X and Z can be wild.",
        "sequence",
        frozenset({"title"}),
        AlphabetParams,
        RuleSection(
            "Conquer the alphabet in title order.",
            [
                "Pick the next letter on the chip.",
                "Chosen wild letters and titles that start with digits can stand in.",
            ],
            [
                "Only watched films count. Your starting film counts if that choice is on.",
            ],
            [
                "A wrong letter blocks the pick. A wildcard can stand in only when no legal reachable title fits."
            ],
            ["Ignore articles to make The Matrix an M film; keep connectors for the next letter."],
            ["wildcard"],
        ),
        "title_first_letter",
    ),
    SequenceModifier(
        "number_in_title",
        "Number in title",
        "🔢",
        "Every title must contain an eligible number.",
        "film",
        frozenset({"title"}),
        NumberParams,
        RuleSection(
            "Watch numbered titles.",
            [
                "Choose titles with digits, number words, or words like First.",
                "Roman numerals II to XX also count.",
            ],
            ["This is a film filter, not a separate victory condition."],
            ["Years from 1900 to 2099 count only if you turn on years."],
            ["Se7en and Rocky II qualify; I, Robot and Mix do not."],
            [],
        ),
        "title_number",
    ),
    SequenceModifier(
        "ascending_numbers",
        "Ascending numbers",
        "🔢",
        "Increase title numbers or count up to a target.",
        "sequence",
        frozenset({"title"}),
        AscendingParams,
        RuleSection(
            "Climb the numbers in film titles.",
            ["Choose a larger number.", "For Count up, choose the next number."],
            ["Only watched films after the start count.", "Count up wins at the target."],
            [
                "Wrong numbers block the pick. A wildcard can stand in only when no reachable title fits."
            ],
            [
                "Titles with multiple numbers use the smallest eligible one; years need explicit opt-in."
            ],
            ["wildcard"],
        ),
        "title_number",
    ),
    SequenceModifier(
        "count_down",
        "Count down",
        "🔟",
        "Count down through title numbers, from 10 to 1 by default.",
        "sequence",
        frozenset({"title"}),
        CountDownParams,
        _section(
            "Count down through the numbers in film titles.",
            ["Pick a title with the next number down.", "For Lower, any smaller number works."],
            ["Only watched films count.", "Count down wins when you reach the last number."],
            ["A wrong number blocks the pick. A wildcard can stand in only when no title fits."],
            ["Titles with several numbers use the largest one that fits."],
        ),
        "title_number",
    ),
    SequenceModifier(
        "title_length",
        "Title length",
        "📏",
        "Each title gets longer, or shorter, than the last.",
        "sequence",
        frozenset({"title"}),
        OrderParams,
        _section(
            "Grow or shrink your titles letter by letter.",
            ["Count the letters in each title.", "Go longer, or shorter, than the last one."],
            ["Only watched films count.", "There is no finish line; it shapes your path."],
            ["A wrong length blocks the pick. A wildcard can stand in when no title fits."],
            ["Leading words like The are not counted; spaces and marks are skipped."],
        ),
        "title_length",
    ),
    SequenceModifier(
        "rating_climb",
        "Rating climb",
        "⭐",
        "Each film is rated higher, or lower, than the last.",
        "sequence",
        frozenset({"vote_average"}),
        OrderParams,
        _section(
            "Climb, or sink, through film ratings.",
            ["Check the rating of the last film.", "Pick one rated higher, or lower."],
            ["Only watched films count.", "There is no finish line; it shapes your path."],
            ["A wrong rating blocks the pick. A wildcard can stand in when no film fits."],
            ["Films with no rating yet cannot be checked, so they wait."],
        ),
        "rating",
    ),
    SequenceModifier(
        "into_obscurity",
        "Into obscurity",
        "🕳",
        "Each film is less popular than the last, or more if you flip it.",
        "sequence",
        frozenset({"popularity"}),
        ObscurityParams,
        _section(
            "Dig down into less known films.",
            ["Pick a film less popular than the last.", "Flip it to climb toward hits."],
            ["Only watched films count.", "There is no finish line; it shapes your path."],
            ["A wrong pick is blocked. A wildcard can stand in when no film fits."],
            ["Popular films early on leave more room to dig."],
        ),
        "popularity",
    ),
    SequenceModifier(
        "last_letter_chain",
        "Letter chain",
        "🔗",
        "Each title starts with the last letter of the one before.",
        "pair",
        frozenset({"title"}),
        ChainParams,
        _section(
            "Chain titles letter to letter.",
            ["Look at the last letter of the last title.", "Pick a title that starts with it."],
            ["Each hop is checked against the film before it."],
            ["A wrong letter blocks the pick."],
            ["Titles ending in a digit can link to anything when digits are wild."],
            [],
        ),
        "title_last_letter",
    ),
]


@dataclass(frozen=True)
class SequenceCoverage:
    """Predicate for one requirement token; `query` is the SQL source of truth."""

    spec: SequenceModifier
    ctx: ModCtx
    token: Any = _NEXT

    @property
    def query(self) -> FacetQuery:
        return self.spec.query(self.ctx, self.token)

    @property
    def id(self) -> str:
        return self.spec.key

    @property
    def label(self) -> str:
        return self.spec.label

    @property
    def emoji(self) -> str:
        return self.spec.emoji

    @property
    def needs(self) -> frozenset[str]:
        return self.spec.needs

    @property
    def difficulty(self) -> int:
        return 1

    @property
    def params(self) -> dict[str, float]:
        return {}

    @property
    def ranges(self) -> dict[str, tuple[float | None, float | None]]:
        return {}

    def check(self, movie: CachedMovie | None, facts: MovieFacts) -> bool | None:
        return None if movie is None else self.query.evaluate(film_values(movie))


def TitleCoveragePredicate(
    spec: SequenceModifier, ctx: ModCtx, token: Any = _NEXT
) -> SequenceCoverage:
    return SequenceCoverage(spec, ctx, _NEXT if token is None else token)


def matching_rows(session: Session, query: FacetQuery, rows: Sequence[CachedMovie]) -> set[int]:
    """Ids of rows the query accepts: SQL over cached films, the same AST for unsaved checklist titles."""
    from app.services import feasibility

    cached = [row.tmdb_id for row in rows if session.get(CachedMovie, row.tmdb_id) is not None]
    matched = set(feasibility.matching_ids(session, query, cached)) if cached else set()
    known = set(cached)
    return matched | {
        row.tmdb_id
        for row in rows
        if row.tmdb_id not in known and query.evaluate(film_values(row)) is True
    }
