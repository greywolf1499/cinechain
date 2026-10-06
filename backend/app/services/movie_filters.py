"""Shared country/decade/genre filtering for cached movies.

Used by both the engine's suggestion logic and the /people/{id}/credits route.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.cache import CachedMovie, CachedMovieRating
from app.schemas.engine import SuggestionFilters
from app.utils.countries import parse_countries
from app.utils.dates import parse_release_year

# TMDB `status` values that mean "not actually released yet" - the ONLY thing
# the reality filter checks. Never touches vote_count/popularity/rating: an
# obscure, zero-vote, already-released indie/international film is always
# eligible.
UNRELEASED_STATUSES = {"Planned", "In Production", "Canceled"}


def is_reality_eligible(movie: CachedMovie) -> bool:
    """Reality filter: excludes unreleased/cancelled/in-production movies from
    the graph and candidate pools. Deliberately release-date/status only -
    NEVER filters on vote_count, popularity, or rating (that would penalize
    indie/regional/vintage world cinema, which this app must never do)."""
    if not movie.release_date:
        return False
    if movie.release_date > datetime.now(UTC).date().isoformat():
        return False
    return movie.status not in UNRELEASED_STATUSES


def passes_filters(movie: CachedMovie, filters: SuggestionFilters) -> bool:
    if not is_reality_eligible(movie):
        return False
    if filters.country is not None:
        countries = parse_countries(movie.origin_country)
        if filters.country not in countries:
            return False
    if filters.decade is not None:
        year = parse_release_year(movie.release_date)
        if year is None or (year // 10) * 10 != filters.decade:
            return False
    if filters.genre_id is not None:
        return bool(movie.genre_ids and filters.genre_id in movie.genre_ids)
    # `on_server` is reserved for the Phase 7 Jellyfin integration - no-op for now.
    return True


def rating_of(session, row: CachedMovie) -> float | None:
    """IMDb rating when OMDb has cached one, else TMDB's user score. Cache-only: judging a film
    never costs an OMDb call."""
    rated = session.get(CachedMovieRating, row.tmdb_id)
    if rated is not None and rated.imdb_rating and rated.imdb_rating != "N/A":
        try:
            return float(rated.imdb_rating)
        except ValueError:
            pass
    return verified_tmdb_rating(row)


def verified_tmdb_rating(row: CachedMovie) -> float | None:
    """Return TMDB's score only when enough votes make it a meaningful rating."""
    if row.vote_count is None or row.vote_count < 10:
        return None
    return row.vote_average
