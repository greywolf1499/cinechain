"""The Method Actor Marathon: watch one actor's career in (near) chronological order.

At creation the actor's acting credits are curated into a *career track* stored in
`rules_config["filmography"]` (oldest first, with the actor's age at release and milestone tags);
`rules_config["actor"]` is `{"id", "name"}`. A step must be a film on the track (a hard rule) and
follow the chosen order: strict (skip 0), relaxed (skip 2), or free (any on-track film).
Legacy numeric `max_skip` remains supported. Endless tracks wrap manually.

Milestones (a film may carry several): `debut` (the first credited film), `breakout` (the first
top-3 billed role in a film many people rated), `prestige_peak` (the best-rated well-known film)
and `modern_resurgence` (a major role in the last five years).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any, ClassVar

from app.engines.base import RunSetupError
from app.engines.conditions import RunOutcome
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
from app.services.tmdb import TMDBError

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


def marathon_skip(rules: dict | None, default: int = DEFAULT_MAX_SKIP) -> int | None:
    config = rules or {}
    if "order" in config:
        return ORDER_SKIPS[config["order"]]
    return config.get(MAX_SKIP_KEY, default)


def marathon_order_rule(rules: dict | None, default: int = DEFAULT_MAX_SKIP) -> str:
    skip = marathon_skip(rules, default)
    return "Pick any unwatched on-track film in any order." if skip is None else (
        f"Advance along the track; skip at most {skip} entries between picks."
    )


def marathon_completion_rule(rules: dict | None) -> str:
    if (rules or {}).get("track_length") == "endless":
        return "Use Wrap the marathon when you are done; completion records your watched percentage."
    if marathon_skip(rules) is None:
        return "Watch every track film to complete the marathon."
    return "Reaching the last track entry completes the marathon."


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
    credits: Sequence[dict[str, Any]], birthday: str | None, today: date | None = None,
    *, length: str = "full",
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
            }
            for c in unique.values()
            if c.get("id") is not None and _is_feature_role(c, today)
        ),
        key=lambda f: (f["release_date"], f["movie_id"]),
    )
    if not films:
        raise RunSetupError("This person has no feature film credits to build a career track from")

    films[0]["milestones"].append("debut")
    breakout = _first_with_tier(
        [f for f in films if f["order"] <= BREAKOUT_BILLING],
        BREAKOUT_VOTE_TIERS,
        lambda eligible: eligible[0],
    )
    if breakout is not None:
        breakout["milestones"].append("breakout")
    peak = _first_with_tier(
        films,
        PRESTIGE_VOTE_TIERS,
        lambda eligible: max(eligible, key=lambda f: (f["vote_average"], f["vote_count"])),
    )
    if peak is not None:
        peak["milestones"].append("prestige_peak")
    recent = _first_with_tier(
        [
            f
            for f in films
            if f["year"] >= today.year - RESURGENCE_YEARS and f["order"] <= RESURGENCE_BILLING
        ],
        RESURGENCE_VOTE_TIERS,
        lambda eligible: max(eligible, key=lambda f: f["vote_count"]),
    )
    if recent is not None:
        recent["milestones"].append("modern_resurgence")

    # Curate: every milestone film, then the best-known leading roles, capped for a marathon.
    def keep_rank(f: dict) -> tuple:
        return (
            bool(f["milestones"]),
            f["order"] <= TRACK_MAX_BILLING and f["vote_count"] >= TRACK_MIN_VOTES,
            f["vote_count"],
        )

    chosen = sorted(films, key=keep_rank, reverse=True)
    solid = [f for f in chosen if f["milestones"] or keep_rank(f)[1]]
    if length == "milestones":
        selected = [f for f in chosen if f["milestones"]]
        # Several milestones can belong to one film; pad with ranked features.
        selected += [f for f in chosen if not f["milestones"]][:max(0, MIN_TRACK_FILMS - len(selected))]
    elif length == "endless":
        selected = chosen[:TRACK_LENGTHS[length]]
    else:
        selected = solid if len(solid) >= MIN_TRACK_FILMS else chosen[:MIN_TRACK_FILMS]
        selected = selected[:TRACK_LENGTHS[length]]
    track = sorted(selected, key=lambda f: (f["release_date"], f["movie_id"]))
    for film in track:
        film["age"] = age_at(birthday, film["release_date"])
    return track


class MethodActorEngine(TrackerEngine):
    rule_fields: ClassVar[list[RuleField]] = [
        *TrackerEngine.rule_fields,
        RuleField(key="track_length", kind="enum", label="Marathon length",
                  options=list(TRACK_LENGTHS), default="feature",
                  help="Milestones (at least 3 when available), short (6), feature (12), full (25), endless (up to 60; wrap manually)."),
        RuleField(key="order", kind="segmented", label="Strictness",
                  options=list(ORDER_SKIPS), default="relaxed",
                  help="Strict: skip 0. Relaxed: skip 2. Free: any order, still on-track."),
    ]
    presets: ClassVar[list[Preset]] = [
        Preset(id="taster", label="Taster", blurb="Career milestones in any order.",
               values={"track_length": "milestones", "order": "free"}),
        Preset(id="biopic", label="Biopic", blurb="Twelve features; skip up to two.",
               values={"track_length": "feature", "order": "relaxed"}),
        Preset(id="completist", label="Completist", blurb="Up to 25 features in strict order.",
               values={"track_length": "full", "order": "strict"}),
    ]
    default_preset = "biopic"
    tagline = "One career, in order"
    tags: ClassVar[list[str]] = ["One actor", "Chronological", "Milestones"]
    rulebook: ClassVar[RuleSection] = RuleSection(
        "Explore {person_name}'s chronological career track.",
        ["{order_rule}"],
        ["{completion_rule}"],
        ["Off-track films are blocked; order violations require a soft-rule override."],
        ["Use skips to avoid an unavailable film without jumping past a milestone.",
         "Compare early and late roles to notice how the actor's craft changes."], ["track", "seed", "wildcard"],
    )

    @classmethod
    def rulebook_values(cls, rules: dict | None) -> dict[str, Any]:
        config = rules or {}
        return {**super().rulebook_values(rules),
                "person_name": (config.get("actor") or {}).get("name", "your chosen actor"),
                "max_skip": marathon_skip(config),
                "order_rule": marathon_order_rule(config),
                "completion_rule": marathon_completion_rule(config)}
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
        track = build_career_track(credits, person.get("birthday"), length=rules.get("track_length", "feature"))
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
