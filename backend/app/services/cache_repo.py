"""JIT SQLite cache for TMDB movies/actors/cast.

Two layers, deliberately kept separate:
  - `CacheRepo`: synchronous SQLModel `Session` CRUD only. Never touches the network.
  - module-level `get_*()` functions: async read-through orchestration. On a
    cache miss they await the async `TMDBClient`, then bridge back into
    `CacheRepo` via `anyio.to_thread.run_sync` to persist the result.

Async callers (API routes, the pathfinder) should only ever call the
module-level functions, never construct/await `CacheRepo` directly.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, timedelta
from typing import TypedDict

import anyio
import httpx
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.config import get_settings
from app.facets import store as facet_store
from app.facets import tropes as trope_facets
from app.integrations.omdb import OMDbClient, OMDbRatings
from app.models.cache import (
    CachedActor,
    CachedCrewCredit,
    CachedCrewPerson,
    CachedDirector,
    CachedGenre,
    CachedMovie,
    CachedMovieCast,
    CachedMovieDirector,
    CachedMovieRating,
)
from app.services.crew_roles import CRAFT_JOBS, ROLE_ACTOR, role_for_job
from app.services.tmdb import (
    TMDBCastMember,
    TMDBClient,
    TMDBCraftCredit,
    TMDBCrewMember,
    TMDBDirector,
    TMDBError,
    TMDBGenre,
    TMDBMovie,
    TMDBPersonCredit,
)
from app.utils.dates import parse_release_year
from app.utils.ids import utcnow

logger = logging.getLogger(__name__)
RATINGS_NEGATIVE_TTL = timedelta(hours=24)


class CastEntry(TypedDict):
    actor_id: int
    name: str
    profile_path: str | None
    character_name: str | None
    cast_order: int | None


@dataclass(frozen=True)
class PersonFilm:
    """One film in a person's cast + key-craft filmography, with the role they filled."""

    movie: CachedMovie
    role: str
    job: str | None = None
    character: str | None = None


