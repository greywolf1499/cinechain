"""Inline, transactional projections. This module never commits or calls providers."""

from collections.abc import Callable, Iterable

from sqlmodel import Session, col, delete, select

from app.facets import lexical, production, reception
from app.facets.models import MovieFacet, MovieFacetStatus
from app.facets.registry import CATALOGUE, FAMILY_VERSIONS, FacetValue
from app.models.cache import (
    CachedActor,
    CachedDirector,
    CachedMovie,
    CachedMovieCast,
    CachedMovieDirector,
    CachedMovieRating,
)

EMPTY_SET = "__empty_set__"


def invalidate(session: Session, movie_ids: Iterable[int], families: Iterable[str]) -> None:
    ids, families = list(movie_ids), list(families)
    session.exec(
        delete(MovieFacetStatus).where(
            col(MovieFacetStatus.movie_id).in_(ids), col(MovieFacetStatus.family).in_(families)
        )
    )
    session.info.pop("bounty_feasibility", None)


def delete_movies(session: Session, movie_ids: Iterable[int]) -> None:
    ids = list(movie_ids)
    session.exec(delete(MovieFacet).where(col(MovieFacet.movie_id).in_(ids)))
    session.exec(delete(MovieFacetStatus).where(col(MovieFacetStatus.movie_id).in_(ids)))
    session.info.pop("bounty_feasibility", None)


def movie_facts(session: Session, movies: list[CachedMovie]) -> dict[int, dict[str, FacetValue]]:
    ids = [m.tmdb_id for m in movies]
    ratings = {
        r.movie_id: r
        for r in session.exec(
            select(CachedMovieRating).where(col(CachedMovieRating.movie_id).in_(ids))
        ).all()
    }
    directors: dict[int, list[CachedMovieDirector]] = {}
    for row in session.exec(
        select(CachedMovieDirector).where(col(CachedMovieDirector.movie_id).in_(ids))
    ).all():
        directors.setdefault(row.movie_id, []).append(row)
    person_ids = {d.person_id for rows in directors.values() for d in rows}
    people = {
        p.person_id: p
        for p in session.exec(
            select(CachedDirector).where(col(CachedDirector.person_id).in_(person_ids))
        ).all()
    }
    filmographies: dict[int, list[CachedMovie]] = {}
    for movie, credit in session.exec(
        select(CachedMovie, CachedMovieDirector)
        .join(CachedMovieDirector, col(CachedMovieDirector.movie_id) == CachedMovie.tmdb_id)
        .where(col(CachedMovieDirector.person_id).in_(person_ids))
    ).all():
        if credit.person_id in people:
            filmographies.setdefault(credit.person_id, []).append(movie)
    cast: dict[int, list[str | None]] = {}
    for credit, actor in session.exec(
        select(CachedMovieCast, CachedActor)
        .join(CachedActor, col(CachedActor.tmdb_id) == CachedMovieCast.actor_id)
        .where(
            col(CachedMovieCast.movie_id).in_(ids),
            col(CachedMovieCast.cast_order) >= 0,
            col(CachedMovieCast.cast_order) < 5,
        )
    ).all():
        cast.setdefault(credit.movie_id, []).append(actor.deathday)
    result = {}
    for movie in movies:
        movie_directors = directors.get(movie.tmdb_id, [])
        genders = [d.gender for d in movie_directors]
        debut, index = production.director_indexes(
            movie,
            [
                filmographies.get(d.person_id, []) if d.person_id in people else None
                for d in movie_directors
            ],
        )
        deathdays = cast.get(movie.tmdb_id, []) + [
            people[d.person_id].deathday if d.person_id in people else None for d in movie_directors
        ]
        result[movie.tmdb_id] = {
            **lexical.evaluate(movie.title),
            **production.evaluate(movie),
            **reception.evaluate(movie, ratings.get(movie.tmdb_id)),
            "female_director": True
            if 1 in genders
            else None
            if not genders or None in genders
            else False,
            "director_debut": debut,
            "director_film_index": index,
            "posthumous_release": production.posthumous(
                movie.release_date,
                deathdays,
                movie.cast_fetched_at is not None and movie.directors_fetched_at is not None,
            ),
        }
    return result


