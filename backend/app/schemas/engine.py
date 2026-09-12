from pydantic import BaseModel


class SharedActorConnection(BaseModel):
    actor_id: int
    actor_name: str
    profile_path: str | None = None
    character_in_from: str | None = None
    character_in_to: str | None = None


class ValidationResult(BaseModel):
    valid: bool
    reason: str | None = None
    connections: list[SharedActorConnection] = []


class SuggestionFilters(BaseModel):
    country: str | None = None
    decade: int | None = None
    genre_id: int | None = None
    on_server: bool | None = None  # reserved for the Phase 7 Jellyfin integration


class Suggestion(BaseModel):
    movie_id: int
    title: str
    poster_path: str | None = None
    release_year: int | None = None
    origin_country: str | None = None
    connecting_actor_id: int
    connecting_actor_name: str


class KeystoneActor(BaseModel):
    actor_id: int
    actor_name: str
    appearances: int


class RunStats(BaseModel):
    total_hops: int
    countries: list[str]
    decades: list[int]
    keystone_actors: list[KeystoneActor]