class CacheRepo:
    """Synchronous SQLite read/write layer over the JIT cache tables."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # --- movies ---

    def get_cached_movie(self, tmdb_id: int) -> CachedMovie | None:
        return self.session.get(CachedMovie, tmdb_id)

    def upsert_movie(self, movie: TMDBMovie) -> CachedMovie:
        """Full upsert from a `/movie/{id}` detail fetch."""
        row = self.session.get(CachedMovie, movie["id"])
        if row is None:
            row = CachedMovie(tmdb_id=movie["id"])
        overview_changed = row.overview != (movie.get("overview") or "")
        work_identity_changed = row.title != movie["title"] or row.release_date != movie.get(
            "release_date"
        )
        genre_ids = movie.get("genre_ids") or []
        genre_changed = (row.genre_ids or []) != genre_ids
        row.title = movie["title"]
        row.imdb_id = movie.get("imdb_id") or row.imdb_id
        if row.narrative_era_label == "Contemporary" and row.release_date != movie.get(
            "release_date"
        ):
            # "Contemporary" was derived from the old release year; a real era is never touched.
            row.narrative_year = None
            row.narrative_era_label = None
        row.release_date = movie.get("release_date")
        # Derived features go stale with their source; they're recomputed on demand.
        if row.poster_path != movie.get("poster_path"):
            row.dominant_color = None
        row.poster_path = movie.get("poster_path")
        if overview_changed:
            row.overview_embedding = None
            row.overview_embedding_model = None
            row.extracted_tropes = None
        row.overview = movie.get("overview") or ""
        row.tagline = movie.get("tagline") or ""
        row.origin_country = json.dumps(movie.get("origin_country") or [])
        row.original_language = movie.get("original_language")
        row.runtime = movie.get("runtime")
        row.genre_ids = genre_ids
        if overview_changed or work_identity_changed or genre_changed:
            trope_facets.invalidate_movie_evidence(
                self.session,
                row.tmdb_id,
                overview_changed=overview_changed,
                work_identity_changed=work_identity_changed,
                genre_ids=genre_ids if genre_changed else None,
            )
        if work_identity_changed:
            row.tvtropes_work_url = None
        row.popularity = movie.get("popularity")
        row.vote_average = movie.get("vote_average")
        row.vote_count = movie.get("vote_count")
        row.status = movie.get("status")
        row.budget = movie.get("budget")
        row.revenue = movie.get("revenue")
        row.collection_id = movie.get("collection_id")
        self.session.add(row)
        affected = set(
            self.session.exec(
                select(CachedMovieDirector.movie_id).where(
                    col(CachedMovieDirector.person_id).in_(
                        select(CachedMovieDirector.person_id).where(
                            CachedMovieDirector.movie_id == row.tmdb_id
                        )
                    )
                )
            ).all()
        ) | {row.tmdb_id}
        facet_store.invalidate(self.session, affected, ["production"])
        facet_store.invalidate(self.session, [row.tmdb_id], ("lexical", "production", "reception"))
        facet_store.refresh(self.session, [row.tmdb_id])
        self.session.commit()
        self.session.refresh(row)
        return row

    def upsert_movie_stub(self, credit: TMDBPersonCredit) -> CachedMovie:
        """Partial upsert discovered via search/person-credits.

        Never overwrites an existing row (a full `get_movie` fetch always
        wins) and never touches `cast_fetched_at` - the movie's cast stays
        un-fetched until `get_movie_cast` runs for it.
        """
        row = self.session.get(CachedMovie, credit["id"])
        if row is not None:
            return row
        try:
            # SAVEPOINT: two concurrent requests can both JIT-discover this
            # exact movie stub for the first time (e.g. the Discovery Hub's
            # cast fetch and candidate-pool fetch racing on the same frontier
            # movie) - fall back to whichever row wins instead of a 500.
            with self.session.begin_nested():
                row = CachedMovie(
                    tmdb_id=credit["id"],
                    title=credit.get("title") or "",
                    release_date=credit.get("release_date"),
                    poster_path=credit.get("poster_path"),
                    original_language=credit.get("original_language"),
                    genre_ids=credit.get("genre_ids") or [],
                    popularity=credit.get("popularity"),
                    vote_average=credit.get("vote_average"),
                    vote_count=credit.get("vote_count"),
                )
                self.session.add(row)
                self.session.flush()
        except IntegrityError:
            row = self.session.get(CachedMovie, credit["id"])
            return row
        facet_store.refresh(self.session, [row.tmdb_id])
        self.session.commit()
        self.session.refresh(row)
        return row

    # --- directors ---

    def get_cached_directors(self, movie_id: int) -> list[CachedMovieDirector] | None:
        """None = never fetched; an empty list = fetched, TMDB lists no director."""
        movie = self.session.get(CachedMovie, movie_id)
        if movie is None or movie.directors_fetched_at is None:
            return None
        return list(
            self.session.exec(
                select(CachedMovieDirector).where(CachedMovieDirector.movie_id == movie_id)
            ).all()
        )

    def upsert_directors(
        self, movie_id: int, directors: list[TMDBDirector]
    ) -> list[CachedMovieDirector]:
        movie = self.session.get(CachedMovie, movie_id)
        if movie is None:
            raise ValueError(f"Movie {movie_id} must be cached before its directors")
        for existing in self.session.exec(
            select(CachedMovieDirector).where(CachedMovieDirector.movie_id == movie_id)
        ).all():
            self.session.delete(existing)
        self.session.flush()
        rows = [
            CachedMovieDirector(
                movie_id=movie_id, person_id=d["id"], name=d["name"], gender=d.get("gender")
            )
            for d in directors
        ]
        self.session.add_all(rows)
        movie.directors_fetched_at = utcnow()
        self.session.add(movie)
        facet_store.invalidate(self.session, [movie_id], ["production"])
        facet_store.refresh(self.session, [movie_id], ["production"])
        self.session.commit()
        return rows

    def get_cached_director_credits(self, person_id: int) -> list[CachedMovie] | None:
        """A director's directed filmography; None = never fetched."""
        if self.session.get(CachedDirector, person_id) is None:
            return None
        statement = (
            select(CachedMovie)
            .join(CachedMovieDirector, CachedMovieDirector.movie_id == CachedMovie.tmdb_id)
            .where(CachedMovieDirector.person_id == person_id)
        )
        return list(self.session.exec(statement).all())

    def upsert_director_credits(
        self, person_id: int, name: str, credits: list[TMDBPersonCredit]
    ) -> list[CachedMovie]:
        """Stores a director's filmography as (movie, director) rows. A stub movie
        gains a partial director row without `directors_fetched_at`, so its full
        director list is still fetched (and replaces the row) when needed."""
        director = self.session.get(CachedDirector, person_id)
        if director is None:
            director = CachedDirector(person_id=person_id, name=name)
            actor = self.session.get(CachedActor, person_id)
            if actor is not None:
                director.deathday = actor.deathday
        director.name = name or director.name
        director.credits_fetched_at = utcnow()

        movies: list[CachedMovie] = []
        for credit in credits:
            movie = self.upsert_movie_stub(credit)
            movies.append(movie)
            if self.session.get(CachedMovieDirector, (movie.tmdb_id, person_id)) is None:
                self.session.add(
                    CachedMovieDirector(movie_id=movie.tmdb_id, person_id=person_id, name=name)
                )
        self.session.add(director)
        affected = [m.tmdb_id for m in movies]
        facet_store.invalidate(self.session, affected, ["production"])
        facet_store.refresh(self.session, affected, ["production"])
        self.session.commit()
        return movies

    # --- key crew (Crew & Craft Trail): composer, cinematographer, writers, director ---

    def get_cached_crew(self, movie_id: int) -> list[CachedCrewCredit] | None:
        """None = never fetched; an empty list = fetched, TMDB lists none of the key crafts."""
        movie = self.session.get(CachedMovie, movie_id)
        if movie is None or movie.crew_fetched_at is None:
            return None
        return list(
            self.session.exec(
                select(CachedCrewCredit)
                .where(CachedCrewCredit.movie_id == movie_id)
                .order_by(CachedCrewCredit.id)
            ).all()
        )

    def upsert_crew(self, movie_id: int, crew: list[TMDBCrewMember]) -> list[CachedCrewCredit]:
        """Replaces a film's key crew with its full, authoritative list."""
        movie = self.session.get(CachedMovie, movie_id)
        if movie is None:
            raise ValueError(f"Movie {movie_id} must be cached before its crew")
        try:
            for existing in self.session.exec(
                select(CachedCrewCredit).where(CachedCrewCredit.movie_id == movie_id)
            ).all():
                self.session.delete(existing)
            self.session.flush()
            rows = [
                CachedCrewCredit(
                    movie_id=movie_id,
                    person_id=member["id"],
                    person_name=member["name"],
                    job=member["job"],
                    department=member.get("department") or CRAFT_JOBS[member["job"]][1],
                    profile_path=member.get("profile_path"),
                )
                for member in crew
                if member.get("job") in CRAFT_JOBS
            ]
            self.session.add_all(rows)
            movie.crew_fetched_at = utcnow()
            self.session.add(movie)
            self.session.commit()
        except IntegrityError:
            # a concurrent request stored the same crew first: use theirs
            self.session.rollback()
            return self.get_cached_crew(movie_id) or []
        return rows

    def get_cached_crew_person(self, person_id: int) -> CachedCrewPerson | None:
        return self.session.get(CachedCrewPerson, person_id)

    def upsert_person_craft_credits(
        self, person_id: int, name: str, credits: list[TMDBCraftCredit]
    ) -> None:
        """Stores a person's key-craft filmography. A stub movie gains partial crew rows without
        `crew_fetched_at`, so its full crew list is still fetched (and replaces them) when needed."""
        person = self.session.get(CachedCrewPerson, person_id) or CachedCrewPerson(
            person_id=person_id, name=name
        )
        person.name = name or person.name
        person.credits_fetched_at = utcnow()
        self.session.add(person)
        for credit in credits:
            job = credit.get("job")
            if job not in CRAFT_JOBS:
                continue
            movie = self.upsert_movie_stub(credit)
            exists = self.session.exec(
                select(CachedCrewCredit).where(
                    CachedCrewCredit.movie_id == movie.tmdb_id,
                    CachedCrewCredit.person_id == person_id,
                    CachedCrewCredit.job == job,
                )
            ).first()
            if exists is None:
                self.session.add(
                    CachedCrewCredit(
                        movie_id=movie.tmdb_id,
                        person_id=person_id,
                        person_name=name,
                        job=job,
                        department=credit.get("department") or CRAFT_JOBS[job][1],
                    )
                )
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()

    def get_cached_person_films(self, person_id: int) -> list[PersonFilm]:
        """Cache-only: every film where this person has a cast credit or a key-craft credit."""
        films: list[PersonFilm] = []
        crew_rows = self.session.exec(
            select(CachedMovie, CachedCrewCredit)
            .join(CachedCrewCredit, CachedCrewCredit.movie_id == CachedMovie.tmdb_id)
            .where(CachedCrewCredit.person_id == person_id)
        ).all()
        for movie, credit in crew_rows:
            role = role_for_job(credit.job)
            if role:
                films.append(PersonFilm(movie, role, credit.job))
        cast_rows = self.session.exec(
            select(CachedMovie, CachedMovieCast)
            .join(CachedMovieCast, CachedMovieCast.movie_id == CachedMovie.tmdb_id)
            .where(CachedMovieCast.actor_id == person_id)
        ).all()
        films += [
            PersonFilm(movie, ROLE_ACTOR, None, cast.character_name) for movie, cast in cast_rows
        ]
        return films

    # --- cast (top-N billing for a given movie) ---

    def get_cast_entry(self, movie_id: int, actor_id: int) -> CastEntry | None:
        """Pure cache read (no TMDB call) - a specific (movie, actor) cast row,
        however it got there (top-billing fetch OR an actor's filmography fetch)."""
        cast_row = self.session.get(CachedMovieCast, (movie_id, actor_id))
        if cast_row is None:
            return None
        actor = self.session.get(CachedActor, actor_id)
        if actor is None:
            return None
        return CastEntry(
            actor_id=actor.tmdb_id,
            name=actor.name,
            profile_path=actor.profile_path,
            character_name=cast_row.character_name,
            cast_order=cast_row.cast_order,
        )

    def get_cached_cast(self, movie_id: int, limit: int) -> list[CastEntry] | None:
        movie = self.session.get(CachedMovie, movie_id)
        if movie is None or movie.cast_fetched_at is None:
            return None
        statement = (
            select(CachedMovieCast, CachedActor)
            .join(CachedActor, CachedMovieCast.actor_id == CachedActor.tmdb_id)
            .where(CachedMovieCast.movie_id == movie_id)
            .order_by(CachedMovieCast.cast_order)
            .limit(limit)
        )
        rows = self.session.exec(statement).all()
        return [
            CastEntry(
                actor_id=actor.tmdb_id,
                name=actor.name,
                profile_path=actor.profile_path,
                character_name=cast.character_name,
                cast_order=cast.cast_order,
            )
            for cast, actor in rows
        ]

    def upsert_cast(
        self, movie_id: int, cast_members: list[TMDBCastMember], limit: int
    ) -> list[CastEntry]:
        top: list[TMDBCastMember] = []
        seen_actor_ids: set[int] = set()
        for member in sorted(cast_members, key=lambda m: m["order"]):
            if member["id"] in seen_actor_ids:
                continue
            seen_actor_ids.add(member["id"])
            top.append(member)
        top = top[:limit]
        entries: list[CastEntry] = []
        for member in top:
            actor = self.session.get(CachedActor, member["id"])
            if actor is None:
                try:
                    # SAVEPOINT: same race as upsert_movie_stub, but for the
                    # actor row - two requests can both JIT-cache this movie's
                    # cast for the first time concurrently.
                    with self.session.begin_nested():
                        actor = CachedActor(
                            tmdb_id=member["id"],
                            name=member["name"],
                            profile_path=member.get("profile_path"),
                        )
                        self.session.add(actor)
                        self.session.flush()
                except IntegrityError:
                    actor = self.session.get(CachedActor, member["id"])
                    actor.name = member["name"]
                    actor.profile_path = member.get("profile_path")
            else:
                actor.name = member["name"]
                actor.profile_path = member.get("profile_path")
            self.session.add(actor)

            cast_row = self.session.get(CachedMovieCast, (movie_id, member["id"]))
            if cast_row is None:
                try:
                    # Concurrent cold-cache requests can both observe this row
                    # as missing. Isolate the insert so the losing request can
                    # recover from the unique constraint without poisoning the
                    # outer transaction.
                    with self.session.begin_nested():
                        cast_row = CachedMovieCast(movie_id=movie_id, actor_id=member["id"])
                        self.session.add(cast_row)
                        self.session.flush()
                except IntegrityError:
                    cast_row = self.session.get(CachedMovieCast, (movie_id, member["id"]))
                    if cast_row is None:
                        raise
            cast_row.cast_order = member["order"]
            cast_row.character_name = member.get("character")
            self.session.add(cast_row)

            entries.append(
                CastEntry(
                    actor_id=member["id"],
                    name=member["name"],
                    profile_path=member.get("profile_path"),
                    character_name=member.get("character"),
                    cast_order=member["order"],
                )
            )

        movie = self.session.get(CachedMovie, movie_id)
        if movie is not None:
            movie.cast_fetched_at = utcnow()
            self.session.add(movie)

        facet_store.invalidate(self.session, [movie_id], ["production"])
        facet_store.refresh(self.session, [movie_id], ["production"])
        self.session.commit()
        return entries

    # --- actor filmography (full, uncapped - a single API call regardless of size) ---

    def get_cached_actor_credits(self, actor_id: int) -> list[CachedMovie] | None:
        actor = self.session.get(CachedActor, actor_id)
        if actor is None or actor.credits_fetched_at is None:
            return None
        statement = (
            select(CachedMovie)
            .join(CachedMovieCast, CachedMovieCast.movie_id == CachedMovie.tmdb_id)
            .where(CachedMovieCast.actor_id == actor_id)
        )
        return list(self.session.exec(statement).all())

    def upsert_actor_credits(
        self, actor_id: int, credits: list[TMDBPersonCredit]
    ) -> list[CachedMovie]:
        actor = self.session.get(CachedActor, actor_id)
        if actor is None:
            # We only ever look up credits for actors already seen via a
            # movie's cast, but fall back to a placeholder name just in case.
            # SAVEPOINT-guarded for the same concurrent-first-JIT-fetch race
            # as upsert_cast/upsert_movie_stub.
            try:
                with self.session.begin_nested():
                    actor = CachedActor(tmdb_id=actor_id, name=f"Unknown actor {actor_id}")
                    self.session.add(actor)
                    self.session.flush()
            except IntegrityError:
                actor = self.session.get(CachedActor, actor_id)
        actor.credits_fetched_at = utcnow()
        self.session.add(actor)

        movies: list[CachedMovie] = []
        for credit in credits:
            movie = self.upsert_movie_stub(credit)
            movies.append(movie)

            cast_row = self.session.get(CachedMovieCast, (credit["id"], actor_id))
            if cast_row is None:
                cast_row = CachedMovieCast(
                    movie_id=credit["id"],
                    actor_id=actor_id,
                    character_name=credit.get("character"),
                )
                self.session.add(cast_row)

        self.session.commit()
        return movies

    # --- genres ---

    def get_cached_genres(self) -> list[CachedGenre]:
        return list(self.session.exec(select(CachedGenre)).all())

    def upsert_genres(self, genres: list[TMDBGenre]) -> list[CachedGenre]:
        rows = []
        for genre in genres:
            row = self.session.get(CachedGenre, genre["id"])
            if row is None:
                row = CachedGenre(id=genre["id"], name=genre["name"])
            else:
                row.name = genre["name"]
            self.session.add(row)
            rows.append(row)
        self.session.commit()
        return rows

    # --- OMDb ratings (JIT, keyed by our own tmdb movie id) ---

    def get_cached_ratings(self, movie_id: int) -> CachedMovieRating | None:
        return self.session.get(CachedMovieRating, movie_id)

    def upsert_ratings(self, movie_id: int, ratings: OMDbRatings | None) -> CachedMovieRating:
        row = self.session.get(CachedMovieRating, movie_id)
        if row is None:
            row = CachedMovieRating(movie_id=movie_id)
        row.imdb_rating = ratings["imdb_rating"] if ratings else None
        row.rotten_tomatoes = ratings["rotten_tomatoes"] if ratings else None
        row.metacritic = ratings["metacritic"] if ratings else None
        row.fetched_at = utcnow()
        self.session.add(row)
        facet_store.invalidate(self.session, [movie_id], ["reception"])
        facet_store.refresh(self.session, [movie_id], ["reception"])
        self.session.commit()
        self.session.refresh(row)
        return row


