from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlmodel import Session

from app.api.deps import get_current_user, get_omdb_client, get_tmdb_client
from app.db import get_session
from app.integrations.omdb import OMDbClient
from app.models.cache import CachedMovie
from app.models.user import User
from app.schemas.engine import SuggestionFilters
from app.schemas.movies import (
    CastMember,
    CrewMember,
    MovieDetail,
    MovieRatings,
    MovieSearchResponse,
    MovieSummary,
    NarrativeEra,
    NarrativeEraUpdate,
    PersonSummary,
    SeedSuggestionOut,
    TropeExtraction,
)
from app.services import (
    cache_repo,
    historical_era,
    llm,
    movie_features,
    pool_options,
    seed_suggestions,
)
from app.services.cache_repo import CastEntry
from app.services.crew_roles import role_for_job
from app.services.movie_filters import passes_filters
from app.services.tmdb import TMDBClient
from app.utils.dates import parse_release_year

router = APIRouter(tags=["movies"])


class GenreOut(BaseModel):
    id: int
    name: str


class BulkRatingsRequest(BaseModel):
    tmdb_ids: list[int]


def _movie_to_summary(movie: CachedMovie) -> MovieSummary:
    return MovieSummary(
        tmdb_id=movie.tmdb_id,
        title=movie.title,
        poster_path=movie.poster_path,
        release_year=parse_release_year(movie.release_date),
        origin_country=movie.origin_country,
    )


def _movie_to_detail(movie: CachedMovie, ratings: MovieRatings | None = None) -> MovieDetail:
    return MovieDetail(
        **_movie_to_summary(movie).model_dump(),
        overview=movie.overview,
        tagline=movie.tagline,
        runtime=movie.runtime,
        original_language=movie.original_language,
        genre_ids=movie.genre_ids or [],
        ratings=ratings,
        extracted_tropes=movie.extracted_tropes,
    )


def _search_result_to_summary(raw: dict[str, Any]) -> MovieSummary:
    # TMDB search results carry no origin_country - only the detail endpoint does.
    return MovieSummary(
        tmdb_id=raw["id"],
        title=raw.get("title") or raw.get("original_title") or "",
        poster_path=raw.get("poster_path"),
        release_year=parse_release_year(raw.get("release_date")),
        origin_country=None,
        popularity=raw.get("popularity"),
    )


def _cast_entry_to_member(entry: CastEntry) -> CastMember:
    return CastMember(
        actor_id=entry["actor_id"],
        name=entry["name"],
        profile_path=entry["profile_path"],
        character_name=entry["character_name"],
        cast_order=entry["cast_order"],
    )


@router.get("/movies/search", response_model=MovieSearchResponse)
async def search_movies(
    q: str = Query(..., min_length=1),
    page: int = Query(default=1, ge=1),
    sort_by: str | None = Query(
        default=None, pattern="^underdog$",
        description="underdog = least popular first, dropping dead entries (popularity < 1.0)"),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    _current_user: User = Depends(get_current_user),
) -> MovieSearchResponse:
    """No popularity/vote/year restrictions - raw TMDB search, world cinema included (the
    Underdog B-Sides flip is the one opt-in exception: it hides dead entries)."""
    raw = await tmdb.search_movies(q, page)
    results = [_search_result_to_summary(r) for r in raw.get("results", [])]
    if sort_by == pool_options.UNDERDOG:
        results = sorted(
            (r for r in results if pool_options.is_underdog(r.popularity)),
            key=lambda r: (r.popularity, r.title))
    return MovieSearchResponse(
        results=results,
        page=raw.get("page", page),
        total_pages=raw.get("total_pages", 1),
    )


@router.get("/people/search", response_model=list[PersonSummary])
async def search_people(
    q: str = Query(..., min_length=1),
    department: str | None = Query(
        None, description="Only people known for this TMDB department, e.g. Directing"),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    _current_user: User = Depends(get_current_user),
) -> list[PersonSummary]:
    """Live TMDB person search (actors first): the Method Actor Marathon's picker. With
    `department` (the Auteur Marathon asks for Directing) only that department's people."""
    raw = await tmdb.search_people(q)
    people = [
        PersonSummary(
            person_id=entry["id"], name=entry.get("name") or "",
            profile_path=entry.get("profile_path"),
            known_for_department=entry.get("known_for_department"),
            known_for=[k.get("title") or k.get("name") or "" for k in entry.get("known_for", [])][:3])
        for entry in raw.get("results", [])
        if department is None or entry.get("known_for_department") == department
    ]
    return sorted(people, key=lambda p: p.known_for_department != "Acting")[:10]


