from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field, model_validator

from app.schemas.discovery import DiscoveryCandidate
from app.utils.countries import parse_countries

FilterSource = Literal[
    "origin_country",
    "release_year",
    "narrative_year",
    "runtime",
    "genre_ids",
    "tug_effect",
    "tier_compliant",
    "new_country",
    "facet",
]

RuleValue = bool | str | int | None


class RuleField(BaseModel):
    key: str
    kind: Literal["int", "bool", "enum", "segmented"]
    label: str
    help: str = ""
    min: int | None = None
    max: int | None = None
    options: list[str] = []
    default: RuleValue = None
    group: Literal["core", "advanced"] = "core"


class Preset(BaseModel):
    id: str
    label: str
    emoji: str = ""
    blurb: str
    values: dict[str, RuleValue]


class FilterSpec(BaseModel):
    key: str
    kind: Literal["select", "toggle", "range"]
    label: str
    source: FilterSource
    default: bool | str | int | None = None
    server_param: Literal["include_off_tier"] | None = None
    help: str = ""
    facet: str | None = None

    @model_validator(mode="after")
    def valid_facet(self):
        from app.facets.registry import CATALOGUE

        if self.source == "facet" and self.facet not in CATALOGUE:
            raise ValueError("Facet filters require a registered facet id")
        if self.source == "facet" and self.facet is not None:
            kind = CATALOGUE[self.facet].kind
            if (self.kind == "range" and kind != "num") or (
                self.kind == "toggle" and kind != "bool"
            ):
                raise ValueError(
                    "Facet ranges require numeric values and toggles require boolean values"
                )
        if self.source != "facet" and self.facet is not None:
            raise ValueError("Only facet filters accept a facet id")
        return self


class SharedActorConnection(BaseModel):
    """A link between two films. Despite the name it also carries a *director*
    link (`kind="director"`, with `actor_id`/`actor_name` holding the director's
    id/name) so bridge paths and validation can describe both with one shape."""

    kind: str = "actor"  # actor | director | craft (Crew & Craft Trail: any cast or crew link)
    # For `kind="craft"` these hold the shared person's id/name, and the roles say what that
    # person was on each film: actor | composer | cinematographer | writer | director.
    actor_id: int
    actor_name: str
    profile_path: str | None = None
    character_in_from: str | None = None
    character_in_to: str | None = None
    role_in_from: str | None = None
    role_in_to: str | None = None


class ValidationResult(BaseModel):
    overlay_skippable: list[str] = []
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
    # Semantic Trope Web: the discrete trope (kebab-case) both films share, when there is one.
    shared_trope: str | None = None
    # Rule evidence for this hop (Chrono year delta, Passport countries), stored on the step.
    mechanic: dict[str, Any] | None = None
    # Meet in the Middle: this film would also connect the opposite end - the chains collide.
    collision: bool = False


class SuggestionFilters(BaseModel):
    country: str | None = None
    decade: int | None = None
    genre_id: int | None = None
    on_server: bool | None = None  # reserved for the Phase 7 Jellyfin integration


class Suggestion(DiscoveryCandidate):
    connecting_actor_id: int | None = None
    connecting_actor_name: str | None = None


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


class RabbitHoleState(BaseModel):
    """Where a Rabbit Hole run stands: the active tier, the lives left, and the tier boundary ahead."""

    depth: int
    tier: int
    tier_name: str
    tier_rule: str  # short label, e.g. "Non-English"
    lives_remaining: int
    max_lives: int
    next_tier: int | None = None
    next_tier_name: str | None = None
    next_tier_rule: str | None = None
    # How many hops until the next tier's rule applies (1 = the very next hop); None on the last tier.
    steps_until_next: int | None = None
    # Set when the next tier is 1 or 2 hops away.
    upcoming_tier_warning: str | None = None
    dead_end: bool = False
    tier_override: int | None = None
    curses: list[dict[str, Any]] = []
    curse_skipped: bool = False
    reroll_tokens: int = 0
    relics: dict[str, int] = {}
    daily: bool = False


class ConstraintInfo(BaseModel):
    """The rule currently shaping a run's *next* hop (e.g. "must be a Director")."""

    kind: str  # director | actor | free | year | country | color | semantic
    title: str
    detail: str | None = None
    # Engine V3 modifiers: ISO codes currently locked out by `country_cooldown`
    # (most recently visited first) and one-line notes for other active modifiers.
    cooldown_countries: list[str] = []
    modifier_notes: list[str] = []
    overlay_progress: list[dict[str, Any]] = []
    rabbit_hole: RabbitHoleState | None = None


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

    @computed_field
    @property
    def origin_countries(self) -> list[str]:
        return parse_countries(self.origin_country)


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
    hints_remaining: int = 0


class TunnelHintRequest(BaseModel):
    side: Literal["head", "tail"]
    level: Literal["actor", "film"]


class TunnelHintActor(BaseModel):
    actor_id: int
    actor_name: str


class TunnelHintFilm(BaseModel):
    movie_id: int
    title: str


class TunnelHintResponse(BaseModel):
    level: Literal["actor", "film"]
    actor: TunnelHintActor | None = None
    film: TunnelHintFilm | None = None
    tokens_remaining: int