# --- async read-through orchestration ---


async def get_person(session: Session, tmdb: TMDBClient, person_id: int) -> dict:
    person = await tmdb.get_person(person_id)
    await anyio.to_thread.run_sync(_persist_person, session, person_id, person)
    return person


def _persist_person(session: Session, person_id: int, person: dict) -> None:
    actor = session.get(CachedActor, person_id)
    if actor is None:
        actor = CachedActor(
            tmdb_id=person_id,
            name=person.get("name") or str(person_id),
            profile_path=person.get("profile_path"),
        )
    director = session.get(CachedDirector, person_id)
    # A director row signifies a complete filmography, so never create it from person detail.
    for row in (actor, director):
        if row is not None:
            row.deathday = person.get("deathday")
            session.add(row)
    affected = set(
        session.exec(
            select(CachedMovieCast.movie_id).where(CachedMovieCast.actor_id == person_id)
        ).all()
    ) | set(
        session.exec(
            select(CachedMovieDirector.movie_id).where(CachedMovieDirector.person_id == person_id)
        ).all()
    )
    facet_store.invalidate(session, affected, ["production"])
    facet_store.refresh(session, affected, ["production"])
    session.commit()


async def get_movie(
    session: Session,
    tmdb: TMDBClient,
    tmdb_id: int,
    *,
    refresh: bool = False,
    require_detail: bool = False,
) -> CachedMovie:
    """Read-through detail fetch; `refresh` re-fetches `/movie/{id}` even when
    a row is cached (search/credits stubs carry no overview or runtime).
    `require_detail` refreshes cached stubs that lack origin-country metadata,
    and serves the stub if TMDB is unavailable."""
    repo = CacheRepo(session)
    cached = await anyio.to_thread.run_sync(repo.get_cached_movie, tmdb_id)
    needs_detail = require_detail and cached is not None and cached.origin_country is None
    if cached is not None and not refresh and not needs_detail:
        return cached
    try:
        movie = await tmdb.get_movie(tmdb_id)
    except (TMDBError, httpx.HTTPError):
        if not require_detail or cached is None:
            raise
        logger.warning(
            "TMDB detail unavailable for cached movie %s; using its existing cache row",
            tmdb_id,
            exc_info=True,
        )
        return cached
    return await anyio.to_thread.run_sync(repo.upsert_movie, movie)


