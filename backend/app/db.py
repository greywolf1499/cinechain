from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine

from app.config import get_settings

settings = get_settings()

# Small, fixed pool: this is a single-worker monolith, not a multi-tenant API server.
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
    pool_size=5,
    max_overflow=5,
)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    if not settings.database_url.startswith("sqlite"):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA cache_size=-8000")
    cursor.execute("PRAGMA temp_store=MEMORY")
    cursor.close()


def get_session():
    with Session(engine) as session:
        yield session
