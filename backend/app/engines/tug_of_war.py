"""Tug of War: two teams use film choices to pull a shared rope."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC
from typing import Any, ClassVar, Literal

from sqlmodel import select

from app.engines.cinechain import CineChainEngine
from app.engines.conditions import RunOutcome
from app.models.run import (
    LEGACY_ENGINE_VERSION,
    RUN_STATUS_ACTIVE,
    RUN_STATUS_COMPLETED,
    Run,
    RunParticipant,
    RunStep,
)
from app.models.user import User
from app.schemas.discovery import DiscoveryCandidate

TUG_OF_WAR = "tug_of_war"
DIMENSION_ERA = "era"
DIMENSION_GEOGRAPHY = "geography"
DIMENSIONS = (DIMENSION_ERA, DIMENSION_GEOGRAPHY)
TEAM_A = "team_a"
TEAM_B = "team_b"
TugTeam = Literal["team_a", "team_b"]
TugEffect = Literal["home", "invasion", "neutral", "sudden_neutral"]

DEFAULT_DIMENSION = DIMENSION_ERA
DEFAULT_TARGET_LEAD = 4
MAX_TARGET_LEAD = 50
DEFAULT_ERA_A_BEFORE = 1975
DEFAULT_ERA_B_AFTER = 2005
DEFAULT_MOMENTUM_CAP = 3
DEFAULT_SUDDEN_DEATH_AFTER = 12
DEFAULT_SUDDEN_DEATH_EVERY = 2

SCORES_KEY = "tug_scores"
PLAYERS_KEY = "tug_players"
MOMENTUM_KEY = "tug_momentum"
TUG_RULES_VERSION_KEY = "tug_rules_version"
SEED_KEY = "seed"
VICTORY_PREFIX = "Tug of War won by"


@dataclass(frozen=True)
class Pull:
    step_id: str
    puller: TugTeam
    territory: TugTeam | None
    kind: TugEffect
    points: int
    streak: int
    multiplier: int


@dataclass(frozen=True)
class TugTally:
    scores: dict[str, int]
    streak: tuple[TugTeam | None, int]
    anchor: TugTeam | None
    effective_target: int
    sudden_death: bool
    pulls: list[Pull]
    next_team: TugTeam


# US plus Europe, including the countries TMDB still files older films under.
WESTERN_COUNTRIES = frozenset(
    {
        "US",
        "AD",
        "AL",
        "AT",
        "BA",
        "BE",
        "BG",
        "BY",
        "CH",
        "CY",
        "CZ",
        "DE",
        "DK",
        "EE",
        "ES",
        "FI",
        "FR",
        "GB",
        "GR",
        "HR",
        "HU",
        "IE",
        "IS",
        "IT",
        "LI",
        "LT",
        "LU",
        "LV",
        "MC",
        "MD",
        "ME",
        "MK",
        "MT",
        "NL",
        "NO",
        "PL",
        "PT",
        "RO",
        "RS",
        "RU",
        "SE",
        "SI",
        "SK",
        "SM",
        "UA",
        "VA",
        "XK",
        "SU",
        "DD",
        "XG",
        "CS",
        "XC",
        "YU",
    }
)


def tug_config(rules: dict | None) -> dict[str, Any]:
    rules = rules or {}
    return {
        "dimension": rules.get("dimension") or DEFAULT_DIMENSION,
        "target_lead": rules.get("target_lead") or DEFAULT_TARGET_LEAD,
        "era_a_before": rules.get("era_a_before") or DEFAULT_ERA_A_BEFORE,
        "era_b_after": rules.get("era_b_after") or DEFAULT_ERA_B_AFTER,
        "steal_enabled": rules.get("steal_enabled", True),
        "momentum_cap": rules.get("momentum_cap", DEFAULT_MOMENTUM_CAP),
        "sudden_death_after": rules.get("sudden_death_after", DEFAULT_SUDDEN_DEATH_AFTER),
        "sudden_death_every": rules.get("sudden_death_every", DEFAULT_SUDDEN_DEATH_EVERY),
    }


def team_of(players: dict[str, str | None], user_id: str | None) -> TugTeam | None:
    if user_id is None:
        return None
    if players.get(TEAM_A) == user_id:
        return TEAM_A
    if players.get(TEAM_B) == user_id:
        return TEAM_B
    return None


def step_turn_team(step: RunStep, players: dict[str, str | None]) -> TugTeam | None:
    explicit_team = (step.transition_metadata or {}).get("tug_team")
    if explicit_team in (TEAM_A, TEAM_B):
        return explicit_team
    return team_of(players, step.logged_by_user_id)


def _first_country(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        countries = json.loads(raw)
    except (TypeError, ValueError):
        countries = [raw]
    if isinstance(countries, str):
        countries = [countries]
    if not isinstance(countries, list) or not countries:
        return None
    return str(countries[0]).upper()


def _territory(
    release_year: int | None, origin_country: str | None, rules: dict | None
) -> TugTeam | None:
    config = tug_config(rules)
    if config["dimension"] == DIMENSION_GEOGRAPHY:
        country = _first_country(origin_country)
        if country is None:
            return None
        return TEAM_A if country in WESTERN_COUNTRIES else TEAM_B
    if release_year is None:
        return None
    if release_year < config["era_a_before"]:
        return TEAM_A
    if release_year > config["era_b_after"]:
        return TEAM_B
    return None


def step_team(step: RunStep, rules: dict | None) -> TugTeam | None:
    """Which territory a film belongs to (None = neutral)."""
    return _territory(step.movie_release_year, step.movie_origin_country, rules)


def _v1_compute_scores(steps: Sequence[RunStep], rules: dict | None) -> dict[str, int]:
    scores = {TEAM_A: 0, TEAM_B: 0}
    for step in steps:
        if step.status != "watched":
            continue
        team = step_team(step, rules)
        if team is not None:
            scores[team] += 1
    return scores


def _ordered_pull_steps(steps: Sequence[RunStep]) -> list[RunStep]:
    """Order ties deterministically while retaining the two seed steps' chronology."""
    return sorted(
        steps,
        key=lambda step: (
            step.logged_at.replace(tzinfo=UTC)
            if step.logged_at.tzinfo is None
            else step.logged_at.astimezone(UTC),
            0 if (step.transition_metadata or {}).get(SEED_KEY) else 1,
            step.id,
        ),
    )