async def get_movie_cast(
    session: Session, tmdb: TMDBClient, tmdb_id: int, limit: int | None = None
) -> list[CastEntry]:
    limit = limit or get_settings().pathfinder_cast_limit
    repo = CacheRepo(session)

    # cast rows FK to a cached movie row
    await get_movie(session, tmdb, tmdb_id)

    cached = await anyio.to_thread.run_sync(repo.get_cached_cast, tmdb_id, limit)
    if cached is not None:
        return cached
    cast = await tmdb.get_movie_credits(tmdb_id)
    return await anyio.to_thread.run_sync(repo.upsert_cast, tmdb_id, cast, limit)


async def get_actor_credits(session: Session, tmdb: TMDBClient, actor_id: int) -> list[CachedMovie]:
    repo = CacheRepo(session)
    cached = await anyio.to_thread.run_sync(repo.get_cached_actor_credits, actor_id)
    if cached is not None:
        return cached
    credits_ = await tmdb.get_person_movie_credits(actor_id)
    return await anyio.to_thread.run_sync(repo.upsert_actor_credits, actor_id, credits_)


async def store_stubs(session: Session, stubs: list[TMDBPersonCredit]) -> list[CachedMovie]:
    """Cache credit-shaped search/discover results as stubs (never overwriting a fully
    fetched row). Sequential on purpose: one Session must not be shared across threads."""
    repo = CacheRepo(session)
    return await anyio.to_thread.run_sync(lambda: [repo.upsert_movie_stub(c) for c in stubs])


