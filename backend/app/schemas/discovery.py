from pydantic import BaseModel


class DiscoveryConnection(BaseModel):
    actor_id: int
    actor_name: str
    profile_path: str | None = None
    character_in_frontier: str | None = None
    character_in_candidate: str | None = None


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
