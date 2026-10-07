"""Authenticated, cache-only catalogue and bounded query previews."""

import sqlite3
import time
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from app.api.deps import get_current_user, run_participant_guard
from app.db import get_session
from app.facets.query import FacetQuery, compile, count, universe_ids
from app.facets.registry import CATALOGUE, FAMILY_VERSIONS
from app.models.user import User

router = APIRouter(prefix="/facets", tags=["facets"])
QUERY_SECONDS = 2.0


@contextmanager
def query_deadline(session: Session):
    deadline = time.monotonic() + QUERY_SECONDS
    connection = session.connection().connection.driver_connection
    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("Facet queries require a SQLite connection")
    connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    try:
        yield
        if time.monotonic() >= deadline:
            raise HTTPException(503, "Facet query exceeded its two-second deadline")
    except OperationalError as exc:
        if isinstance(exc.orig, sqlite3.OperationalError) and "interrupted" in str(exc.orig):
            raise HTTPException(503, "Facet query exceeded its two-second deadline") from exc
        raise
    finally:
        connection.set_progress_handler(None, 0)


class CountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: FacetQuery
    run_id: str | None = None


@router.get("")
def catalogue(
    session: Session = Depends(get_session), _user: User = Depends(get_current_user)
) -> dict:
    with query_deadline(session):
        total = session.execute(text("SELECT COUNT(*) FROM cached_movies")).scalar_one()
        facets = []
        for facet in CATALOGUE.values():
            if facet.relative:
                column = "vote_count" if facet.id == "vote_count_band" else "popularity"
                known = session.execute(
                    text(f"SELECT COUNT({column}) FROM cached_movies")
                ).scalar_one()
            else:
                known = session.execute(
                    text("""SELECT COUNT(DISTINCT f.movie_id)
                    FROM movie_facets AS f JOIN movie_facet_status AS s ON s.movie_id=f.movie_id
                    WHERE f.facet_id=:facet AND s.family=:family AND s.version=:version AND s.status='ok'"""),
                    {
                        "facet": facet.id,
                        "family": facet.family,
                        "version": FAMILY_VERSIONS[facet.family],
                    },
                ).scalar_one()
            facets.append(
                {
                    "id": facet.id,
                    "label": facet.label,
                    "emoji": facet.emoji,
                    "kind": facet.kind,
                    "family": facet.family,
                    "tier": facet.tier,
                    "version": facet.version,
                    "ops": facet.ops,
                    "relative": facet.relative,
                    "named_variants": {},
                    "known": known,
                    "coverage": 100 * known / total if total else 0,
                }
            )
    return {"facets": facets, "cached_movies": total}


@router.post("/count")
def preview(
    body: CountRequest,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    universe = None
    if body.run_id:
        run_participant_guard(body.run_id, session, user)
        # Preview the cache still available to the run, excluding its queued/watched films.
        universe = """SELECT tmdb_id AS movie_id FROM cached_movies WHERE tmdb_id NOT IN
            (SELECT movie_id FROM run_steps WHERE run_id=:run_id)"""
    with query_deadline(session):
        if universe:
            ids = session.execute(text(universe), {"run_id": body.run_id}).scalars().all()
        else:
            ids = session.execute(text("SELECT tmdb_id FROM cached_movies")).scalars().all()
        result = count(session, body.query, ids)
        sql, extra = universe_ids(ids)
        compiled, params = compile(body.query, sql)
        samples = (
            session.execute(
                text(f"""SELECT m.tmdb_id,m.title FROM ({compiled}) AS q
            JOIN cached_movies AS m ON m.tmdb_id=q.movie_id WHERE q.verdict IS TRUE
            ORDER BY m.tmdb_id LIMIT 6"""),
                {**params, **extra},
            )
            .mappings()
            .all()
        )
    return {**result, "sample": [dict(row) for row in samples]}
