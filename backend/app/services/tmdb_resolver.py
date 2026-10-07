"""Shared tiered movie matching for diaries and the synchronous list scraper."""

from __future__ import annotations

import difflib
import re
import time
import unicodedata
from collections.abc import Awaitable, Callable
from typing import Any, NamedTuple, Protocol

from app.services.tmdb import TMDBDirector
from app.services.tmdb_backoff import fetch_with_backoff

PER_CALL_DEADLINE_SECONDS = 120.0
ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


class MatchResult(NamedTuple):
    tmdb_id: int | None
    tier: str | None
    status: str
    reason: str | None


class MovieMatcher(Protocol):
    async def search_movies(
        self, query: str, page: int = 1, year: int | None = None
    ) -> dict[str, Any]: ...

    async def find_by_imdb_id(self, imdb_id: str) -> dict[str, Any]: ...

    async def get_movie_directors(self, tmdb_id: int) -> list[TMDBDirector]: ...


def clean_title_str(title: str) -> str:
    folded = "".join(
        c for c in unicodedata.normalize("NFKD", title) if not unicodedata.combining(c)
    )
    return folded.replace('"', "").replace("'", "").strip()


def _number_token(token: str) -> str:
    if not token or not re.fullmatch(
        r"m{0,3}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})", token
    ):
        return token
    values = [ROMAN_VALUES[char] for char in token]
    return str(
        sum(
            -value if index + 1 < len(values) and value < values[index + 1] else value
            for index, value in enumerate(values)
        )
    )


def normalize_title(title: str) -> str:
    folded = unicodedata.normalize("NFKD", title).casefold().replace("&", " and ")
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    tokens = re.sub(r"[^\w\s]", " ", folded).split()
    if len(tokens) > 1 and tokens[0] in {"the", "a", "an"}:
        tokens.pop(0)
    # Letterboxd exports sometimes move the article to the end ("Thing, The").
    if len(tokens) > 1 and tokens[-1] in {"the", "a", "an"}:
        tokens.pop()
    return " ".join(_number_token(token) for token in tokens)


def slug_search_query(slug: str | None, clean_q: str) -> str | None:
    if not slug:
        return None
    query = re.sub(r"-(?:18|19|20)\d{2}$", "", slug.strip("/").split("/")[-1])
    query = query.replace("-", " ").strip()
    return query if query and query.casefold() != clean_q.casefold() else None


def search_year_order(year: int | None) -> list[int]:
    return [] if year is None else [year, year - 1, year + 1]


def _year(candidate: dict[str, Any]) -> int | None:
    raw = str(candidate.get("release_date") or "")[:4]
    return int(raw) if raw.isdigit() else None


def _score(candidate: dict[str, Any], query: str, normalized: bool) -> float:
    clean = normalize_title if normalized else lambda text: clean_title_str(text).casefold()
    return max(
        (
            difflib.SequenceMatcher(None, clean(query), clean(str(candidate[key]))).ratio()
            for key in ("title", "original_title")
            if candidate.get(key)
        ),
        default=0.0,
    )


def rank_candidates(
    results: list[dict[str, Any]],
    query: str,
    year: int | None,
    *,
    threshold: float = 0.88,
    normalized: bool = False,
    year_slack: int = 2,
) -> list[dict[str, Any]]:
    ranked = [
        candidate
        for candidate in results
        if candidate.get("id")
        and candidate.get("media_type", "movie") == "movie"
        and _score(candidate, query, normalized) >= threshold
        and (
            year is None
            or (
                (candidate_year := _year(candidate)) is not None
                and abs(candidate_year - year) <= year_slack
            )
        )
    ]
    return sorted(ranked, key=lambda candidate: _score(candidate, query, normalized), reverse=True)


