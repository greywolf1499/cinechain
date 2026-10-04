from pydantic import BaseModel


class MovieSummary(BaseModel):
    tmdb_id: int
    title: str
    poster_path: str | None = None
    release_year: int | None = None
    origin_country: str | None = None


class SeedSuggestionOut(MovieSummary):
    reason: str  # why it was picked, e.g. "On the SS22 list", "IMDb 8.4"


class MovieRatings(BaseModel):
    imdb_rating: str | None = None
    rotten_tomatoes: str | None = None
    metacritic: str | None = None


class MovieDetail(MovieSummary):
    overview: str | None = None
    tagline: str | None = None
    runtime: int | None = None
    original_language: str | None = None
    genre_ids: list[int] = []
    ratings: MovieRatings | None = None


class CastMember(BaseModel):
    actor_id: int
    name: str
    profile_path: str | None = None
    character_name: str | None = None
    cast_order: int | None = None


class CrewMember(BaseModel):
    """A key craft credit (Crew & Craft Trail): composer, cinematographer, writer or director."""

    person_id: int
    name: str
    job: str
    department: str
    role: str  # composer | cinematographer | writer | director
    profile_path: str | None = None


class MovieSearchResponse(BaseModel):
    results: list[MovieSummary]
    page: int
    total_pages: int