@router.get("/movies/genres", response_model=list[GenreOut])
async def list_genres(
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    _current_user: User = Depends(get_current_user),
) -> list[GenreOut]:
    genres = await cache_repo.get_genres(session, tmdb)
    return [GenreOut(id=g.id, name=g.name) for g in genres]


@router.get("/movies/seed-suggestion", response_model=SeedSuggestionOut | None)
def get_seed_suggestion(
    exclude: str = Query(default="", description="Comma-separated TMDB ids already offered"),
    game_type: str | None = Query(default=None),
    session: Session = Depends(get_session),
    _current_user: User = Depends(get_current_user),
) -> SeedSuggestionOut | None:
    """A random well-regarded film from the local cache (canon-listed or highly rated,
    else simply popular) to start a run with. Null when the cache has nothing to offer."""
    skipped = {int(part) for part in exclude.split(",") if part.strip().isdigit()}
    suggestion = seed_suggestions.suggest_seed(session, skipped, game_type)
    if suggestion is None:
        return None
    return SeedSuggestionOut(**_movie_to_summary(suggestion.movie).model_dump(), reason=suggestion.reason)


@router.get("/movies/{tmdb_id}", response_model=MovieDetail)
async def get_movie(
    tmdb_id: int,
    refresh: bool = Query(default=False),
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    omdb: OMDbClient = Depends(get_omdb_client),
    _current_user: User = Depends(get_current_user),
) -> MovieDetail:
    movie = await cache_repo.get_movie(session, tmdb, tmdb_id, refresh=refresh)
    if (movie.overview is None or movie.tagline is None) and not refresh:
        # NULL overview marks a stub cached from a lightweight TMDB payload; NULL
        # tagline marks a detail row cached before taglines were stored (JIT backfill).
        movie = await cache_repo.get_movie(session, tmdb, tmdb_id, refresh=True)
    rating_row = await cache_repo.get_movie_ratings(session, tmdb, omdb, tmdb_id)
    ratings = (
        MovieRatings(
            imdb_rating=rating_row.imdb_rating,
            rotten_tomatoes=rating_row.rotten_tomatoes,
            metacritic=rating_row.metacritic,
        )
        if rating_row is not None
        else None
    )
    return _movie_to_detail(movie, ratings)


@router.post("/movies/{tmdb_id}/tropes/extract", response_model=TropeExtraction)
async def extract_movie_tropes(
    tmdb_id: int,
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    _current_user: User = Depends(get_current_user),
) -> TropeExtraction:
    """JIT trope extraction: asks the configured LLM once and caches the result on the movie.
    Already-extracted films are returned as-is; with the LLM off the answer is an empty,
    uncached list (`enabled=false`) so enabling it later still extracts."""
    movie = await cache_repo.get_movie(session, tmdb, tmdb_id)
    if movie.overview is None:
        movie = await cache_repo.get_movie(session, tmdb, tmdb_id, refresh=True)
    if movie.extracted_tropes is not None:
        return TropeExtraction(tmdb_id=tmdb_id, tropes=movie.extracted_tropes, cached=True)
    config = llm.load_config(session)
    if not config.enabled:
        return TropeExtraction(tmdb_id=tmdb_id, tropes=[], cached=False, enabled=False)
    try:
        tropes = await movie_features.extract_and_store_tropes(session, movie, config)
    except llm.LlmUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return TropeExtraction(tmdb_id=tmdb_id, tropes=tropes, cached=False)


