"""The Bounty Board: a wildcard quest system layered over any challenge run.

A run created with `rules_config["bounty_board"] = True` starts with no wildcards and three
cinephile bounties in `rules_config["active_bounties"]`. Logging a film that satisfies one completes
it: the step is stamped `transition_metadata["completed_bounty"]`, the run earns a wildcard
(`wildcards_budget += 1`) and a fresh bounty is drawn so the board always holds three. Completed
bounties are remembered in `rules_config["completed_bounties"]`; a replacement comes from the ones
not yet completed (and, once every bounty has been done, from any not currently on the board).
At most one bounty completes per step, in board order.

AI bounties: once the six static bounties are all done or on the board, a replacement is generated
by the LLM instead (when it is on), and a player can also ask for one with `roll_custom`. The model
proposes a title, icon and description plus a *rule* - 1-3 conditions that must all hold, each a
runtime / year / decade / genre / keyword test (see `normalize_rule`) - which is validated and then
evaluated programmatically like any static bounty. Definitions live in `rules_config["custom_bounties"]`
(id -> definition), the board references them by id (`ai_xxxxxx`).
"""

from __future__ import annotations

import json
import random
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, NamedTuple

from sqlmodel import Session

from app.models.cache import CachedMovie, CachedMovieDirector
from app.services import cache_repo, llm
from app.services.tmdb import TMDBClient, TMDBError
from app.utils.dates import parse_release_year

BOUNTY_BOARD_KEY = "bounty_board"
ACTIVE_KEY = "active_bounties"
COMPLETED_KEY = "completed_bounties"
BOARD_SIZE = 3
COMPLETED_METADATA_KEY = "completed_bounty"
REPLACEMENT_METADATA_KEY = "bounty_replacement"
CUSTOM_KEY = "custom_bounties"
AI_PREFIX = "ai_"

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
    genre_ids: list[int] = field(default_factory=list)
    text: str = ""  # lower-cased title, tagline and plot: what keyword rules search


@dataclass(frozen=True)
class Bounty:
    id: str
    title: str
    icon: str
    description: str
    check: Callable[[MovieFacts], bool]
    needs_directors: bool = False
    ai: bool = False


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


def resolve(rules: dict | None, bounty_id: str) -> Bounty | None:
    """A static bounty, or an AI one rebuilt from its stored definition."""
    if bounty_id in BOUNTIES:
        return BOUNTIES[bounty_id]
    definition = ((rules or {}).get(CUSTOM_KEY) or {}).get(bounty_id)
    return custom_bounty(definition) if isinstance(definition, dict) else None


def active_bounties(rules: dict | None) -> list[str]:
    return [b for b in ((rules or {}).get(ACTIVE_KEY) or []) if resolve(rules, b) is not None]


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
        director_genders=[d.gender for d in directors],
        genre_ids=list(movie.genre_ids or []),
        text=" ".join(filter(None, (movie.title, movie.tagline, movie.overview))).lower())


def completed_by(active: list[Bounty], facts: MovieFacts) -> str | None:
    """The first of the active bounties these facts satisfy (board order)."""
    return next((b.id for b in active if b.check(facts)), None)


class BountyAward(NamedTuple):
    """A completed bounty, the one drawn to replace it and, for an AI replacement, its definition."""

    completed: str
    replacement: str | None
    custom: dict | None = None


async def evaluate(
    session: Session, tmdb: TMDBClient, rules: dict | None, movie: CachedMovie,
    rng: random.Random | None = None,
) -> BountyAward | None:
    """The award, if logging `movie` completes a bounty on the board.

    Fetches only what the board needs (full film detail, the directors' genders); a TMDB failure
    just means no award this time. When no static bounty is left to offer as the replacement, the
    LLM (if on) writes a new one; if it fails the static ones are recycled."""
    active = [b for b in (resolve(rules, i) for i in active_bounties(rules)) if b is not None]
    if not board_enabled(rules) or not active:
        return None
    try:
        if movie.origin_country is None or movie.runtime is None:
            movie = await cache_repo.get_movie(session, tmdb, movie.tmdb_id, refresh=True)
        directors: list[CachedMovieDirector] = []
        if any(b.needs_directors for b in active):
            directors = await _directors_with_gender(session, tmdb, movie.tmdb_id)
    except TMDBError:
        return None
    done = completed_by(active, facts_of(movie, directors))
    if done is None:
        return None
    rng = rng or random.Random()
    remaining = [b.id for b in active if b.id != done]
    completed = [*((rules or {}).get(COMPLETED_KEY) or []), done]
    if not [b for b in BOUNTIES if b not in remaining and b not in completed]:
        custom = await _try_generate(session, rules)
        if custom is not None:
            return BountyAward(done, custom["id"], custom)
    return BountyAward(done, draw_replacement(remaining, completed, rng))


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


