import random
from datetime import UTC, datetime, timedelta

from app.engines.tug_of_war import (
    MOMENTUM_KEY,
    PLAYERS_KEY,
    TEAM_A,
    TEAM_B,
    TUG_RULES_VERSION_KEY,
    compute_scores,
    preview_pull,
    tally,
    winner,
)
from app.models.run import RunStep

PLAYERS = {TEAM_A: "alice", TEAM_B: "bob"}
RULES = {
    TUG_RULES_VERSION_KEY: 2,
    "dimension": "era",
    "target_lead": 4,
    "era_a_before": 1975,
    "era_b_after": 2005,
    "momentum_cap": 3,
    "sudden_death_after": 12,
    "sudden_death_every": 2,
}


def step(
    index: int,
    team: str,
    year: int | None,
    *,
    status: str = "watched",
    seed: bool = False,
) -> RunStep:
    return RunStep(
        id=f"step-{index:02}",
        run_id="run",
        movie_id=index,
        movie_title=f"Film {index}",
        movie_release_year=year,
        logged_by_user_id="alice",
        status=status,
        logged_at=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(minutes=index),
        transition_metadata={"tug_team": team, **({"seed": True} if seed else {})},
    )


def test_seed_and_planned_steps_do_not_affect_v2_tally():
    result = tally(
        [
            step(0, TEAM_A, 1950, seed=True),
            step(1, TEAM_A, 1960, status="planned"),
        ],
        RULES,
        PLAYERS,
    )

    assert result.scores == {TEAM_A: 0, TEAM_B: 0}
    assert result.pulls == []
    assert result.next_team == TEAM_A


def test_solo_run_keeps_team_a_as_the_next_pull():
    result = tally([step(1, TEAM_A, 1950)], RULES, {TEAM_A: "alice", TEAM_B: None})

    assert result.next_team == TEAM_A


def test_blood_feud_keeps_target_and_neutral_bank_after_many_pulls():
    rules = {**RULES, "target_lead": 9, "sudden_death_enabled": False}
    steps = [step(i, TEAM_A if i % 2 else TEAM_B, 1990) for i in range(1, 101)]
    result = tally(steps, rules, PLAYERS)
    assert result.effective_target == 9
    assert result.sudden_death is False
    assert all(pull.kind == "neutral" and pull.points == 0 for pull in result.pulls)
    assert result.scores == {TEAM_A: 0, TEAM_B: 0}
    assert preview_pull(TEAM_A, None, result, rules) == ("neutral", 0)


def test_home_streak_caps_at_configured_momentum_limit():
    result = tally(
        [step(i, TEAM_A, 1950 + i) for i in range(1, 5)],
        RULES,
        PLAYERS,
    )

    assert [pull.points for pull in result.pulls] == [1, 2, 3, 3]
    assert result.streak == (TEAM_A, 3)
    assert result.scores[TEAM_A] == 9


def test_neutral_resets_streak_sets_anchor_and_doubles_holders_next_score():
    result = tally(
        [
            step(1, TEAM_A, 1950),
            step(2, TEAM_B, 1990),
            step(3, TEAM_A, 1960),
            step(4, TEAM_B, 2010),
        ],
        RULES,
        PLAYERS,
    )

    assert [pull.kind for pull in result.pulls] == ["home", "neutral", "home", "home"]
    assert result.pulls[2].streak == 1
    assert result.pulls[3].multiplier == 2
    assert result.pulls[3].points == 2
    assert result.scores == {TEAM_A: 2, TEAM_B: 2}
    assert result.anchor is None


def test_invasion_steals_one_point_and_records_two_point_rope_swing():
    result = tally(
        [step(1, TEAM_B, 2010), step(2, TEAM_A, 2015)],
        RULES,
        PLAYERS,
    )

    invasion = result.pulls[-1]
    assert invasion.kind == "invasion"
    assert invasion.points == 2
    assert result.scores == {TEAM_A: 1, TEAM_B: 0}
    assert result.streak == (TEAM_A, 1)


def test_preview_steal_matches_realised_delta_at_zero():
    before = tally([], RULES, PLAYERS)
    effect, preview = preview_pull(TEAM_A, TEAM_B, before, RULES)
    after = tally([step(1, TEAM_A, 2010)], RULES, PLAYERS)

    assert effect == "invasion"
    assert preview == 1
    assert after.pulls[0].points == preview
    assert after.scores == {TEAM_A: 1, TEAM_B: 0}


def test_preview_steal_full_when_opponent_has_points():
    before = tally([step(1, TEAM_B, 2010), step(2, TEAM_B, 2011)], RULES, PLAYERS)

    effect, preview = preview_pull(TEAM_A, TEAM_B, before, RULES)

    assert effect == "invasion"
    assert before.scores[TEAM_B] > 0
    assert preview == 2


def test_sudden_death_shrinks_target_and_neutral_concedes():
    sudden_death_rules = {**RULES, "sudden_death_after": 4}
    steps = [
        step(1, TEAM_A, 1950),
        step(2, TEAM_B, 2010),
        step(3, TEAM_A, 1950),
        step(4, TEAM_B, 1990),
        step(5, TEAM_A, 1950),
        step(6, TEAM_B, 1990),
    ]
    after_four = tally(steps[:4], sudden_death_rules, PLAYERS)
    after_six = tally(steps, sudden_death_rules, PLAYERS)

    assert after_four.sudden_death
    assert after_four.pulls[-1].kind == "sudden_neutral"
    assert after_four.scores[TEAM_A] == 3
    assert after_four.effective_target == 4
    assert after_six.effective_target == 3


def test_v2_game_terminates_by_the_documented_turn_bound():
    rules = {
        **RULES,
        "sudden_death_after": 12,
        "sudden_death_every": 2,
        "target_lead": 4,
    }
    latest_turn = (
        rules["sudden_death_after"] + rules["sudden_death_every"] * (rules["target_lead"] - 1) + 1
    )

    for seed in range(100):
        rng = random.Random(seed)
        steps = []
        for index in range(1, latest_turn + 1):
            team = TEAM_A if index % 2 else TEAM_B
            territory = rng.choice((None, team, TEAM_B if team == TEAM_A else TEAM_A))
            year = 1990 if territory is None else 1950 if territory == TEAM_A else 2010
            steps.append(step(index, team, year))
            if winner(tally(steps, rules, PLAYERS)) is not None:
                break

        assert winner(tally(steps, rules, PLAYERS)) is not None
        assert len(steps) <= latest_turn


def test_legacy_score_wrapper_keeps_v1_territory_scoring():
    legacy_rules = {key: value for key, value in RULES.items() if key != TUG_RULES_VERSION_KEY}
    legacy_steps = [step(1, TEAM_A, 1950), step(2, TEAM_B, 2010)]

    assert compute_scores(legacy_steps, legacy_rules) == {TEAM_A: 1, TEAM_B: 1}
    # New state remains a fold result, not trusted cached/client rules state.
    assert MOMENTUM_KEY not in legacy_rules
    assert PLAYERS_KEY not in legacy_rules
