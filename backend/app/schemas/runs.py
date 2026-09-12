from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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


class RunUpdate(BaseModel):
    name: str | None = None
    status: str | None = None  # active | completed | abandoned


class RunStepCreate(BaseModel):
    movie_id: int
    transition_metadata: dict[str, Any] | None = None
    user_notes: str | None = None
    force: bool = False


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
    logged_by_user_id: str | None
    logged_at: datetime


class RunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    game_type: str
    status: str
    created_at: datetime
    completed_at: datetime | None


class RunDetail(RunSummary):
    steps: list[RunStepPublic]
    participants: list[ParticipantPublic]
