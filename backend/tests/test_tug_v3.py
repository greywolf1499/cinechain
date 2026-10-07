"""V3 rope mechanics and symmetric, seeded mirror-strategy acceptance."""

import random
import time

import pytest
import respx
from sqlmodel import Session

from app.engines.tug_of_war import (
    DEFAULT_TARGET_LEAD,
    TEAM_A,
    TEAM_B,
    TUG_RULES_VERSION_KEY,
    TugOfWarEngine,
    compute_scores,
    preview_pull,
    tally,
    tug_config,
    winner,
)
from app.models.cache import CachedActor, CachedMovie, CachedMovieCast
from app.models.run import Run
from app.services.cache_repo import CacheRepo
from app.utils.ids import utcnow
from tests.test_fork_veto_tug import bob, client, db_engine, detail, log, mock_films, tug
from tests.test_tug_momentum import PLAYERS, RULES, step

__all__ = ["bob", "client", "db_engine"]

V3 = {**RULES, "tug_rules_version": 3, "target_lead": 7}
BANK_HEAVY = ("build", "raid", *("bank",) * 30)


def test_alternating_builds_keep_both_streaks_and_cap():
    steps = [step(i, TEAM_A if i % 2 else TEAM_B, 1950 if i % 2 else 2010) for i in range(1, 9)]
    result = tally(steps, V3, PLAYERS)
    assert result.streaks == {TEAM_A: 3, TEAM_B: 3}
    assert [pull.points for pull in result.pulls] == [1, 1, 2, 2, 3, 3, 3, 3]
    assert result.rope == 0
    assert result.rounds == 4


def test_raid_moves_unclamped_rope_and_breaks_defender_streak():
    steps = [step(1, TEAM_A, 1950), step(2, TEAM_B, 2010), step(3, TEAM_A, 2010)]
    result = tally(steps, V3, PLAYERS)
    assert result.rope == 2
    assert result.streaks == {TEAM_A: 1, TEAM_B: 0}
    assert result.scores == {TEAM_A: 3, TEAM_B: 1}
    assert tally([step(1, TEAM_A, 2010)], V3, PLAYERS).rope == 2


def test_bank_is_per_team_doubles_next_pull_and_preview_matches():
    steps = [step(1, TEAM_A, 1990), step(2, TEAM_B, 1990)]
    before = tally(steps, V3, PLAYERS)
    assert before.banks == {TEAM_A: True, TEAM_B: True}
    assert preview_pull(TEAM_A, TEAM_B, before, V3) == ("invasion", 4)
    result = tally([*steps, step(3, TEAM_A, 2010), step(4, TEAM_B, 2010)], V3, PLAYERS)
    assert result.banks == {TEAM_A: False, TEAM_B: False}
    assert [pull.points for pull in result.pulls] == [0, 0, 4, 2]
    assert result.rope == 2


def test_sudden_death_uses_complete_rounds_and_trailing_initiative():
    rules = {**V3, "sudden_death_after": 4}
    steps = [
        step(1, TEAM_A, 2010),
        step(2, TEAM_B, 1990),
        step(3, TEAM_A, 1950),
        step(4, TEAM_B, 2010),
    ]
    result = tally(steps, rules, PLAYERS)
    assert result.sudden_death and result.next_team == TEAM_B
    after_first = tally([*steps, step(5, TEAM_B, 1950)], rules, PLAYERS)
    assert after_first.next_team == TEAM_A
    assert after_first.effective_target == result.effective_target
    assert winner(after_first) is None
    after_round = tally([*steps, step(5, TEAM_B, 1950), step(6, TEAM_A, 1990)], rules, PLAYERS)
    assert after_round.rounds == 3
    assert after_round.effective_target == 6


def simulate(version: int, actions: tuple[str, ...] = ("build", "raid", "bank")) -> float:
    wins = 0
    for seed in range(2000):
        rng = random.Random(seed)
        rules = {**V3, "tug_rules_version": version}
        history = []
        result = tally(history, rules, PLAYERS)
        # A generous deterministic acceptance bound; banking forever intentionally draws.
        for index in range(1, 501):
            puller = result.next_team
            action = rng.choice(actions)
            territory = (
                puller
                if action == "build"
                else (TEAM_B if puller == TEAM_A else TEAM_A)
                if action == "raid"
                else None
            )
            year = 1950 if territory == TEAM_A else 2010 if territory == TEAM_B else 1990
            history.append(step(index, puller, year))
            result = tally(history, rules, PLAYERS)
            winning = winner(result)
            if winning:
                wins += winning == TEAM_A
                break
        else:
            pytest.fail(f"Version {version}, seed {seed} did not terminate within 500 pulls")
    return wins / 2000


