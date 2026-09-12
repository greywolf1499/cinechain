"""Shared country/decade/genre filtering for cached movies.

Used by both the engine's suggestion logic and the /people/{id}/credits route.
"""

from __future__ import annotations

import json

from app.models.cache import CachedMovie
from app.schemas.engine import SuggestionFilters
from app.utils.dates import parse_release_year


def passes_filters(movie: CachedMovie, filters: SuggestionFilters) -> bool:
    if filters.country is not None:
        countries = json.loads(
            movie.origin_country) if movie.origin_country else []
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
