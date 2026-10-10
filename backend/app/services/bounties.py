"""Mode-aware side quests with feasible draws and server-owned rewards.

Up to three nontrivial quests occupy a board; at most one completes per step.
The engine awards its usable currency (wildcard, life, hint or victory star).
AI definitions contain 1-3 normalized AND conditions and must pass the same
feasibility guard as static quests. Unknown facts never award a bounty.
"""

from __future__ import annotations

import json
import random
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.engines.predicates import MovieFacts, Predicate, QueryPredicate, named_predicate
from app.engines.rulebook import RuleSection
from app.facets.genres import GENRE_IDS
from app.facets.query import FacetQuery
from app.facets.registry import CATALOGUE, named_variants

RULEBOOK = RuleSection(
    "Earn rewards by completing film side quests.",
    [
        "Match a film to an active bounty while obeying your mode's rules.",
        "Swap one quest for free per run.",
        "Quests you cannot meet are replaced for free.",
    ],
    [
        "At most one bounty completes per logged step. Reward: {bounty_reward}; a feasible quest replaces it when available."
    ],
    ["A bounty does not waive the main mode's restrictions."],
    [
        "Choose a film satisfying both a bounty and a useful onward route.",
        "Use a rewarded wildcard to skip a missing cast link. Other film rules still apply.",
    ],
    ["bounty", "wildcard", "life", "hint", "star"],
)

from sqlmodel import Session

from app.models.cache import CachedMovie, CachedMovieDirector
from app.services import cache_repo, llm
from app.services.tmdb import TMDBClient, TMDBError

BOUNTY_BOARD_KEY = "bounty_board"
ACTIVE_KEY = "active_bounties"
COMPLETED_KEY = "completed_bounties"
BOARD_SIZE = 3
COMPLETED_METADATA_KEY = "completed_bounty"
REPLACEMENT_METADATA_KEY = "bounty_replacement"
CUSTOM_KEY = "custom_bounties"
AI_PREFIX = "ai_"

SHORT_RUNTIME = named_variants()["short"]["query"]["value"]
EPIC_RUNTIME = named_variants()["epic"]["query"]["value"]
CAPSULE_YEAR = named_variants()["vintage"]["query"]["value"]
HIDDEN_GEM_POPULARITY = named_variants()["hidden_gem"]["query"]["value"]
FEMALE = 1  # TMDB gender code


@dataclass(frozen=True)
class Bounty:
    id: str
    title: str
    icon: str
    description: str
    check: Callable[[MovieFacts], bool]
    needs_directors: bool = False
    ai: bool = False
    predicate: Predicate | None = None


def _short_king(f: MovieFacts) -> bool:
    return bool(f.runtime) and named_predicate("short").check(None, f) is True


def _time_capsule(f: MovieFacts) -> bool:
    return named_predicate("vintage").check(None, f) is True


def _hidden_gem(f: MovieFacts) -> bool:
    return named_predicate("hidden_gem").check(None, f) is True


def _foreign_horizon(f: MovieFacts) -> bool:
    return named_predicate("foreign").check(None, f) is True


def _female_gaze(f: MovieFacts) -> bool:
    return named_predicate("female_director").check(None, f) is True


def _epic_odyssey(f: MovieFacts) -> bool:
    return bool(f.runtime) and named_predicate("epic").check(None, f) is True


