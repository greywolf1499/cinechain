"""Reserve daily provider quota before HTTP, across threads and process restarts."""

import logging

from sqlalchemy import and_
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.config import get_settings
from app.models.system import ProviderBudget
from app.services import settings_repo
from app.utils.ids import utcnow

PROVIDERS = ("omdb",)
logger = logging.getLogger(__name__)


class BudgetExhausted(Exception):
    pass


class ProviderLimitReachedError(BudgetExhausted):
    """The upstream API has refused further requests."""


def soft_cap(session: Session, provider: str) -> int:
    if provider != "omdb":
        raise ValueError(f"Unknown metered provider: {provider}")
    value = settings_repo.get_overrides(session).get("omdb_soft_cap")
    return int(value) if value is not None else get_settings().omdb_soft_cap


def paused_reason(session: Session, provider: str) -> str | None:
    row = session.get(ProviderBudget, (provider, utcnow().date().isoformat()))
    if row and row.exhausted:
        return "omdb_limit"
    cap = soft_cap(session, provider)
    if cap > 0 and row and row.used >= cap:
        return "omdb_budget"
    return None


def mark_exhausted(engine: Engine, provider: str) -> None:
    day = utcnow().date().isoformat()
    table = ProviderBudget.__table__
    statement = insert(table).values(provider=provider, day=day, used=0, exhausted=True)
    statement = statement.on_conflict_do_update(
        index_elements=[table.c.provider, table.c.day],
        set_={"exhausted": True},
    )
    with Session(engine) as session:
        session.execute(statement)
        session.commit()
        row = session.get(ProviderBudget, (provider, day))
        logger.warning("%s API daily limit reached after %d reserved calls", provider, row.used)


def reserve(engine: Engine, provider: str, *, limit: int | None = None) -> bool:
    day = utcnow().date().isoformat()
    table = ProviderBudget.__table__
    with Session(engine) as session:
        ceiling = soft_cap(session, provider) if limit is None else limit
        if ceiling < 0:
            raise ValueError("Provider soft cap cannot be negative")
        allowed = table.c.exhausted.is_(False)
        if ceiling > 0:
            allowed = and_(allowed, table.c.used < ceiling)
        statement = insert(table).values(provider=provider, day=day, used=1, exhausted=False)
        statement = statement.on_conflict_do_update(
            index_elements=[table.c.provider, table.c.day],
            set_={"used": table.c.used + 1},
            where=allowed,
        )
        result = session.execute(statement)
        session.commit()
        return result.rowcount == 1
