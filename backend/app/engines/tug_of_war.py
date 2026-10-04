"""Tug of War: a two-player rope pulled by the films you log.

The chain is the classic shared-cast CineChain, so this engine extends `CineChainEngine` (itself
a `BaseChallengeEngine`). On top of it every watched film scores one point for a team, decided
by one *dimension* of the film's metadata:

- `era`: Team A = released before `era_a_before` (default 1975), Team B = after `era_b_after`
  (default 2005); films in between score for nobody.
- `geography`: Team A = Western (US + Europe), Team B = the rest of the world, judged on the
  film's first production country; films with no country on record score for nobody.

Team A is the run's owner, Team B the next partner. Scores are always recomputed from the steps
(and cached in `rules_config["tug_scores"]` for the UI), so deleting a step can never leave them
stale. The run is won as soon as one team leads by `target_lead` points. Run modifiers (chrono,
runtime, cooldown) would let one player lock the other out, so they are off.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, ClassVar

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

TUG_OF_WAR = "tug_of_war"
DIMENSION_ERA = "era"
DIMENSION_GEOGRAPHY = "geography"
DIMENSIONS = (DIMENSION_ERA, DIMENSION_GEOGRAPHY)
TEAM_A = "team_a"
TEAM_B = "team_b"

DEFAULT_DIMENSION = DIMENSION_ERA
DEFAULT_TARGET_LEAD = 4
MAX_TARGET_LEAD = 50
DEFAULT_ERA_A_BEFORE = 1975
DEFAULT_ERA_B_AFTER = 2005

SCORES_KEY = "tug_scores"
PLAYERS_KEY = "tug_players"
VICTORY_PREFIX = "Tug of War won by"

# US plus Europe, including the countries TMDB still files older films under.
WESTERN_COUNTRIES = frozenset({
    "US",
    "AD", "AL", "AT", "BA", "BE", "BG", "BY", "CH", "CY", "CZ", "DE", "DK", "EE", "ES", "FI",
    "FR", "GB", "GR", "HR", "HU", "IE", "IS", "IT", "LI", "LT", "LU", "LV", "MC", "MD", "ME",
    "MK", "MT", "NL", "NO", "PL", "PT", "RO", "RS", "RU", "SE", "SI", "SK", "SM", "UA", "VA",
    "XK", "SU", "DD", "XG", "CS", "XC", "YU",
})


def tug_config(rules: dict | None) -> dict[str, Any]:
    rules = rules or {}
    return {
        "dimension": rules.get("dimension") or DEFAULT_DIMENSION,
        "target_lead": rules.get("target_lead") or DEFAULT_TARGET_LEAD,
        "era_a_before": rules.get("era_a_before") or DEFAULT_ERA_A_BEFORE,
        "era_b_after": rules.get("era_b_after") or DEFAULT_ERA_B_AFTER,
    }


def _first_country(step: RunStep) -> str | None:
    raw = step.movie_origin_country
    if not raw:
        return None
    try:
        countries = json.loads(raw)
    except ValueError:
        countries = [raw]
    if isinstance(countries, str):
        countries = [countries]
    return str(countries[0]).upper() if countries else None


def step_team(step: RunStep, rules: dict | None) -> str | None:
    """Which team a film scores for under the run's dimension (None = nobody)."""
    config = tug_config(rules)
    if config["dimension"] == DIMENSION_GEOGRAPHY:
        country = _first_country(step)
        if country is None:
            return None
        return TEAM_A if country in WESTERN_COUNTRIES else TEAM_B
    year = step.movie_release_year
    if year is None:
        return None
    if year < config["era_a_before"]:
        return TEAM_A
    if year > config["era_b_after"]:
        return TEAM_B
    return None


def compute_scores(steps: Sequence[RunStep], rules: dict | None) -> dict[str, int]:
    """Points per team from the steps actually watched (a planned film hasn't pulled yet)."""
    scores = {TEAM_A: 0, TEAM_B: 0}
    for step in steps:
        if step.status != "watched":
            continue
        team = step_team(step, rules)
        if team is not None:
            scores[team] += 1
    return scores


def leading_team(scores: dict[str, int], rules: dict | None) -> str | None:
    """The team that has won the tug (lead >= target_lead), else None."""
    lead = scores[TEAM_A] - scores[TEAM_B]
    if abs(lead) >= tug_config(rules)["target_lead"]:
        return TEAM_A if lead > 0 else TEAM_B
    return None


class TugOfWarEngine(CineChainEngine):
    game_type = TUG_OF_WAR
    display_name = "Tug of War"
    description = (
        "Two partners pull a rope with the films they pick: every film scores for one side of "
        "a dimension (old vs new, or West vs the rest of the world). First to lead by the "
        "target wins."
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
        if all(isinstance(v, int) for v in (before, after)) and before > after + 1:
            problems.append("era_a_before can't be later than era_b_after")
        return problems

    def prepare_rules_config(self, rules: dict) -> dict:
        config = tug_config(rules)
        return {
            **rules, "dimension": config["dimension"], "target_lead": config["target_lead"]}

    def team_players(self, run: Run) -> dict[str, str | None]:
        """Team A = the run's owner, Team B = the next partner who joined."""
        participants = self.session.exec(
            select(RunParticipant).where(RunParticipant.run_id == run.id)
            .order_by(RunParticipant.joined_at)).all()
        ordered = sorted(participants, key=lambda p: p.role != "owner")
        ids = [p.user_id for p in ordered]
        return {TEAM_A: ids[0] if ids else None, TEAM_B: ids[1] if len(ids) > 1 else None}

    def sync_run_state(self, run: Run, steps: Sequence[RunStep]) -> None:
        rules = run.rules_config or {}
        scores = compute_scores(steps, rules)
        players = self.team_players(run)
        if rules.get(SCORES_KEY) != scores or rules.get(PLAYERS_KEY) != players:
            run.rules_config = {**rules, SCORES_KEY: scores, PLAYERS_KEY: players}
            self.session.add(run)

    def _team_name(self, run: Run, team: str) -> str:
        user_id = self.team_players(run)[team]
        user = self.session.get(User, user_id) if user_id else None
        return user.display_name if user else ("Team A" if team == TEAM_A else "Team B")

    def evaluate_run_outcome(self, run: Run, steps: list[RunStep]) -> RunOutcome | None:
        """Victory: complete the run once one team leads by `target_lead` points."""
        if run.engine_version <= LEGACY_ENGINE_VERSION or run.status != RUN_STATUS_ACTIVE:
            return None
        scores = compute_scores(steps, run.rules_config)
        winner = leading_team(scores, run.rules_config)
        if winner is not None:
            loser = TEAM_B if winner == TEAM_A else TEAM_A
            return RunOutcome(
                RUN_STATUS_COMPLETED,
                f"{VICTORY_PREFIX} {self._team_name(run, winner)}, "
                f"{scores[winner]}-{scores[loser]}!")
        return super().evaluate_run_outcome(run, steps)
