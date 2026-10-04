from typing import Any, Literal

from pydantic import BaseModel, Field


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
    # Meet in the Middle: this film would also connect the opposite end - the chains collide.
    collision: bool = False


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
    # Engine V3 modifiers: ISO codes currently locked out by `country_cooldown`
    # (most recently visited first) and one-line notes for other active modifiers.
    cooldown_countries: list[str] = []
    modifier_notes: list[str] = []


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
    movie: RouletteMovie  # the first (or only) pick
    pool_size: int  # how many cached films matched the filters
    # Every distinct pick, `movie` first: up to `count` films for a Blind Draft.
    movies: list[RouletteMovie] = []


class PathTagsResult(BaseModel):
    tags: list[PathTag]
    nodes: list[BridgeNode]


class RunStats(BaseModel):
    total_hops: int
    countries: list[str]
    decades: list[int]
    keystone_actors: list[KeystoneActor]


class PitchRequest(BaseModel):
    previous_movie_id: int
    candidate_movie_id: int
    # What links them, if the caller knows (e.g. "Tom Hanks"): grounds the pitch.
    link_label: str | None = Field(default=None, max_length=120)
    # "critic": a witty warning about why the hop might be exhausting (Blind Fork veto advice).
    style: Literal["pitch", "critic"] = "pitch"


class PitchResult(BaseModel):
    pitch: str


class TeaserRequest(BaseModel):
    movie_ids: list[int] = Field(min_length=1, max_length=5)


class TeaserResult(BaseModel):
    # movie id -> spoiler-free teaser; a film whose teaser failed is simply absent.
    teasers: dict[int, str]


class LlmStatus(BaseModel):
    enabled: bool
    provider: str


class TunnelState(BaseModel):
    """Meet in the Middle: where the two ends are and how far apart."""

    head_frontier_movie_id: int | None = None
    tail_frontier_movie_id: int | None = None
    head_steps: int = 0
    tail_steps: int = 0
    collided: bool = False
    # Movie-hops between the two frontiers: 0 = collided, None = none found within the limits.
    distance_hops: int | None = None
    searched_depth: int = 0
    message: str | None = None