def test_v3_mirror_strategy_is_fair():
    rate = simulate(3)
    assert 0.45 <= rate <= 0.55, rate


def test_v3_bank_heavy_mirror_strategy_is_fair():
    rate = simulate(3, BANK_HEAVY)
    assert 0.45 <= rate <= 0.55, rate


@pytest.mark.xfail(strict=True, reason="V2 immediate victory and turn-parity Sudden Death bias")
def test_v2_mirror_strategy_documents_bias():
    rate = simulate(2, BANK_HEAVY)
    assert 0.45 <= rate <= 0.55, rate


def test_explicit_dispatch_keeps_historical_v1_v2_totals():
    steps = [step(1, TEAM_B, 2010), step(2, TEAM_A, 2010)]
    for version in (None, 1):
        assert compute_scores(steps, {"tug_rules_version": version}) == {TEAM_A: 0, TEAM_B: 2}
    assert compute_scores(steps, {**RULES, "tug_players": PLAYERS}) == {TEAM_A: 1, TEAM_B: 0}
    assert compute_scores(steps, {**V3, "tug_players": PLAYERS}) == {TEAM_A: 2, TEAM_B: 1}
    with pytest.raises(ValueError, match="Unsupported Tug rules version"):
        compute_scores(steps, {"tug_rules_version": 99})


def test_v3_banks_stay_zero_even_in_sudden_death_and_disabled_raids_bank():
    rules = {**V3, "sudden_death_after": 4, "steal_enabled": False}
    steps = [step(i, TEAM_A if i % 2 else TEAM_B, 1990) for i in range(1, 30)]
    before = tally(steps, rules, PLAYERS)
    assert before.sudden_death and before.rope == 0 and winner(before) is None
    assert preview_pull(TEAM_A, TEAM_B, before, rules) == ("neutral", 0)
    result = tally([*steps, step(30, TEAM_B, 1950)], rules, PLAYERS)
    assert result.pulls[-1].kind == "neutral" and result.pulls[-1].points == 0
    assert result.banks[TEAM_B]


def test_lookahead_is_cached_deduplicated_and_excludes_watched(client, db_engine):
    with respx.mock:
        mock_films()
        run_id = tug(client)["id"]
    with Session(db_engine) as session:
        session.add(CachedActor(tmdb_id=42, name="Cached", credits_fetched_at=utcnow()))
        session.add(
            CachedMovie(
                tmdb_id=101, title="Choice", release_date="1960-01-01", cast_fetched_at=utcnow()
            )
        )
        session.add(CachedMovie(tmdb_id=102, title="Score", release_date="2010-01-01"))
        session.add(CachedMovie(tmdb_id=103, title="Bank", release_date="1990-01-01"))
        session.commit()
        for movie_id in (101, 102, 103):
            session.add(CachedMovieCast(movie_id=movie_id, actor_id=42, cast_order=0))
        session.commit()
    with respx.mock(assert_all_mocked=True):
        result = client.get(f"/api/runs/{run_id}/tug/lookahead?movie_ids=101,101,404")
    assert result.status_code == 200
    assert result.json() == {
        "movies": {
            "101": {"scoring": 1, "neutral": 1, "partial": False},
            "404": {"scoring": 0, "neutral": 0, "partial": True},
        },
        "partial": True,
    }
    assert client.get(f"/api/runs/{run_id}/tug/lookahead?movie_ids=0").status_code == 422
    assert (
        client.get(f"/api/runs/{run_id}/tug/lookahead?movie_ids={','.join(['1'] * 11)}").status_code
        == 422
    )
    assert client.get("/api/runs/missing/tug/lookahead?movie_ids=1").status_code == 404


def test_lookahead_slow_cache_loader_returns_partial_within_budget(client, monkeypatch):
    with respx.mock:
        mock_films()
        run_id = tug(client)["id"]

    def slow_cast(self, movie_id, limit):
        time.sleep(2)
        return []

    monkeypatch.setattr(CacheRepo, "get_cached_cast", slow_cast)
    started = time.monotonic()
    response = client.get(f"/api/runs/{run_id}/tug/lookahead?movie_ids=101,102")
    assert time.monotonic() - started < 1.8
    assert response.status_code == 200
    assert response.json()["partial"] is True
    assert all(value["partial"] for value in response.json()["movies"].values())