@router.post("/movies/{tmdb_id}/narrative-era", response_model=NarrativeEra)
async def update_narrative_era(
    tmdb_id: int,
    payload: NarrativeEraUpdate | None = None,
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    _current_user: User = Depends(get_current_user),
) -> NarrativeEra:
    """Historical Time-Travel's setting year. With a `narrative_year` and/or `narrative_era_label`
    this is a manual correction (stored as-is); with an empty body the era is re-resolved from
    TMDB keywords, the plot and (when on) the model, replacing whatever was stored."""
    movie = await cache_repo.get_movie(session, tmdb, tmdb_id)
    if movie.overview is None:
        movie = await cache_repo.get_movie(session, tmdb, tmdb_id, refresh=True)
    payload = payload or NarrativeEraUpdate()
    label = (payload.narrative_era_label or "").strip()
    if payload.narrative_year is None and not label:
        year, era = await historical_era.ensure_narrative_era(
            session, tmdb, movie, llm.load_config(session), force=True)
        source = "resolved" if movie.narrative_year == year else "default"
        return NarrativeEra(
            tmdb_id=tmdb_id, narrative_year=year, narrative_era_label=era, source=source)
    year = payload.narrative_year
    if year is None:
        year = historical_era.effective_era(movie)[0]
    if not label:
        label = historical_era.era_label_for_year(year, parse_release_year(movie.release_date))
    historical_era.set_narrative_era(session, movie, year, label)
    return NarrativeEra(
        tmdb_id=tmdb_id, narrative_year=year, narrative_era_label=movie.narrative_era_label or label,
        source="manual")


@router.get("/movies/{tmdb_id}/ratings", response_model=MovieRatings | None)
async def get_movie_ratings(
    tmdb_id: int,
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    omdb: OMDbClient = Depends(get_omdb_client),
    _current_user: User = Depends(get_current_user),
) -> MovieRatings | None:
    rating_row = await cache_repo.get_movie_ratings(session, tmdb, omdb, tmdb_id)
    if rating_row is None:
        return None
    return MovieRatings(
        imdb_rating=rating_row.imdb_rating,
        rotten_tomatoes=rating_row.rotten_tomatoes,
        metacritic=rating_row.metacritic,
    )


@router.post("/movies/ratings/bulk", response_model=dict[str, MovieRatings | None])
async def get_movie_ratings_bulk(
    payload: BulkRatingsRequest,
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    omdb: OMDbClient = Depends(get_omdb_client),
    _current_user: User = Depends(get_current_user),
) -> dict[str, MovieRatings | None]:
    results: dict[str, MovieRatings | None] = {}
    for tmdb_id in payload.tmdb_ids:
        rating_row = await cache_repo.get_movie_ratings(session, tmdb, omdb, tmdb_id)
        results[str(tmdb_id)] = (
            MovieRatings(
                imdb_rating=rating_row.imdb_rating,
                rotten_tomatoes=rating_row.rotten_tomatoes,
                metacritic=rating_row.metacritic,
            )
            if rating_row is not None
            else None
        )
    return results


@router.get("/movies/{tmdb_id}/cast", response_model=list[CastMember])
async def get_movie_cast(
    tmdb_id: int,
    limit: int = Query(default=15, ge=1, le=50),
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    _current_user: User = Depends(get_current_user),
) -> list[CastMember]:
    cast = await cache_repo.get_movie_cast(session, tmdb, tmdb_id, limit)
    return [_cast_entry_to_member(member) for member in cast]


@router.get("/movies/{tmdb_id}/crew", response_model=list[CrewMember])
async def get_movie_crew(
    tmdb_id: int,
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    _current_user: User = Depends(get_current_user),
) -> list[CrewMember]:
    """The film's key craft crew only (composer, cinematographer, writers, director)."""
    crew = await cache_repo.get_movie_crew(session, tmdb, tmdb_id)
    return [
        CrewMember(
            person_id=row.person_id, name=row.person_name, job=row.job,
            department=row.department, role=role_for_job(row.job) or "",
            profile_path=row.profile_path)
        for row in crew
    ]


@router.get("/people/{person_id}/credits", response_model=list[MovieSummary])
async def get_person_credits(
    person_id: int,
    country: str | None = Query(default=None),
    decade: int | None = Query(default=None),
    genre_id: int | None = Query(default=None),
    session: Session = Depends(get_session),
    tmdb: TMDBClient = Depends(get_tmdb_client),
    _current_user: User = Depends(get_current_user),
) -> list[MovieSummary]:
    movies = await cache_repo.get_actor_credits(session, tmdb, person_id)
    filters = SuggestionFilters(
        country=country, decade=decade, genre_id=genre_id)
    return [_movie_to_summary(movie) for movie in movies if passes_filters(movie, filters)]
