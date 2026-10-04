from datetime import datetime

from sqlalchemy import LargeBinary, UniqueConstraint
from sqlmodel import JSON, Column, Field, SQLModel

from app.utils.ids import utcnow


class CachedMovie(SQLModel, table=True):
    __tablename__ = "cached_movies"

    tmdb_id: int = Field(primary_key=True)
    title: str = Field(index=True)
    release_date: str | None = None
    poster_path: str | None = None
    overview: str | None = None
    # NULL = detail never fetched (or cached before taglines existed); "" = TMDB has none.
    tagline: str | None = None
    origin_country: str | None = None  # JSON array of ISO country codes
    original_language: str | None = None
    runtime: int | None = None
    genre_ids: list[int] | None = Field(default=None, sa_column=Column(JSON))
    # TMDB's popularity score - display/sort only, never a filter
    popularity: float | None = None
    # TMDB's 0-10 user score: only the Rabbit Hole's B-Movie Abyss reads it (when IMDb has no rating).
    vote_average: float | None = None
    # TMDB release status ("Released", "Planned", "In Production", "Canceled",
    # etc) - used ONLY for the reality filter (unreleased/cancelled exclusion),
    # never for popularity/vote/quality filtering.
    status: str | None = None
    # Completeness flags: distinguish "no cast" from "cast not yet fetched".
    cast_fetched_at: datetime | None = None
    # Same idea for directors: NULL = never fetched, set + no rows = "no directors".
    directors_fetched_at: datetime | None = None
    # Same again for the craft crew (composer, cinematographer, writers, director).
    crew_fetched_at: datetime | None = None
    # Aesthetic Gradient: poster's dominant colour as "#rrggbb"; NULL = not computed yet.
    dominant_color: str | None = Field(default=None, max_length=7)
    # Semantic Trope Web: float32 little-endian 384-d unit vector of `overview`; NULL = not computed.
    overview_embedding: bytes | None = Field(default=None, sa_column=Column(LargeBinary))
    # "provider:model" that produced `overview_embedding`; NULL = the local ONNX model (pre-23b rows).
    # Vectors from different models live in different spaces and are never compared.
    overview_embedding_model: str | None = None
    # LLM-extracted kebab-case tropes/themes ("heist", "time-loop"); NULL = not extracted yet.
    extracted_tropes: list[str] | None = Field(
        default=None, sa_column=Column(JSON(none_as_null=True)))


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


class CachedMovieRating(SQLModel, table=True):
    """JIT-cached OMDb ratings for a movie. `fetched_at` distinguishes a
    genuine cache hit (even if OMDb had nothing for that title) from never
    having been queried at all."""

    __tablename__ = "cached_movie_ratings"

    movie_id: int = Field(
        foreign_key="cached_movies.tmdb_id", primary_key=True)
    imdb_rating: str | None = None
    rotten_tomatoes: str | None = None
    metacritic: str | None = None
    fetched_at: datetime = Field(default_factory=utcnow)


class CachedDirector(SQLModel, table=True):
    """A director whose *directed* filmography has been fetched (Auteur Relay):
    NULL `credits_fetched_at` is never a row, so presence = "filmography cached"."""

    __tablename__ = "cached_directors"

    person_id: int = Field(primary_key=True)
    name: str
    credits_fetched_at: datetime = Field(default_factory=utcnow)


class CachedMovieDirector(SQLModel, table=True):
    """A movie's director(s), from the TMDB credits crew list (Passport "top directors")."""

    __tablename__ = "cached_movie_directors"

    movie_id: int = Field(foreign_key="cached_movies.tmdb_id", primary_key=True)
    person_id: int = Field(primary_key=True, index=True)
    name: str
    # TMDB gender code (0 unspecified, 1 female, 2 male, 3 non-binary); NULL = cached before it was stored.
    gender: int | None = None


class CachedCrewCredit(SQLModel, table=True):
    """One key craft credit on a film (Crew & Craft Trail). Only the jobs in
    `app.services.crew_roles.CRAFT_JOBS` are ever stored - never a film's full crew list."""

    __tablename__ = "cached_crew_credits"
    __table_args__ = (UniqueConstraint("movie_id", "person_id", "job"),)

    id: int | None = Field(default=None, primary_key=True)
    movie_id: int = Field(foreign_key="cached_movies.tmdb_id", index=True)
    person_id: int = Field(index=True)
    person_name: str
    job: str
    department: str
    profile_path: str | None = None


class CachedCrewPerson(SQLModel, table=True):
    """A person whose full filmography (cast + craft credits) has been fetched for the Crew &
    Craft Trail: presence = "filmography cached"."""

    __tablename__ = "cached_crew_people"

    person_id: int = Field(primary_key=True)
    name: str
    credits_fetched_at: datetime = Field(default_factory=utcnow)
