from pydantic import BaseModel


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
    movie_id: int
    title: str
    poster_path: str | None = None
    release_year: int | None = None
    origin_country: str | None = None
    genre_ids: list[int] = []
    popularity: float | None = None
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
    # Chrono modes: release year minus the frontier film's (negative on a descent).
    year_delta: int | None = None
