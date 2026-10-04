"""The Auteur Marathon: watch a director's feature films in release order.

At creation the director's credits (`job == "Director"`) are curated into a *filmography* stored in
`rules_config["filmography"]` (oldest first); `rules_config["director"]` is `{"id", "name"}`. A step
must be a film on the filmography (a hard rule) and follow the previous one along it: later on the
list, skipping at most `max_skip` (default 1) films - breaking the order is a soft violation, so only
a wildcard can buy it. Logging the last film completes the run.

Only feature-length narrative films count: shorts (under 40 minutes), documentaries, TV movies,
videos (music videos...), adult titles and unreleased or undated films are left out.
"""

from __future__ import annotations

import re
import time
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

from app.engines.base import RunSetupError
from app.engines.conditions import RunOutcome
from app.engines.trackers import TrackerEngine
from app.models.cache import CachedMovie
from app.models.run import (
    LEGACY_ENGINE_VERSION,
    RUN_STATUS_ACTIVE,
    RUN_STATUS_COMPLETED,
    Run,
    RunStep,
)
from app.schemas.engine import ValidationResult
from app.services import cache_repo
from app.services.tmdb import TMDBError
from app.services.tmdb_backoff import DeadlineReached, fetch_with_backoff

AUTEUR_MARATHON = "auteur_marathon"
DIRECTOR_ID_KEY = "director_id"
MAX_SKIP_KEY = "max_skip"
DEFAULT_MAX_SKIP = 1
MAX_SKIP_LIMIT = 10

DIRECTOR_JOB = "Director"
MIN_FEATURE_RUNTIME = 40  # minutes; the usual shorts / features boundary
HYDRATE_SECONDS = 60.0
DOCUMENTARY, TV_MOVIE = 99, 10770
_EXCLUDED_GENRES = {DOCUMENTARY, TV_MOVIE}


def _is_candidate(entry: dict, today: date) -> bool:
    release = entry.get("release_date") or ""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", release) or release > today.isoformat():
        return False
    if entry.get("job") != DIRECTOR_JOB or entry.get("id") is None:
        return False
    if entry.get("adult") or entry.get("video"):
        return False
    return not _EXCLUDED_GENRES & set(entry.get("genre_ids") or [])


def _is_feature(runtime: int | None, genre_ids: Sequence[int], votes: int) -> bool:
    if _EXCLUDED_GENRES & set(genre_ids):
        return False
    if runtime:
        return runtime >= MIN_FEATURE_RUNTIME
    return votes > 0  # no runtime on file: keep only films somebody has rated


