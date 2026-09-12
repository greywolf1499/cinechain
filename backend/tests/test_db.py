from sqlalchemy import text


def test_pragmas_applied_on_connect(config_dir):
    from app.db import engine

    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert conn.exec_driver_sql("PRAGMA synchronous").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000
        assert conn.exec_driver_sql("PRAGMA cache_size").scalar() == -8000
        assert conn.exec_driver_sql("PRAGMA temp_store").scalar() == 2


def test_models_create_all_tables(config_dir):
    from sqlmodel import SQLModel

    from app import models  # noqa: F401
    from app.db import engine

    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                text("select name from sqlite_master where type='table'")
            ).all()
        }
    expected = {
        "users",
        "runs",
        "run_participants",
        "run_steps",
        "cached_movies",
        "cached_actors",
        "cached_movie_cast",
        "cached_genres",
    }
    assert expected.issubset(tables)
