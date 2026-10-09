from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from app.models.run import DEFAULT_RULES_CONFIG
from app.utils.countries import parse_countries

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
    # Meet in the Middle: Partner B's starting film (`seed_movie_id` is Partner A's).
    tail_seed_movie_id: int | None = None
    rules_config: dict[str, Any] | None = None


class RunUpdate(BaseModel):
    name: str | None = None
    status: RunStatusLiteral | None = None


class RunRulesUpdate(BaseModel):
    model_config = ConfigDict(strict=True)

    preset: str = "custom"
    allow_repeats: str = "strict"  # strict | penalty | allowed
    no_consecutive_actor: bool = True
    max_cast_order: int = 15
    min_runtime: int = 40
    wildcards_budget: int = 2  # -1 = unlimited
    # Standalone modes (Chrono, Passport, Aesthetic, Semantic): only sent when set.
    require_cast_link: bool | None = None
    direction: Literal["climb", "descent"] | None = None
    # Engine V3 composable modifiers (null = off / engine default).
    chrono_direction: Literal["climb", "descent"] | None = None
    runtime_staircase: Literal["ascending", "descending"] | None = None
    country_cooldown: int | None = Field(default=None, ge=0, le=20)
    modifiers: list[dict[str, Any]] | None = None
    # Blind Fork workflow switch (offer 3, partner vetoes 1 and picks from the rest).
    blind_fork: bool | None = None
    max_lives: int | None = None
    allow_reroll: bool | None = None
    daily: bool | None = None
    curses: bool | None = None
    fog: Literal["off", "fog", "abyss"] | None = None
    target_lead: int | None = None
    sudden_death_enabled: bool | None = None
    steal_enabled: bool | None = None
    momentum_cap: int | None = None
    sudden_death_after: int | None = None
    sudden_death_every: int | None = None
    track_length: str | None = None
    order: str | None = None
    career_eras: list[dict[str, Any]] | None = None


MODIFIER_UPDATE_KEYS = ("chrono_direction", "runtime_staircase", "country_cooldown")


class RunStepCreate(BaseModel):
    skip_overlays: list[str] = Field(default_factory=list, max_length=2)
    acting_participant_id: str | None = None
    movie_id: int
    transition_metadata: dict[str, Any] | None = None
    # Tug of War: team whose turn is being logged (shared-device support).
    tug_team: Literal["team_a", "team_b"] | None = None
    use_tug_portal: bool = False
    user_notes: str | None = None
    force: bool = False
    status: str = "watched"  # watched | planned
    # ignored (forced null) when status="planned"
    watched_at: datetime | None = None
    # Meet in the Middle: which end of the tunnel this film extends.
    tunnel_side: Literal["head", "tail"] | None = None
    # Rotten Tomatoes Split: the household's joint rating of the film (required there).
    household_score: int | None = Field(default=None, ge=1, le=100)
    no_contest: bool = False


class StepValidateRequest(BaseModel):
    movie_id: int
    tunnel_side: Literal["head", "tail"] | None = None


class ForkOffer(BaseModel):
    """Blind Fork step 1: the three films offered to the partner."""

    acting_participant_id: str | None = None
    movie_ids: list[int] = Field(min_length=3, max_length=3)
    # Optional per-film link metadata (actor, characters) from the Pick Next card, so the film
    # is logged with the same connection a direct pick would have had. Keyed by movie id.
    links: dict[int, dict[str, Any]] = Field(default_factory=dict)

    @field_validator("movie_ids")
    @classmethod
    def _distinct(cls, value: list[int]) -> list[int]:
        if len(set(value)) != len(value):
            raise ValueError("Offer three different films")
        return value


class ForkVeto(BaseModel):
    acting_participant_id: str | None = None
    movie_id: int


class ForkAccept(BaseModel):
    acting_participant_id: str | None = None
    movie_id: int
    user_notes: str | None = None
    status: Literal["watched", "planned"] = "watched"


class GoldenVeto(BaseModel):
    acting_participant_id: str | None = None
    # fork = tear up the partner's pending offer; step = remove the partner's latest step.
    target: Literal["fork", "step"] = "fork"


class GoldenVetoResult(BaseModel):
    target: Literal["fork", "step"]
    veto_tokens: int
    run: "RunDetail"


class MarkWatchedRequest(BaseModel):
    acting_participant_id: str | None = None
    watched_at: datetime | None = None
    user_notes: str | None = None
    household_score: int | None = Field(default=None, ge=1, le=100)
    no_contest: bool = False


class RunStepUpdate(BaseModel):
    acting_participant_id: str | None = None
    user_notes: str | None = None
    transition_metadata: dict[str, Any] | None = None
    watched_at: datetime | None = None
    household_score: int | None = Field(default=None, ge=1, le=100)
    no_contest: bool = False


class RunStepPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    movie_id: int
    movie_title: str
    movie_poster_path: str | None
    movie_release_year: int | None
    movie_origin_country: str | None
    # Poster colour from the movie cache (Aesthetic Gradient swatch); filled in by the routes.
    movie_dominant_color: str | None = None
    # Historical Time-Travel: when the film is set (negative = BCE) and its era label, from the cache.
    movie_narrative_year: int | None = None
    movie_narrative_era_label: str | None = None
    transition_metadata: dict[str, Any] | None
    user_notes: str | None
    status: str
    watched_at: datetime | None
    logged_by_user_id: str | None
    logged_at: datetime

    @computed_field
    @property
    def movie_origin_countries(self) -> list[str]:
        return parse_countries(self.movie_origin_country)


class RunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    game_type: str
    status: RunStatusLiteral
    # 1 = legacy run (V2 rules bypassed), 2 = Challenge Engine V2.
    engine_version: int = 1
    status_reason: str | None = None
    rules_config: dict[str, Any] = Field(default_factory=lambda: dict(DEFAULT_RULES_CONFIG))
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


GoldenVetoResult.model_rebuild()