def build_filmography(
    credits: Sequence[dict[str, Any]],
    runtimes: dict[int, tuple[int | None, Sequence[int]]] | None = None,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Curates TMDB crew credits into the chronological filmography (see the module docstring).

    `runtimes` maps a film id to its `(runtime, genre_ids)` from the film's full detail."""
    today = today or datetime.now(UTC).date()
    runtimes = runtimes or {}
    seen: set[int] = set()
    films: list[dict[str, Any]] = []
    for entry in credits:
        if not _is_candidate(entry, today) or entry["id"] in seen:
            continue
        seen.add(entry["id"])
        runtime, genres = runtimes.get(entry["id"], (None, entry.get("genre_ids") or []))
        if not _is_feature(runtime, genres, int(entry.get("vote_count") or 0)):
            continue
        films.append({
            "movie_id": entry["id"], "title": entry.get("title") or "",
            "release_date": entry["release_date"], "year": int(entry["release_date"][:4]),
            "poster_path": entry.get("poster_path"), "runtime": runtime or None})
    if not films:
        raise RunSetupError("This person has no feature films as a director to build a marathon from")
    return sorted(films, key=lambda f: (f["release_date"], f["movie_id"]))


class AuteurMarathonEngine(TrackerEngine):
    game_type = AUTEUR_MARATHON
    display_name = "The Auteur Marathon"
    description = (
        "Pick a director and work through their feature films in release order, from the first "
        "film to the last."
    )

    def validate_rules_config(self, rules: dict | None) -> list[str]:
        problems = super().validate_rules_config(rules)
        rules = rules or {}
        director_id = rules.get(DIRECTOR_ID_KEY)
        if (
            (isinstance(director_id, bool) or not isinstance(director_id, int) or director_id < 1)
            and not isinstance((rules.get("director") or {}).get("id"), int)
        ):
            problems.append(f"{DIRECTOR_ID_KEY} is required (search for a director)")
        skip = rules.get(MAX_SKIP_KEY)
        if skip is not None and (
            isinstance(skip, bool) or not isinstance(skip, int) or not 0 <= skip <= MAX_SKIP_LIMIT
        ):
            problems.append(f"{MAX_SKIP_KEY} must be a whole number from 0 to {MAX_SKIP_LIMIT}")
        return problems

    async def prepare_run(self, rules: dict, user_id: str) -> dict:
        director_id = rules[DIRECTOR_ID_KEY]
        try:
            person = await self.tmdb.get_person(director_id)
            credits = await self.tmdb.get_person_crew_credits_raw(director_id)
            candidates = [c for c in credits if _is_candidate(c, datetime.now(UTC).date())]
            details = await self._details(candidates)
        except TMDBError as exc:
            if exc.status_code == 404:
                raise RunSetupError(f"No person with TMDB id {director_id}") from exc
            raise RunSetupError(f"TMDB lookup failed: {exc}", 502) from exc
        filmography = build_filmography(candidates, details)
        rest = {k: v for k, v in rules.items() if k != DIRECTOR_ID_KEY}
        return {
            **rest, MAX_SKIP_KEY: rules.get(MAX_SKIP_KEY, DEFAULT_MAX_SKIP),
            "director": {"id": director_id, "name": person.get("name") or str(director_id)},
            "filmography": filmography,
        }

    async def _details(
        self, candidates: Sequence[dict]
    ) -> dict[int, tuple[int | None, Sequence[int]]]:
        """Runtime and genres of every candidate film, from the cache or TMDB's film detail
        (credits carry neither a runtime nor the shorts' tell-tale)."""
        deadline = time.monotonic() + HYDRATE_SECONDS
        details: dict[int, tuple[int | None, Sequence[int]]] = {}
        for entry in candidates:
            movie_id = entry["id"]
            if movie_id in details:
                continue
            row = self.session.get(CachedMovie, movie_id)
            if row is None or row.runtime is None:
                try:
                    row = await fetch_with_backoff(
                        lambda movie_id=movie_id: cache_repo.get_movie(
                            self.session, self.tmdb, movie_id, refresh=True), deadline) or row
                except DeadlineReached:
                    raise RunSetupError(
                        "TMDB is rate-limiting us - wait a minute and try again", 503) from None
            if row is not None and row.runtime is not None:
                details[movie_id] = (row.runtime, row.genre_ids or entry.get("genre_ids") or [])
        return details

    # --- the release order ---

    @staticmethod
    def _positions(rules: dict | None) -> dict[int, int]:
        return {f["movie_id"]: i for i, f in enumerate((rules or {}).get("filmography") or [])}

    @staticmethod
    def _max_skip(rules: dict | None) -> int:
        skip = (rules or {}).get(MAX_SKIP_KEY)
        return skip if isinstance(skip, int) and not isinstance(skip, bool) and skip >= 0 else DEFAULT_MAX_SKIP

    def _title(self, movie_id: int) -> str:
        row = self.session.get(CachedMovie, movie_id)
        return row.title if row is not None else f"Film {movie_id}"

    def _check(self, rules: dict | None, previous_id: int | None, movie_id: int) -> ValidationResult:
        positions = self._positions(rules)
        filmography = (rules or {}).get("filmography") or []
        director = ((rules or {}).get("director") or {}).get("name", "this director")
        if movie_id not in positions:
            return ValidationResult(
                valid=False, blocked=True,
                reason=f"Off the filmography: {self._title(movie_id)} isn't a feature by {director}")
        position = positions[movie_id]
        before = positions.get(previous_id, -1) if previous_id is not None else -1
        title = filmography[position]["title"]
        if position <= before:
            return ValidationResult(
                valid=False, reason=f"Release order: {title} came before the film you just watched")
        skipped = position - before - 1
        if skipped > self._max_skip(rules):
            return ValidationResult(
                valid=False,
                reason=(f"Release order: {title} skips {skipped} films - at most "
                        f"{self._max_skip(rules)} may be skipped"))
        return ValidationResult(valid=True)

    async def validate_candidate(self, movie_id: int, rules: dict) -> ValidationResult:
        return self._check(rules, None, movie_id)

    async def validate_primary(
        self,
        from_movie_id: int,
        to_movie_id: int,
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
    ) -> ValidationResult:
        return self._check(rules, from_movie_id, to_movie_id)

    def evaluate_run_outcome(self, run: Run, steps: list[RunStep]) -> RunOutcome | None:
        if run.engine_version <= LEGACY_ENGINE_VERSION or run.status != RUN_STATUS_ACTIVE:
            return None
        rules = run.rules_config or {}
        filmography = rules.get("filmography") or []
        if filmography and any(step.movie_id == filmography[-1]["movie_id"] for step in steps):
            name = (rules.get("director") or {}).get("name", "the director")
            return RunOutcome(RUN_STATUS_COMPLETED, f"Completed the works of {name}!")
        return super().evaluate_run_outcome(run, steps)
