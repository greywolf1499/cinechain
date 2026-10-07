"""Curated canon lists (Sight & Sound, Letterboxd Top 250, custom imports) and
per-user Letterboxd watchlist sync."""

from datetime import datetime
from typing import Literal

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.utils.ids import new_id, utcnow


class CuratedSourceAccount(SQLModel, table=True):
    """Tier 0: a Letterboxd curator account (Criterion, Sight & Sound, BFI, ...)
    whose published lists can be browsed and enabled."""

    __tablename__ = "curated_source_accounts"

    id: str = Field(default_factory=new_id, primary_key=True)
    username: str = Field(unique=True, index=True)
    display_name: str | None = None
    avatar_url: str | None = None
    bio: str | None = None
    is_hq: bool = False
    account_tier: str | None = None
    total_public_lists: int = 0
    last_inspected_at: datetime | None = None
    lists_discovered_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)


class CuratedList(SQLModel, table=True):
    """One canon list (a preset like Sight & Sound 2022, or an admin-imported
    custom Letterboxd list URL)."""

    __tablename__ = "curated_lists"

    id: str = Field(default_factory=new_id, primary_key=True)
    # Null for custom (non-preset) imports.
    preset_key: str | None = Field(default=None, index=True)
    source_account_id: str | None = Field(default=None, foreign_key="curated_source_accounts.id")
    title: str
    url: str
    badge_prefix: str
    badge_color: str = "#d9a441"
    is_ranked: bool = False
    total_items: int = 0  # films matched to TMDB by the last sync
    # Tier 1 discovery metadata; discovered lists start disabled until a user enables them.
    is_enabled: bool = True
    film_count: int = 0
    description: str | None = None
    preview_posters: str = "[]"  # JSON-encoded list of poster URLs
    # Unified customization (all lists, preset or custom).
    slug: str | None = Field(default=None, unique=True, index=True)
    badge_emoji: str | None = None  # overrides the heuristic badge icon
    image_url: str | None = None  # chosen remote logo (served through the image proxy)
    image_filename: str | None = None  # uploaded logo under config_dir/list_images
    image_updated_at: datetime | None = None
    last_synced_at: datetime | None = None
    last_sync_error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class CanonMovieBadge(SQLModel, table=True):
    """A single movie's membership in a curated list, e.g. "SS22 #1"."""

    __tablename__ = "canon_movie_badges"

    id: str = Field(default_factory=new_id, primary_key=True)
    curated_list_id: str = Field(foreign_key="curated_lists.id", index=True)
    movie_id: int = Field(index=True)  # TMDB movie id
    badge_label: str
    rank: int | None = None


EntryStatus = Literal["matched", "unmatched", "tv_title", "ambiguous"]


class CuratedListEntry(SQLModel, table=True):
    """The source snapshot, including titles that could not become movie badges."""

    __tablename__ = "curated_list_entries"
    __table_args__ = (
        UniqueConstraint("list_id", "position", name="uq_curated_entry_position"),
        CheckConstraint(
            "status IN ('matched', 'unmatched', 'tv_title', 'ambiguous')",
            name="ck_curated_entry_status",
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    list_id: str = Field(foreign_key="curated_lists.id", index=True)
    position: int
    slug: str
    title: str
    year: int | None = None
    imdb_id: str | None = None
    tmdb_id: int | None = None
    match_tier: str | None = None
    status: str = "unmatched"
    reason: str | None = None
    attempted_at: datetime = Field(default_factory=utcnow)


class LetterboxdWatchlist(SQLModel, table=True):
    """A CineChain user's synced Letterboxd watchlist entries."""

    __tablename__ = "letterboxd_watchlists"

    id: str = Field(default_factory=new_id, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    letterboxd_username: str
    movie_id: int = Field(index=True)
    title: str
    year: int | None = None
    synced_at: datetime = Field(default_factory=utcnow)