async def discover_movies(session: Session, tmdb: TMDBClient, **params) -> list[CachedMovie]:
    """Popular films matching raw TMDB discover filters (`pages` bounds the size)."""
    return await store_stubs(session, await tmdb.discover_movies(**params))


async def get_related_movies(session: Session, tmdb: TMDBClient, tmdb_id: int) -> list[CachedMovie]:
    """TMDB recommendations + similar titles for a film."""
    return await store_stubs(session, await tmdb.get_related_movies(tmdb_id))


async def get_genres(session: Session, tmdb: TMDBClient) -> list[CachedGenre]:
    repo = CacheRepo(session)
    cached = await anyio.to_thread.run_sync(repo.get_cached_genres)
    if cached:
        return cached
    genres = await tmdb.get_genres()
    return await anyio.to_thread.run_sync(repo.upsert_genres, genres)


async def get_cast_entry(session: Session, movie_id: int, actor_id: int) -> CastEntry | None:
    """Cache-only lookup - only meaningful after get_movie_cast/get_actor_credits
    has already populated cached_movie_cast for this pair. Never calls TMDB."""
    repo = CacheRepo(session)
    return await anyio.to_thread.run_sync(repo.get_cast_entry, movie_id, actor_id)


async def get_movie_directors(
    session: Session, tmdb: TMDBClient, tmdb_id: int
) -> list[CachedMovieDirector]:
    """Read-through director lookup (one TMDB credits call on a miss)."""
    repo = CacheRepo(session)
    await get_movie(session, tmdb, tmdb_id)  # director rows FK to a cached movie
    cached = await anyio.to_thread.run_sync(repo.get_cached_directors, tmdb_id)
    if cached is not None:
        return cached
    directors = await tmdb.get_movie_directors(tmdb_id)
    return await anyio.to_thread.run_sync(repo.upsert_directors, tmdb_id, directors)


