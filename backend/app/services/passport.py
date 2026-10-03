"""Passport aggregation: one user's watch history across *all* runs.

Reads `RunStep` rows with `logged_by_user_id == user` and `status == "watched"`
(imported Letterboxd history lives on the hidden `import` run, so it is included
like any other run) and joins the local director cache - no TMDB calls here.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict

from sqlmodel import Session, col, func, select

from app.models.cache import CachedMovie, CachedMovieDirector
from app.models.run import RunStep
from app.schemas.passport import (
    CountryCount,
    DirectorCount,
    DirectorCoverage,
    PassportOut,
)

TOP_DIRECTORS = 5

# TMDB still emits a few historical codes for older films. Fold them into a
# successor so every code exists on a modern SVG map (approximate by design).
LEGACY_COUNTRY_CODES = {
    "SU": "RU",  # Soviet Union
    "YU": "RS",  # Yugoslavia
    "CS": "RS",  # Serbia and Montenegro
    "XC": "CZ",  # Czechoslovakia
    "DD": "DE",  # East Germany
    "AN": "CW",  # Netherlands Antilles
}
_CODE_RE = re.compile(r"^[A-Z]{2}$")


def parse_country_codes(raw: str | None) -> list[tuple[str, str | None]]:
    """`RunStep.movie_origin_country` (a JSON array string, but legacy rows may hold a
    bare code or "US, GB") -> unique `(normalized_code, merged_from_legacy_code)` pairs."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        values = parsed if isinstance(parsed, list) else [parsed]
    except (TypeError, ValueError):
        values = re.split(r"[,;|/\s]+", raw)

    seen: dict[str, str | None] = {}
    for value in values:
        code = str(value).strip().upper()
        legacy = None
        if code in LEGACY_COUNTRY_CODES:
            legacy, code = code, LEGACY_COUNTRY_CODES[code]
        if _CODE_RE.match(code) and code not in seen:
            seen[code] = legacy
    return list(seen.items())


def decade_label(year: int) -> str:
    return f"{(year // 10) * 10}s"


def build_passport(session: Session, user_id: str) -> PassportOut:
    watched = (RunStep.logged_by_user_id == user_id) & (RunStep.status == "watched")

    total_watches = session.exec(select(func.count()).select_from(RunStep).where(watched)).one()
    # One row per distinct film; the metadata is identical across a film's steps.
    films = session.exec(
        select(
            RunStep.movie_id,
            func.min(RunStep.movie_release_year),
            func.min(RunStep.movie_origin_country),
        )
        .where(watched)
        .group_by(RunStep.movie_id)
    ).all()

    decades: Counter[int] = Counter()
    country_counts: Counter[str] = Counter()
    merged: dict[str, set[str]] = defaultdict(set)
    for _movie_id, year, origin in films:
        if year:
            decades[(year // 10) * 10] += 1
        for code, legacy in parse_country_codes(origin):
            country_counts[code] += 1
            if legacy:
                merged[code].add(legacy)

    watched_movie_ids = select(RunStep.movie_id).where(watched)
    films_per_director = func.count(func.distinct(CachedMovieDirector.movie_id))
    director_rows = session.exec(
        select(CachedMovieDirector.person_id, func.min(CachedMovieDirector.name), films_per_director)
        .where(col(CachedMovieDirector.movie_id).in_(watched_movie_ids))
        .group_by(CachedMovieDirector.person_id)
        .order_by(films_per_director.desc(), func.min(CachedMovieDirector.name))
        .limit(TOP_DIRECTORS)
    ).all()

    with_directors = session.exec(
        select(func.count(func.distinct(CachedMovie.tmdb_id))).where(
            col(CachedMovie.tmdb_id).in_(watched_movie_ids),
            col(CachedMovie.directors_fetched_at).is_not(None),
        )
    ).one()

    return PassportOut(
        total_movies_watched=len(films),
        total_watches=int(total_watches),
        decades_distribution={decade_label(d): n for d, n in sorted(decades.items())},
        countries=[
            CountryCount(code=code, count=n, merged_from=sorted(merged.get(code, ())))
            for code, n in sorted(country_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        top_directors=[
            DirectorCount(person_id=pid, name=name, count=int(n)) for pid, name, n in director_rows
        ],
        directors_coverage=DirectorCoverage(
            movies_with_directors=int(with_directors), movies_total=len(films)),
    )


def movie_ids_missing_directors(session: Session, user_id: str) -> list[int]:
    """Watched films whose directors were never looked up (or are not cached at all)."""
    watched = (RunStep.logged_by_user_id == user_id) & (RunStep.status == "watched")
    done = select(CachedMovie.tmdb_id).where(col(CachedMovie.directors_fetched_at).is_not(None))
    return list(session.exec(
        select(RunStep.movie_id).where(watched, col(RunStep.movie_id).not_in(done))
        .group_by(RunStep.movie_id)
    ).all())
