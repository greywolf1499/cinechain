from pydantic import BaseModel


class CountryCount(BaseModel):
    # ISO 3166-1 alpha-2, uppercase - maps 1:1 onto SVG world-map country ids.
    code: str
    count: int
    # Historical codes folded into this one (e.g. "SU" into "RU").
    merged_from: list[str] = []


class DirectorCount(BaseModel):
    person_id: int
    name: str
    count: int


class DirectorCoverage(BaseModel):
    """Directors come from the local TMDB cache; films not looked up yet are
    absent from `top_directors` until `POST /passport/backfill-directors` runs."""

    movies_with_directors: int
    movies_total: int


class PassportOut(BaseModel):
    # Distinct films; a rewatch or a film seen in several runs counts once.
    total_movies_watched: int
    # Every watched log entry, rewatches included.
    total_watches: int
    # "1970s" -> count, ordered oldest decade first.
    decades_distribution: dict[str, int]
    countries: list[CountryCount]
    top_directors: list[DirectorCount]
    directors_coverage: DirectorCoverage
