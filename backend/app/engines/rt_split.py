"""The Rotten Tomatoes Split: critics vs audiences, settled by the household.

Only *split* films count: the Rotten Tomatoes Tomatometer and the audience score disagree by at
least `MIN_DIVERGENCE` points. OMDb has no Rotten Tomatoes audience score, so the audience side is
the IMDb rating on a 0-100 scale (7.2 -> 72). Both come from the local ratings cache
(`cached_movie_ratings`).

Partner A (the run's owner) is Team Critic 🍅, Partner B Team Audience 🍿. Every watched film is
logged with the household's joint `household_score` (1-100); the point goes to Team Critic when the
household sits strictly closer to the critics' score, otherwise (a tie included) to Team Audience.
The first team to `target_points` (default 3) wins. Scores are recomputed from the steps and cached
in `rules_config["split_scores"]` for the UI.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from sqlmodel import col, select

from app.engines.conditions import RunOutcome
from app.engines.rulebook import RuleSection
from app.engines.trackers import TrackerEngine
from app.models.cache import CachedMovie, CachedMovieRating
from app.models.run import (
    LEGACY_ENGINE_VERSION,
    RUN_STATUS_ACTIVE,
    RUN_STATUS_COMPLETED,
    Run,
    RunParticipant,
    RunStep,
)
from app.models.user import User
from app.schemas.engine import ValidationResult

RT_SPLIT = "rt_split"
MIN_DIVERGENCE = 25
TEAM_CRITIC = "team_a"
TEAM_AUDIENCE = "team_b"
DEFAULT_TARGET_POINTS = 3
MAX_TARGET_POINTS = 20

SCORES_KEY = "split_scores"
PLAYERS_KEY = "split_players"
TARGET_KEY = "target_points"
VICTORY_PREFIX = "Split Decided:"
TEAM_LABELS = {TEAM_CRITIC: "Team Critic 🍅", TEAM_AUDIENCE: "Team Audience 🍿"}


@dataclass(frozen=True)
class SplitScores:
    critic: int
    audience: int

    @property
    def divergence(self) -> int:
        return abs(self.critic - self.audience)


def parse_percent(raw: str | None) -> int | None:
    """OMDb's Rotten Tomatoes value ("88%") as a whole number."""
    try:
        value = int((raw or "").strip().rstrip("%"))
    except ValueError:
        return None
    return value if 0 <= value <= 100 else None


def parse_imdb(raw: str | None) -> int | None:
    """OMDb's IMDb rating ("7.2") on a 0-100 scale."""
    try:
        value = round(float((raw or "").strip()) * 10)
    except ValueError:
        return None
    return value if 0 <= value <= 100 else None


def split_scores(rating: CachedMovieRating | None) -> SplitScores | None:
    if rating is None:
        return None
    critic, audience = parse_percent(rating.rotten_tomatoes), parse_imdb(rating.imdb_rating)
    if critic is None or audience is None:
        return None
    return SplitScores(critic, audience)


def settle_point(household_score: int, scores: SplitScores) -> str:
    """The team a household rating scores for: strictly closer to the critics, else the audience."""
    if abs(household_score - scores.critic) < abs(household_score - scores.audience):
        return TEAM_CRITIC
    return TEAM_AUDIENCE


def compute_scores(steps: Sequence[RunStep]) -> dict[str, int]:
    scores = {TEAM_CRITIC: 0, TEAM_AUDIENCE: 0}
    for step in steps:
        if (step.transition_metadata or {}).get("split_no_contest"):
            continue
        team = (step.transition_metadata or {}).get("point_to")
        if step.status == "watched" and team in scores:
            scores[team] += 1
    return scores


def target_points(rules: dict | None) -> int:
    target = (rules or {}).get(TARGET_KEY)
    if isinstance(target, int) and not isinstance(target, bool) and target >= 1:
        return target
    return DEFAULT_TARGET_POINTS


def winning_team(scores: dict[str, int], rules: dict | None) -> str | None:
    target = target_points(rules)
    reached = [team for team, points in scores.items() if points >= target]
    return max(reached, key=lambda team: scores[team]) if reached else None


