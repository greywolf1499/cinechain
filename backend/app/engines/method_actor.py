"""The Method Actor Marathon: watch one actor's career in (near) chronological order.

At creation the actor's acting credits are curated into a *career track* stored in
`rules_config["filmography"]` (oldest first, with the actor's age at release and milestone tags);
`rules_config["actor"]` is `{"id", "name"}`. A step must be a film on the track (a hard rule) and
follow the chosen order: strict (skip 0), relaxed (skip 2), or free (any on-track film).
Legacy numeric `max_skip` remains supported. Endless tracks wrap manually.

Milestones carry TMDB or measured overview evidence, not biographical claims. Subjective
era labels are player annotations; keyword-based villain turns are suggestions only.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, date, datetime
from itertools import pairwise
from typing import Any, ClassVar

import numpy as np
from sqlmodel import Session

from app.engines.base import RunSetupError
from app.engines.conditions import RunOutcome
from app.engines.genre_pendulum import TMDB_GENRE_IDS
from app.engines.rulebook import RuleSection
from app.engines.trackers import TrackerEngine
from app.models.cache import CachedMovie
from app.models.run import (
    LEGACY_ENGINE_VERSION,
    RUN_STATUS_ACTIVE,
    RUN_STATUS_COMPLETED,
    Run,
    RunStep,
)
from app.schemas.engine import Preset, RuleField, ValidationResult
from app.services import embeddings
from app.services.tmdb import TMDBClient, TMDBError

METHOD_ACTOR = "method_actor"
ACTOR_ID_KEY = "actor_id"
MAX_SKIP_KEY = "max_skip"
DEFAULT_MAX_SKIP = 2
MAX_SKIP_LIMIT = 10

MAX_TRACK_FILMS = 25
MIN_TRACK_FILMS = 3
TRACK_LENGTHS = {"milestones": 3, "short": 6, "feature": 12, "full": MAX_TRACK_FILMS, "endless": 60}
ORDER_SKIPS = {"strict": 0, "relaxed": 2, "free": None}
TRACK_MIN_VOTES = 50
TRACK_MAX_BILLING = 14
BREAKOUT_BILLING = 2  # top-3 billed
BREAKOUT_VOTE_TIERS = (500, 100)
PRESTIGE_VOTE_TIERS = (200, 20)
RESURGENCE_YEARS = 5
RESURGENCE_BILLING = 4  # top-5 billed
RESURGENCE_VOTE_TIERS = (100, 20)
DOCUMENTARY, TV_MOVIE = 99, 10770
_NOT_A_ROLE = re.compile(
    r"\b(self|himself|herself|themselves|archive footage|uncredited)\b", re.IGNORECASE
)
_GENRE_NAMES = {value: name for name, value in TMDB_GENRE_IDS.items()}
ERA_BOUNDARIES = {"genre_pivot", "against_type", "comeback"}
VILLAIN_KEYWORDS = {"villain", "supervillain", "antagonist", "serial killer", "psychopath"}
RELEASE_EVIDENCE_SECONDS = 10.0
VILLAIN_EVIDENCE_SECONDS = 10.0


def _copy_films(films: Sequence[dict]) -> list[dict]:
    return [
        {**f, "milestones": list(f.get("milestones", [])), "evidence": dict(f.get("evidence", {}))}
        for f in films
    ]


def _flag(film: dict, milestone: str, evidence: str) -> None:
    if milestone not in film["milestones"]:
        film["milestones"].append(milestone)
    film["evidence"][milestone] = evidence


def flag_first_lead(films: Sequence[dict]) -> list[dict]:
    result = _copy_films(films)
    first = next((f for f in result if f.get("order") == 0), None)
    if first is not None:
        _flag(
            first,
            "first_lead",
            f"First top-billed credit (TMDB order 0): {first.get('character') or 'unnamed role'}.",
        )
    return result


def _genre_distance(a: set[int], b: set[int]) -> float:
    return 1 - len(a & b) / len(a | b) if a | b else 0.0


def flag_genre_pivots(films: Sequence[dict]) -> list[dict]:
    result = _copy_films(films)
    for index in range(4, len(result) - 2):
        previous = result[index - 4 : index]
        following = result[index + 1 : index + 3]
        if not all(f.get("genre_ids") for f in [*previous, result[index], *following]):
            continue
        old = set().union(*(set(f["genre_ids"]) for f in previous))
        new = set(result[index]["genre_ids"])
        if _genre_distance(old, new) < 0.75:
            continue
        if all(
            _genre_distance(set(f["genre_ids"]), new) < _genre_distance(set(f["genre_ids"]), old)
            for f in following
        ):

            def names(ids: set[int]) -> str:
                return "/".join(_GENRE_NAMES.get(g, f"genre {g}") for g in sorted(ids))

            _flag(
                result[index],
                "genre_pivot",
                f"{names(new)} after four {names(old)} credits; the next two films confirm the shift.",
            )
    return result


def flag_comebacks(films: Sequence[dict]) -> list[dict]:
    result = _copy_films(films)
    for previous, film in pairwise(result):
        earlier, later = (
            date.fromisoformat(previous["release_date"]),
            date.fromisoformat(film["release_date"]),
        )
        years = (
            later.year - earlier.year - ((later.month, later.day) < (earlier.month, earlier.day))
        )
        if years >= 4:
            _flag(
                film,
                "comeback",
                f"First qualifying credit in {years} years: {earlier.isoformat()} to {later.isoformat()}.",
            )
    return result


def flag_language_crossover(films: Sequence[dict]) -> list[dict]:
    result = _copy_films(films)
    languages = Counter(f["original_language"] for f in result if f.get("original_language"))
    if languages:
        mode = languages.most_common(1)[0][0]
        first = next(
            (f for f in result if f.get("original_language") and f["original_language"] != mode),
            None,
        )
        if first is not None:
            _flag(
                first,
                "language_crossover",
                f"First {first['original_language']}-language credit; career modal language is {mode} (TMDB).",
            )
    return result


def flag_against_type(films: Sequence[dict], vectors: dict[int, np.ndarray]) -> list[dict]:
    result = _copy_films(films)
    shapes = {vector.shape for vector in vectors.values()}
    if len(shapes) != 1 or any(
        vector.ndim != 1
        or vector.size == 0
        or not np.all(np.isfinite(vector))
        or np.linalg.norm(vector) == 0
        for vector in vectors.values()
    ):
        return result
    distances: list[tuple[int, float]] = []
    for index, film in enumerate(result):
        history = result[max(0, index - 5) : index]
        current = vectors.get(film["movie_id"])
        previous = [vectors[f["movie_id"]] for f in history if f["movie_id"] in vectors]
        if current is None or len(previous) != len(history) or not previous:
            continue
        centroid = np.mean(previous, axis=0)
        if np.linalg.norm(centroid) == 0:
            continue
        distances.append((index, 1 - embeddings.cosine_similarity(current, centroid)))
    if len(distances) < 5:
        return result
    values = np.array([distance for _, distance in distances])
    spread = float(np.std(values))
    if spread <= 1e-8:
        return result
    threshold = float(np.mean(values)) + 2 * spread
    for index, distance in distances:
        if distance >= threshold:
            _flag(
                result[index],
                "against_type",
                f"Overview distance {distance:.3f} from the previous up-to-five credits; career mean + 2 SD is {threshold:.3f}.",
            )
    return result


def group_career_eras(films: Sequence[dict]) -> list[dict]:
    result = _copy_films(films)
    eras: list[list[dict]] = []
    for film in result:
        if not eras or ERA_BOUNDARIES & set(film["milestones"]):
            eras.append([])
        eras[-1].append(film)
    for index, era in enumerate(eras):
        genres = Counter(g for film in era for g in film.get("genre_ids", []))
        genre = genres.most_common(1)[0][0] if genres else None
        label = f"{_GENRE_NAMES.get(genre, 'Mixed')} era" if genre is not None else None
        for film in era:
            film.update(era_index=index, era_label=label)
    return result


def flag_first_theatrical(films: Sequence[dict], releases: dict[int, list[dict]]) -> list[dict]:
    result = _copy_films(films)
    if not result:
        return result

    def dates(film: dict) -> list[tuple[str, int]]:
        return sorted(
            (r["release_date"][:10], r["type"])
            for country in releases.get(film["movie_id"], [])
            for r in country.get("release_dates", [])
            if r.get("type") in {1, 2, 3, 4, 5, 6}
            and re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", r.get("release_date", ""))
        )

    debut = dates(result[0])
    if not debut or debut[0][1] not in {1, 4, 6}:
        return result
    theatrical = [(day, film) for film in result[:3] for day, kind in dates(film) if kind in {2, 3}]
    if theatrical:
        day, film = min(theatrical, key=lambda item: (item[0], item[1]["movie_id"]))
        _flag(
            film,
            "first_theatrical",
            f"Earliest debut release: {debut[0][0]} (TMDB type {debut[0][1]}); first recorded cinema release among the first three track films: {day}.",
        )
    return result


async def enrich_career_track(
    films: Sequence[dict], tmdb: TMDBClient, session: Session
) -> list[dict]:
    result = _copy_films(films)
    releases: dict[int, list[dict]] = {}
    try:
        async with asyncio.timeout(RELEASE_EVIDENCE_SECONDS):
            for film in result[:3]:
                releases[film["movie_id"]] = await tmdb.get_movie_release_dates(film["movie_id"])
    except (TMDBError, TimeoutError):
        pass  # Optional release evidence must never block career creation.
    else:
        result = flag_first_theatrical(result, releases)
    pending = [f for f in result if (f.get("overview") or "").strip()]
    if pending:
        config = embeddings.load_config(session)
        local = embeddings.EmbeddingConfig(local_preset=config.local_preset)
        try:
            batch = await embeddings.embed_batch(local, [f["overview"] for f in pending])
        except embeddings.EmbeddingUnavailable:
            pass
        else:
            if batch.fingerprint == local.fingerprint and len(batch.vectors) == len(pending):
                result = flag_against_type(
                    result, {f["movie_id"]: v for f, v in zip(pending, batch.vectors, strict=True)}
                )
    candidates = [
        f
        for f in result
        if f.get("character")
        and f.get("overview")
        and re.search(rf"(?<!\w){re.escape(f['character'])}(?!\w)", f["overview"], re.IGNORECASE)
    ]
    try:
        async with asyncio.timeout(VILLAIN_EVIDENCE_SECONDS):
            for film in candidates[:5]:
                keywords = {
                    keyword.lower() for keyword in await tmdb.get_movie_keywords(film["movie_id"])
                }
                if keywords & VILLAIN_KEYWORDS:
                    film["suggestions"] = ["villain"]
                    film["suggestion_evidence"] = (
                        f"TMDB keywords: {', '.join(sorted(keywords & VILLAIN_KEYWORDS))}; "
                        f"the overview mentions {film['character']}. Player confirmation required."
                    )
    except (TMDBError, TimeoutError):
        pass  # These optional suggestions never assert the character's role.
    return group_career_eras(result)


def career_era_problems(eras: Any, track: Sequence[dict]) -> list[str]:
    if not isinstance(eras, list) or len(eras) > 60:
        return ["career_eras must be a list of at most 60 player-tagged spans"]
    positions = {f["movie_id"]: index for index, f in enumerate(track)}
    occupied: set[int] = set()
    for era in eras:
        if not isinstance(era, dict) or set(era) != {"start_movie_id", "end_movie_id", "label"}:
            return ["Each career era needs start_movie_id, end_movie_id and label"]
        label = era["label"]
        start, end = era["start_movie_id"], era["end_movie_id"]
        if (
            type(start) is not int
            or type(end) is not int
            or start not in positions
            or end not in positions
            or positions[start] > positions[end]
            or not isinstance(label, str)
            or not label.strip()
            or len(label) > 80
        ):
            return [
                "Career eras need an ordered on-track span and a nonblank label of at most 80 characters"
            ]
        span = set(range(positions[start], positions[end] + 1))
        if occupied & span:
            return ["Player-tagged career eras cannot overlap"]
        occupied |= span
    return []


def marathon_skip(rules: dict | None, default: int = DEFAULT_MAX_SKIP) -> int | None:
    config = rules or {}
    if "order" in config:
        return ORDER_SKIPS[config["order"]]
    return config.get(MAX_SKIP_KEY, default)


def marathon_order_rule(rules: dict | None, default: int = DEFAULT_MAX_SKIP) -> str:
    skip = marathon_skip(rules, default)
    return (
        "Pick any unwatched on-track film in any order."
        if skip is None
        else (f"Move along the track. Skip at most {skip} films between picks.")
    )


def marathon_completion_rule(rules: dict | None) -> str:
    if (rules or {}).get("track_length") == "endless":
        return (
            "Use Wrap the marathon when you are done; completion records your watched percentage."
        )
    if marathon_skip(rules) is None:
        return "Watch every track film to complete the marathon."
    return "Watch the last track film to finish."


def marathon_finished(rules: dict, steps: list[RunStep]) -> bool:
    track = rules.get("filmography") or []
    if not track or rules.get("track_length") == "endless":
        return False
    watched = {step.movie_id for step in steps if step.status == "watched"}
    if marathon_skip(rules) is None:
        return {film["movie_id"] for film in track} <= watched
    return track[-1]["movie_id"] in watched


def _year(entry: dict) -> int:
    return int(entry["release_date"][:4])


def _is_feature_role(entry: dict, today: date) -> bool:
    release = entry.get("release_date") or ""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", release) or release > today.isoformat():
        return False
    if entry.get("adult") or {DOCUMENTARY, TV_MOVIE} & set(entry.get("genre_ids") or []):
        return False
    return not _NOT_A_ROLE.search(entry.get("character") or "")


def _first_with_tier(films: list[dict], tiers: tuple[int, ...], pick) -> dict | None:
    """The first non-empty candidate set by descending vote tier, reduced by `pick`."""
    for tier in tiers:
        eligible = [f for f in films if f["vote_count"] >= tier]
        if eligible:
            return pick(eligible)
    return None


def age_at(birthday: str | None, release_date: str) -> int | None:
    try:
        born = date.fromisoformat(birthday or "")
        released = date.fromisoformat(release_date)
    except ValueError:
        return None
    age = released.year - born.year - ((released.month, released.day) < (born.month, born.day))
    return age if age >= 0 else None


def build_career_track(
    credits: Sequence[dict[str, Any]],
    birthday: str | None,
    today: date | None = None,
    *,
    length: str | None = "full",
    acting: bool = True,
) -> list[dict[str, Any]]:
    """Curates TMDB acting credits into the career track (see the module docstring)."""
    today = today or datetime.now(UTC).date()
    unique = {credit["id"]: credit for credit in credits if credit.get("id") is not None}
    films = sorted(
        (
            {
                "movie_id": c["id"],
                "title": c.get("title") or "",
                "release_date": c["release_date"],
                "year": _year(c),
                "poster_path": c.get("poster_path"),
                "character": c.get("character") or None,
                "order": c["order"] if isinstance(c.get("order"), int) else 99,
                "vote_average": float(c.get("vote_average") or 0.0),
                "vote_count": int(c.get("vote_count") or 0),
                "milestones": [],
                "evidence": {},
                "genre_ids": list(c.get("genre_ids") or []),
                "original_language": c.get("original_language"),
                "overview": (c.get("overview") or "").strip(),
            }
            for c in unique.values()
            if c.get("id") is not None and _is_feature_role(c, today)
        ),
        key=lambda f: (f["release_date"], f["movie_id"]),
    )
    if not films:
        raise RunSetupError("This person has no feature film credits to build a career track from")

    _flag(films[0], "debut", f"First qualifying feature credit: {films[0]['release_date']}.")
    breakout = _first_with_tier(
        [f for f in films if f["order"] <= BREAKOUT_BILLING],
        BREAKOUT_VOTE_TIERS,
        lambda eligible: eligible[0],
    )
    if breakout is not None and acting:
        _flag(
            breakout,
            "breakout",
            f"First qualifying top-three billed credit: order {breakout['order']}, {breakout['vote_count']} TMDB votes.",
        )
    peak = _first_with_tier(
        films,
        PRESTIGE_VOTE_TIERS,
        lambda eligible: max(eligible, key=lambda f: (f["vote_average"], f["vote_count"])),
    )
    if peak is not None:
        _flag(
            peak,
            "prestige_peak",
            f"Highest TMDB rating in the qualifying vote tier: {peak['vote_average']:.1f}/10 from {peak['vote_count']} votes.",
        )
    recent = _first_with_tier(
        [
            f
            for f in films
            if f["year"] >= today.year - RESURGENCE_YEARS and f["order"] <= RESURGENCE_BILLING
        ],
        RESURGENCE_VOTE_TIERS,
        lambda eligible: max(eligible, key=lambda f: f["vote_count"]),
    )
    if recent is not None and acting:
        _flag(
            recent,
            "modern_resurgence",
            f"Most-voted top-five billed credit in the last {RESURGENCE_YEARS} years: {recent['vote_count']} votes.",
        )

    if acting:
        films = flag_first_lead(films)
    films = flag_language_crossover(flag_comebacks(flag_genre_pivots(films)))

    # Curate: every milestone film, then the best-known leading roles, capped for a marathon.
    def keep_rank(f: dict) -> tuple:
        return (
            bool(f["milestones"]),
            f["order"] <= TRACK_MAX_BILLING and f["vote_count"] >= TRACK_MIN_VOTES,
            f["vote_count"],
        )

    chosen = sorted(films, key=keep_rank, reverse=True)
    solid = [f for f in chosen if f["milestones"] or keep_rank(f)[1]]
    if length is None:
        selected = films
    elif length == "milestones":
        selected = [f for f in chosen if f["milestones"]]
        # Several milestones can belong to one film; pad with ranked features.
        selected += [f for f in chosen if not f["milestones"]][
            : max(0, MIN_TRACK_FILMS - len(selected))
        ]
    elif length == "endless":
        selected = chosen[: TRACK_LENGTHS[length]]
    else:
        selected = solid if len(solid) >= MIN_TRACK_FILMS else chosen[:MIN_TRACK_FILMS]
        selected = selected[: TRACK_LENGTHS[length]]
    track = sorted(selected, key=lambda f: (f["release_date"], f["movie_id"]))
    for film in track:
        film["age"] = age_at(birthday, film["release_date"])
    return group_career_eras(track)


class MethodActorEngine(TrackerEngine):
    modifier_scopes = frozenset({"film"})
    rule_fields: ClassVar[list[RuleField]] = [
        *TrackerEngine.rule_fields,
        RuleField(
            key="track_length",
            kind="enum",
            label="Marathon length",
            options=list(TRACK_LENGTHS),
            default="feature",
            help="Milestones (at least 3 when available), short (6), feature (12), full (25), endless (up to 60; wrap manually).",
        ),
        RuleField(
            key="order",
            kind="segmented",
            label="Strictness",
            options=list(ORDER_SKIPS),
            default="relaxed",
            help="Strict: skip 0. Relaxed: skip 2. Free: any order, still on-track.",
        ),
    ]
    presets: ClassVar[list[Preset]] = [
        Preset(
            id="taster",
            label="Taster",
            blurb="Career milestones in any order.",
            values={"track_length": "milestones", "order": "free"},
        ),
        Preset(
            id="biopic",
            label="Biopic",
            blurb="Twelve features; skip up to two.",
            values={"track_length": "feature", "order": "relaxed"},
        ),
        Preset(
            id="completist",
            label="Completist",
            blurb="Up to 25 features in strict order.",
            values={"track_length": "full", "order": "strict"},
        ),
    ]
    default_preset = "biopic"
    tagline = "One career, in order"
    tags: ClassVar[list[str]] = ["One actor", "Chronological", "Milestones"]
    rulebook: ClassVar[RuleSection] = RuleSection(
        "Watch {person_name}'s films through the years.",
        ["{order_rule}"],
        ["{completion_rule}"],
        ["Films off the track are blocked.", "Use a wildcard to skip too far ahead."],
        [
            "Use skips to avoid an unavailable film without jumping past a milestone.",
            "Compare early and late roles to notice how the actor's craft changes.",
        ],
        ["track", "seed", "wildcard"],
    )

    @classmethod
    def rulebook_values(cls, rules: dict | None) -> dict[str, Any]:
        config = rules or {}
        return {
            **super().rulebook_values(rules),
            "person_name": (config.get("actor") or {}).get("name", "your chosen actor"),
            "max_skip": marathon_skip(config),
            "order_rule": marathon_order_rule(config),
            "completion_rule": marathon_completion_rule(config),
        }

    seed_policy = "none"
    game_type = METHOD_ACTOR
    display_name = "The Method Actor Marathon"
    description = (
        "Pick an actor and watch their career unfold in order: from the debut through the "
        "breakout and the prestige peak to the modern resurgence."
    )

    def validate_rules_config(self, rules: dict | None) -> list[str]:
        problems = super().validate_rules_config(rules)
        rules = rules or {}
        actor_id = rules.get(ACTOR_ID_KEY)
        if (
            isinstance(actor_id, bool) or not isinstance(actor_id, int) or actor_id < 1
        ) and not isinstance((rules.get("actor") or {}).get("id"), int):
            problems.append(f"{ACTOR_ID_KEY} is required (search for an actor)")
        skip = rules.get(MAX_SKIP_KEY)
        if skip is not None and (
            isinstance(skip, bool) or not isinstance(skip, int) or not 0 <= skip <= MAX_SKIP_LIMIT
        ):
            problems.append(f"{MAX_SKIP_KEY} must be a whole number from 0 to {MAX_SKIP_LIMIT}")
        if "career_eras" in rules and "filmography" in rules:
            problems += career_era_problems(rules["career_eras"], rules["filmography"])
        return problems

    async def prepare_run(self, rules: dict, user_id: str) -> dict:
        actor_id = rules[ACTOR_ID_KEY]
        try:
            person = await self.tmdb.get_person(actor_id)
            credits = await self.tmdb.get_person_cast_credits_raw(actor_id)
        except TMDBError as exc:
            if exc.status_code == 404:
                raise RunSetupError(f"No person with TMDB id {actor_id}") from exc
            raise RunSetupError(f"TMDB lookup failed: {exc}", 502) from exc
        track = build_career_track(
            credits, person.get("birthday"), length=rules.get("track_length", "feature")
        )
        track = await enrich_career_track(track, self.tmdb, self.session)
        era_problems = career_era_problems(rules.get("career_eras", []), track)
        if era_problems:
            raise RunSetupError("; ".join(era_problems))
        rest = {k: v for k, v in rules.items() if k != ACTOR_ID_KEY}
        return {
            **rest,
            "track_length": rules.get("track_length", "feature"),
            MAX_SKIP_KEY: marathon_skip(rules),
            "actor": {"id": actor_id, "name": person.get("name") or str(actor_id)},
            "filmography": track,
        }

    # --- the career order ---

    @staticmethod
    def _positions(rules: dict | None) -> dict[int, int]:
        return {f["movie_id"]: i for i, f in enumerate((rules or {}).get("filmography") or [])}

    @staticmethod
    def _max_skip(rules: dict | None) -> int | None:
        return marathon_skip(rules)

    def _check(
        self, rules: dict | None, previous_id: int | None, movie_id: int
    ) -> ValidationResult:
        positions = self._positions(rules)
        track = (rules or {}).get("filmography") or []
        actor = ((rules or {}).get("actor") or {}).get("name", "this actor")
        if movie_id not in positions:
            title = self._title(movie_id)
            return ValidationResult(
                valid=False,
                blocked=True,
                reason=f"Off the track: {title} isn't on {actor}'s career track",
            )
        position = positions[movie_id]
        max_skip = self._max_skip(rules)
        if max_skip is None:
            return ValidationResult(valid=True)
        before = positions.get(previous_id, -1) if previous_id is not None else -1
        title = track[position]["title"]
        if position <= before:
            return ValidationResult(
                valid=False, reason=f"Career order: {title} comes before the film you just watched"
            )
        skipped = position - before - 1
        if skipped > max_skip:
            return ValidationResult(
                valid=False,
                reason=(
                    f"Career order: {title} skips {skipped} films - at most "
                    f"{self._max_skip(rules)} may be skipped"
                ),
            )
        return ValidationResult(valid=True)

    def _title(self, movie_id: int) -> str:
        row = self.session.get(CachedMovie, movie_id)
        return row.title if row is not None else f"Film {movie_id}"

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
        track = rules.get("filmography") or []
        if track and marathon_finished(rules, steps):
            name = (rules.get("actor") or {}).get("name", "the actor")
            return RunOutcome(RUN_STATUS_COMPLETED, f"Career complete: {name}")
        return super().evaluate_run_outcome(run, steps)
