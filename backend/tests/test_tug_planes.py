"""Tug plane classification, balancing, draft and stamped-fold regressions."""

import random
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import SQLModel, create_engine

from app.api.routes_runs import _cached_tug_link_state, _tug_portal_available
from app.engines.traversal import get_policy
from app.engines.tug_of_war import (
    PLAYERS_KEY,
    TUG_RULES_VERSION_KEY,
    TugOfWarEngine,
    tally,
    winner,
)
from app.engines.tug_planes import (
    NEUTRAL,
    TEAM_A,
    TEAM_B,
    assess_facts,
    build_plane,
    catalogue,
    check_balance,
    deal,
    graph_density,
)
from app.models.cache import CachedActor, CachedMovie, CachedMovieCast, CachedMovieDirector
from app.models.run import Run, RunStep
from app.services.blind_fork import SERVER_OWNED_RULES, strip_server_rules


@pytest.fixture()
def db_engine(config_dir):
    engine = create_engine(
        f"sqlite:///{config_dir}/tug_planes.db",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _step(index: int, team: str, territory: str) -> RunStep:
    return RunStep(
        id=f"v4-{index:03}",
        run_id="run",
        movie_id=index,
        movie_title=f"Film {index}",
        logged_by_user_id="alice",
        status="watched",
        logged_at=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(minutes=index),
        transition_metadata={
            "tug_team": team,
            "tug_territory": territory,
            "tug_territory_evidence": {"team_a": True, "team_b": False},
        },
    )


def test_plane_catalogue_and_territory_unknown_neutral_and_contested():
    rows = catalogue()
    assert {item["id"] for item in rows} == {
        "bipolar_decades",
        "country_pair",
        "language_pair",
        "genre_clusters",
        "runtime_poles",
        "setting_eras",
        "critic_audience",
    }
    plane = build_plane("bipolar_decades", {"team_a_before": 1970, "team_b_from": 2000})
    assert plane.territory({"release_year": 1969}) == TEAM_A
    assert plane.territory({"release_year": 2000}) == TEAM_B
    assert plane.territory({"release_year": 1980}) == NEUTRAL
    assert plane.unknown({}) is True


def test_balance_accepts_boundary_thresholds_and_rejects_underrepresented_poles():
    plane = build_plane("bipolar_decades")
    boundary = (
        [{"release_year": 1960}] * 5 + [{"release_year": 2010}] * 5 + [{"release_year": 1990}] * 10
    )
    result = assess_facts(plane, boundary)
    assert result["balanced"] is True
    assert result["team_a_rate"] == 0.25
    assert result["team_b_rate"] == 0.25
    assert result["neutral_rate"] == 0.5

    imbalanced = assess_facts(
        plane,
        [{"release_year": 2010}] * 19 + [{"release_year": 1990}],
    )
    assert imbalanced["balanced"] is False
    assert any("5%" in issue for issue in imbalanced["issues"])


def test_graph_policy_downgrades_to_first_allowed_non_graph_on_low_density(db_engine):
    from sqlmodel import Session

    with Session(db_engine) as session:
        plane = build_plane("bipolar_decades")
        result = check_balance(
            session,
            plane,
            {},
            bridge_density=0.019,
            traversal="shared_cast",
        )
    assert result["traversal"] == "genre_overlap"
    assert "below 2%" in result["explanation"]
    assert get_policy(result["traversal"]).graph is False
    assert get_policy("shared_cast").graph is True


def test_graph_density_uses_unique_pairs_and_selected_people_policy(db_engine):
    from sqlmodel import Session

    with Session(db_engine) as session:
        for movie_id in (1, 2, 3):
            session.add(CachedMovie(tmdb_id=movie_id, title=f"Film {movie_id}"))
        for actor_id in (10, 11, 12):
            session.add(CachedActor(tmdb_id=actor_id, name=f"Actor {actor_id}"))
        session.add_all(
            [
                CachedMovieCast(movie_id=1, actor_id=10),
                CachedMovieCast(movie_id=2, actor_id=10),
                CachedMovieCast(movie_id=2, actor_id=11),
                CachedMovieCast(movie_id=3, actor_id=11),
                CachedMovieCast(movie_id=1, actor_id=12),
                CachedMovieCast(movie_id=2, actor_id=12),
                CachedMovieDirector(movie_id=1, person_id=20, name="Director"),
                CachedMovieDirector(movie_id=2, person_id=20, name="Director"),
            ]
        )
        session.commit()
        ids = [1, 2, 3]
        assert graph_density(session, ids, traversal="shared_cast") == pytest.approx(2 / 3)
        assert graph_density(session, ids, traversal="shared_director") == pytest.approx(1 / 3)
        assert graph_density(session, ids, traversal="shared_any_person") == pytest.approx(2 / 3)


def test_portal_link_check_requires_cached_proof_of_no_shared_people(db_engine):
    from sqlmodel import Session

    now = datetime.now(UTC)
    with Session(db_engine) as session:
        session.add_all(
            [
                CachedMovie(
                    tmdb_id=1,
                    title="Frontier",
                    cast_fetched_at=now,
                    directors_fetched_at=now,
                    crew_fetched_at=now,
                ),
                CachedMovie(
                    tmdb_id=2,
                    title="Linked",
                    cast_fetched_at=now,
                    directors_fetched_at=now,
                    crew_fetched_at=now,
                ),
                CachedMovie(
                    tmdb_id=3,
                    title="Unlinked",
                    cast_fetched_at=now,
                    directors_fetched_at=now,
                    crew_fetched_at=now,
                ),
                CachedMovie(tmdb_id=4, title="Unknown"),
                CachedActor(tmdb_id=10, name="Actor"),
                CachedMovieCast(movie_id=1, actor_id=10),
                CachedMovieCast(movie_id=2, actor_id=10),
            ]
        )
        session.commit()
        assert _cached_tug_link_state(session, 1, 2, "shared_cast") is True
        assert _cached_tug_link_state(session, 1, 3, "shared_cast") is False
        assert _cached_tug_link_state(session, 1, 4, "shared_cast") is None


def test_draft_deals_have_distinct_balanced_entries_and_seeded_determinism():
    pools = {
        TEAM_A: [1, 2, 3, 4],
        TEAM_B: [5, 6, 7, 8],
        NEUTRAL: [9, 10, 11],
    }
    first = deal(1234, pools)
    assert first == deal(1234, pools)
    assert len({entry["movie_id"] for entry in first}) == len(first)
    assert {entry["territory"] for entry in first} == {TEAM_A, TEAM_B, NEUTRAL}
    assert deal(1235, pools) != first


def _mirror_win_rate(seed_offset: int, territory_pool: tuple[str, ...]) -> float:
    wins = 0
    rules = {
        TUG_RULES_VERSION_KEY: 4,
        "target_lead": 7,
        "sudden_death_enabled": True,
        "sudden_death_after": 12,
        "sudden_death_every": 2,
        "steal_enabled": True,
        "momentum_cap": 3,
        PLAYERS_KEY: {TEAM_A: "alice", TEAM_B: "bob"},
    }
    for game in range(2000):
        rng = random.Random(seed_offset + game)
        steps = []
        winning_team = None
        for index in range(1, 1001):
            team = TEAM_A if index % 2 else TEAM_B
            steps.append(_step(index, team, rng.choice(territory_pool)))
            if index % 2 == 0:
                winning_team = winner(tally(steps, rules))
                if winning_team is not None:
                    break
        assert winning_team is not None
        wins += winning_team == TEAM_A
    return wins / 2000


def test_stamped_mirror_play_is_fair_for_genre_and_draft_planes():
    genre_rate = _mirror_win_rate(7000, (TEAM_A, TEAM_B, NEUTRAL))
    draft_rate = _mirror_win_rate(9000, (TEAM_A, TEAM_A, TEAM_B, TEAM_B, NEUTRAL))
    assert 0.45 <= genre_rate <= 0.55, genre_rate
    assert 0.45 <= draft_rate <= 0.55, draft_rate


def test_version_four_fold_uses_stamped_territory_and_legacy_versions_stay_legacy():
    steps = [_step(1, TEAM_A, TEAM_A), _step(2, TEAM_B, TEAM_B)]
    v4 = tally(
        steps,
        {
            TUG_RULES_VERSION_KEY: 4,
            "target_lead": 20,
            "steal_enabled": True,
            PLAYERS_KEY: {TEAM_A: "alice", TEAM_B: "bob"},
        },
    )
    assert [pull.territory for pull in v4.pulls] == [TEAM_A, TEAM_B]
    assert tally(steps, {TUG_RULES_VERSION_KEY: 3, "target_lead": 20}).pulls[0].territory is None


def test_legacy_runs_receive_a_read_only_plane_snapshot():
    run = Run(
        id="legacy",
        name="Legacy",
        game_type="tug_of_war",
        rules_config={"dimension": "geography", TUG_RULES_VERSION_KEY: 3},
    )
    public = TugOfWarEngine.public_rules(run.rules_config, run)
    assert public["tug_plane_snapshot"]["id"] == "geo_west_rest"
    assert public["tug_plane_snapshot"]["poles"][TEAM_A]["label"] == "Western"


def test_portal_precheck_rejects_finished_runs_spent_charges_and_non_graph():
    run = Run(
        id="run",
        name="Tug",
        game_type="tug_of_war",
        status="completed",
        rules_config={},
    )
    assert not _tug_portal_available(None, run, {TUG_RULES_VERSION_KEY: 4})  # type: ignore[arg-type]
    run.status = "active"
    assert not _tug_portal_available(
        None,
        run,
        {TUG_RULES_VERSION_KEY: 4, "tug_traversal": "shared_cast", "tug_portals": {"remaining": 0}},
    )  # type: ignore[arg-type]
    assert not _tug_portal_available(
        None,
        run,
        {
            TUG_RULES_VERSION_KEY: 4,
            "tug_traversal": "genre_overlap",
            "tug_portals": {"remaining": 1},
        },
    )  # type: ignore[arg-type]


def test_server_owned_plane_state_cannot_be_forged():
    forged = {
        "tug_plane_snapshot": {"id": "invented"},
        "tug_seed": 1,
        "tug_deal": [{"movie_id": 123, "territory": TEAM_A}],
        "tug_portals": {"remaining": 999},
        "tug_plane": {"id": "genre_clusters", "params": {}},
        "tug_traversal": "draft",
    }
    cleaned = strip_server_rules(forged)
    assert all(key not in cleaned for key in SERVER_OWNED_RULES if key.startswith("tug_"))
    assert cleaned["tug_plane"] == forged["tug_plane"]
    assert cleaned["tug_traversal"] == "draft"
