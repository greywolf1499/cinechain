from datetime import datetime
from typing import Any

from sqlmodel import JSON, Column, Field, SQLModel

from app.utils.ids import new_id, utcnow

# Baked into new runs via Run.rules_config's default_factory; also used as a
# fallback wherever a legacy/null rules_config is read.
DEFAULT_RULES_CONFIG: dict[str, Any] = {
    "preset": "standard",
    "allow_repeats": "strict",  # strict | penalty | allowed
    "no_consecutive_actor": True,
    "max_cast_order": 15,
    "min_runtime": 40,
    "wildcards_budget": 2,  # -1 = unlimited
}


class Run(SQLModel, table=True):
    __tablename__ = "runs"

    id: str = Field(default_factory=new_id, primary_key=True)
    name: str
    game_type: str = Field(default="cinechain", index=True)
    # active | completed | abandoned
    status: str = Field(default="active", index=True)
    rules_config: dict[str, Any] = Field(
        default_factory=lambda: dict(DEFAULT_RULES_CONFIG), sa_column=Column(JSON)
    )
    created_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None


class RunParticipant(SQLModel, table=True):
    __tablename__ = "run_participants"

    run_id: str = Field(foreign_key="runs.id", primary_key=True)
    user_id: str = Field(foreign_key="users.id", primary_key=True)
    role: str = Field(default="member")  # owner | member
    joined_at: datetime = Field(default_factory=utcnow)


class RunStep(SQLModel, table=True):
    __tablename__ = "run_steps"

    id: str = Field(default_factory=new_id, primary_key=True)
    run_id: str = Field(foreign_key="runs.id", index=True)
    movie_id: int  # TMDB movie id
    movie_title: str
    movie_poster_path: str | None = None
    movie_release_year: int | None = None
    movie_origin_country: str | None = None
    transition_metadata: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON))
    user_notes: str | None = None
    status: str = Field(default="watched", index=True)  # watched | planned
    watched_at: datetime | None = None  # null while planned
    logged_by_user_id: str | None = Field(default=None, foreign_key="users.id")
    logged_at: datetime = Field(default_factory=utcnow)