def award(
    rules: dict, bounty_id: str, replacement: str | None, custom: dict | None = None
) -> dict:
    """The rules after `bounty_id` is completed: +1 wildcard, the bounty swapped for `replacement`
    (`custom` = its definition when it is an AI bounty)."""
    customs = dict(rules.get(CUSTOM_KEY) or {})
    if custom is not None:
        customs[custom["id"]] = custom
    active = [b for b in active_bounties(rules) if b != bounty_id]
    if replacement is not None:
        active.append(replacement)
    return {
        **rules, ACTIVE_KEY: active, COMPLETED_KEY: [*(rules.get(COMPLETED_KEY) or []), bounty_id],
        "wildcards_budget": max(rules.get("wildcards_budget", 0), 0) + 1,
        **({CUSTOM_KEY: customs} if customs else {}),
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



# --- AI bounties: the rule grammar ---

MAX_CONDITIONS = 3
MAX_KEYWORDS = 5
MIN_YEAR, MAX_RUNTIME = 1888, 400
GENRE_IDS = {
    "action": 28, "adventure": 12, "animation": 16, "comedy": 35, "crime": 80, "documentary": 99,
    "drama": 18, "family": 10751, "fantasy": 14, "history": 36, "horror": 27, "music": 10402,
    "musical": 10402, "mystery": 9648, "romance": 10749, "science fiction": 878,
    "sci-fi": 878, "thriller": 53, "war": 10752, "western": 37,
}


def _whole(value: object, low: int, high: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
        return None
    return int(value) if low <= int(value) <= high else None


def _range(raw: dict, low: int, high: int) -> dict[str, int] | None:
    bounds: dict[str, int] = {}
    for key in ("min", "max"):
        if raw.get(key) is not None:
            value = _whole(raw[key], low, high)
            if value is None:
                return None
            bounds[key] = value
    if not bounds or (len(bounds) == 2 and bounds["min"] > bounds["max"]):
        return None
    return bounds


def normalize_condition(raw: object) -> dict[str, Any] | None:
    """One condition in canonical form, or None if it isn't a valid runtime / year / decade /
    genre / keyword test."""
    if not isinstance(raw, dict):
        return None
    kind = raw.get("type")
    if kind == "runtime":
        bounds = _range(raw, 1, MAX_RUNTIME)
        return {"type": "runtime", **bounds} if bounds else None
    if kind == "year":
        bounds = _range(raw, MIN_YEAR, datetime.now(UTC).year)
        return {"type": "year", **bounds} if bounds else None
    if kind == "decade":
        decade = _whole(raw.get("decade"), MIN_YEAR, datetime.now(UTC).year)
        return {"type": "year", "min": decade // 10 * 10, "max": decade // 10 * 10 + 9} if decade else None
    if kind == "genre":
        genre = str(raw.get("genre") or "").strip().lower()
        return {"type": "genre", "genre": genre} if genre in GENRE_IDS else None
    if kind == "keyword":
        words = raw.get("keywords", raw.get("keyword"))
        words = [words] if isinstance(words, str) else words
        if not isinstance(words, list):
            return None
        keywords = list(dict.fromkeys(
            w.strip().lower() for w in words if isinstance(w, str) and len(w.strip()) >= 3))
        return {"type": "keyword", "keywords": keywords[:MAX_KEYWORDS]} if keywords else None
    return None


def normalize_rule(raw: object) -> list[dict[str, Any]] | None:
    """A rule - one condition or a list of 1-3 that must all hold - in canonical form."""
    items = [raw] if isinstance(raw, dict) else raw
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_CONDITIONS:
        return None
    conditions = [normalize_condition(item) for item in items]
    return None if any(c is None for c in conditions) else conditions  # type: ignore[return-value]


def _holds(condition: dict[str, Any], facts: MovieFacts) -> bool:
    """Missing data never satisfies a condition."""
    kind = condition["type"]
    if kind == "genre":
        return GENRE_IDS[condition["genre"]] in facts.genre_ids
    if kind == "keyword":
        return any(re.search(rf"\b{re.escape(word)}", facts.text) for word in condition["keywords"])
    value = facts.runtime if kind == "runtime" else facts.year
    if not value:
        return False
    return condition.get("min", value) <= value <= condition.get("max", value)


def describe_rule(rule: list[dict[str, Any]]) -> str:
    parts = []
    for c in rule:
        if c["type"] in ("runtime", "year"):
            unit = " min" if c["type"] == "runtime" else ""
            low, high = c.get("min"), c.get("max")
            span = f"{low}-{high}{unit}" if low and high else (f"{low}+{unit}" if low else f"up to {high}{unit}")
            parts.append(f"{'Runtime' if c['type'] == 'runtime' else 'Released'} {span}")
        elif c["type"] == "genre":
            parts.append(f"{c['genre'].title()} genre")
        else:
            parts.append("Mentions " + " / ".join(c["keywords"]))
    return "; ".join(parts)


def custom_bounty(definition: dict) -> Bounty | None:
    """A runnable bounty from a stored AI definition (None if the stored rule is no longer valid)."""
    rule = normalize_rule(definition.get("rule"))
    if rule is None or not isinstance(definition.get("id"), str):
        return None
    return Bounty(
        id=definition["id"], title=str(definition.get("title") or "AI Bounty"),
        icon=str(definition.get("icon") or "✨"),
        description=str(definition.get("description") or describe_rule(rule)),
        check=lambda facts: all(_holds(c, facts) for c in rule), ai=True)


# --- AI bounties: generation ---

BOUNTY_SYSTEM = (
    "You invent ONE fun challenge for a cinephile movie night: a kind of film to go and watch. "
    "Reply with ONLY a JSON object: "
    '{"title": "2-4 words", "icon": "one emoji", "description": "one short sentence", "rule": [...]}. '
    "The rule is a list of 1 to 3 conditions that must ALL hold. Each condition is one of: "
    '{"type":"runtime","min":N,"max":N} (minutes, either bound optional), '
    '{"type":"year","min":N,"max":N} (release year, either bound optional), '
    '{"type":"decade","decade":1970}, '
    '{"type":"genre","genre":"<a TMDB genre such as Musical, Horror, Western>"}, '
    '{"type":"keyword","keywords":["word","word"]} (any word appearing in the title or plot). '
    'Example: {"title":"Golden Age Musical","icon":"🎷","description":"A musical from Hollywood\'s '
    'golden age.","rule":[{"type":"genre","genre":"Musical"},{"type":"year","min":1930,"max":1959}]}'
)
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
MAX_TITLE, MAX_DESCRIPTION = 40, 110


def parse_custom_bounty(text: str, taken_titles: list[str] | None = None) -> dict | None:
    """A validated AI bounty definition from the model's reply, or None if it is unusable (not
    JSON, an invalid rule, or a title already on the board)."""
    text = llm._THINK_BLOCK.sub("", text).split("</think>")[-1]
    match = _JSON_OBJECT.search(text)
    if match is None:
        return None
    try:
        raw = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(raw, dict):
        return None
    title = " ".join(str(raw.get("title") or "").split())
    rule = normalize_rule(raw.get("rule"))
    if not 3 <= len(title) <= MAX_TITLE or rule is None:
        return None
    if title.lower() in {t.lower() for t in taken_titles or []}:
        return None
    icon = str(raw.get("icon") or "").strip()
    description = " ".join(str(raw.get("description") or "").split())[:MAX_DESCRIPTION]
    return {
        "id": f"{AI_PREFIX}{uuid.uuid4().hex[:6]}", "title": title,
        "icon": icon[:4] if icon and not icon.isascii() else "✨",
        "description": description or describe_rule(rule), "rule": rule}


async def generate_custom_bounty(
    config: llm.LlmConfig, taken_titles: list[str] | None = None
) -> dict:
    """Asks the model for a new bounty (one retry). Raises `LlmUnavailable` if it can't produce one."""
    avoid = f" Do not reuse these titles: {', '.join(taken_titles)}." if taken_titles else ""
    for _ in range(2):
        text = await llm.generate(
            config, BOUNTY_SYSTEM, f"Invent a new bounty.{avoid}", max_tokens=260)
        bounty = parse_custom_bounty(text, taken_titles)
        if bounty is not None:
            return bounty
    raise llm.LlmUnavailable("The model didn't produce a usable bounty")


def _taken_titles(rules: dict | None) -> list[str]:
    titles = [b.title for b in BOUNTIES.values()]
    titles += [d.get("title", "") for d in ((rules or {}).get(CUSTOM_KEY) or {}).values()]
    return [t for t in titles if t]


async def _try_generate(session: Session, rules: dict | None) -> dict | None:
    config = llm.load_config(session)
    if not config.enabled:
        return None
    try:
        return await generate_custom_bounty(config, _taken_titles(rules))
    except llm.LlmUnavailable:
        return None


class BountyError(Exception):
    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


async def roll_custom(session: Session, rules: dict | None) -> dict:
    """The rules after the player's "✨ Roll Custom Bounty": the oldest bounty on the board is
    swapped for a freshly generated AI one (it returns to the pool, uncompleted)."""
    rules = rules or {}
    if not board_enabled(rules):
        raise BountyError("This run has no Bounty Board")
    active = active_bounties(rules)
    if any(b.startswith(AI_PREFIX) for b in active):
        raise BountyError("An AI bounty is already on the board - complete it first")
    config = llm.load_config(session)
    if not config.enabled:
        raise BountyError(
            "The generative model is off - an admin can enable it under "
            "Settings > Integrations > AI & Embeddings.")
    try:
        custom = await generate_custom_bounty(config, _taken_titles(rules))
    except llm.LlmUnavailable as exc:
        raise BountyError(str(exc), 503) from exc
    board = [*active[1:], custom["id"]] if active else [custom["id"]]
    return {**rules, ACTIVE_KEY: board, CUSTOM_KEY: {**(rules.get(CUSTOM_KEY) or {}), custom["id"]: custom}}