def tally(
    steps: Sequence[RunStep],
    rules: dict | None,
    players: dict[str, str | None] | None = None,
) -> TugTally:
    """Fold watched, non-seed steps into a deterministic Tug of War state."""
    rules = rules or {}
    config = tug_config(rules)
    players = players or rules.get(PLAYERS_KEY) or {}
    scores = {TEAM_A: 0, TEAM_B: 0}
    streak_team: TugTeam | None = None
    streak = 0
    anchor: TugTeam | None = None
    turns = 0
    next_team: TugTeam = TEAM_A
    pulls: list[Pull] = []
    target = config["target_lead"]

    ordered_steps = _ordered_pull_steps(steps)
    for step in ordered_steps:
        if step.status != "watched" or (step.transition_metadata or {}).get(SEED_KEY):
            continue
        puller = step_turn_team(step, players)
        if puller is None:
            continue

        territory = step_team(step, rules)
        turns += 1
        next_team = TEAM_B if puller == TEAM_A and players.get(TEAM_B) is not None else TEAM_A
        multiplier = 2 if anchor == puller else 1
        if territory is None:
            kind: TugEffect = (
                "sudden_neutral" if turns >= config["sudden_death_after"] else "neutral"
            )
            streak_team, streak = None, 0
            if kind == "sudden_neutral":
                opponent = TEAM_B if puller == TEAM_A else TEAM_A
                scores[opponent] += 1
                points = -1
            else:
                points = 0
            anchor = puller
            multiplier = 1
        elif territory == puller:
            kind = "home"
            streak = min(
                streak + 1 if streak_team == puller else 1,
                config["momentum_cap"],
            )
            streak_team = puller
            points = streak * multiplier
            scores[puller] += points
            anchor = None if anchor == puller else anchor
        elif config["steal_enabled"]:
            kind = "invasion"
            streak = min(
                streak + 1 if streak_team == puller else 1,
                config["momentum_cap"],
            )
            streak_team = puller
            points = multiplier * 2
            scores[puller] += multiplier
            opponent = TEAM_B if puller == TEAM_A else TEAM_A
            scores[opponent] = max(0, scores[opponent] - multiplier)
            anchor = None if anchor == puller else anchor
        else:
            kind = "sudden_neutral" if turns >= config["sudden_death_after"] else "neutral"
            streak_team, streak = None, 0
            if kind == "sudden_neutral":
                opponent = TEAM_B if puller == TEAM_A else TEAM_A
                scores[opponent] += 1
                points = -1
            else:
                points = 0
            anchor = puller
            multiplier = 1

        pulls.append(
            Pull(
                step_id=step.id,
                puller=puller,
                territory=territory,
                kind=kind,
                points=points,
                streak=streak,
                multiplier=multiplier,
            )
        )
        target = max(
            1,
            config["target_lead"]
            - max(
                (turns - config["sudden_death_after"]) // config["sudden_death_every"],
                0,
            ),
        )

    sudden_death = turns >= config["sudden_death_after"]
    return TugTally(
        scores=scores,
        streak=(streak_team, streak),
        anchor=anchor,
        effective_target=target,
        sudden_death=sudden_death,
        pulls=pulls,
        next_team=next_team,
    )


