from datetime import datetime
from typing import Literal

from sqlalchemy import Column, Index, String
from sqlalchemy.orm import declared_attr
from sqlmodel import Field, SQLModel

from app.utils.ids import utcnow


class MovieFacet(SQLModel, table=True):
    @declared_attr.directive
    def __tablename__(cls) -> str:
        return "movie_facets"

    __table_args__ = (
        Index("ix_movie_facets_movie_family", "movie_id", "facet_id"),
        {"sqlite_with_rowid": False},
    )

    facet_id: str = Field(primary_key=True)
    value_text: str = Field(default="", primary_key=True)
    value_num: float = Field(default=0, primary_key=True)
    movie_id: int = Field(foreign_key="cached_movies.tmdb_id", primary_key=True)
    source: str = Field(default="cache", primary_key=True)
    confidence: float | None = Field(default=1.0, nullable=True)
    source_url: str | None = None


class MovieFacetStatus(SQLModel, table=True):
    @declared_attr.directive
    def __tablename__(cls) -> str:
        return "movie_facet_status"

    __table_args__ = {"sqlite_with_rowid": False}

    movie_id: int = Field(foreign_key="cached_movies.tmdb_id", primary_key=True)
    family: str = Field(primary_key=True)
    version: int
    status: Literal["ok", "unavailable", "error"] = Field(sa_column=Column(String, nullable=False))
    computed_at: datetime = Field(default_factory=utcnow)
