"""The Regional Deep Dive: conquer one slice of a canon list - one country's and/or one decade's films.

At creation the chosen curated list (`curated_list_id`, e.g. Sight & Sound 2022) is sliced by
`target_country` (an ISO 3166-1 alpha-2 code, e.g. "JP") and/or `target_decade` (e.g. 1970) into an
*expedition checklist*: every film on the list matching the filters. It lives in
`rules_config["expedition"]` = `{"list_id", "list_title", "badge_prefix", "badge_color", "country",
"country_name", "decade", "movie_ids", "films"}`. A step must be on the checklist (a hard rule);
watching the whole checklist wins the run.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

import anyio
from sqlmodel import col, select

from app.engines.base import RunSetupError
from app.engines.conditions import RunOutcome
from app.engines.trackers import MIN_DECADE, TrackerEngine
from app.models.cache import CachedMovie
from app.models.curated import CanonMovieBadge, CuratedList
from app.models.run import (
    LEGACY_ENGINE_VERSION,
    RUN_STATUS_ACTIVE,
    RUN_STATUS_COMPLETED,
    Run,
    RunStep,
)
from app.schemas.engine import ValidationResult
from app.services.bridge_paths import parse_countries
from app.services.cache_repo import CacheRepo
from app.services.tmdb import TMDBError
from app.services.tmdb_backoff import DeadlineReached, fetch_with_backoff
from app.utils.dates import parse_release_year

REGIONAL_DEEP_DIVE = "regional_deep_dive"
LIST_ID_KEY = "curated_list_id"
COUNTRY_KEY = "target_country"
DECADE_KEY = "target_decade"
EXPEDITION_KEY = "expedition"

HYDRATE_SECONDS = 90.0
HYDRATE_BATCH = 20

COUNTRY_NAMES = {
    "AR": "Argentina",
    "AT": "Austria",
    "AU": "Australia",
    "BE": "Belgium",
    "BR": "Brazil",
    "CA": "Canada",
    "CH": "Switzerland",
    "CL": "Chile",
    "CN": "China",
    "CO": "Colombia",
    "CU": "Cuba",
    "CZ": "Czechia",
    "DE": "Germany",
    "DK": "Denmark",
    "EG": "Egypt",
    "ES": "Spain",
    "FI": "Finland",
    "FR": "France",
    "GB": "United Kingdom",
    "GR": "Greece",
    "HK": "Hong Kong",
    "HU": "Hungary",
    "IE": "Ireland",
    "IL": "Israel",
    "IN": "India",
    "IR": "Iran",
    "IS": "Iceland",
    "IT": "Italy",
    "JP": "Japan",
    "KR": "South Korea",
    "MX": "Mexico",
    "NL": "Netherlands",
    "NO": "Norway",
    "NZ": "New Zealand",
    "PH": "Philippines",
    "PL": "Poland",
    "PT": "Portugal",
    "RO": "Romania",
    "RS": "Serbia",
    "RU": "Russia",
    "SE": "Sweden",
    "SN": "Senegal",
    "SU": "Soviet Union",
    "TH": "Thailand",
    "TR": "Turkey",
    "TW": "Taiwan",
    "UA": "Ukraine",
    "US": "United States",
    "VN": "Vietnam",
    "XC": "Czechoslovakia",
    "YU": "Yugoslavia",
    "ZA": "South Africa",
}


def country_name(code: str | None) -> str | None:
    return COUNTRY_NAMES.get(code, code) if code else None


def _decade_of(release_date: str | None) -> int | None:
    year = parse_release_year(release_date)
    return year // 10 * 10 if year else None


class RegionalDeepDiveEngine(TrackerEngine):
    game_type = REGIONAL_DEEP_DIVE
    display_name = "Regional Deep Dive"
    description = (
        "Slice a canon list by country and/or decade - say Japan on Sight & Sound, or the 1970s "
        "on the Letterboxd Top 250 - and conquer every film in the slice."
    )

    def validate_rules_config(self, rules: dict | None) -> list[str]:
        problems = super().validate_rules_config(rules)
        rules = rules or {}
        if not isinstance(rules.get(LIST_ID_KEY), str) or not rules[LIST_ID_KEY]:
            problems.append(f"{LIST_ID_KEY} is required (pick a curated list)")
        country, decade = rules.get(COUNTRY_KEY), rules.get(DECADE_KEY)
        if country is not None and (
            not isinstance(country, str) or len(country) != 2 or not country.isalpha()
        ):
            problems.append(f"{COUNTRY_KEY} must be a 2-letter country code (e.g. JP)")
        latest = datetime.now(UTC).year // 10 * 10
        if decade is not None and (
            isinstance(decade, bool)
            or not isinstance(decade, int)
            or decade % 10
            or not MIN_DECADE <= decade <= latest
        ):
            problems.append(
                f"{DECADE_KEY} must be a decade start between {MIN_DECADE} and {latest}"
            )
        if country is None and decade is None:
            problems.append(f"Pick a {COUNTRY_KEY} and/or a {DECADE_KEY} to slice the list by")
        return problems

    async def prepare_run(self, rules: dict, user_id: str) -> dict:
        curated = self.session.get(CuratedList, rules[LIST_ID_KEY])
        if curated is None:
            raise RunSetupError("Unknown curated list")
        if not curated.is_enabled:
            raise RunSetupError(f"{curated.title} isn't enabled - enable it in Settings first")
        country = (rules.get(COUNTRY_KEY) or "").upper() or None
        decade = rules.get(DECADE_KEY)

        badges = {}
        for badge in self.session.exec(
            select(CanonMovieBadge).where(CanonMovieBadge.curated_list_id == curated.id)
        ).all():
            badges.setdefault(badge.movie_id, badge)
        await self._hydrate(list(badges), country, decade)

        rows = self.session.exec(
            select(CanonMovieBadge, CachedMovie)
            .join(CachedMovie, col(CachedMovie.tmdb_id) == col(CanonMovieBadge.movie_id))
            .where(CanonMovieBadge.curated_list_id == curated.id)
        ).all()
        films = []
        seen: set[int] = set()
        for badge, movie in rows:
            if movie.tmdb_id in seen:
                continue
            if country and country not in parse_countries(movie.origin_country):
                continue
            if decade is not None and _decade_of(movie.release_date) != decade:
                continue
            seen.add(movie.tmdb_id)
            films.append(
                {
                    "movie_id": movie.tmdb_id,
                    "title": movie.title,
                    "year": parse_release_year(movie.release_date),
                    "poster_path": movie.poster_path,
                    "runtime": movie.runtime,
                    "badge_label": badge.badge_label,
                    "rank": badge.rank,
                }
            )
        if not films:
            raise RunSetupError(
                f"No films on {curated.title} match that "
                f"{' / '.join(p for p in (country_name(country), f'{decade}s' if decade else None) if p)}"
                " slice"
            )
        films.sort(key=lambda f: (f["rank"] is None, f["rank"] or 0, f["title"]))
        inputs = {LIST_ID_KEY, COUNTRY_KEY, DECADE_KEY}
        return {
            **{k: v for k, v in rules.items() if k not in inputs},
            EXPEDITION_KEY: {
                "list_id": curated.id,
                "list_title": curated.title,
                "badge_prefix": curated.badge_prefix,
                "badge_color": curated.badge_color,
                "country": country,
                "country_name": country_name(country),
                "decade": decade,
                "movie_ids": [f["movie_id"] for f in films],
                "films": films,
            },
        }

    async def _hydrate(self, movie_ids: list[int], country: str | None, decade: int | None) -> None:
        """Fetch the detail of every list film the slice can't be judged on yet: canon syncs only
        store TMDB ids, and a film's countries only come with its full detail. Progress is cached,
        so a run that times out can simply be created again."""
        repo = CacheRepo(self.session)
        missing = []
        for movie_id in movie_ids:
            row = self.session.get(CachedMovie, movie_id)
            if (
                row is None
                or (country and row.origin_country is None)
                or (decade is not None and not row.release_date)
            ):
                missing.append(movie_id)
        deadline = time.monotonic() + HYDRATE_SECONDS
        for start in range(0, len(missing), HYDRATE_BATCH):
            batch = missing[start : start + HYDRATE_BATCH]
            results = await asyncio.gather(
                *(
                    fetch_with_backoff(lambda movie_id=m: self.tmdb.get_movie(movie_id), deadline)
                    for m in batch
                ),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, DeadlineReached):
                    raise RunSetupError(
                        "Still indexing this list's films from TMDB - try again in a minute", 503
                    )
                if isinstance(result, TMDBError) or result is None:
                    continue  # unfetchable film: it can't be placed in a slice
                if isinstance(result, BaseException):
                    raise result
                await anyio.to_thread.run_sync(repo.upsert_movie, result)

    # --- the checklist ---

    @staticmethod
    def _checklist(rules: dict | None) -> list[int]:
        return list(((rules or {}).get(EXPEDITION_KEY) or {}).get("movie_ids") or [])

    async def validate_candidate(self, movie_id: int, rules: dict) -> ValidationResult:
        if movie_id in self._checklist(rules):
            return ValidationResult(valid=True)
        expedition = (rules or {}).get(EXPEDITION_KEY) or {}
        row = self.session.get(CachedMovie, movie_id)
        title = row.title if row is not None else f"Film {movie_id}"
        return ValidationResult(
            valid=False,
            blocked=True,
            reason=f"Off the expedition: {title} isn't on the {expedition.get('list_title', 'list')} "
            "checklist for this slice",
        )

    def evaluate_run_outcome(self, run: Run, steps: list[RunStep]) -> RunOutcome | None:
        if run.engine_version <= LEGACY_ENGINE_VERSION or run.status != RUN_STATUS_ACTIVE:
            return None
        expedition = (run.rules_config or {}).get(EXPEDITION_KEY) or {}
        checklist = set(self._checklist(run.rules_config))
        if checklist and checklist <= {step.movie_id for step in steps}:
            region = (
                expedition.get("country_name")
                or expedition.get("country")
                or (f"{expedition['decade']}s" if expedition.get("decade") else "this slice of")
            )
            return RunOutcome(
                RUN_STATUS_COMPLETED,
                f"Expedition Complete: Conquered {region} cinema on "
                f"{expedition.get('list_title', 'the list')}!",
            )
        return super().evaluate_run_outcome(run, steps)
