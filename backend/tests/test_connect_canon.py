from sqlmodel import Session, SQLModel, create_engine

from app.engines.connect_canon import ConnectCanonEngine
from app.models.run import RUN_STATUS_COMPLETED, Run, RunStep
from app.schemas.engine import ValidationResult
from app.services import goal_graph


def _watched(movie_id: int) -> RunStep:
    return RunStep(
        run_id="run",
        movie_id=movie_id,
        movie_title=f"Movie {movie_id}",
        status="watched",
    )


def test_bridge_lock_tracks_active_leg():
    rules = {
        "assist": False,
        "current_leg": 1,
        "legs": [
            {"from": 1, "to": 2},
            {"from": 2, "to": 3},
        ],
    }
    assert ConnectCanonEngine.bridge_locked(rules, 2, 3) is True
    assert ConnectCanonEngine.bridge_locked(rules, 1, 2) is False
    assert ConnectCanonEngine.bridge_locked({**rules, "assist": True}, 2, 3) is False


def test_waypoint_reached_metadata_is_server_generated():
    connect = ConnectCanonEngine(session=None, tmdb=None)
    result = ValidationResult(valid=True, mechanic={"waypoint_reached": True})
    assert connect.link_metadata(result, {"waypoint_reached": False}) == {
        "waypoint_reached": True
    }


def test_sync_state_marks_leg_progress(config_dir):
    engine = create_engine(f"sqlite:///{config_dir}/connect_sync.db", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        connect = ConnectCanonEngine(session, tmdb=None)
        run = Run(
            name="Connect",
            game_type="connect_canon",
            rules_config={
                "legs": [
                    {"from": 10, "to": 20, "reached_at_step": None},
                    {"from": 20, "to": 30, "reached_at_step": None},
                ]
            },
        )
        connect.sync_run_state(run, [_watched(10), _watched(20)])
    assert run.rules_config["current_leg"] == 1
    assert run.rules_config["legs"][0]["reached_at_step"] == 2
    assert run.rules_config["legs"][1]["reached_at_step"] is None
    connect.sync_run_state(run, [_watched(10)])
    assert run.rules_config["current_leg"] == 0
    assert run.rules_config["legs"][0]["reached_at_step"] is None


def test_outcome_completes_after_all_waypoints(config_dir):
    engine = create_engine(
        f"sqlite:///{config_dir}/connect_outcome.db",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        connect = ConnectCanonEngine(session, tmdb=None)
        run = Run(
            name="Connect",
            game_type="connect_canon",
            rules_config={
                "waypoints": [10, 20, 30],
                "current_leg": 2,
                "legs": [
                    {"from": 10, "to": 20, "par": 1},
                    {"from": 20, "to": 30, "par": 1},
                ],
            },
        )
        outcome = connect.evaluate_run_outcome(run, [_watched(10), _watched(20), _watched(30)])
    assert outcome is not None
    assert outcome.status == RUN_STATUS_COMPLETED


def test_par_score_sums_completed_leg_hops_minus_par():
    run = Run(
        name="Connect",
        game_type="connect_canon",
        rules_config={
            "legs": [
                {"from": 10, "to": 20, "par": 2, "reached_at_step": 3},
                {"from": 20, "to": 30, "par": 2, "reached_at_step": 5},
            ]
        },
    )
    assert ConnectCanonEngine.par_score(
        run,
        [_watched(10), _watched(11), _watched(20), _watched(21), _watched(30)],
    ) == 0


def test_prepare_run_computes_disjoint_leg_pars(config_dir, monkeypatch):
    engine = create_engine(
        f"sqlite:///{config_dir}/connect_par.db",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    calls = []

    def search(session, sources, targets, **kwargs):
        calls.append(set(kwargs["excluded_movie_ids"]))
        path = [10, 15, 20] if sources == [10] else [20, 25, 30]
        return goal_graph.GoalGraphResult(2, path, [], 2)

    monkeypatch.setattr(goal_graph, "search", search)
    with Session(engine) as session:
        connect = ConnectCanonEngine(session, tmdb=None)
        rules = __import__("asyncio").run(
            connect.prepare_run({"waypoint_movie_ids": [10, 20, 30]}, "u1")
        )
    assert [leg["par"] for leg in rules["legs"]] == [2, 2]
    assert calls == [set(), {15}]


def test_out_of_order_waypoint_does_not_advance_active_leg(config_dir):
    engine = create_engine(
        f"sqlite:///{config_dir}/connect_order.db",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        connect = ConnectCanonEngine(session, tmdb=None)
        run = Run(
            name="Connect",
            game_type="connect_canon",
            rules_config={
                "legs": [
                    {"from": 10, "to": 20, "reached_at_step": None},
                    {"from": 20, "to": 30, "reached_at_step": None},
                ]
            },
        )
        connect.sync_run_state(run, [_watched(10), _watched(30)])
    assert run.rules_config["current_leg"] == 0
    assert run.rules_config["legs"][1]["reached_at_step"] is None
