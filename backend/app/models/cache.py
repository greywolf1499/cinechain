from datetime import datetime

from sqlmodel import JSON, Column, Field, SQLModel


class CachedMovie(SQLModel, table=True):
    __tablename__ = "cached_movies"

    tmdb_id: int = Field(primary_key=True)
    title: str = Field(index=True)
    release_date: str | None = None
    poster_path: str | None = None
    overview: str | None = None
    origin_country: str | None = None  # JSON array of ISO country codes
    original_language: str | None = None
    runtime: int | None = None
    genre_ids: list[int] | None = Field(default=None, sa_column=Column(JSON))
    # TMDB's popularity score - display/sort only, never a filter
    popularity: float | None = None
    # Completeness flags: distinguish "no cast" from "cast not yet fetched".
    cast_fetched_at: datetime | None = None


class CachedActor(SQLModel, table=True):
    __tablename__ = "cached_actors"

    tmdb_id: int = Field(primary_key=True)
    name: str
    profile_path: str | None = None
    # Completeness flag: distinguish "no credits" from "credits not yet fetched".
    credits_fetched_at: datetime | None = None


class CachedMovieCast(SQLModel, table=True):
    __tablename__ = "cached_movie_cast"

    movie_id: int = Field(
        foreign_key="cached_movies.tmdb_id", primary_key=True)
    actor_id: int = Field(foreign_key="cached_actors.tmdb_id",
                          primary_key=True, index=True)
    cast_order: int | None = None  # billing position (top 15)
    character_name: str | None = None


class CachedGenre(SQLModel, table=True):
    __tablename__ = "cached_genres"

    id: int = Field(primary_key=True)
    name: str
