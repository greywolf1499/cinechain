import math
from datetime import UTC, datetime

from app.facets.registry import FacetValue
from app.models.cache import CachedMovie, CachedMovieRating
from app.services.movie_filters import verified_tmdb_rating
from app.utils.dates import parse_release_year


def _number(raw: str | None) -> float | None:
    try:
        value = float((raw or "").rstrip("%"))
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def parsed(raw: str | None, maximum: float) -> float | None:
    value = _number(raw)
    return value if value is not None and 0 <= value <= maximum else None


def evaluate(
    movie: CachedMovie, rated: CachedMovieRating | None, *, current_year: int | None = None
) -> dict[str, FacetValue]:
    raw_imdb = _number(rated.imdb_rating) if rated else None
    imdb = raw_imdb if raw_imdb is not None and 0 <= raw_imdb <= 10 else None
    rt = parsed(rated.rotten_tomatoes, 100) if rated else None
    mc = parsed(rated.metacritic, 100) if rated else None
    gap = rt - imdb * 10 if rt is not None and imdb is not None else None
    year = parse_release_year(movie.release_date)
    age = (current_year or datetime.now(UTC).year) - year if year is not None else None
    cult = None
    if (
        gap is not None
        and rt is not None
        and imdb is not None
        and movie.vote_count is not None
        and age is not None
    ):
        cult = movie.vote_count >= 1000 and age >= 15 and (gap <= -20 or (rt < 60 and imdb >= 7))
    budget, revenue = movie.budget, movie.revenue
    bomb = sleeper = None
    if budget is not None and revenue is not None and budget > 0 and revenue > 0:
        bomb = budget >= 1_000_000 and revenue < 0.5 * budget
        sleeper = revenue >= 5 * budget
    return {
        "imdb_rating": imdb,
        "tomatometer": rt,
        "metacritic": mc,
        "rating": raw_imdb if raw_imdb is not None else verified_tmdb_rating(movie),
        "critic_audience_gap": gap,
        "cult_classic": cult,
        "critic_darling": gap >= 20 and rt >= 85 if gap is not None and rt is not None else None,
        "box_office_bomb": bomb,
        "sleeper_hit": sleeper,
    }