def compute_scores(steps: Sequence[RunStep], rules: dict | None) -> dict[str, int]:
    """Scores for the configured rules version, preserving the v1 scoring path."""
    rules = rules or {}
    if rules.get(TUG_RULES_VERSION_KEY) != 2:
        return _v1_compute_scores(steps, rules)
    players = rules.get(PLAYERS_KEY) or {}
    return tally(steps, rules, players).scores


def leading_team(scores: dict[str, int], rules: dict | None) -> str | None:
    """The team that has won the legacy tug, else None."""
    lead = scores[TEAM_A] - scores[TEAM_B]
    if abs(lead) >= tug_config(rules)["target_lead"]:
        return TEAM_A if lead > 0 else TEAM_B
    return None


def winner(result: TugTally) -> TugTeam | None:
    lead = result.scores[TEAM_A] - result.scores[TEAM_B]
    if abs(lead) >= result.effective_target:
        return TEAM_A if lead > 0 else TEAM_B
    return None


def preview_pull(
    puller: TugTeam,
    territory: TugTeam | None,
    result: TugTally,
    rules: dict | None,
) -> tuple[TugEffect, int]:
    """Candidate effect and net rope movement from the current puller's perspective."""
    config = tug_config(rules)
    if territory is None or (territory != puller and not config["steal_enabled"]):
        if result.sudden_death:
            return "sudden_neutral", -1
        return "neutral", 0
    multiplier = 2 if result.anchor == puller else 1
    if territory == puller:
        streak_team, streak = result.streak
        streak = min(
            streak + 1 if streak_team == puller else 1,
            config["momentum_cap"],
        )
        return "home", streak * multiplier
    return "invasion", 2 * multiplier


