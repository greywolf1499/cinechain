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
