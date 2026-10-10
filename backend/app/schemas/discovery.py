from typing import Literal

from pydantic import BaseModel, computed_field

from app.utils.countries import parse_countries


class DiscoveryDiagnostics(BaseModel):
    engine_pool: int = 0
    after_modifiers: int = 0
    after_filters: int = 0
    widened: bool = False
    reason: str | None = None


class DiscoveryConnection(BaseModel):
    # actor | director (director links carry the director's id/name) | craft (Crew & Craft Trail:
    # `actor_id`/`actor_name` are the shared person, `role_in_*` what they were on each film)
    kind: str = "actor"
    actor_id: int
    actor_name: str
    profile_path: str | None = None
    character_in_frontier: str | None = None
    character_in_candidate: str | None = None
    role_in_frontier: str | None = None
    role_in_candidate: str | None = None


class DiscoveryCandidate(BaseModel):
    facet_values: dict[str, str | int | float | bool | list[str] | list[int] | None] = {}
    overlay_ok: dict[str, bool | None] = {}
    movie_id: int
    title: str
    poster_path: str | None = None
    release_year: int | None = None
    origin_country: str | None = None
    genre_ids: list[int] = []
    popularity: float | None = None
    original_language: str | None = None
    rating: float | None = None
    runtime: int | None = None  # minutes, when the film's detail is cached
    vibe_load: float | None = None
    connections: list[DiscoveryConnection] = []
    already_in_run: bool = False
    # 1-based position in the run's step order, when already_in_run - lets the
    # frontend show "Already in Run (Step X)" instead of a generic badge.
    existing_step_number: int | None = None
    # The run's constraint (e.g. World Passport country) couldn't be checked yet
    # because the film's details haven't been fetched; logging re-checks it.
    constraint_unverified: bool = False
    # Algorithm Sandbox modes: the candidate's poster colour ("#rrggbb") and its
    # semantic (plot) similarity to the frontier film, 0..1.
    dominant_color: str | None = None
    semantic_score: float | None = None
    # Semantic Trope Web: validated AI, TVTropes and manually confirmed trope union.
    tropes: list[str] = []
    trope_sources: dict[str, list[str]] = {}
    trope_urls: dict[str, str] = {}
    # The Rabbit Hole: True = verified to satisfy the active tier's rule (None = couldn't be checked),
    # and the warning when a new tier is 1-2 hops away.
    tier_compliant: bool | None = None
    upcoming_tier_warning: str | None = None
    # Chrono modes: release year minus the frontier film's (negative on a descent).
    year_delta: int | None = None
    # Historical Time-Travel: the year the film is *set* in (negative = BCE), its era label and the
    # leap from the frontier film's setting year (negative on a descent).
    narrative_year: int | None = None
    narrative_era_label: str | None = None
    narrative_delta: int | None = None
    # Tug of War: the next pull's effect and projected net rope movement.
    tug_effect: Literal["home", "invasion", "neutral", "sudden_neutral"] | None = None
    tug_points: int | None = None
    tug_breaks_streak: bool = False
    tug_territory: Literal["team_a", "team_b", "neutral"] | None = None
    tug_territory_evidence: dict[str, bool | None] = {}
    tug_link: dict[str, object] | None = None
    tug_portal_available: bool = False
    grid_cells: list[str] = []
    grid_jump_cells: list[str] = []
    target_distance: int | None = None

    @computed_field
    @property
    def origin_countries(self) -> list[str]:
        return parse_countries(self.origin_country)


class DiscoveryEnvelope(BaseModel):
    candidates: list[DiscoveryCandidate]
    diagnostics: DiscoveryDiagnostics


class TugReachable(BaseModel):
    scoring: int = 0
    neutral: int = 0
    partial: bool = False


class TugLookahead(BaseModel):
    movies: dict[int, TugReachable]
    partial: bool = False
    portal_available: bool | None = None