async def get_director_credits(
    session: Session, tmdb: TMDBClient, person_id: int, name: str = ""
) -> list[CachedMovie]:
    """Read-through "films this person directed" (one TMDB call on a miss)."""
    repo = CacheRepo(session)
    cached = await anyio.to_thread.run_sync(repo.get_cached_director_credits, person_id)
    if cached is not None:
        return cached
    credits_ = await tmdb.get_person_directed_credits(person_id)
    return await anyio.to_thread.run_sync(repo.upsert_director_credits, person_id, name, credits_)


async def get_movie_crew(
    session: Session, tmdb: TMDBClient, tmdb_id: int
) -> list[CachedCrewCredit]:
    """Read-through key crew of a film (composer, cinematographer, writers, director): served
    from `cached_crew_credits`, one TMDB credits call on a miss."""
    repo = CacheRepo(session)
    await get_movie(session, tmdb, tmdb_id)  # crew rows FK to a cached movie
    cached = await anyio.to_thread.run_sync(repo.get_cached_crew, tmdb_id)
    if cached is not None:
        return cached
    crew = await tmdb.get_movie_crew(tmdb_id)
    return await anyio.to_thread.run_sync(repo.upsert_crew, tmdb_id, crew)


async def get_person_filmography(
    session: Session, tmdb: TMDBClient, person_id: int, name: str = ""
) -> list[PersonFilm]:
    """Read-through "every film this person acted in or held a key craft on", from one TMDB call
    on a miss. Cast credits land in the existing actor tables, craft credits in the crew table."""
    repo = CacheRepo(session)
    actor = await anyio.to_thread.run_sync(session.get, CachedActor, person_id)
    needs_cast = actor is None or actor.credits_fetched_at is None
    needs_crew = await anyio.to_thread.run_sync(repo.get_cached_crew_person, person_id) is None
    if needs_cast or needs_crew:
        cast, craft = await tmdb.get_person_craft_credits(person_id)
        if needs_cast:
            await anyio.to_thread.run_sync(repo.upsert_actor_credits, person_id, cast)
            if name:
                await anyio.to_thread.run_sync(_name_placeholder_actor, session, person_id, name)
        if needs_crew:
            await anyio.to_thread.run_sync(repo.upsert_person_craft_credits, person_id, name, craft)
    return await anyio.to_thread.run_sync(repo.get_cached_person_films, person_id)


