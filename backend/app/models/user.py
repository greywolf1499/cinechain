from datetime import datetime

from sqlmodel import Field, SQLModel

from app.utils.ids import new_id, utcnow


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: str = Field(default_factory=new_id, primary_key=True)
    username: str = Field(unique=True, index=True)
    display_name: str
    password_hash: str
    is_admin: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow)
    # Golden Veto: one token per 30 days, refilled lazily by `refresh_veto_tokens` on the
    # first authenticated request after the window lapses (there is no background job).
    veto_tokens: int = Field(default=1)
    last_veto_reset_at: datetime = Field(default_factory=utcnow)