@pytest.mark.parametrize("mark_route", ["mark-watched", ""])
def test_queued_v3_turn_folds_at_watch_time_not_queue_time(client, bob, mark_route):
    with respx.mock:
        mock_films()
        run_id = tug(client, partner=bob, target_lead=20)["id"]
        queued = log(client, run_id, 7, tug_team=TEAM_B, status="planned").json()
        path = f"/api/runs/{run_id}/steps/{queued['id']}" + (f"/{mark_route}" if mark_route else "")
        payload = {} if mark_route else {"watched_at": utcnow().isoformat()}
        assert client.patch(path, json=payload).status_code == 409
        assert log(client, run_id, 2).status_code == 201
        assert client.patch(path, json=payload).status_code == 200
        state = detail(client, run_id)["rules_config"]["tug_momentum"]
    assert [pull["puller"] for pull in state["pulls"]] == [TEAM_A, TEAM_B]
    assert state["rounds"] == 1 and state["next_team"] == TEAM_A


def test_persisted_v2_run_keeps_raid_clamp_and_immediate_completion(client, bob, db_engine):
    with respx.mock:
        mock_films()
        run_id = tug(client, partner=bob, target_lead=2)["id"]
        with Session(db_engine) as session:
            run = session.get(Run, run_id)
            run.rules_config = {**run.rules_config, "tug_rules_version": 2}
            session.add(run)
            session.commit()
        assert log(client, run_id, 7).status_code == 201  # Raid at zero = +1, not v3's +2.
        assert log(bob, run_id, 12).status_code == 201
        assert log(client, run_id, 2).status_code == 201
        run = detail(client, run_id)
    assert run["status"] == "completed"
    assert run["rules_config"]["tug_scores"] == {TEAM_A: 2, TEAM_B: 0}
    assert "rope" not in run["rules_config"]["tug_momentum"]


def test_v3_seed_planned_and_client_state_do_not_score(client):
    with respx.mock:
        mock_films()
        run_id = tug(
            client,
            tug_momentum={
                "rope": 999,
                "streaks": {TEAM_A: 99, TEAM_B: 99},
                "banks": {TEAM_A: True, TEAM_B: True},
                "rounds": 999,
            },
        )["id"]
        assert log(client, run_id, 2, status="planned").status_code == 201
        state = detail(client, run_id)["rules_config"]["tug_momentum"]
    assert state["rope"] == 0 and state["rounds"] == 0
    assert state["streaks"] == {TEAM_A: 0, TEAM_B: 0}
    assert state["banks"] == {TEAM_A: False, TEAM_B: False}


# --- Phase F0: one target_lead default, legacy targets untouched ---

LEGACY_TARGET = 4
THREE_A_BUILDS = [
    step(1, TEAM_A, 1950),
    step(2, TEAM_B, 1990),
    step(3, TEAM_A, 1950),
    step(4, TEAM_B, 1990),
    step(5, TEAM_A, 1950),
    step(6, TEAM_B, 1990),
]


def test_the_default_target_lead_is_seven_everywhere():
    assert DEFAULT_TARGET_LEAD == 7
    assert tug_config({TUG_RULES_VERSION_KEY: 3})["target_lead"] == DEFAULT_TARGET_LEAD
    field = next(f for f in TugOfWarEngine.rule_fields if f.key == "target_lead")
    assert field.default == DEFAULT_TARGET_LEAD
    bare = {k: v for k, v in V3.items() if k != "target_lead"}
    assert tally([], bare, PLAYERS).effective_target == DEFAULT_TARGET_LEAD


def test_a_run_that_stored_the_old_target_still_wins_at_that_target():
    legacy = tally(THREE_A_BUILDS, {**V3, "target_lead": LEGACY_TARGET}, PLAYERS)
    assert legacy.rope == 6 and legacy.effective_target == LEGACY_TARGET
    assert winner(legacy) == TEAM_A

    current = tally(THREE_A_BUILDS, V3, PLAYERS)
    assert current.rope == 6 and current.effective_target == DEFAULT_TARGET_LEAD
    assert winner(current) is None