class TugOfWarEngine(CineChainEngine):
    game_type = TUG_OF_WAR
    display_name = "Tug of War"
    description = (
        "Two teams pull a shared rope: home films build momentum, invasions steal ground, "
        "neutral films set an anchor, and Sudden Death ensures an end."
    )
    capabilities: ClassVar[list[str]] = [
        *(cap for cap in CineChainEngine.capabilities if cap != "modifiers"),
        "tug_of_war",
    ]
    supports_modifiers = False

    def validate_rules_config(self, rules: dict | None) -> list[str]:
        problems = super().validate_rules_config(rules)
        rules = rules or {}
        dimension = rules.get("dimension", DEFAULT_DIMENSION)
        if dimension not in DIMENSIONS:
            problems.append(f"dimension must be one of: {', '.join(DIMENSIONS)}")
        lead = rules.get("target_lead", DEFAULT_TARGET_LEAD)
        if isinstance(lead, bool) or not isinstance(lead, int) or not 2 <= lead <= MAX_TARGET_LEAD:
            problems.append(f"target_lead must be a whole number from 2 to {MAX_TARGET_LEAD}")
        config = {key: rules.get(key) for key in ("era_a_before", "era_b_after")}
        for key, value in config.items():
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                problems.append(f"{key} must be a year")
        before = config["era_a_before"] or DEFAULT_ERA_A_BEFORE
        after = config["era_b_after"] or DEFAULT_ERA_B_AFTER
        if all(isinstance(value, int) for value in (before, after)) and before > after + 1:
            problems.append("era_a_before can't be later than era_b_after")

        for key, default in (
            ("steal_enabled", True),
            ("momentum_cap", DEFAULT_MOMENTUM_CAP),
            ("sudden_death_after", DEFAULT_SUDDEN_DEATH_AFTER),
            ("sudden_death_every", DEFAULT_SUDDEN_DEATH_EVERY),
        ):
            value = rules.get(key, default)
            if key == "steal_enabled":
                if not isinstance(value, bool):
                    problems.append("steal_enabled must be a boolean")
            else:
                limits = {
                    "momentum_cap": (1, 5),
                    "sudden_death_after": (4, 50),
                    "sudden_death_every": (1, 10),
                }
                low, high = limits[key]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or not low <= value <= high
                ):
                    problems.append(f"{key} must be a whole number from {low} to {high}")
        return problems

    def prepare_rules_config(self, rules: dict) -> dict:
        config = tug_config(rules)
        return {
            **rules,
            "dimension": config["dimension"],
            "target_lead": config["target_lead"],
            TUG_RULES_VERSION_KEY: 2,
            "steal_enabled": config["steal_enabled"],
            "momentum_cap": config["momentum_cap"],
            "sudden_death_after": config["sudden_death_after"],
            "sudden_death_every": config["sudden_death_every"],
        }

    def team_players(self, run: Run) -> dict[str, str | None]:
        """Team A = owner; Team B = the first other participant to join."""
        participants = self.session.exec(
            select(RunParticipant)
            .where(RunParticipant.run_id == run.id)
            .order_by(RunParticipant.joined_at)
        ).all()
        ordered = sorted(participants, key=lambda p: p.role != "owner")
        ids = [participant.user_id for participant in ordered]
        return {TEAM_A: ids[0] if ids else None, TEAM_B: ids[1] if len(ids) > 1 else None}

    def sync_run_state(self, run: Run, steps: Sequence[RunStep]) -> None:
        rules = run.rules_config or {}
        players = self.team_players(run)
        scores = compute_scores(steps, {**rules, PLAYERS_KEY: players})
        state = {**rules, SCORES_KEY: scores, PLAYERS_KEY: players}
        if rules.get(TUG_RULES_VERSION_KEY) == 2:
            result = tally(steps, rules, players)
            state[MOMENTUM_KEY] = {
                "streak_team": result.streak[0],
                "streak": result.streak[1],
                "anchor": result.anchor,
                "effective_target": result.effective_target,
                "sudden_death": result.sudden_death,
                "next_team": result.next_team,
                "pulls": [asdict(pull) for pull in result.pulls],
            }
        if any(rules.get(key) != value for key, value in state.items()):
            run.rules_config = state
            self.session.add(run)

    def team_name(self, run: Run, team: str) -> str:
        user_id = self.team_players(run)[team]
        user = self.session.get(User, user_id) if user_id else None
        return user.display_name if user else ("Team A" if team == TEAM_A else "Team B")

    def evaluate_run_outcome(self, run: Run, steps: list[RunStep]) -> RunOutcome | None:
        if run.engine_version <= LEGACY_ENGINE_VERSION or run.status != RUN_STATUS_ACTIVE:
            return None
        rules = run.rules_config or {}
        if rules.get(TUG_RULES_VERSION_KEY) == 2:
            result = tally(steps, rules, self.team_players(run))
            winning_team = winner(result)
        else:
            scores = _v1_compute_scores(steps, rules)
            result = None
            winning_team = leading_team(scores, rules)
        if winning_team is not None:
            scores = result.scores if result is not None else _v1_compute_scores(steps, rules)
            loser = TEAM_B if winning_team == TEAM_A else TEAM_A
            suffix = " (Sudden Death)" if result is not None and result.sudden_death else ""
            return RunOutcome(
                RUN_STATUS_COMPLETED,
                f"{VICTORY_PREFIX} {self.team_name(run, winning_team)}, "
                f"{scores[winning_team]}-{scores[loser]}{suffix}!",
            )
        return super().evaluate_run_outcome(run, steps)

    async def discover_candidates(
        self,
        frontier_movie_id: int,
        mode: str = "or",
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
        history: Sequence[RunStep] | None = None,
    ) -> list[DiscoveryCandidate]:
        candidates = await super().discover_candidates(
            frontier_movie_id,
            mode,
            cast_limit,
            rules,
            previous_transition,
            history,
        )
        rules = rules or {}
        if rules.get(TUG_RULES_VERSION_KEY) != 2:
            return candidates
        players = rules.get(PLAYERS_KEY) or {}
        result = tally(history or [], rules, players)
        for candidate in candidates:
            territory = _territory(candidate.release_year, candidate.origin_country, rules)
            effect, points = preview_pull(result.next_team, territory, result, rules)
            candidate.tug_effect = effect
            candidate.tug_points = points
        return candidates
