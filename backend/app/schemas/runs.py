from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.run import DEFAULT_RULES_CONFIG

RunStatusLiteral = Literal["active", "completed", "forfeited", "failed"]


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
    status: RunStatusLiteral | None = None


class RunRulesUpdate(BaseModel):
    preset: str = "custom"
    allow_repeats: str = "strict"  # strict | penalty | allowed
    no_consecutive_actor: bool = True
    max_cast_order: int = 15
    min_runtime: int = 40
    wildcards_budget: int = 2  # -1 = unlimited


class RunStepCreate(BaseModel):
    movie_id: int
    transition_metadata: dict[str, Any] | None = None
    user_notes: str | None = None
    force: bool = False
    status: str = "watched"  # watched | planned
    # ignored (forced null) when status="planned"
    watched_at: datetime | None = None


class StepValidateRequest(BaseModel):
    movie_id: int


class MarkWatchedRequest(BaseModel):
    watched_at: datetime | None = None
    user_notes: str | None = None


class RunStepUpdate(BaseModel):
    user_notes: str | None = None
    transition_metadata: dict[str, Any] | None = None
    watched_at: datetime | None = None


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
    status: RunStatusLiteral
    # 1 = legacy run (V2 rules bypassed), 2 = Challenge Engine V2.
    engine_version: int = 1
    status_reason: str | None = None
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