BOUNTIES: dict[str, Bounty] = {
    b.id: b
    for b in (
        Bounty(
            "short_king",
            "Short King",
            "⏱️",
            f"Runtime under {SHORT_RUNTIME} minutes",
            _short_king,
            predicate=named_predicate("short"),
        ),
        Bounty(
            "time_capsule",
            "Time Capsule",
            "📼",
            f"Released before {CAPSULE_YEAR}",
            _time_capsule,
            predicate=named_predicate("vintage"),
        ),
        Bounty(
            "hidden_gem",
            "Hidden Gem",
            "💎",
            "Obscure: TMDB popularity under 12",
            _hidden_gem,
            predicate=named_predicate("hidden_gem"),
        ),
        Bounty(
            "foreign_horizon",
            "Foreign Horizon",
            "🌍",
            "Non-English language and not a US production",
            _foreign_horizon,
            predicate=named_predicate("foreign"),
        ),
        Bounty(
            "female_gaze",
            "Female Gaze",
            "🎥",
            "Directed by a woman",
            _female_gaze,
            needs_directors=True,
            predicate=named_predicate("female_director"),
        ),
        Bounty(
            "epic_odyssey",
            "Epic Odyssey",
            "🏔️",
            f"Runtime over {EPIC_RUNTIME} minutes",
            _epic_odyssey,
            predicate=named_predicate("epic"),
        ),
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
    active: list[str],
    completed: list[str],
    rng: random.Random,
    feasible: Callable[[str], bool] | None = None,
    difficulty: Callable[[str], int] | None = None,
) -> str | None:
    """A bounty not on the board: from the never-completed ones first, else any other."""
    eligible = [b for b in BOUNTIES if b not in active and (feasible is None or feasible(b))]
    fresh = [b for b in eligible if b not in completed]
    pool = fresh or eligible
    if difficulty:
        pool.sort(key=difficulty)
    return rng.choice(pool) if pool else None


def prepare_board(
    rules: dict,
    rng: random.Random | None = None,
    feasible: Callable[[str], bool] | None = None,
    difficulty: Callable[[str], int] | None = None,
) -> dict:
    """Start with up to three feasible, nontrivial quests and one free discard."""
    rng = rng or random.Random()
    pool = [b for b in BOUNTIES if feasible is None or feasible(b)]
    if difficulty:
        pool.sort(key=difficulty)
    return {
        **rules,
        "wildcards_budget": 0,
        ACTIVE_KEY: sorted(rng.sample(pool, min(BOARD_SIZE, len(pool))), key=difficulty)
        if difficulty
        else rng.sample(pool, min(BOARD_SIZE, len(pool))),
        COMPLETED_KEY: [],
        "bounty_discards_left": 1,
        "bounty_stars": 0,
    }


def _countries(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return []
    return [c for c in parsed if isinstance(c, str)] if isinstance(parsed, list) else []


def completed_by(active: list[Bounty], facts: MovieFacts) -> str | None:
    """The first of the active bounties these facts satisfy (board order)."""
    return next((b.id for b in active if b.check(facts)), None)


class BountyAward(NamedTuple):
    """A completed bounty, the one drawn to replace it and, for an AI replacement, its definition."""

    completed: str
    replacement: str | None
    custom: dict | None = None


async def evaluate(
    session: Session,
    tmdb: TMDBClient,
    rules: dict | None,
    movie: CachedMovie,
    rng: random.Random | None = None,
    feasible: Callable[[Bounty], bool] | None = None,
    context: str = "",
    difficulty: Callable[[Bounty], int] | None = None,
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
        if any(b.needs_directors for b in active):
            await _directors_with_gender(session, tmdb, movie.tmdb_id)
    except TMDBError:
        return None
    from app.services import feasibility

    done = next(
        (
            b.id
            for b in active
            if b.predicate and feasibility.verdict(session, b.predicate, movie.tmdb_id) is True
        ),
        None,
    )
    if done is None:
        return None
    rng = rng or random.Random()
    remaining = [b.id for b in active if b.id != done]
    completed = [*((rules or {}).get(COMPLETED_KEY) or []), done]
    if not [b for b in BOUNTIES if b not in remaining and b not in completed]:
        custom = await _try_generate(session, rules, feasible, context)
        if custom is not None:
            return BountyAward(done, custom["id"], custom)
    return BountyAward(
        done,
        draw_replacement(
            remaining,
            completed,
            rng,
            (lambda bounty_id: feasible(BOUNTIES[bounty_id])) if feasible else None,
            (lambda bounty_id: difficulty(BOUNTIES[bounty_id])) if difficulty else None,
        ),
    )


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


def award(rules: dict, bounty_id: str, replacement: str | None, custom: dict | None = None) -> dict:
    """The rules after `bounty_id` is completed: +1 wildcard, the bounty swapped for `replacement`
    (`custom` = its definition when it is an AI bounty)."""
    customs = dict(rules.get(CUSTOM_KEY) or {})
    if custom is not None:
        customs[custom["id"]] = custom
    active = [b for b in active_bounties(rules) if b != bounty_id]
    if replacement is not None:
        active.append(replacement)
    return {
        **rules,
        ACTIVE_KEY: active,
        COMPLETED_KEY: [*(rules.get(COMPLETED_KEY) or []), bounty_id],
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
        **rules,
        ACTIVE_KEY: active,
        COMPLETED_KEY: completed,
        "wildcards_budget": max(rules.get("wildcards_budget", 0) - 1, 0),
    }


# --- AI bounties: the rule grammar ---

MAX_CONDITIONS = 3
MAX_KEYWORDS = 5
MIN_YEAR, MAX_RUNTIME = 1888, 400


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
    if "facet" in raw:
        leaf = {k: v for k, v in raw.items() if k != "type"}
        try:
            query = FacetQuery.model_validate(leaf)
        except ValidationError:
            return None
        if query.facet is None:
            return None
        return {"type": "facet", **query.model_dump(by_alias=True, exclude_none=True)}
    kind = raw.get("type")
    if kind == "runtime":
        bounds = _range(raw, 1, MAX_RUNTIME)
        return {"type": "runtime", **bounds} if bounds else None
    if kind == "year":
        bounds = _range(raw, MIN_YEAR, datetime.now(UTC).year)
        return {"type": "year", **bounds} if bounds else None
    if kind == "decade":
        decade = _whole(raw.get("decade"), MIN_YEAR, datetime.now(UTC).year)
        return (
            {"type": "year", "min": decade // 10 * 10, "max": decade // 10 * 10 + 9}
            if decade
            else None
        )
    if kind == "genre":
        genre = str(raw.get("genre") or "").strip().lower()
        return {"type": "genre", "genre": genre} if genre in GENRE_IDS else None
    if kind == "keyword":
        words = raw.get("keywords", raw.get("keyword"))
        words = [words] if isinstance(words, str) else words
        if not isinstance(words, list):
            return None
        keywords = list(
            dict.fromkeys(
                w.strip().lower() for w in words if isinstance(w, str) and len(w.strip()) >= 3
            )
        )
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
    if kind == "facet":
        return (
            QueryPredicate("custom", "Custom", condition_query(condition)).check(None, facts)
            is True
        )
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
        if c["type"] == "facet":
            parts.append(f"{CATALOGUE[c['facet']].label} {c['op']} {c['value']}")
        elif c["type"] in ("runtime", "year"):
            unit = " min" if c["type"] == "runtime" else ""
            low, high = c.get("min"), c.get("max")
            span = (
                f"{low}-{high}{unit}"
                if low and high
                else (f"{low}+{unit}" if low else f"up to {high}{unit}")
            )
            parts.append(f"{'Runtime' if c['type'] == 'runtime' else 'Released'} {span}")
        elif c["type"] == "genre":
            parts.append(f"{c['genre'].title()} genre")
        else:
            parts.append("Mentions " + " / ".join(c["keywords"]))
    return "; ".join(parts)


@dataclass(frozen=True)
class CustomPredicate:
    id: str
    label: str
    emoji: str
    conditions: list[dict[str, Any]]
    needs: frozenset[str] = frozenset({"runtime", "release_date", "genre_ids", "overview"})
    difficulty: int = 3

    @property
    def query(self) -> FacetQuery:
        return FacetQuery(all=[condition_query(c) for c in self.conditions])

    @property
    def params(self) -> dict[str, float]:
        return {}

    @property
    def ranges(self) -> dict[str, tuple[float | None, float | None]]:
        bounds: dict[str, tuple[float | None, float | None]] = {}
        for condition in self.conditions:
            kind = condition["type"]
            if kind not in ("runtime", "year"):
                continue
            previous_low, previous_high = bounds.get(kind, (None, None))
            lows = [v for v in (previous_low, condition.get("min")) if v is not None]
            highs = [v for v in (previous_high, condition.get("max")) if v is not None]
            bounds[kind] = (max(lows) if lows else None, min(highs) if highs else None)
        return bounds

    def check(self, movie: CachedMovie | None, facts: MovieFacts) -> bool | None:
        return QueryPredicate(self.id, self.label, self.query).check(movie, facts)


def condition_query(condition: dict) -> FacetQuery:
    kind = condition["type"]
    if kind == "facet":
        return FacetQuery.model_validate({k: v for k, v in condition.items() if k != "type"})
    if kind == "genre":
        return FacetQuery(facet="genre", op="contains", value=GENRE_IDS[condition["genre"]])
    if kind == "keyword":
        return FacetQuery(
            any=[FacetQuery(facet="text", op="contains", value=w) for w in condition["keywords"]]
        )
    facet = "runtime" if kind == "runtime" else "release_year"
    return FacetQuery(
        all=[
            FacetQuery.model_validate({"facet": facet, "op": op, "value": condition[key]})
            for key, op in (("min", "ge"), ("max", "le"))
            if key in condition
        ]
    )


def custom_bounty(definition: dict) -> Bounty | None:
    """A runnable bounty from a stored AI definition (None if the stored rule is no longer valid)."""
    rule = normalize_rule(definition.get("rule"))
    if rule is None or not isinstance(definition.get("id"), str):
        return None
    test = CustomPredicate(definition["id"], str(definition.get("title", "AI Bounty")), "✨", rule)
    return Bounty(
        id=definition["id"],
        title=str(definition.get("title") or "AI Bounty"),
        icon=str(definition.get("icon") or "✨"),
        description=str(definition.get("description") or describe_rule(rule)),
        check=lambda facts: all(_holds(c, facts) for c in rule),
        ai=True,
        predicate=test,
        needs_directors=any(
            c.get("facet")
            in ("female_director", "director_debut", "director_film_index", "posthumous_release")
            for c in rule
        ),
    )


# --- AI bounties: generation ---

BOUNTY_SYSTEM = (
    "You invent ONE fun challenge for a cinephile movie night: a kind of film to go and watch. "
    "Reply with ONLY a JSON object: "
    '{"title": "2-4 words", "icon": "one emoji", "description": "one short sentence", "rule": [...]}. '
    "Prefer facet leaves from the supplied best-covered catalogue, each as "
    '{"facet":"catalogue_id","op":"allowed_operator","value":typed_value}. '
    "The rule is a list of 1 to 3 conditions that must ALL hold. Legacy forms also accepted: "
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
        "id": f"{AI_PREFIX}{uuid.uuid4().hex[:6]}",
        "title": title,
        "icon": icon[:4] if icon and not icon.isascii() else "✨",
        "description": description or describe_rule(rule),
        "rule": rule,
    }


async def generate_custom_bounty(
    config: llm.LlmConfig,
    taken_titles: list[str] | None = None,
    feasible: Callable[[Bounty], bool] | None = None,
    context: str = "",
) -> dict:
    """Asks the model for a new bounty (one retry). Raises `LlmUnavailable` if it can't produce one."""
    avoid = f" Do not reuse these titles: {', '.join(taken_titles)}." if taken_titles else ""

    class BountyReply(BaseModel):
        model_config = ConfigDict(extra="forbid")

        title: str = Field(max_length=MAX_TITLE)
        icon: str = Field(default="✨", max_length=4)
        description: str = Field(default="", max_length=MAX_DESCRIPTION)
        rule: Any

    def valid(reply: BountyReply) -> bool:
        bounty = parse_custom_bounty(json.dumps(reply.model_dump()), taken_titles)
        runnable = custom_bounty(bounty) if bounty else None
        return (
            bounty is not None and runnable is not None and (feasible is None or feasible(runnable))
        )

    result = await llm.generate_structured(
        config,
        BOUNTY_SYSTEM
        + " Return JSON with title, icon, description and rule fields. "
        + "The rule must contain one to three conditions. "
        + avoid,
        {"mode_context": context},
        BountyReply,
        retries=1,
        fallback={"title": "", "icon": "✨", "description": "", "rule": {}},
        validator=valid,
    )
    bounty = parse_custom_bounty(json.dumps(result.value.model_dump()), taken_titles)
    runnable = custom_bounty(bounty) if bounty else None
    if bounty is None or runnable is None or (feasible is not None and not feasible(runnable)):
        raise llm.LlmUnavailable("The model didn't produce a usable bounty")
    return bounty


def _taken_titles(rules: dict | None) -> list[str]:
    titles = [b.title for b in BOUNTIES.values()]
    titles += [d.get("title", "") for d in ((rules or {}).get(CUSTOM_KEY) or {}).values()]
    return [t for t in titles if t]


async def _try_generate(
    session: Session,
    rules: dict | None,
    feasible: Callable[[Bounty], bool] | None = None,
    context: str = "",
) -> dict | None:
    config = llm.load_config(session)
    if not config.enabled:
        return None
    try:
        return await generate_custom_bounty(config, _taken_titles(rules), feasible, context)
    except llm.LlmUnavailable:
        return None


class BountyError(Exception):
    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


async def roll_custom(
    session: Session,
    rules: dict | None,
    feasible: Callable[[Bounty], bool] | None = None,
    context: str = "",
    difficulty: Callable[[Bounty], int] | None = None,
) -> dict:
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
            "Settings > Integrations > AI & Embeddings."
        )
    try:
        custom = await generate_custom_bounty(config, _taken_titles(rules), feasible, context)
    except llm.LlmUnavailable as exc:
        replacement = (
            draw_replacement(
                active,
                rules.get(COMPLETED_KEY) or [],
                random.Random(),
                (lambda bounty_id: feasible(BOUNTIES[bounty_id])) if feasible else None,
                (lambda bounty_id: difficulty(BOUNTIES[bounty_id])) if difficulty else None,
            )
            if feasible
            else None
        )
        if replacement is None:
            raise BountyError(f"{exc}; no fair static fallback is available", 503) from exc
        return {
            **rules,
            ACTIVE_KEY: [*active[1:], replacement],
            "bounty_roll_note": "AI quest unavailable or unfair; dealt a feasible static quest instead.",
        }
    board = [*active[1:], custom["id"]] if active else [custom["id"]]
    return {
        **rules,
        ACTIVE_KEY: board,
        "bounty_roll_note": None,
        CUSTOM_KEY: {**(rules.get(CUSTOM_KEY) or {}), custom["id"]: custom},
    }
