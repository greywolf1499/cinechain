"""Tug of War: two teams use film choices to pull a shared rope."""

from __future__ import annotations

import json
import random
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC
from typing import Any, ClassVar, Literal

from sqlmodel import select

from app.engines.cinechain import CineChainEngine
from app.engines.conditions import RunOutcome
from app.engines.rulebook import RuleSection
from app.engines.traversal import get_policy
from app.engines.tug_planes import (
    NEUTRAL,
    build_plane,
    check_balance,
    graph_density,
)
from app.engines.tug_planes import (
    TEAM_A as PLANE_TEAM_A,
)
from app.engines.tug_planes import (
    TEAM_B as PLANE_TEAM_B,
)
from app.engines.tug_planes import (
    deal as create_deal,
)
from app.facets.query import values_for
from app.models.cache import CachedMovie
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
from app.schemas.engine import FilterSpec, Preset, RuleField, ValidationResult
from app.services import feasibility
from app.services.movie_filters import is_reality_eligible
from app.utils.dates import parse_release_year

TUG_OF_WAR = "tug_of_war"
DIMENSION_ERA = "era"
DIMENSION_GEOGRAPHY = "geography"
DIMENSIONS = (DIMENSION_ERA, DIMENSION_GEOGRAPHY)
TEAM_A = "team_a"
TEAM_B = "team_b"
TugTeam = Literal["team_a", "team_b"]
TugEffect = Literal["home", "invasion", "neutral", "sudden_neutral"]

DEFAULT_DIMENSION = DIMENSION_ERA
DEFAULT_TARGET_LEAD = 7
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
PLANE_KEY = "tug_plane"
PLANE_SNAPSHOT_KEY = "tug_plane_snapshot"
TUG_SEED_KEY = "tug_seed"
TUG_DEAL_KEY = "tug_deal"
TUG_PORTALS_KEY = "tug_portals"
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
    rope: int | None = None
    streaks: dict[str, int] = field(default_factory=dict)
    banks: dict[str, bool] = field(default_factory=dict)
    rounds: int = 0
    round_complete: bool = True


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
        "sudden_death_enabled": rules.get("sudden_death_enabled", True),
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


def plane_for_rules(rules: dict | None):
    rules = rules or {}
    snapshot = rules.get(PLANE_SNAPSHOT_KEY) or {}
    plane_id = snapshot.get("id")
    config = rules.get(PLANE_KEY) or {}
    if not plane_id:
        plane_id = (
            ("geo_west_rest" if rules.get("dimension") == DIMENSION_GEOGRAPHY else "era_classic")
            if rules.get("dimension")
            else config.get("id", "genre_clusters")
        )
    params = snapshot.get("params") or config.get("params") or {}
    return build_plane(plane_id, params)


def _stored_territory(step: RunStep, rules: dict | None) -> TugTeam | None:
    if (rules or {}).get(TUG_RULES_VERSION_KEY) == 4:
        territory = (step.transition_metadata or {}).get("tug_territory")
        return territory if territory in (TEAM_A, TEAM_B) else None
    return step_team(step, rules)


def plane_facts_for(session, movie_ids: Sequence[int], rules: dict | None) -> dict[int, dict]:
    plane = plane_for_rules(rules)

    def leaf_ids(query):
        if query.facet:
            return {query.facet}
        return set().union(*(leaf_ids(child) for child in query.children()))

    facet_ids = leaf_ids(plane.pole_a.query) | leaf_ids(plane.pole_b.query)
    return values_for(session, movie_ids, facet_ids)


def plane_verdict(
    session, movie_id: int, rules: dict | None
) -> tuple[str | None, dict[str, bool | None]]:
    plane = plane_for_rules(rules)
    facts = plane_facts_for(session, [movie_id], rules).get(movie_id, {})
    evidence = {
        TEAM_A: plane.pole_a.query.evaluate(facts),
        TEAM_B: plane.pole_b.query.evaluate(facts),
    }
    if plane.unknown(facts):
        return "unknown", evidence
    territory = plane.territory(facts)
    return territory or NEUTRAL, evidence


def territory_of_step(step: RunStep, rules: dict | None) -> TugTeam | None:
    return _stored_territory(step, rules)


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


