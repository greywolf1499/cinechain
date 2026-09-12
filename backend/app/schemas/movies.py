from pydantic import BaseModel


class MovieSummary(BaseModel):
    tmdb_id: int
    title: str
    poster_path: str | None = None
    release_year: int | None = None
    origin_country: str | None = None


class MovieDetail(MovieSummary):
    overview: str | None = None
    runtime: int | None = None
    original_language: str | None = None
    genre_ids: list[int] = []


class CastMember(BaseModel):
    actor_id: int
    name: str
    profile_path: str | None = None
    character_name: str | None = None
    cast_order: int | None = None


class MovieSearchResponse(BaseModel):
    results: list[MovieSummary]
    page: int
    total_pages: int
