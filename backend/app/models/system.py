from datetime import datetime
from typing import Any

from sqlmodel import JSON, Column, Field, SQLModel

from app.utils.ids import new_id, utcnow


class ProviderBudget(SQLModel, table=True):
    __tablename__ = "provider_budgets"

    provider: str = Field(primary_key=True)
    day: str = Field(primary_key=True)
    used: int = 0


class SystemSetting(SQLModel, table=True):
    """Admin-configured overrides (e.g. tmdb_api_key) that take precedence over .env."""

    __tablename__ = "system_settings"

    key: str = Field(primary_key=True)
    value: str
    updated_at: datetime = Field(default_factory=utcnow)


class SystemTask(SQLModel, table=True):
    """A heavy job running inside the API process (FastAPI BackgroundTasks, no broker).

    The row is the single source of truth for progress, so the frontend can poll
    or stream it and a restart can tell which jobs were interrupted.
    `progress_data` holds `{"progress": {...latest tick...}, "result": {...}, "error": {...}}`.
    """

    __tablename__ = "system_tasks"

    id: str = Field(default_factory=new_id, primary_key=True)
    name: str = Field(index=True)  # task type, e.g. "watchlist_sync"
    # pending | running | completed | failed
    status: str = Field(default="pending", index=True)
    progress_data: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    error: str | None = None
    user_id: str | None = Field(default=None, foreign_key="users.id", index=True)
    # Only one active task per key (stops double-clicks from scraping twice).
    dedupe_key: str | None = Field(default=None, index=True)
    cancel_requested: bool = False
    link: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow, index=True)
