"""Reserve daily provider quota before HTTP, across threads and process restarts."""

from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.models.system import ProviderBudget
from app.utils.ids import utcnow

LIMITS = {"omdb": 900}


class BudgetExhausted(Exception):
    pass


def reserve(engine: Engine, provider: str, *, limit: int | None = None) -> bool:
    ceiling = LIMITS[provider] if limit is None else limit
    day = utcnow().date().isoformat()
    table = ProviderBudget.__table__
    statement = insert(table).values(provider=provider, day=day, used=1)
    statement = statement.on_conflict_do_update(
        index_elements=[table.c.provider, table.c.day],
        set_={"used": table.c.used + 1},
        where=table.c.used < ceiling,
    )
    if ceiling <= 0:
        return False
    with Session(engine) as session:
        result = session.execute(statement)
        session.commit()
        return result.rowcount == 1