def _name_placeholder_actor(session: Session, person_id: int, name: str) -> None:
    actor = session.get(CachedActor, person_id)
    if actor is not None and actor.name.startswith("Unknown actor"):
        actor.name = name
        session.add(actor)
        session.commit()


async def get_movie_ratings(
    session: Session, tmdb: TMDBClient, omdb: OMDbClient, tmdb_id: int, force: bool = False
) -> CachedMovieRating | None:
    """JIT read-through for OMDb ratings. Returns None (no HTTP call at all)
    whenever OMDb isn't configured, so this is a no-op for installs without an
    OMDb key - never blocks/slows down a plain movie-detail fetch.
    Raises BudgetExhausted before HTTP when the daily provider quota is spent."""
    if not omdb.enabled:  # cheap short-circuit, avoids a wasted movie lookup
        return None
    repo = CacheRepo(session)
    cached = await anyio.to_thread.run_sync(repo.get_cached_ratings, tmdb_id)
    if cached is not None and not force:
        if any((cached.imdb_rating, cached.rotten_tomatoes, cached.metacritic)):
            return cached
        now = utcnow()
        fetched_at = cached.fetched_at
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=UTC)
        if now - fetched_at <= RATINGS_NEGATIVE_TTL:
            return cached
    movie = await get_movie(session, tmdb, tmdb_id)
    if not movie.imdb_id and movie.origin_country is None:
        movie = await get_movie(session, tmdb, tmdb_id, require_detail=True)
    from app.services.provider_budgets import (
        BudgetExhausted,
        ProviderLimitReachedError,
        mark_exhausted,
        reserve,
    )

    if not await anyio.to_thread.run_sync(reserve, session.get_bind(), "omdb"):
        raise BudgetExhausted(
            "OMDb daily limit or soft cap reached; check Data Spa settings or try again tomorrow"
        )
    try:
        lookup = (
            await omdb.lookup_by_imdb_id(movie.imdb_id)
            if movie.imdb_id
            else await omdb.lookup_by_title(movie.title, parse_release_year(movie.release_date))
        )
    except ProviderLimitReachedError:
        await anyio.to_thread.run_sync(mark_exhausted, session.get_bind(), "omdb")
        raise
    if lookup.transient:
        return cached
    return await anyio.to_thread.run_sync(repo.upsert_ratings, tmdb_id, lookup.ratings)
