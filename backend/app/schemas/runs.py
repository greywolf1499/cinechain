from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.run import DEFAULT_RULES_CONFIG


class ParticipantAdd(BaseModel):
    user_id: str
    role: str = "member"  # owner | member


class ParticipantPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    role: str
    joined_at: datetime


class RunCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    game_type: str = "cinechain"
    participant_user_ids: list[str] = Field(default_factory=list)
    seed_movie_id: int | None = None
    rules_config: dict[str, Any] | None = None


class RunUpdate(BaseModel):
    name: str | None = None
    status: str | None = None  # active | completed | abandoned


class RunStepCreate(BaseModel):
    movie_id: int
    transition_metadata: dict[str, Any] | None = None
    user_notes: str | None = None
    force: bool = False
    status: str = "watched"  # watched | planned
    # ignored (forced null) when status="planned"
    watched_at: datetime | None = None


class MarkWatchedRequest(BaseModel):
    watched_at: datetime | None = None
    user_notes: str | None = None


class RunStepUpdate(BaseModel):
    user_notes: str | None = None
    transition_metadata: dict[str, Any] | None = None


class RunStepPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    movie_id: int
    movie_title: str
    movie_poster_path: str | None
    movie_release_year: int | None
    movie_origin_country: str | None
    transition_metadata: dict[str, Any] | None
    user_notes: str | None
    status: str
    watched_at: datetime | None
    logged_by_user_id: str | None
    logged_at: datetime


class RunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    game_type: str
    status: str
    rules_config: dict[str, Any] = Field(
        default_factory=lambda: dict(DEFAULT_RULES_CONFIG))
    created_at: datetime
    completed_at: datetime | None

    @field_validator("rules_config", mode="before")
    @classmethod
    def _default_rules_config(cls, value: dict[str, Any] | None) -> dict[str, Any]:
        # Rows created before this field existed have rules_config=NULL.
        return value if value else dict(DEFAULT_RULES_CONFIG)


class RunDetail(RunSummary):
    steps: list[RunStepPublic]
    participants: list[ParticipantPublic]