class RottenTomatoesSplitEngine(TrackerEngine):
    tagline = "🍅 Critics vs 🍿 Audience"
    tags: ClassVar[list[str]] = ["Tomatometer split", "Head to head", "Household rating"]
    rulebook: ClassVar[RuleSection] = RuleSection(
        "Be the first side to {target_points} points.",
        ["Choose a film with at least {divergence} points between RT critics and IMDb audience (IMDb x10).",
         "Watch it, then give a household rating; retry missing scores or log it as no-contest."],
        ["The side closer to the household rating gains 1 point; ties go to audience.",
         "No-contest records the watched film with zero points."],
        ["The opposing side wins when it reaches {target_points} first."],
        ["Pick a split where your household's taste is likely to favour your side.",
         "A dramatic gap is an opportunity, not a guarantee of your point."], ["no_contest"],
    )

    @classmethod
    def rulebook_values(cls, rules: dict | None) -> dict[str, Any]:
        return {**super().rulebook_values(rules), "target_points": target_points(rules),
                "divergence": MIN_DIVERGENCE}
    seed_policy = "none"
    game_type = RT_SPLIT
    requires: ClassVar[list[str]] = ["omdb"]
    display_name = "The Rotten Tomatoes Split"
    description = (
        "Critics vs audiences: watch films the Tomatometer and the crowd disagree about, rate "
        "them together, and see whose side the household lands on. First to 3 points wins."
    )

    def validate_rules_config(self, rules: dict | None) -> list[str]:
        problems = super().validate_rules_config(rules)
        target = (rules or {}).get(TARGET_KEY)
        if target is not None and (
            isinstance(target, bool)
            or not isinstance(target, int)
            or not 1 <= target <= MAX_TARGET_POINTS
        ):
            problems.append(f"{TARGET_KEY} must be a whole number from 1 to {MAX_TARGET_POINTS}")
        return problems

    def prepare_rules_config(self, rules: dict) -> dict:
        return {**rules, TARGET_KEY: target_points(rules)}

    # --- the pool ---

    def scores_of(self, movie_id: int) -> SplitScores | None:
        return split_scores(self.session.get(CachedMovieRating, movie_id))

    def split_pool(
        self, exclude_ids: Sequence[int] = (), limit: int = 30
    ) -> list[tuple[CachedMovie, SplitScores]]:
        """Cached films whose critic and audience scores diverge enough, biggest split first."""
        pool: list[tuple[CachedMovie, SplitScores]] = []
        for rating, movie in self.session.exec(
            select(CachedMovieRating, CachedMovie).join(
                CachedMovie, col(CachedMovie.tmdb_id) == col(CachedMovieRating.movie_id)
            )
        ).all():
            scores = split_scores(rating)
            if scores and scores.divergence >= MIN_DIVERGENCE and movie.tmdb_id not in exclude_ids:
                pool.append((movie, scores))
        pool.sort(key=lambda entry: (-entry[1].divergence, entry[0].title))
        return pool[:limit]

    async def validate_candidate(
        self, movie_id: int, rules: dict, *, no_contest: bool = False
    ) -> ValidationResult:
        if no_contest:
            return ValidationResult(valid=True)
        row = self.session.get(CachedMovie, movie_id)
        title = row.title if row is not None else f"Film {movie_id}"
        scores = self.scores_of(movie_id)
        if scores is None:
            return ValidationResult(
                valid=False,
                blocked=True,
                reason=f"No Rotten Tomatoes and IMDb scores on file for {title} (needs OMDb)",
            )
        if scores.divergence < MIN_DIVERGENCE:
            return ValidationResult(
                valid=False,
                blocked=True,
                reason=(
                    f"Not a split: {title} has critics {scores.critic}% and audience "
                    f"{scores.audience}% (a gap of {scores.divergence}, need {MIN_DIVERGENCE}+)"
                ),
            )
        return ValidationResult(valid=True)

    def settle(self, movie_id: int, household_score: int) -> dict[str, Any]:
        """The server-owned metadata for a watched split film rated `household_score`."""
        scores = self.scores_of(movie_id)
        if scores is None:
            raise ValueError(f"Film {movie_id} has no split scores")
        return {
            "household_score": household_score,
            "critic_score": scores.critic,
            "audience_score": scores.audience,
            "divergence": scores.divergence,
            "point_to": settle_point(household_score, scores),
        }

    # --- teams and scoring ---

    def team_players(self, run: Run) -> dict[str, str | None]:
        """Team Critic = the run's owner, Team Audience = the next partner who joined."""
        participants = self.session.exec(
            select(RunParticipant)
            .where(RunParticipant.run_id == run.id)
            .order_by(RunParticipant.joined_at)
        ).all()
        ids = [p.user_id for p in sorted(participants, key=lambda p: p.role != "owner")]
        return {
            TEAM_CRITIC: ids[0] if ids else None,
            TEAM_AUDIENCE: ids[1] if len(ids) > 1 else None,
        }

    def sync_run_state(self, run: Run, steps: Sequence[RunStep]) -> None:
        rules = run.rules_config or {}
        scores = compute_scores(steps)
        players = self.team_players(run)
        if rules.get(SCORES_KEY) != scores or rules.get(PLAYERS_KEY) != players:
            run.rules_config = {**rules, SCORES_KEY: scores, PLAYERS_KEY: players}
            self.session.add(run)

    def _team_name(self, run: Run, team: str) -> str:
        user_id = self.team_players(run)[team]
        user = self.session.get(User, user_id) if user_id else None
        return f"{TEAM_LABELS[team]} ({user.display_name})" if user else TEAM_LABELS[team]

    def evaluate_run_outcome(self, run: Run, steps: list[RunStep]) -> RunOutcome | None:
        if run.engine_version <= LEGACY_ENGINE_VERSION or run.status != RUN_STATUS_ACTIVE:
            return None
        scores = compute_scores(steps)
        winner = winning_team(scores, run.rules_config)
        if winner is not None:
            loser = TEAM_AUDIENCE if winner == TEAM_CRITIC else TEAM_CRITIC
            return RunOutcome(
                RUN_STATUS_COMPLETED,
                f"{VICTORY_PREFIX} {self._team_name(run, winner)} wins "
                f"{scores[winner]}-{scores[loser]}!",
            )
        return super().evaluate_run_outcome(run, steps)
