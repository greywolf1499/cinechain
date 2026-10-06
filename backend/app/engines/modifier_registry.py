"""Declarative, engine-scoped modifiers; legacy checks keep their original semantics."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.engines.conditions import RunOutcome
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
                {self.key: values["steps"]}, ctx.earlier, film, ctx.history,
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
        LegacyModifier("chrono_direction", "Chrono direction", "↕", "Move strictly through release years.",
                       "pair", frozenset(), ChronoParams, RULEBOOK["chrono_direction"]),
        LegacyModifier("runtime_staircase", "Runtime staircase", "↗", "Each film must be longer or shorter.",
                       "pair", frozenset({"runtime"}), RuntimeParams, RULEBOOK["runtime_staircase"]),
        LegacyModifier("country_cooldown", "Country cooldown", "🌍", "Avoid recently visited countries.",
                       "pair", frozenset({"origin_country"}), CooldownParams, RULEBOOK["country_cooldown"]),
        LegacyModifier("require_cast_link", "Require shared cast", "🔗", "Add shared cast to a standalone rule.",
                       "pair", frozenset(), CastParams, RULEBOOK["require_cast_link"]),
    ]
    return {entry.key: entry for entry in entries}


def registry() -> dict[str, ModifierSpec]:
    return legacy_entries()


def param_values(key: str, value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    field = "steps" if key == "country_cooldown" else "enabled" if key == "require_cast_link" else "direction"
    return {field: value}


def contexts(active: dict[str, Any], history: Sequence[RunStep] | None = None,
             earlier: CachedMovie | None = None) -> list[tuple[ModifierSpec, ModCtx]]:
    specs = registry()
    return [(specs[key], ModCtx(specs[key].params.model_validate(param_values(key, value)),
                               history or (), earlier))
            for key in specs if (value := active.get(key)) is not None]
