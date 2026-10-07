"""Narrative setting year: *when a film is set*, as opposed to when it was released.

`resolve_narrative_era` reads, in order: TMDB keywords (a fixed taxonomy of eras), century and
year references in the plot overview and, when the generative model is on, a one-shot question
(`resolve_narrative_era_async`). A film with no period indicator is "Contemporary": its narrative
year is its release year. Years are signed - negative means BCE.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Iterable
from datetime import UTC, datetime

import httpx
from sqlmodel import Session

from app.models.cache import CachedMovie
from app.services import cache_repo, llm
from app.services.tmdb import TMDBClient, TMDBError
from app.utils.dates import parse_release_year

logger = logging.getLogger(__name__)

CONTEMPORARY = "Contemporary"
MIN_YEAR = -10_000
MAX_YEAR = 10_000
MAX_LABEL_LENGTH = 60
KEYWORD_CONCURRENCY = 4

# keyword slug -> (approximate narrative year, era label). Order is priority: the first entry any
# of the film's keywords hits wins, so concrete historical eras come before generic futures.
TAXONOMY: dict[str, tuple[int, str]] = {
    "ancient-egypt": (-1200, "Ancient Egypt"),
    "ancient-greece": (-400, "Ancient Greece"),
    "ancient-rome": (100, "Ancient Rome"),
    "middle-ages": (1250, "Middle Ages"),
    "medieval": (1250, "Middle Ages"),
    "renaissance": (1500, "Renaissance"),
    "samurai": (1600, "Samurai Era"),
    "18th-century": (1750, "18th Century"),
    "victorian-era": (1870, "Victorian Era"),
    "19th-century": (1870, "19th Century"),
    "wild-west": (1880, "Wild West"),
    "world-war-i": (1916, "World War I"),
    "roaring-twenties": (1925, "Roaring Twenties"),
    "world-war-ii": (1943, "World War II"),
    "cold-war": (1965, "Cold War"),
    "1970s": (1975, "1970s"),
    "1980s": (1985, "1980s"),
    "1990s": (1995, "1990s"),
    "post-apocalyptic": (2060, "Post-Apocalyptic Future"),
    "cyberpunk": (2080, "Cyberpunk Future"),
    "space-travel": (2150, "Space Age Future"),
    "dystopia": (2150, "Dystopian Future"),
}

# Other spellings TMDB uses for the same era (slugs).
ALIASES: dict[str, str] = {
    "roman-empire": "ancient-rome",
    "ancient-roman": "ancient-rome",
    "antiquity": "ancient-rome",
    "pharaoh": "ancient-egypt",
    "medieval-times": "middle-ages",
    "dark-ages": "middle-ages",
    "feudal-japan": "samurai",
    "edo-period": "samurai",
    "victorian": "victorian-era",
    "1800s": "19th-century",
    "1700s": "18th-century",
    "old-west": "wild-west",
    "wwi": "world-war-i",
    "world-war-1": "world-war-i",
    "1920s": "roaring-twenties",
    "jazz-age": "roaring-twenties",
    "wwii": "world-war-ii",
    "world-war-2": "world-war-ii",
    "second-world-war": "world-war-ii",
    "post-apocalypse": "post-apocalyptic",
    "post-apocalyptic-future": "post-apocalyptic",
    "dystopian": "dystopia",
    "dystopian-future": "dystopia",
    "space-opera": "space-travel",
    "cyber-punk": "cyberpunk",
}

_NON_SLUG = re.compile(r"[^a-z0-9]+")
_CENTURY = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)[\s-]+century(?:\s+(BCE?|B\.C\.E?\.?))?", re.IGNORECASE
)
_BC_YEAR = re.compile(r"\b(\d{1,4})\s*(BCE?|B\.C\.E?\.?)(?!\w)", re.IGNORECASE)
_AD_YEAR = re.compile(r"\b(\d{3,4})\s*(?:AD|A\.D\.|CE)\b")
_PLAIN_YEAR = re.compile(r"\b(1[0-9]{3}|2[0-9]{3})\b")
_JSON_OBJECT = re.compile(r"\{.*?\}", re.DOTALL)

ERA_SYSTEM = (
    'You date the setting of films. Reply with only a JSON object {"year": int, "era": str}: '
    "the year the story takes place in (negative for BCE; the film's release year when it is set "
    'in its own time) and a short era name of at most 4 words, such as "Ancient Rome" or '
    '"World War II". No other text.'
)


def keyword_slug(keyword: str) -> str:
    return _NON_SLUG.sub("-", keyword.lower()).strip("-")


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def era_label_for_year(year: int, release_year: int | None = None) -> str:
    """A generic era name for a bare year (when nothing more specific is known)."""
    if year < 0:
        return "Ancient World"
    if year < 500:
        return "Classical Antiquity"
    if year < 1400:
        return "Middle Ages"
    if year < 1600:
        return "Renaissance"
    if year < 1800:
        return "Early Modern Era"
    if year < 1900:
        return "19th Century"
    if release_year is not None and year > release_year + 5:
        return "Future"
    return f"{year // 10 * 10}s"


def _from_keywords(keywords: Iterable[str]) -> tuple[int, str] | None:
    slugs = {ALIASES.get(slug, slug) for slug in (keyword_slug(k) for k in keywords)}
    for slug, found in TAXONOMY.items():
        if slug in slugs:
            return found
    return None


def _from_overview(overview: str, release_year: int | None) -> tuple[int, str] | None:
    century = _CENTURY.search(overview)
    if century and 1 <= int(century.group(1)) <= 40:
        n = int(century.group(1))
        if century.group(2):
            return -(n * 100 - 50), f"{_ordinal(n)} Century BC"
        return (n - 1) * 100 + 50, f"{_ordinal(n)} Century"
    bc = _BC_YEAR.search(overview)
    if bc:
        year = -int(bc.group(1))
        return year, era_label_for_year(year)
    ad = _AD_YEAR.search(overview)
    if ad:
        year = int(ad.group(1))
        return year, era_label_for_year(year, release_year)
    plain = _PLAIN_YEAR.search(overview)
    if plain:
        year = int(plain.group(1))
        return year, era_label_for_year(year, release_year)
    return None


def detect_narrative_era(
    overview: str | None, keywords: Iterable[str], release_year: int | None = None
) -> tuple[int, str] | None:
    """The era the keywords or the overview point at, or None when neither names one."""
    return _from_keywords(keywords) or _from_overview(overview or "", release_year)


def default_era(movie: CachedMovie) -> tuple[int, str]:
    year = parse_release_year(movie.release_date)
    return (year if year is not None else datetime.now(UTC).year), CONTEMPORARY


def resolve_narrative_era(
    movie: CachedMovie, overview: str, keywords: list[str]
) -> tuple[int, str]:
    """(narrative year, era label): keywords, then the overview, else Contemporary."""
    release_year = parse_release_year(movie.release_date)
    return detect_narrative_era(overview, keywords, release_year) or default_era(movie)


def parse_era_reply(text: str) -> tuple[int, str] | None:
    """`{"year": int, "era": str}` from a model reply; None when it is missing or implausible."""
    match = _JSON_OBJECT.search(llm.clean_output(text))
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    year, era = data.get("year"), data.get("era")
    if isinstance(year, bool) or not isinstance(year, int) or not MIN_YEAR <= year <= MAX_YEAR:
        return None
    if not isinstance(era, str) or not era.strip():
        return None
    return year, era.strip()[:MAX_LABEL_LENGTH]


async def ask_model_for_era(
    movie: CachedMovie, overview: str, config: llm.LlmConfig
) -> tuple[int, str] | None:
    """One-shot LLM guess. None = the model had no usable answer; `LlmUnavailable` if it failed."""
    release_year = parse_release_year(movie.release_date)
    prompt = (
        f"Film: {movie.title}" + (f" ({release_year})" if release_year else "") + "\n"
        f"Plot: {(overview or '')[:600]}\nWhen is the story set?"
    )
    return parse_era_reply(await llm.generate(config, ERA_SYSTEM, prompt, max_tokens=48))


def effective_era(movie: CachedMovie) -> tuple[int, str]:
    """The stored era, else the contemporary default (never persisted by this call)."""
    if movie.narrative_year is not None:
        return movie.narrative_year, movie.narrative_era_label or era_label_for_year(
            movie.narrative_year, parse_release_year(movie.release_date)
        )
    return default_era(movie)


def _store(session: Session, movie: CachedMovie, year: int, label: str) -> None:
    movie.narrative_year = year
    movie.narrative_era_label = label
    session.add(movie)
    from app.facets.store import invalidate, refresh

    invalidate(session, [movie.tmdb_id], ["production"])
    refresh(session, [movie.tmdb_id], ["production"])
    session.commit()
    session.refresh(movie)


async def _settle(
    session: Session,
    tmdb: TMDBClient,
    movie: CachedMovie,
    keywords: list[str],
    config: llm.LlmConfig | None,
    allow_llm: bool,
) -> tuple[int, str]:
    """Works out the movie's era from its keywords and plot and saves it - unless the answer is
    not trustworthy yet (no plot to read, or the model is on but skipped or failed), in which
    case the contemporary default is returned unsaved so a later call tries again."""
    release_year = parse_release_year(movie.release_date)
    found = detect_narrative_era(movie.overview, keywords, release_year)
    if found is None and movie.overview is None:  # a search/credits stub: read the real plot
        try:
            movie = await cache_repo.get_movie(session, tmdb, movie.tmdb_id, refresh=True)
        except (TMDBError, httpx.HTTPError):
            return default_era(movie)
        found = detect_narrative_era(movie.overview, keywords, release_year)
    if found is None and config is not None and config.enabled and (movie.overview or "").strip():
        if not allow_llm:
            return default_era(movie)
        try:
            found = await ask_model_for_era(movie, movie.overview or "", config)
        except llm.LlmUnavailable:
            logger.warning("LLM era guess failed for %s", movie.tmdb_id, exc_info=True)
            return default_era(movie)
    year, label = found or default_era(movie)
    _store(session, movie, year, label)
    return year, label


async def ensure_narrative_era(
    session: Session,
    tmdb: TMDBClient,
    movie: CachedMovie,
    config: llm.LlmConfig | None = None,
    *,
    allow_llm: bool = True,
    force: bool = False,
) -> tuple[int, str]:
    """The movie's narrative era, resolving and saving it first when missing (or when `force`).
    TMDB's keywords being unreachable leaves the film unresolved (the default is returned)."""
    if movie.narrative_year is not None and not force:
        return effective_era(movie)
    try:
        keywords = await tmdb.get_movie_keywords(movie.tmdb_id)
    except (TMDBError, httpx.HTTPError):
        logger.warning("Keywords unavailable for %s", movie.tmdb_id, exc_info=True)
        return effective_era(movie)
    return await _settle(session, tmdb, movie, keywords, config, allow_llm)


async def ensure_narrative_eras(
    session: Session,
    tmdb: TMDBClient,
    movies: Iterable[CachedMovie],
    config: llm.LlmConfig | None = None,
    *,
    allow_llm: bool = False,
) -> None:
    """Resolves every unresolved film (keywords fetched concurrently, saved one by one)."""
    pending = [m for m in movies if m.narrative_year is None]
    if not pending:
        return
    gate = asyncio.Semaphore(KEYWORD_CONCURRENCY)

    async def fetch(movie: CachedMovie) -> tuple[CachedMovie, list[str] | None]:
        async with gate:
            try:
                return movie, await tmdb.get_movie_keywords(movie.tmdb_id)
            except (TMDBError, httpx.HTTPError):
                return movie, None

    for movie, keywords in await asyncio.gather(*(fetch(m) for m in pending)):
        if keywords is not None:
            await _settle(session, tmdb, movie, keywords, config, allow_llm)


def set_narrative_era(session: Session, movie: CachedMovie, year: int, label: str) -> None:
    """A manual edit."""
    _store(session, movie, year, label.strip()[:MAX_LABEL_LENGTH])
