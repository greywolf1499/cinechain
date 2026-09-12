from datetime import datetime

from sqlmodel import Field, SQLModel

from app.utils.ids import utcnow


class SystemSetting(SQLModel, table=True):
    """Admin-configured overrides (e.g. tmdb_api_key) that take precedence over .env."""

    __tablename__ = "system_settings"

    key: str = Field(primary_key=True)
    value: str
    updated_at: datetime = Field(default_factory=utcnow)
