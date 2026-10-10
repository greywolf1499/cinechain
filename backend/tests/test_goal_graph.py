from sqlmodel import Session, SQLModel, create_engine

from app.models.cache import CachedActor, CachedCrewCredit, CachedMovie, CachedMovieCast
from app.services import goal_graph


def _seed_movies(session: Session, movie_ids: list[int]) -> None:
    for movie_id in movie_ids:
        session.add(
            CachedMovie(
                tmdb_id=movie_id,
                title=f"Movie {movie_id}",
                release_date="2000-01-01",
                status="Released",
            )
        )


def _seed_actor_links(session: Session, pairs: list[tuple[int, int]]) -> None:
    actor_base = 10_000
    for offset, (from_movie, to_movie) in enumerate(pairs):
        actor_id = actor_base + offset
        session.add(CachedActor(tmdb_id=actor_id, name=f"Actor {actor_id}"))
        session.add(CachedMovieCast(movie_id=from_movie, actor_id=actor_id, cast_order=0))
        session.add(CachedMovieCast(movie_id=to_movie, actor_id=actor_id, cast_order=0))


def test_search_finds_shortest_path_and_connections(config_dir):
    engine = create_engine(f"sqlite:///{config_dir}/goal_graph.db", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        _seed_movies(session, [1, 2, 3, 4, 10, 20])
        _seed_actor_links(session, [(1, 2), (2, 3), (3, 10), (20, 4)])
        session.commit()
        result = goal_graph.search(session, [1, 20], [10], max_depth=5)
    assert result.distance == 3
    assert result.path_movie_ids == [1, 2, 3, 10]
    assert len(result.connections) == 3
    assert all(connection.kind == "actor" for connection in result.connections)


def test_search_respects_exclusions_but_keeps_endpoints(config_dir):
    engine = create_engine(
        f"sqlite:///{config_dir}/goal_graph_excluded.db",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        _seed_movies(session, [1, 2, 3, 10])
        _seed_actor_links(session, [(1, 2), (2, 3), (3, 10)])
        session.commit()
        blocked = goal_graph.search(session, [1], [10], excluded_movie_ids={2}, max_depth=5)
        seeded = goal_graph.search(session, [1], [10], excluded_movie_ids={1, 10}, max_depth=5)
    assert blocked.distance is None
    assert seeded.distance == 3
    assert seeded.path_movie_ids == [1, 2, 3, 10]


def test_distance_to_targets_and_timeout(config_dir):
    engine = create_engine(
        f"sqlite:///{config_dir}/goal_graph_distance.db",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        _seed_movies(session, [1, 2, 3, 4, 9])
        _seed_actor_links(session, [(1, 2), (2, 3), (3, 4)])
        session.commit()
        session.add(
            CachedCrewCredit(
                movie_id=9,
                person_id=99,
                person_name="Crew",
                job="Writer",
                department="Writing",
            )
        )
        session.commit()
        distances = goal_graph.distance_to_targets(session, [1, 2, 3, 4, 9], [4], max_depth=6)
        timed = goal_graph.search(session, [1], [4], max_depth=6, max_seconds=0.0)
    assert distances == {1: 3, 2: 2, 3: 1, 4: 0, 9: None}
    assert timed.distance is None
    assert timed.timed_out is True
