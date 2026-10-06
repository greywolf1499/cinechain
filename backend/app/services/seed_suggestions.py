""" "Recommend Seed Movie": a random well-regarded film from the local cache."""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlmodel import Session, select

from app.models.cache import CachedMovie, CachedMovieRating
from app.models.curated import CanonMovieBadge
from app.services.movie_filters import is_reality_eligible

MIN_IMDB_RATING = 7.5
POPULAR_FALLBACK_SIZE = 150

# What each mode needs on the seed row for its first hop to be judged.
MODE_REQUIREMENTS = {
    "chrono_climb": lambda m: bool(m.release_date),
    "historical_time_travel": lambda m: bool(m.release_date),
    "world_passport": lambda m: bool(m.origin_country),
    "semantic_trope": lambda m: bool((m.overview or "").strip()),
    "aesthetic_gradient": lambda m: bool(m.poster_path),
}


@dataclass
class SeedSuggestion:
    movie: CachedMovie
    reason: str


def _imdb_score(raw: str | None) -> float | None:
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def suggest_seed(
    session: Session,
    exclude_ids: set[int],
    game_type: str | None = None,
    rng: random.Random | None = None,
    *,
    allowed_ids: set[int] | None = None,
    min_runtime: int | None = None,
) -> SeedSuggestion | None:
    """Prefers canon-listed and highly rated films, then simply popular ones. Skips
    `exclude_ids` (earlier re-rolls) and, when possible, films the mode can't judge."""
    rng = rng or random.Random()
    reasons: dict[int, str] = {}

    for badge in session.exec(select(CanonMovieBadge)).all():
        reasons.setdefault(badge.movie_id, f"On the {badge.badge_label} list")
    for rating in session.exec(select(CachedMovieRating)).all():
        score = _imdb_score(rating.imdb_rating)
        if score is not None and score >= MIN_IMDB_RATING:
            reasons.setdefault(rating.movie_id, f"IMDb {score:.1f}")

    def eligible(movie: CachedMovie | None) -> bool:
        return (
            movie is not None
            and movie.tmdb_id not in exclude_ids
            and (allowed_ids is None or movie.tmdb_id in allowed_ids)
            and (allowed_ids is not None or bool(movie.poster_path))
            and bool(movie.title)
            and is_reality_eligible(movie)
            and (not min_runtime or movie.runtime is None or movie.runtime >= min_runtime)
        )

    acclaimed = [m for m in (session.get(CachedMovie, i) for i in reasons) if eligible(m)]
    popular = [
        m
        for m in session.exec(
            select(CachedMovie)
            .where(CachedMovie.popularity.is_not(None))  # type: ignore[union-attr]
            .order_by(CachedMovie.popularity.desc())
            .limit(POPULAR_FALLBACK_SIZE)  # type: ignore[union-attr]
        ).all()
        if eligible(m)
    ]
    needs = MODE_REQUIREMENTS.get(game_type or "", lambda _m: True)
    allowed = (
        [m for m in (session.get(CachedMovie, i) for i in sorted(allowed_ids)) if eligible(m)]
        if allowed_ids is not None
        else []
    )

    for pool, label in ((acclaimed, None), (popular, "Popular pick"), (allowed, "From your slice")):
        for strict in (True, False):
            choices = [m for m in pool if not strict or needs(m)]
            if choices:
                movie = rng.choice(choices)
                return SeedSuggestion(
                    movie, reasons.get(movie.tmdb_id) or label or "From your slice"
                )
    return None
