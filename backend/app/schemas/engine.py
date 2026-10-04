from typing import Any

from pydantic import BaseModel


class SharedActorConnection(BaseModel):
    """A link between two films. Despite the name it also carries a *director*
    link (`kind="director"`, with `actor_id`/`actor_name` holding the director's
    id/name) so bridge paths and validation can describe both with one shape."""

    kind: str = "actor"  # actor | director
    actor_id: int
    actor_name: str
    profile_path: str | None = None
    character_in_from: str | None = None
    character_in_to: str | None = None


class ValidationResult(BaseModel):
    valid: bool
    reason: str | None = None
    connections: list[SharedActorConnection] = []
    # A hard block (e.g. "not in this run's canon list") can't be bought back
    # with a wildcard, unlike a plain "no shared cast" miss.
    blocked: bool = False
    # Auteur Relay: which kind of link this hop is/must be ("actor" | "director").
    connection_type: str | None = None
    # Algorithm Sandbox modes: how close the two films measured (None = unknown).
    similarity: float | None = None  # Semantic Trope Web, cosine similarity (-1..1)
    color_distance: float | None = None  # Aesthetic Gradient, RGB Euclidean distance
    # Rule evidence for this hop (Chrono year delta, Passport countries), stored on the step.
    mechanic: dict[str, Any] | None = None


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


class BridgeNode(BaseModel):
    movie_id: int
    title: str
    poster_path: str | None = None
    release_year: int | None = None
    popularity: float | None = None
    runtime: int | None = None  # None = detail not fetched yet
    origin_countries: list[str] = []


class PathTag(BaseModel):
    """A dynamic highlight chip derived from analysing a whole path."""

    key: str  # canon_heavy | multi_country | epic_runtimes
    label: str
    emoji: str
    detail: str


class BridgeResult(BaseModel):
    path: list[BridgeNode]
    hops: int
    connections: list[SharedActorConnection]
    tags: list[PathTag] = []


class SwapCandidate(BaseModel):
    """A movie that can replace a path node while keeping both its actor links."""

    node: BridgeNode
    connection_in: SharedActorConnection
    connection_out: SharedActorConnection


class SwapNodeResult(BaseModel):
    candidates: list[SwapCandidate]
    total: int  # candidates found before the response cap


class ConstraintInfo(BaseModel):
    """The rule currently shaping a run's *next* hop (e.g. "must be a Director")."""

    kind: str  # director | actor | free | year | country | color | semantic
    title: str
    detail: str | None = None


class RouletteMovie(BaseModel):
    tmdb_id: int
    title: str
    poster_path: str | None = None
    release_year: int | None = None
    origin_country: str | None = None
    runtime: int | None = None
    overview: str | None = None
    tagline: str | None = None
    genre_ids: list[int] = []
    imdb_rating: str | None = None


class RouletteSpinResult(BaseModel):
    movie: RouletteMovie
    pool_size: int  # how many cached films matched the filters


class PathTagsResult(BaseModel):
    tags: list[PathTag]
    nodes: list[BridgeNode]


class RunStats(BaseModel):
    total_hops: int
    countries: list[str]
    decades: list[int]
    keystone_actors: list[KeystoneActor]
