import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.engines.base import RunSetupError
from app.engines.canon_infiltration import CanonInfiltrationEngine
from app.models.cache import CachedMovie
from app.models.curated import CanonMovieBadge, CuratedList
from app.models.run import RUN_STATUS_COMPLETED, RUN_STATUS_FAILED, Run, RunStep
from app.schemas.discovery import DiscoveryCandidate
from app.services import goal_graph


def _watched(movie_id: int) -> RunStep:
    return RunStep(
        run_id="run",
        movie_id=movie_id,
        movie_title=f"Movie {movie_id}",
        status="watched",
    )


def test_prepare_run_validates_seed_distance_band(config_dir, monkeypatch):
    engine = create_engine(
        f"sqlite:///{config_dir}/infiltration_prepare.db",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(CuratedList(id="canon-list", title="Canon", url="https://x", badge_prefix="C"))
        session.commit()
        session.add(CanonMovieBadge(curated_list_id="canon-list", movie_id=10, badge_label="C"))
        session.commit()
        infiltration = CanonInfiltrationEngine(session, tmdb=None)
        infiltration.setup_seed_movie_id = 1
        rules = {"target_list_id": "canon-list", "hop_limit": 4}

        monkeypatch.setattr(
            goal_graph,
            "search",
            lambda *args, **kwargs: goal_graph.GoalGraphResult(1, [1, 10], [], 1),
        )
        with pytest.raises(RunSetupError, match="between 2 and 4"):
            import asyncio

            asyncio.run(infiltration.prepare_run(rules, "u1"))

        monkeypatch.setattr(
            goal_graph,
            "search",
            lambda *args, **kwargs: goal_graph.GoalGraphResult(3, [1, 2, 3, 10], [], 3),
        )
        import asyncio

        prepared = asyncio.run(infiltration.prepare_run(rules, "u1"))
    assert prepared["infiltration_par"] == 3


def test_prepare_run_requires_a_seed(config_dir):
    engine = create_engine(
        f"sqlite:///{config_dir}/infiltration_seed.db",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        infiltration = CanonInfiltrationEngine(session, tmdb=None)
        with pytest.raises(RunSetupError, match="needs a B-movie seed"):
            import asyncio

            asyncio.run(infiltration.prepare_run({}, "u1"))


def test_seed_candidates_enforce_b_movie_floor(config_dir, monkeypatch):
    engine = create_engine(
        f"sqlite:///{config_dir}/infiltration_seed_candidates.db",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                CachedMovie(tmdb_id=1, title="Low-rated", vote_average=5.5, vote_count=50),
                CachedMovie(tmdb_id=2, title="Too popular", vote_average=5.4, vote_count=49),
                CachedMovie(tmdb_id=3, title="High-rated", vote_average=6.0, vote_count=200),
            ]
        )
        session.commit()
        monkeypatch.setattr(
            "app.engines.canon_infiltration.feasibility.matching_ids",
            lambda session, query, universe: [3],
        )
        infiltration = CanonInfiltrationEngine(session, tmdb=None)
        candidates = __import__("asyncio").run(infiltration.seed_candidates({}))
    assert candidates == [1, 3]


def test_live_distance_fallback_is_bounded_and_accepts_solver_result(config_dir, monkeypatch):
    engine = create_engine(
        f"sqlite:///{config_dir}/infiltration_live_distance.db",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        infiltration = CanonInfiltrationEngine(session, tmdb=object())
        targets = []

        async def solve(seed_id, target_id, **kwargs):
            targets.append(target_id)
            yield {"type": "result", "hops": 3}

        monkeypatch.setattr(infiltration, "solve_bridge", solve)
        distance = __import__("asyncio").run(infiltration._live_distance(1, [10, 11, 12, 13], 4))
    assert distance == 3
    assert len(targets) == 1


def test_discovery_returns_distance_chips_and_ranks_by_target_distance(config_dir, monkeypatch):
    engine = create_engine(
        f"sqlite:///{config_dir}/infiltration_discovery.db",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    candidates = [
        DiscoveryCandidate(movie_id=1, title="Farther"),
        DiscoveryCandidate(movie_id=2, title="Closer"),
    ]

    async def discover(*args, **kwargs):
        return candidates

    monkeypatch.setattr("app.engines.cinechain.CineChainEngine.discover_candidates", discover)
    monkeypatch.setattr(goal_graph, "distance_to_targets", lambda *args, **kwargs: {1: 3, 2: 1})
    with Session(engine) as session:
        infiltration = CanonInfiltrationEngine(session, tmdb=None)
        monkeypatch.setattr(infiltration, "_target_ids", lambda rules: [99])
        result = __import__("asyncio").run(
            infiltration.discover_candidates(9, rules={"target_list_id": "canon"})
        )
    assert [candidate.movie_id for candidate in result] == [2, 1]
    assert [candidate.target_distance for candidate in result] == [1, 3]


def test_fog_hides_target_distance_chips(config_dir, monkeypatch):
    engine = create_engine(
        f"sqlite:///{config_dir}/infiltration_fog.db",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    candidates = [DiscoveryCandidate(movie_id=1, title="Candidate")]

    async def discover(*args, **kwargs):
        return candidates

    monkeypatch.setattr("app.engines.cinechain.CineChainEngine.discover_candidates", discover)
    with Session(engine) as session:
        infiltration = CanonInfiltrationEngine(session, tmdb=None)
        monkeypatch.setattr(infiltration, "_target_ids", lambda rules: [99])
        result = __import__("asyncio").run(
            infiltration.discover_candidates(9, rules={"target_list_id": "canon", "fog": True})
        )
    assert result[0].target_distance is None


def test_outcome_handles_success_and_hop_limit_failure(config_dir):
    engine = create_engine(
        f"sqlite:///{config_dir}/infiltration_outcome.db",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(CuratedList(id="canon-list", title="Canon", url="https://x", badge_prefix="C"))
        session.commit()
        session.add(CanonMovieBadge(curated_list_id="canon-list", movie_id=10, badge_label="C"))
        session.commit()
        infiltration = CanonInfiltrationEngine(session, tmdb=None)
        run = Run(
            name="Infiltration",
            game_type="canon_infiltration",
            rules_config={"target_list_id": "canon-list", "hop_limit": 2},
        )
        success = infiltration.evaluate_run_outcome(run, [_watched(1), _watched(10)])
        failure = infiltration.evaluate_run_outcome(
            run, [_watched(1), _watched(2), _watched(3), _watched(4)]
        )
    assert success is not None and success.status == RUN_STATUS_COMPLETED
    assert failure is not None and failure.status == RUN_STATUS_FAILED