def refresh(
    session: Session,
    movie_ids: Iterable[int],
    families: Iterable[str] = FAMILY_VERSIONS,
    *,
    check_cancelled: Callable[[], None] | None = None,
) -> None:
    ids, families = list(dict.fromkeys(movie_ids)), set(families)
    if families - FAMILY_VERSIONS.keys():
        raise ValueError(f"Unknown facet families: {sorted(families - FAMILY_VERSIONS.keys())}")
    session.flush()
    statuses = {
        (s.movie_id, s.family): s
        for s in session.exec(
            select(MovieFacetStatus).where(col(MovieFacetStatus.movie_id).in_(ids))
        ).all()
    }
    stale = {
        movie_id: {
            family
            for family in families
            if (status := statuses.get((movie_id, family))) is None
            or status.version != FAMILY_VERSIONS[family]
            or status.status != "ok"
        }
        for movie_id in ids
    }
    movies = list(
        session.exec(
            select(CachedMovie).where(col(CachedMovie.tmdb_id).in_([i for i in ids if stale[i]]))
        ).all()
    )
    if not movies:
        return
    facts = movie_facts(session, movies)
    for movie in movies:
        if check_cancelled is not None:
            check_cancelled()
        for family in stale[movie.tmdb_id]:
            facet_ids = [f.id for f in CATALOGUE.values() if f.family == family and not f.relative]
            session.exec(
                delete(MovieFacet).where(
                    col(MovieFacet.movie_id) == movie.tmdb_id,
                    col(MovieFacet.facet_id).in_(facet_ids),
                )
            )
            for facet_id in facet_ids:
                value = facts[movie.tmdb_id].get(facet_id)
                if value is None:
                    continue
                values = value if isinstance(value, list) else [value]
                if not values:
                    session.add(
                        MovieFacet(movie_id=movie.tmdb_id, facet_id=facet_id, value_text=EMPTY_SET)
                    )
                for item in set(values):
                    session.add(
                        MovieFacet(
                            movie_id=movie.tmdb_id,
                            facet_id=facet_id,
                            value_text=item if isinstance(item, str) else "",
                            value_num=float(item) if not isinstance(item, str) else 0,
                        )
                    )
            session.merge(
                MovieFacetStatus(
                    movie_id=movie.tmdb_id,
                    family=family,
                    version=FAMILY_VERSIONS[family],
                    status="ok",
                )
            )
    session.flush()


def stored_values(
    session: Session, movie_ids: Iterable[int], facets: Iterable[str] | None = None
) -> dict[int, dict[str, FacetValue]]:
    """Read-only decode of current stored facets; never refreshes or computes.

    Only facets whose family status is ``ok`` at the current version are returned; a known
    empty set is ``[]`` and a current family without a row is ``None``. Relative facets and
    stale/missing families are omitted, so callers can fall back to live evaluation."""
    ids = list(dict.fromkeys(movie_ids))
    wanted = [
        f
        for f in (CATALOGUE.values() if facets is None else (CATALOGUE[i] for i in facets))
        if not f.relative
    ]
    if not ids or not wanted:
        return {}
    current = {
        (s.movie_id, s.family)
        for s in session.exec(
            select(MovieFacetStatus).where(
                col(MovieFacetStatus.movie_id).in_(ids), MovieFacetStatus.status == "ok"
            )
        ).all()
        if s.version == FAMILY_VERSIONS[s.family]
    }
    result: dict[int, dict[str, FacetValue]] = {
        movie_id: {f.id: None for f in wanted if (movie_id, f.family) in current}
        for movie_id in ids
    }
    rows = session.exec(
        select(MovieFacet).where(
            col(MovieFacet.movie_id).in_(ids), col(MovieFacet.facet_id).in_([f.id for f in wanted])
        )
    ).all()
    for row in rows:
        values = result.get(row.movie_id)
        if values is None or row.facet_id not in values:
            continue
        kind = CATALOGUE[row.facet_id].kind
        if row.value_text == EMPTY_SET:
            values[row.facet_id] = []
            continue
        base = kind.removeprefix("set_")
        item: FacetValue = (
            row.value_text
            if base == "cat"
            else bool(row.value_num)
            if base == "bool"
            else int(row.value_num)
            if float(row.value_num).is_integer()
            else row.value_num
        )
        if kind.startswith("set_"):
            existing = values[row.facet_id]
            values[row.facet_id] = sorted([*(existing if isinstance(existing, list) else []), item])
        else:
            values[row.facet_id] = item
    return {movie_id: values for movie_id, values in result.items() if values}