def tally_v2(
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
                "sudden_neutral"
                if config["sudden_death_enabled"] and turns >= config["sudden_death_after"]
                else "neutral"
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
            scores[puller] += multiplier
            opponent = TEAM_B if puller == TEAM_A else TEAM_A
            steal = min(scores[opponent], multiplier)
            scores[opponent] -= steal
            points = multiplier + steal
            anchor = None if anchor == puller else anchor
        else:
            kind = (
                "sudden_neutral"
                if config["sudden_death_enabled"] and turns >= config["sudden_death_after"]
                else "neutral"
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
        target = (
            max(
                1,
                config["target_lead"]
                - max(
                    (turns - config["sudden_death_after"]) // config["sudden_death_every"],
                    0,
                ),
            )
            if config["sudden_death_enabled"]
            else config["target_lead"]
        )

    sudden_death = config["sudden_death_enabled"] and turns >= config["sudden_death_after"]
    return TugTally(
        scores=scores,
        streak=(streak_team, streak),
        anchor=anchor,
        effective_target=target,
        sudden_death=sudden_death,
        pulls=pulls,
        next_team=next_team,
    )


def preview_pull_v3(
    puller: TugTeam,
    territory: TugTeam | None,
    result: TugTally,
    rules: dict | None,
) -> tuple[TugEffect, int]:
    config = tug_config(rules)
    if territory is None or (territory != puller and not config["steal_enabled"]):
        return "neutral", 0
    multiplier = 2 if result.banks.get(puller) else 1
    if territory == puller:
        return "home", min(result.streaks.get(puller, 0) + 1, config["momentum_cap"]) * multiplier
    return "invasion", 2 * multiplier


def tally_v3(
    steps: Sequence[RunStep],
    rules: dict | None,
    players: dict[str, str | None] | None = None,
    territory_of: Callable[[RunStep], str | None] | None = None,
) -> TugTally:
    """A round gives each team one pull; only completed rounds can settle victory."""
    rules = rules or {}
    config = tug_config(rules)
    players = players or rules.get(PLAYERS_KEY) or {}
    paired = players.get(TEAM_B) is not None
    scores = {TEAM_A: 0, TEAM_B: 0}
    streaks = {TEAM_A: 0, TEAM_B: 0}
    banks = {TEAM_A: False, TEAM_B: False}
    rope = rounds = 0
    first: TugTeam = TEAM_A
    next_team: TugTeam = first
    round_teams: set[str] = set()
    target = config["target_lead"]
    sudden = False
    pulls: list[Pull] = []
    for step in _ordered_pull_steps(steps):
        if step.status != "watched" or (step.transition_metadata or {}).get(SEED_KEY):
            continue
        puller = step_turn_team(step, players)
        if puller is None:
            continue
        opponent: TugTeam = TEAM_B if puller == TEAM_A else TEAM_A
        territory = (territory_of or (lambda item: step_team(item, rules)))(step)
        if territory == NEUTRAL:
            territory = None
        multiplier = 2 if banks[puller] else 1
        if territory is None or (territory != puller and not config["steal_enabled"]):
            kind: TugEffect = "neutral"
            points = 0
            streaks[puller] = 0
            banks[puller] = True
            multiplier = 1
        else:
            if territory == puller:
                kind = "home"
                streaks[puller] = min(streaks[puller] + 1, config["momentum_cap"])
                points = streaks[puller] * multiplier
            else:
                kind = "invasion"
                points = 2 * multiplier
                streaks[opponent] = 0
            banks[puller] = False
            scores[puller] += points
            rope += points if puller == TEAM_A else -points
        pulls.append(Pull(step.id, puller, territory, kind, points, streaks[puller], multiplier))
        round_teams.add(puller)
        if not paired or len(round_teams) == 2:
            rounds += 1
            round_teams.clear()
            completed_pulls = rounds * (2 if paired else 1)
            sudden = (
                config["sudden_death_enabled"] and completed_pulls >= config["sudden_death_after"]
            )
            if sudden:
                target = max(
                    1,
                    config["target_lead"]
                    - (completed_pulls - config["sudden_death_after"])
                    // config["sudden_death_every"],
                )
                first = (
                    TEAM_B
                    if rope > 0
                    else TEAM_A
                    if rope < 0
                    else (TEAM_B if first == TEAM_A else TEAM_A)
                )
            next_team = first if paired else TEAM_A
        else:
            next_team = opponent
    return TugTally(
        scores,
        (None, 0),
        None,
        target,
        sudden,
        pulls,
        next_team,
        rope,
        streaks,
        banks,
        rounds,
        not round_teams,
    )


def tally(
    steps: Sequence[RunStep],
    rules: dict | None,
    players: dict[str, str | None] | None = None,
) -> TugTally:
    version = (rules or {}).get(TUG_RULES_VERSION_KEY)
    if version in (3, 4):
        territory_of = (lambda step: _stored_territory(step, rules)) if version == 4 else None
        return tally_v3(steps, rules, players, territory_of)
    return tally_v2(steps, rules, players)


def compute_scores(steps: Sequence[RunStep], rules: dict | None) -> dict[str, int]:
    """Scores for the configured rules version, preserving the v1 scoring path."""
    rules = rules or {}
    version = rules.get(TUG_RULES_VERSION_KEY)
    if version in (None, 1):
        return _v1_compute_scores(steps, rules)
    if version not in (2, 3, 4):
        raise ValueError(f"Unsupported Tug rules version: {version}")
    players = rules.get(PLAYERS_KEY) or {}
    return tally(steps, rules, players).scores


def leading_team(scores: dict[str, int], rules: dict | None) -> str | None:
    """The team that has won the legacy tug, else None."""
    lead = scores[TEAM_A] - scores[TEAM_B]
    if abs(lead) >= tug_config(rules)["target_lead"]:
        return TEAM_A if lead > 0 else TEAM_B
    return None


def winner(result: TugTally) -> TugTeam | None:
    if result.rope is not None and not result.round_complete:
        return None
    lead = result.rope if result.rope is not None else result.scores[TEAM_A] - result.scores[TEAM_B]
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
    if (rules or {}).get(TUG_RULES_VERSION_KEY) == 3:
        return preview_pull_v3(puller, territory, result, rules)
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
    opponent = TEAM_B if puller == TEAM_A else TEAM_A
    steal = min(result.scores[opponent], multiplier)
    return "invasion", multiplier + steal


class TugOfWarEngine(CineChainEngine):
    rule_fields: ClassVar[list[RuleField]] = [
        *CineChainEngine.rule_fields,
        RuleField(
            key="target_lead",
            kind="int",
            label="Target lead",
            min=2,
            max=MAX_TARGET_LEAD,
            default=DEFAULT_TARGET_LEAD,
        ),
        RuleField(
            key="sudden_death_enabled",
            kind="bool",
            label="Sudden Death",
            default=True,
            help="Shrink the target at round boundaries; trailing team pulls first.",
        ),
        RuleField(
            key="steal_enabled", kind="bool", label="Allow raids", default=True, group="advanced"
        ),
        RuleField(
            key="momentum_cap",
            kind="int",
            label="Momentum cap",
            min=1,
            max=5,
            default=DEFAULT_MOMENTUM_CAP,
            group="advanced",
        ),
        RuleField(
            key="sudden_death_after",
            kind="int",
            label="Sudden Death after pulls",
            min=4,
            max=50,
            default=DEFAULT_SUDDEN_DEATH_AFTER,
            group="advanced",
        ),
        RuleField(
            key="sudden_death_every",
            kind="int",
            label="Shrink target every pulls",
            min=1,
            max=10,
            default=DEFAULT_SUDDEN_DEATH_EVERY,
            group="advanced",
        ),
    ]
    presets: ClassVar[list[Preset]] = [
        Preset(
            id="friendly",
            label="Friendly",
            blurb="A quick five-point match.",
            values={"target_lead": 5, "sudden_death_enabled": True},
        ),
        Preset(
            id="rivalry",
            label="Rivalry",
            blurb="Seven points with Sudden Death.",
            values={"target_lead": 7, "sudden_death_enabled": True},
        ),
        Preset(
            id="blood_feud",
            label="Blood Feud",
            blurb="Nine points, no shrinking target.",
            values={"target_lead": 9, "sudden_death_enabled": False},
        ),
    ]
    default_preset = "rivalry"
    discovery_filters: ClassVar[list[FilterSpec]] = [
        FilterSpec(
            key="tug_effect",
            kind="select",
            label="Pull effect",
            source="tug_effect",
            default="home",
            help="Build your territory, Raid the opponent, or Bank a neutral film.",
        ),
    ]
    tagline = "Pull the rope your way"
    tags: ClassVar[list[str]] = ["Shared cast", "Two teams", "Era or geography"]
    rulebook: ClassVar[RuleSection] = RuleSection(
        "Lead by {target_lead} points to win (now: {effective_target}).",
        ["Link a film through shared cast.", "{tug_turn}"],
        [
            "{tug_first_turn}",
            "Team A: {territory_a}. Team B: {territory_b}.",
            "{tug_scoring}",
            "{sudden_rule}",
        ],
        ["The other team wins if it reaches the lead target first.", "{fail_goal}"],
        [
            "Your pick sets your opponent's options: leave a frontier they cannot easily exploit.",
            "{tug_tip}",
        ],
        ["seed", "build", "raid", "bank", "streak", "sudden_death"],
    )

    @classmethod
    def rulebook_values(cls, rules: dict | None) -> dict[str, Any]:
        config = tug_config(rules)
        state = (rules or {}).get(MOMENTUM_KEY) or {}
        legacy = (rules or {}).get(TUG_RULES_VERSION_KEY) == 1
        v3 = (rules or {}).get(TUG_RULES_VERSION_KEY, 3) in (3, 4)
        snapshot = (rules or {}).get(PLANE_SNAPSHOT_KEY) or {}
        poles = snapshot.get("poles") or {}
        team_a_label = (poles.get(TEAM_A) or {}).get("label")
        team_b_label = (poles.get(TEAM_B) or {}).get("label")
        return {
            **super().rulebook_values(rules),
            **config,
            "effective_target": state.get("effective_target", config["target_lead"]),
            "territory_a": team_a_label
            or (
                f"pre-{config['era_a_before']}"
                if config["dimension"] == DIMENSION_ERA
                else "US and Europe"
            ),
            "territory_b": team_b_label
            or (
                f"post-{config['era_b_after']}"
                if config["dimension"] == DIMENSION_ERA
                else "the rest of the world"
            ),
            "tug_first_turn": "Log shared-cast films."
            if legacy
            else "Each team pulls once per round. Check for a win after both pulls."
            if v3
            else "Take alternating shared-cast turns.",
            "tug_tip": "Choose actors with routes back home. Their home films help their side."
            if legacy
            else "Raid to break their streak; bank to double your next scoring pull without resetting their streak."
            if v3
            else "A raid removes only available points; bank when the doubled next pull outweighs waiting.",
            "tug_turn": "Watch a home film to score for that side. Neutral films score no points."
            if legacy
            else "Build at home, raid their home, or bank a neutral film.",
            "tug_scoring": "Each watched home film gives its side 1 point. Neutral films score no points."
            if legacy
            else (
                f"Build grows your streak up to {config['momentum_cap']}. Their builds do not reset yours. "
                + (
                    "Raid moves the rope 2 points and resets their streak. "
                    if config["steal_enabled"]
                    else "Raids are disabled; opposing films are neutral. "
                )
                + "Bank scores 0 and resets your streak. It doubles your next scoring pull. Each team keeps its own bank."
            )
            if v3
            else (
                f"Build adds streak points up to {config['momentum_cap']}; a bank doubles the next scoring pull. "
                + (
                    "Raids gain 1 and remove up to 1 available opponent point, doubled by a bank."
                    if config["steal_enabled"]
                    else "Raids are disabled: opposing-territory films count as neutral."
                )
            ),
            "sudden_rule": "This run has no streak bonus or Sudden Death."
            if legacy
            else "Sudden Death is disabled; the target stays fixed."
            if not config["sudden_death_enabled"]
            else f"After {config['sudden_death_after']} pulls, shrink the target every {config['sudden_death_every']} pulls. Finish the round first. The team behind goes first. Ties swap who starts. Banks still score 0."
            if v3
            else f"Sudden Death begins after {config['sudden_death_after']} pulls: neutral films give the opponent 1 point and the target shrinks every {config['sudden_death_every']} pulls.",
        }

    def coach_line(self, run: Run, steps: Sequence[RunStep]) -> str | None:
        rules = run.rules_config or {}
        if rules.get(TUG_RULES_VERSION_KEY) not in (2, 3, 4):
            return None
        players = self.team_players(run)
        result = tally(steps, rules, players)
        if rules.get("steal_enabled", True):
            streaks = (
                result.streaks
                if rules.get(TUG_RULES_VERSION_KEY) == 3
                else {result.streak[0]: result.streak[1]}
            )
            for team in (TEAM_A, TEAM_B):
                streak = streaks.get(team, 0)
                if streak >= 2:
                    name = self.team_name(run, team)
                    return f"{name}'s streak is ×{streak} — a Raid breaks it."
        return f"{self.team_name(run, result.next_team)} pulls next."

    game_type = TUG_OF_WAR
    display_name = "Tug of War"
    description = (
        "Two teams pull a shared rope: home films build momentum, invasions steal ground, "
        "neutral films bank a doubled pull, and Sudden Death lowers the winning target."
    )
    capabilities: ClassVar[list[str]] = [
        *(cap for cap in CineChainEngine.capabilities if cap != "modifiers"),
        "tug_of_war",
        "tug_draft",
    ]
    modifier_scopes = frozenset({"film", "sequence"})

    def validate_rules_config(self, rules: dict | None) -> list[str]:
        problems = super().validate_rules_config(rules)
        rules = rules or {}
        dimension = rules.get("dimension", DEFAULT_DIMENSION)
        plane_choice = rules.get(PLANE_KEY)
        if plane_choice is None and dimension not in DIMENSIONS:
            problems.append(f"dimension must be one of: {', '.join(DIMENSIONS)}")
        if plane_choice is not None:
            if not isinstance(plane_choice, dict) or not isinstance(plane_choice.get("id"), str):
                problems.append("tug_plane must include a plane id")
            else:
                try:
                    plane = build_plane(plane_choice["id"], plane_choice.get("params") or {})
                    traversal = rules.get("tug_traversal", plane.default_traversal)
                    if traversal not in plane.allowed_traversals:
                        problems.append("tug_traversal is not allowed for the selected plane")
                except (TypeError, ValueError) as exc:
                    problems.append(str(exc))
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
        plane_config = rules.get(PLANE_KEY)
        if plane_config is None:
            return {
                **rules,
                "dimension": config["dimension"],
                "target_lead": config["target_lead"],
                TUG_RULES_VERSION_KEY: 3,
                "steal_enabled": config["steal_enabled"],
                "momentum_cap": config["momentum_cap"],
                "sudden_death_after": config["sudden_death_after"],
                "sudden_death_every": config["sudden_death_every"],
                "sudden_death_enabled": config["sudden_death_enabled"],
            }
        plane_id = plane_config["id"]
        params = plane_config.get("params") or {}
        plane = build_plane(plane_id, params)
        return {
            **rules,
            "dimension": config["dimension"],
            "target_lead": config["target_lead"],
            PLANE_KEY: {"id": plane.id, "params": plane.defaults},
            "tug_traversal": rules.get("tug_traversal", plane.default_traversal),
            TUG_RULES_VERSION_KEY: 4,
            "steal_enabled": config["steal_enabled"],
            "momentum_cap": config["momentum_cap"],
            "sudden_death_after": config["sudden_death_after"],
            "sudden_death_every": config["sudden_death_every"],
            "sudden_death_enabled": config["sudden_death_enabled"],
        }

    async def prepare_run(self, rules: dict, user_id: str) -> dict:
        rules = dict(rules)
        if rules.get(TUG_RULES_VERSION_KEY) != 4:
            return rules
        plane_config = rules.get(PLANE_KEY) or {}
        plane = build_plane(plane_config.get("id", "genre_clusters"), plane_config.get("params"))
        params = plane.defaults
        selected_traversal = rules.get("tug_traversal", plane.default_traversal)
        movies = feasibility.movies(self.session)
        eligible = {
            movie_id: movie for movie_id, movie in movies.items() if is_reality_eligible(movie)
        }
        density = (
            graph_density(self.session, list(eligible), traversal=selected_traversal)
            if get_policy(selected_traversal).graph
            else None
        )
        balance = check_balance(
            self.session,
            plane,
            eligible,
            bridge_density=density,
            traversal=selected_traversal,
        )
        selected_traversal = balance["traversal"]
        rules[PLANE_KEY] = {"id": plane.id, "params": params}
        rules["tug_traversal"] = selected_traversal
        rules[PLANE_SNAPSHOT_KEY] = plane.snapshot(params, selected_traversal, balance)
        seed = random.SystemRandom().randrange(1, 2**63)
        rules[TUG_SEED_KEY] = seed
        rules[TUG_PORTALS_KEY] = {"remaining": 1}
        if selected_traversal == "draft":
            values = plane_facts_for(self.session, list(eligible), rules)
            buckets = {PLANE_TEAM_A: [], PLANE_TEAM_B: [], NEUTRAL: []}
            for movie_id in eligible:
                territory = plane.territory(values.get(movie_id, {}))
                if territory in buckets:
                    buckets[territory].append(movie_id)
            rules[TUG_DEAL_KEY] = create_deal(seed, buckets)
        return rules

    @classmethod
    def public_rules(cls, rules: dict | None, run: Run) -> dict[str, Any]:
        result = dict(rules or {})
        if result.get(TUG_RULES_VERSION_KEY) == 4 and result.get(PLANE_SNAPSHOT_KEY):
            return result
        legacy_id = (
            "geo_west_rest" if result.get("dimension") == DIMENSION_GEOGRAPHY else "era_classic"
        )
        plane = build_plane(legacy_id)
        result[PLANE_SNAPSHOT_KEY] = plane.snapshot(
            plane.defaults,
            "shared_cast",
            {"legacy": True},
        )
        return result

    async def validate_primary(
        self,
        from_movie_id: int,
        to_movie_id: int,
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
    ) -> ValidationResult:
        if (rules or {}).get(TUG_RULES_VERSION_KEY) != 4:
            return await super().validate_primary(
                from_movie_id, to_movie_id, cast_limit, rules, previous_transition
            )
        traversal = get_policy((rules or {}).get("tug_traversal"))
        if traversal.key == "draft" and to_movie_id not in {
            entry["movie_id"] for entry in (rules or {}).get(TUG_DEAL_KEY, [])
        }:
            return ValidationResult(valid=False, reason="Film is not in this round's draft deal")
        return await traversal.validate(
            self,
            from_movie_id,
            to_movie_id,
            cast_limit,
            rules,
            previous_transition,
            None,
        )

    async def validate_attribute_link(
        self, traversal: str, from_movie_id: int, to_movie_id: int, rules: dict | None
    ) -> ValidationResult:
        earlier = self.session.get(CachedMovie, from_movie_id)
        later = self.session.get(CachedMovie, to_movie_id)
        if earlier is None or later is None:
            return ValidationResult(valid=False, reason="Cached film details are incomplete")
        if traversal == "genre_overlap":
            from_genres = set(earlier.genre_ids or [])
            to_genres = set(later.genre_ids or [])
            shared = sorted(from_genres & to_genres)
            valid = bool(shared)
            evidence = {"genre_ids": shared}
        elif traversal == "decade_adjacent":
            year_a = parse_release_year(earlier.release_date)
            year_b = parse_release_year(later.release_date)
            valid = (
                year_a is not None and year_b is not None and abs(year_a // 10 - year_b // 10) <= 1
            )
            evidence = {"release_years": [year_a, year_b]}
        elif traversal == "language":
            valid = bool(
                earlier.original_language and earlier.original_language == later.original_language
            )
            evidence = {"language": earlier.original_language}
        elif traversal == "shared_trope":
            from app.facets.tropes import trusted_tropes

            shared = sorted(
                set(trusted_tropes(self.session, [from_movie_id]).get(from_movie_id, []))
                & set(trusted_tropes(self.session, [to_movie_id]).get(to_movie_id, []))
            )
            valid = bool(shared)
            evidence = {"tropes": shared}
        else:
            return ValidationResult(
                valid=False, reason=f"Unsupported attribute traversal: {traversal}"
            )
        return ValidationResult(
            valid=bool(valid),
            reason=None if valid else f"No {traversal.replace('_', ' ')} link found",
            mechanic={"tug_link": {"kind": traversal, **evidence}} if valid else None,
        )

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
        if rules.get(TUG_RULES_VERSION_KEY) in (2, 3, 4):
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
            if rules.get(TUG_RULES_VERSION_KEY) in (3, 4):
                state[MOMENTUM_KEY].update(
                    {
                        "rope": result.rope,
                        "streaks": result.streaks,
                        "banks": result.banks,
                        "rounds": result.rounds,
                        "round_complete": result.round_complete,
                    }
                )
            if rules.get(TUG_RULES_VERSION_KEY) == 4:
                state[TUG_DEAL_KEY] = self._next_draft_deal(rules, steps)
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
        if rules.get(TUG_RULES_VERSION_KEY) in (2, 3, 4):
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
        if (rules or {}).get(TUG_RULES_VERSION_KEY) == 4 and not get_policy(
            (rules or {}).get("tug_traversal")
        ).graph:
            candidates = await self._discover_attribute_pool(frontier_movie_id, rules, history)
        else:
            candidates = await super().discover_candidates(
                frontier_movie_id,
                mode,
                cast_limit,
                rules,
                previous_transition,
                history,
            )
        return self.annotate_candidates(candidates, rules, history)

    async def _discover_attribute_pool(
        self, frontier_movie_id: int, rules: dict | None, history: Sequence[RunStep] | None
    ) -> list[DiscoveryCandidate]:
        rules = rules or {}
        traversal = get_policy(rules.get("tug_traversal"))
        source_ids = (
            [entry["movie_id"] for entry in rules.get(TUG_DEAL_KEY, [])]
            if traversal.key == "draft"
            else list(feasibility.movies(self.session))
        )
        visited = {step.movie_id for step in history or []}
        output: list[DiscoveryCandidate] = []
        movies = feasibility.movies(self.session)
        for movie_id in source_ids:
            movie = movies.get(movie_id)
            if (
                movie is None
                or movie_id == frontier_movie_id
                or movie_id in visited
                or not is_reality_eligible(movie)
                or (movie.runtime is not None and movie.runtime < rules.get("min_runtime", 0))
            ):
                continue
            link = await self.validate_primary(
                frontier_movie_id, movie_id, rules=rules, previous_transition=None
            )
            if not link.valid:
                continue
            output.append(
                DiscoveryCandidate(
                    movie_id=movie_id,
                    title=movie.title,
                    poster_path=movie.poster_path,
                    release_year=parse_release_year(movie.release_date),
                    origin_country=movie.origin_country,
                    genre_ids=movie.genre_ids or [],
                    popularity=movie.popularity,
                    original_language=movie.original_language,
                    rating=movie.vote_average,
                    runtime=movie.runtime,
                )
            )
            if len(output) >= 500:
                break
        return output

    def _next_draft_deal(self, rules: dict, steps: Sequence[RunStep]) -> list[dict]:
        if rules.get("tug_traversal") != "draft":
            return rules.get(TUG_DEAL_KEY, [])
        movies = feasibility.movies(self.session)
        plane = plane_for_rules(rules)
        ids = list(movies)
        facts = plane_facts_for(self.session, ids, rules)
        buckets = {PLANE_TEAM_A: [], PLANE_TEAM_B: [], NEUTRAL: []}
        watched_ids = {
            step.movie_id
            for step in steps
            if step.status == "watched" and not (step.transition_metadata or {}).get(SEED_KEY)
        }
        for movie_id, movie in movies.items():
            if movie_id in watched_ids or not is_reality_eligible(movie):
                continue
            territory = plane.territory(facts.get(movie_id, {}))
            if territory in buckets:
                buckets[territory].append(movie_id)
        pulls = sum(
            step.status == "watched" and not (step.transition_metadata or {}).get(SEED_KEY)
            for step in steps
        )
        return create_deal((rules.get(TUG_SEED_KEY, 0) + pulls) % (2**63), buckets)

    def annotate_candidates(
        self,
        candidates: list[DiscoveryCandidate],
        rules: dict | None = None,
        history: Sequence[RunStep] | None = None,
    ) -> list[DiscoveryCandidate]:
        candidates = super().annotate_candidates(candidates, rules, history)
        rules = rules or {}
        if rules.get(TUG_RULES_VERSION_KEY) not in (2, 3, 4):
            return candidates
        players = rules.get(PLAYERS_KEY) or {}
        result = tally(history or [], rules, players)
        for candidate in candidates:
            if rules.get(TUG_RULES_VERSION_KEY) == 4:
                territory, evidence = plane_verdict(self.session, candidate.movie_id, rules)
                candidate.tug_territory = (
                    territory if territory in (PLANE_TEAM_A, PLANE_TEAM_B, NEUTRAL) else None
                )
                candidate.tug_territory_evidence = evidence
                if candidate.tug_territory is None:
                    continue
                territory = None if candidate.tug_territory == NEUTRAL else candidate.tug_territory
            else:
                territory = _territory(candidate.release_year, candidate.origin_country, rules)
            effect, points = preview_pull(result.next_team, territory, result, rules)
            candidate.tug_effect = effect
            candidate.tug_points = points
            candidate.tug_breaks_streak = (
                effect == "invasion"
                and result.streaks.get(
                    TEAM_B if result.next_team == TEAM_A else TEAM_A,
                    0,
                )
                > 0
            )
        return candidates
