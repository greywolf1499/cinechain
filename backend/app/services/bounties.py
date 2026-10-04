"""The Bounty Board: a wildcard quest system layered over any challenge run.

A run created with `rules_config["bounty_board"] = True` starts with no wildcards and three
cinephile bounties in `rules_config["active_bounties"]`. Logging a film that satisfies one completes
it: the step is stamped `transition_metadata["completed_bounty"]`, the run earns a wildcard
(`wildcards_budget += 1`) and a fresh bounty is drawn so the board always holds three. Completed
bounties are remembered in `rules_config["completed_bounties"]`; a replacement comes from the ones
not yet completed (and, once every bounty has been done, from any not currently on the board).
At most one bounty completes per step, in board order.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable
from dataclasses import dataclass

from sqlmodel import Session

from app.models.cache import CachedMovie, CachedMovieDirector
from app.services import cache_repo
from app.services.tmdb import TMDBClient, TMDBError
from app.utils.dates import parse_release_year

BOUNTY_BOARD_KEY = "bounty_board"
ACTIVE_KEY = "active_bounties"
COMPLETED_KEY = "completed_bounties"
BOARD_SIZE = 3
COMPLETED_METADATA_KEY = "completed_bounty"
REPLACEMENT_METADATA_KEY = "bounty_replacement"

SHORT_RUNTIME = 90
EPIC_RUNTIME = 150
CAPSULE_YEAR = 1960
HIDDEN_GEM_POPULARITY = 12.0  # the cache keeps TMDB popularity, not the vote count
FEMALE = 1  # TMDB gender code


@dataclass(frozen=True)
class MovieFacts:
    runtime: int | None
    year: int | None
    popularity: float | None
    language: str | None
    countries: list[str] | None
    director_genders: list[int | None]


@dataclass(frozen=True)
class Bounty:
    id: str
    title: str
    icon: str
    description: str
    check: Callable[[MovieFacts], bool]
    needs_directors: bool = False


def _short_king(f: MovieFacts) -> bool:
    return bool(f.runtime) and f.runtime < SHORT_RUNTIME


def _time_capsule(f: MovieFacts) -> bool:
    return f.year is not None and f.year < CAPSULE_YEAR


def _hidden_gem(f: MovieFacts) -> bool:
    return f.popularity is not None and f.popularity < HIDDEN_GEM_POPULARITY


def _foreign_horizon(f: MovieFacts) -> bool:
    return bool(f.language) and f.language != "en" and f.countries is not None and "US" not in f.countries


def _female_gaze(f: MovieFacts) -> bool:
    return FEMALE in f.director_genders


def _epic_odyssey(f: MovieFacts) -> bool:
    return bool(f.runtime) and f.runtime > EPIC_RUNTIME


BOUNTIES: dict[str, Bounty] = {
    b.id: b
    for b in (
        Bounty("short_king", "Short King", "⏱️", f"Runtime under {SHORT_RUNTIME} minutes", _short_king),
        Bounty("time_capsule", "Time Capsule", "📼", f"Released before {CAPSULE_YEAR}", _time_capsule),
        Bounty("hidden_gem", "Hidden Gem", "💎", "Obscure: TMDB popularity under 12", _hidden_gem),
        Bounty("foreign_horizon", "Foreign Horizon", "🌍",
               "Non-English language and not a US production", _foreign_horizon),
        Bounty("female_gaze", "Female Gaze", "🎥", "Directed by a woman", _female_gaze,
               needs_directors=True),
        Bounty("epic_odyssey", "Epic Odyssey", "🏔️", f"Runtime over {EPIC_RUNTIME} minutes", _epic_odyssey),
    )
}


def board_enabled(rules: dict | None) -> bool:
    return (rules or {}).get(BOUNTY_BOARD_KEY) is True


def active_bounties(rules: dict | None) -> list[str]:
    return [b for b in ((rules or {}).get(ACTIVE_KEY) or []) if b in BOUNTIES]


def draw_replacement(
    active: list[str], completed: list[str], rng: random.Random
) -> str | None:
    """A bounty not on the board: from the never-completed ones first, else any other."""
    fresh = [b for b in BOUNTIES if b not in active and b not in completed]
    pool = fresh or [b for b in BOUNTIES if b not in active]
    return rng.choice(pool) if pool else None


def prepare_board(rules: dict, rng: random.Random | None = None) -> dict:
    """The rules a Bounty Board run starts with: no wildcards, three random bounties."""
    rng = rng or random.Random()
    return {
        **rules, "wildcards_budget": 0, ACTIVE_KEY: rng.sample(list(BOUNTIES), BOARD_SIZE),
        COMPLETED_KEY: [],
    }


def _countries(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return []
    return [c for c in parsed if isinstance(c, str)] if isinstance(parsed, list) else []


def facts_of(movie: CachedMovie, directors: list[CachedMovieDirector]) -> MovieFacts:
    return MovieFacts(
        runtime=movie.runtime, year=parse_release_year(movie.release_date),
        popularity=movie.popularity, language=movie.original_language,
        countries=_countries(movie.origin_country),
        director_genders=[d.gender for d in directors])


def completed_by(active: list[str], facts: MovieFacts) -> str | None:
    """The first active bounty these facts satisfy (board order)."""
    return next((b for b in active if BOUNTIES[b].check(facts)), None)


async def evaluate(
    session: Session, tmdb: TMDBClient, rules: dict | None, movie: CachedMovie,
    rng: random.Random | None = None,
) -> tuple[str, str | None] | None:
    """`(completed bounty id, its replacement)` if logging `movie` completes a bounty on the board.

    Fetches only what the board needs (full film detail, the directors' genders); a TMDB failure
    just means no award this time."""
    active = active_bounties(rules)
    if not board_enabled(rules) or not active:
        return None
    try:
        if movie.origin_country is None or movie.runtime is None:
            movie = await cache_repo.get_movie(session, tmdb, movie.tmdb_id, refresh=True)
        directors: list[CachedMovieDirector] = []
        if any(BOUNTIES[b].needs_directors for b in active):
            directors = await _directors_with_gender(session, tmdb, movie.tmdb_id)
    except TMDBError:
        return None
    done = completed_by(active, facts_of(movie, directors))
    if done is None:
        return None
    remaining = [b for b in active if b != done]
    completed = list((rules or {}).get(COMPLETED_KEY) or [])
    return done, draw_replacement(remaining, [*completed, done], rng or random.Random())


async def _directors_with_gender(
    session: Session, tmdb: TMDBClient, movie_id: int
) -> list[CachedMovieDirector]:
    directors = await cache_repo.get_movie_directors(session, tmdb, movie_id)
    if any(d.gender is None for d in directors):
        # Cached before TMDB genders were stored: read the crew again.
        movie = session.get(CachedMovie, movie_id)
        if movie is not None:
            movie.directors_fetched_at = None
            session.add(movie)
            session.commit()
            directors = await cache_repo.get_movie_directors(session, tmdb, movie_id)
    return directors


def award(rules: dict, bounty_id: str, replacement: str | None) -> dict:
    """The rules after `bounty_id` is completed: +1 wildcard, the bounty swapped for `replacement`."""
    active = [b for b in active_bounties(rules) if b != bounty_id]
    if replacement is not None:
        active.append(replacement)
    return {
        **rules, ACTIVE_KEY: active, COMPLETED_KEY: [*(rules.get(COMPLETED_KEY) or []), bounty_id],
        "wildcards_budget": max(rules.get("wildcards_budget", 0), 0) + 1,
    }


def revoke(rules: dict, bounty_id: str, replacement: str | None) -> dict:
    """Undo `award` (the completing step was deleted): the bounty returns to the board."""
    active = [b for b in active_bounties(rules) if b != replacement]
    if bounty_id not in active:
        active.insert(0, bounty_id)
    completed = list(rules.get(COMPLETED_KEY) or [])
    if bounty_id in completed:
        completed.remove(bounty_id)
    return {
        **rules, ACTIVE_KEY: active, COMPLETED_KEY: completed,
        "wildcards_budget": max(rules.get("wildcards_budget", 0) - 1, 0),
    }

