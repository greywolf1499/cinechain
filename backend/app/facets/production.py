from datetime import UTC, date, datetime

from app.facets.micro_eras import micro_eras
from app.facets.regions import regions_of
from app.facets.registry import FacetValue
from app.models.cache import CachedMovie
from app.utils.countries import parse_countries
from app.utils.dates import parse_release_year


def evaluate(movie: CachedMovie) -> dict[str, FacetValue]:
    year = parse_release_year(movie.release_date)
    countries = None if movie.origin_country is None else parse_countries(movie.origin_country)
    runtime = movie.runtime
    return {
        "release_year": year,
        "release_decade": year // 10 * 10 if year is not None else None,
        "runtime": runtime,
        "runtime_band": None
        if not runtime
        else "short"
        if runtime < 90
        else "epic"
        if runtime > 150
        else "standard",
        "origin_country": countries,
        "region": regions_of(countries) if countries is not None else None,
        "micro_era": micro_eras(year, countries),
        "original_language": movie.original_language or None,
        "non_us_non_english": None
        if not movie.original_language
        else False
        if movie.original_language == "en"
        else None
        if countries is None
        else "US" not in countries,
        "genre": movie.genre_ids,
        "setting_year": movie.narrative_year,
        "setting_era": movie.narrative_era_label,
        "collection_id": str(movie.collection_id) if movie.collection_id else None,
        "in_collection": True
        if movie.collection_id
        else False
        if movie.overview is not None
        else None,
    }


def posthumous(release: str | None, deathdays: list[str | None], complete: bool) -> bool | None:
    try:
        released = date.fromisoformat(release or "")
    except ValueError:
        return None
    known = complete and bool(deathdays)
    for deathday in deathdays:
        if deathday:
            try:
                if date.fromisoformat(deathday) < released:
                    return True
            except ValueError:
                known = False
        else:
            known = False
    # NULL cannot distinguish a living person from a person not fetched yet.
    return False if known else None


def director_indexes(
    movie: CachedMovie, filmographies: list[list[CachedMovie] | None]
) -> tuple[bool | None, int | None]:
    from app.engines.auteur_marathon import build_filmography
    from app.engines.base import RunSetupError

    indexes = []
    incomplete = False
    for films in filmographies:
        if films is None:
            incomplete = True
            continue
        try:
            ordered = build_filmography(
                [
                    {
                        "id": film.tmdb_id,
                        "job": "Director",
                        "title": film.title,
                        "release_date": film.release_date,
                        "genre_ids": film.genre_ids,
                        "vote_count": film.vote_count,
                    }
                    for film in films
                ],
                {film.tmdb_id: (film.runtime, film.genre_ids or []) for film in films},
                today=datetime.now(UTC).date(),
            )
        except RunSetupError:
            continue
        indexes.extend(i for i, film in enumerate(ordered, 1) if film["movie_id"] == movie.tmdb_id)
    if not indexes:
        return None, None
    return (True if 1 in indexes else None if incomplete else False), (
        None if incomplete else min(indexes)
    )
