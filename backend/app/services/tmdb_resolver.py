"""Async TMDB title -> movie id resolver for imported diaries.

Same multi-pass strategy as the scraper's `letterboxd.resolve_tmdb_multipass`
(exact year -> +-1 year -> title only -> Letterboxd-slug fallback, with the same
fuzzy scoring: both share `letterboxd.rank_candidates`), but built on the app's
async `TMDBClient`: globally paced, `Retry-After` aware, and - like the bridge
solver - it pauses with `asyncio.sleep` on a persistent 429 instead of failing.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from app.services import letterboxd
from app.services.tmdb import TMDBClient
from app.services.tmdb_backoff import fetch_with_backoff

PER_CALL_DEADLINE_SECONDS = 120.0


async def resolve_movie_id(
    tmdb: TMDBClient,
    title: str,
    year: int | None,
    *,
    slug: str | None = None,
    on_pause: Callable[[float], None] | None = None,
    per_call_seconds: float = PER_CALL_DEADLINE_SECONDS,
) -> int | None:
    """Returns the best TMDB movie id, or None when nothing matches well enough.

    Raises `DeadlineReached` if TMDB keeps rate-limiting one lookup for longer
    than `per_call_seconds`; other TMDB failures propagate as `TMDBError`.
    """
    clean_q = letterboxd.clean_title_str(title) or title.strip()
    if not clean_q:
        return None

    async def search(query: str, search_year: int | None) -> list[dict[str, Any]]:
        data = await fetch_with_backoff(
            lambda: tmdb.search_movies(query, year=search_year),
            time.monotonic() + per_call_seconds,
            on_pause,
        )
        return (data or {}).get("results", [])

    for search_year in letterboxd.search_year_order(year):
        ranked = letterboxd.rank_candidates(await search(clean_q, search_year), clean_q, year)
        if ranked:
            return ranked[0]["id"]

    ranked = letterboxd.rank_candidates(await search(clean_q, None), clean_q, year)
    if ranked:
        return ranked[0]["id"]

    slug_query = letterboxd.slug_search_query(slug, clean_q)
    if slug_query:
        results = await search(slug_query, year) if year else []
        results = results or await search(slug_query, None)
        ranked = letterboxd.rank_candidates(results, clean_q, year)
        if ranked:
            return ranked[0]["id"]
    return None
