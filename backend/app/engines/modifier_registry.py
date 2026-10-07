"""Declarative, engine-scoped modifiers; legacy checks keep their original semantics."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.engines.conditions import RunOutcome
from app.engines.predicates import MovieFacts
from app.engines.rulebook import RuleSection
from app.models.cache import CachedMovie
from app.models.run import RunStep

if TYPE_CHECKING:
    from app.engines.base import BaseChallengeEngine


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
    return {**legacy_entries(), **{spec.key: spec for spec in TITLE_MODIFIERS}}


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
) -> list[tuple[ModifierSpec, ModCtx]]:
    specs = registry()
    return [
        (
            specs[key],
            ModCtx(
                specs[key].params.model_validate(param_values(key, value)), history or (), earlier
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


@dataclass
class TitleModifier(LegacyModifier):
    def compatible(self, engine: type[BaseChallengeEngine]) -> str | None:
        if self.scope not in engine.modifier_scopes:
            return f"{self.label} isn't supported by {engine.display_name}: incompatible ordering or no film choice"
        return None

    def state(self, ctx: ModCtx) -> tuple[int, int]:
        from app.utils.title_tokens import first_letter, title_numbers

        params = ctx.params.model_dump()
        alphabet = self.key == "alphabet_run"
        position = 0 if alphabet else params.get("start", 1) - 1
        count = 0
        letters = (
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            if params.get("direction") != "za"
            else "ZYXWVUTSRQPONMLKJIHGFEDCBA"
        )
        for step in ctx.history:
            if step.status != "watched":
                continue
            if (step.transition_metadata or {}).get("seed"):
                if alphabet and params["seed_sets_start"]:
                    letter = first_letter(step.movie_title, params["ignore_articles"])
                    if letter and letter in letters:
                        position = letters.index(letter) + 1
                continue
            skipped = self.key in ((step.transition_metadata or {}).get("overlay_skips") or [])
            if alphabet:
                if position >= 26:
                    break
                letter = first_letter(step.movie_title, params["ignore_articles"])
                if skipped or letters[position] in params["wild_letters"] or letter == "#":
                    position += 1
                    count += 1
                elif letter and letter in letters:
                    index = letters.index(letter)
                    if index == position or (
                        not params["strict"] and index >= max(0, position - 1)
                    ):
                        position = index + 1
                        count += 1
            else:
                nums = title_numbers(step.movie_title, params["allow_years"])
                eligible = (
                    [n for n in nums if n == position + 1]
                    if params.get("mode") == "count_up"
                    else [n for n in nums if n > position]
                )
                if skipped or eligible:
                    position = position + 1 if skipped else min(eligible)
                    count += 1
        return position, count

    def check(self, ctx: ModCtx, film: CachedMovie) -> Verdict:
        from app.utils.title_tokens import first_letter, title_numbers

        params = ctx.params.model_dump()
        if self.key == "number_in_title":
            ok = bool(title_numbers(film.title, params["allow_years"]))
            return Verdict(ok, None if ok else "Number in title: this title has no eligible number")
        position, _ = self.state(ctx)
        if self.key == "alphabet_run":
            letters = (
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                if params["direction"] == "az"
                else "ZYXWVUTSRQPONMLKJIHGFEDCBA"
            )
            if position >= 26:
                return Verdict()
            letter = first_letter(film.title, params["ignore_articles"])
            expected = letters[position]
            index = letters.find(letter) if letter else -1
            ok = (
                expected in params["wild_letters"]
                or letter == "#"
                or index == position
                or (not params["strict"] and index >= max(0, position - 1))
            )
            return Verdict(
                ok,
                None
                if ok
                else f"A-Z: Next: {expected}; {film.title} starts with {letter or 'no letter'}",
            )
        nums = title_numbers(film.title, params["allow_years"])
        ok = (
            position + 1 in nums
            if params["mode"] == "count_up"
            else any(n > position for n in nums)
        )
        return Verdict(
            ok,
            None
            if ok
            else f"Ascending numbers: Next: {position + 1}; no matching number in {film.title}",
        )

    def progress(self, ctx: ModCtx) -> dict[str, Any] | None:
        if self.key == "number_in_title":
            return {"key": self.key, "label": "🔢 Number in title", "next": None}
        position, count = self.state(ctx)
        params = ctx.params.model_dump()
        if self.key == "alphabet_run":
            letters = (
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                if params["direction"] == "az"
                else "ZYXWVUTSRQPONMLKJIHGFEDCBA"
            )
            next_token = letters[position] if position < 26 else None
            label = f"🔤 Next: {next_token or 'Complete'} · {position}/26"
        else:
            next_token = str(position + 1)
            label = f"🔢 Next: {next_token} · {count} films"
        return {"key": self.key, "label": label, "next": next_token}

    def outcome(self, ctx: ModCtx) -> RunOutcome | None:
        position, count = self.state(ctx)
        params = ctx.params.model_dump()
        if self.key == "alphabet_run" and position >= 26:
            return RunOutcome(
                "completed",
                f"{'A to Z' if params['direction'] == 'az' else 'Z to A'} conquered in {count} films",
            )
        if (
            self.key == "ascending_numbers"
            and params["mode"] == "count_up"
            and position >= params["target"]
        ):
            return RunOutcome(
                "completed", f"Count to {params['target']} conquered in {count} films"
            )
        return None


TITLE_MODIFIERS = [
    TitleModifier(
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
    ),
    TitleModifier(
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
    ),
    TitleModifier(
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
    ),
]


@dataclass(frozen=True)
class TitleCoveragePredicate:
    spec: TitleModifier
    ctx: ModCtx
    token: str | int | None = None

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
        return frozenset({"title"})

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
        from app.utils.title_tokens import first_letter, title_numbers

        if movie is None:
            return None
        if self.token is None:
            return self.spec.check(self.ctx, movie).ok
        params = self.ctx.params.model_dump()
        if self.spec.key == "alphabet_run":
            return self.token in params["wild_letters"] or first_letter(
                movie.title,
                params["ignore_articles"],
            ) in (self.token, "#")
        assert isinstance(self.token, int)
        return self.token in title_numbers(movie.title, params["allow_years"]) or (
            params["mode"] == "increasing"
            and any(
                number >= self.token for number in title_numbers(movie.title, params["allow_years"])
            )
        )