async def resolve_movie(
    tmdb: MovieMatcher,
    title: str,
    year: int | None,
    *,
    slug: str | None = None,
    inline_id: int | None = None,
    tmdb_type: str | None = None,
    directors: list[str] | None = None,
    imdb_id: str | None = None,
    load_imdb: Callable[[], Awaitable[str | None]] | None = None,
    on_pause: Callable[[float], None] | None = None,
    per_call_seconds: float = PER_CALL_DEADLINE_SECONDS,
) -> MatchResult:
    if tmdb_type == "tv":
        return MatchResult(None, "inline", "tv_title", "TMDB identifies this entry as television.")
    if inline_id:
        return MatchResult(inline_id, "inline", "matched", None)
    query = clean_title_str(title) or title.strip()
    normalized_query = normalize_title(title)
    ambiguous = False
    plausible_ids: set[int] = set()
    director_cache: dict[int, list[TMDBDirector]] = {}
    searched: dict[tuple[str, int | None], list[dict[str, Any]]] = {}

    async def search(text: str, search_year: int | None) -> list[dict[str, Any]]:
        key = (text.casefold(), search_year)
        if key not in searched:
            data = await fetch_with_backoff(
                lambda: tmdb.search_movies(text, year=search_year),
                time.monotonic() + per_call_seconds,
                on_pause,
            )
            searched[key] = (data or {}).get("results", [])
        return searched[key]

    async def choose(candidates: list[dict[str, Any]]) -> int | None:
        nonlocal ambiguous
        if not candidates:
            return None
        plausible_ids.update(candidate["id"] for candidate in candidates)
        if len(plausible_ids) == 1:
            return next(iter(plausible_ids))
        if len(plausible_ids) > 1:
            if directors:
                matches = []
                for movie_id in plausible_ids:
                    if movie_id not in director_cache:
                        found = await fetch_with_backoff(
                            lambda movie_id=movie_id: tmdb.get_movie_directors(movie_id),
                            time.monotonic() + per_call_seconds,
                            on_pause,
                        )
                        director_cache[movie_id] = found or []
                    if any(
                        difflib.SequenceMatcher(
                            None, normalize_title(target), normalize_title(person.get("name", ""))
                        ).ratio()
                        > 0.8
                        for target in directors
                        for person in director_cache[movie_id]
                    ):
                        matches.append(movie_id)
                if len(matches) == 1:
                    return matches[0]
            ambiguous = True
        return None

    if query:
        if year is not None:
            found = await choose(
                rank_candidates(
                    await search(query, year),
                    title,
                    year,
                    threshold=0.92,
                    year_slack=0,
                )
            )
            if found:
                return MatchResult(found, "exact", "matched", None)
        for search_year in search_year_order(year) or [None]:
            found = await choose(
                rank_candidates(
                    await search(normalized_query or query, search_year),
                    title,
                    year,
                    threshold=0.85,
                    normalized=True,
                    year_slack=1,
                )
            )
            if found:
                return MatchResult(found, "normalized", "matched", None)
        slug_query = slug_search_query(slug, query)
        for text in [query, *([slug_query] if slug_query else [])]:
            found = await choose(
                rank_candidates(
                    await search(text, None),
                    title,
                    year,
                    threshold=0.80,
                )
            )
            if found:
                return MatchResult(found, "search", "matched", None)
    if not imdb_id and load_imdb:
        imdb_id = await load_imdb()
    if imdb_id:
        data = await fetch_with_backoff(
            lambda: tmdb.find_by_imdb_id(imdb_id),
            time.monotonic() + per_call_seconds,
            on_pause,
        )
        data = data or {}
        ids = {movie["id"] for movie in data.get("movie_results", []) if movie.get("id")}
        if len(ids) == 1:
            return MatchResult(next(iter(ids)), "imdb", "matched", None)
        ambiguous = ambiguous or len(ids) > 1
        if not ids and (data.get("tv_results") or data.get("tv_episode_results")):
            return MatchResult(None, "imdb", "tv_title", "IMDb identifies a television title.")
    if ambiguous:
        return MatchResult(None, None, "ambiguous", "Multiple plausible movies; choose a match.")
    return MatchResult(None, None, "unmatched", "No matching TMDB movie found.")


async def resolve_movie_id(
    tmdb: MovieMatcher,
    title: str,
    year: int | None,
    *,
    slug: str | None = None,
    on_pause: Callable[[float], None] | None = None,
    per_call_seconds: float = PER_CALL_DEADLINE_SECONDS,
) -> int | None:
    """Compatibility wrapper for diary imports; provider errors still propagate."""
    return (
        await resolve_movie(
            tmdb,
            title,
            year,
            slug=slug,
            on_pause=on_pause,
            per_call_seconds=per_call_seconds,
        )
    ).tmdb_id
