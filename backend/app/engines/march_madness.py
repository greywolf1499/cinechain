"""Watchlist March Madness: a 16-film single-elimination bracket.

`rules_config["bracket"]` holds the whole tournament:

    {"round_of_16": [8 matchups], "quarterfinals": [4], "semifinals": [2], "finals": [1],
     "champion": None}

Each matchup is `{"id": "quarterfinals-2", "a": movie_id | None, "b": movie_id | None,
"winner": movie_id | None, "votes": {user_id: movie_id}}`. A winner moves into the next round's
matchup `index // 2` (slot `a` for an even index, `b` for an odd one); deciding the final crowns the
champion. Winners are logged as watched steps by the bracket endpoints (`api/routes_bracket.py`) -
nothing can be logged by hand - and `rules_config["bracket_films"]` snapshots every film's card data
so the bracket renders without 16 detail fetches.
"""

from __future__ import annotations

import copy
import random
from typing import Any, ClassVar

from sqlmodel import Session, select

from app.engines.base import RunSetupError
from app.engines.rulebook import RuleSection
from app.engines.trackers import TrackerEngine
from app.models.cache import CachedMovie
from app.models.curated import LetterboxdWatchlist
from app.schemas.engine import ValidationResult
from app.services import cache_repo
from app.utils.dates import parse_release_year

MARCH_MADNESS = "march_madness"
BRACKET_SIZE = 16
ROUNDS = ("round_of_16", "quarterfinals", "semifinals", "finals")
IDS_KEY = "bracket_movie_ids"
SEED_FLAG = "seed_from_watchlist"


class BracketError(Exception):
    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


def build_bracket(movie_ids: list[int]) -> dict[str, Any]:
    """The opening bracket: adjacent films meet in the round of 16 (1v2, 3v4...)."""
    if len(movie_ids) != BRACKET_SIZE or len(set(movie_ids)) != BRACKET_SIZE:
        raise ValueError(f"A bracket needs {BRACKET_SIZE} distinct films")

    def matchup(round_name: str, index: int, a: int | None = None, b: int | None = None) -> dict:
        return {"id": f"{round_name}-{index + 1}", "a": a, "b": b, "winner": None, "votes": {}}

    bracket: dict[str, Any] = {
        "round_of_16": [
            matchup("round_of_16", i, movie_ids[2 * i], movie_ids[2 * i + 1]) for i in range(8)
        ],
        "quarterfinals": [matchup("quarterfinals", i) for i in range(4)],
        "semifinals": [matchup("semifinals", i) for i in range(2)],
        "finals": [matchup("finals", 0)],
        "champion": None,
    }
    return bracket


def find_matchup(bracket: dict, matchup_id: str) -> tuple[str, int, dict]:
    for round_name in ROUNDS:
        for index, matchup in enumerate(bracket.get(round_name, [])):
            if matchup["id"] == matchup_id:
                return round_name, index, matchup
    raise BracketError(f"No matchup {matchup_id!r} in this bracket", 404)


def advance_matchup(
    bracket: dict, matchup_id: str, winning_movie_id: int
) -> tuple[dict, str, bool]:
    """(the new bracket, the round that was decided, whether this crowned the champion).
    The input is never mutated."""
    if bracket.get("champion") is not None:
        raise BracketError("The champion has already been crowned")
    updated = copy.deepcopy(bracket)
    round_name, index, matchup = find_matchup(updated, matchup_id)
    if matchup["a"] is None or matchup["b"] is None:
        raise BracketError("This matchup isn't ready: both films must be decided first")
    if matchup["winner"] is not None:
        raise BracketError("This matchup has already been decided")
    if winning_movie_id not in (matchup["a"], matchup["b"]):
        raise BracketError("The winner must be one of the two films in the matchup", 422)
    matchup["winner"] = winning_movie_id
    matchup["votes"] = {}
    if round_name == "finals":
        updated["champion"] = winning_movie_id
        return updated, round_name, True
    following = ROUNDS[ROUNDS.index(round_name) + 1]
    updated[following][index // 2]["a" if index % 2 == 0 else "b"] = winning_movie_id
    return updated, round_name, False


def record_vote(bracket: dict, matchup_id: str, user_id: str, movie_id: int) -> dict:
    updated = copy.deepcopy(bracket)
    _, _, matchup = find_matchup(updated, matchup_id)
    if matchup["a"] is None or matchup["b"] is None or matchup["winner"] is not None:
        raise BracketError("This matchup isn't open for voting")
    if movie_id not in (matchup["a"], matchup["b"]):
        raise BracketError("Vote for one of the two films in the matchup", 422)
    matchup["votes"][user_id] = movie_id
    return updated


def majority_winner(matchup: dict, participant_ids: list[str]) -> int | None:
    """The film with a strict majority of *all* participants' votes, else None (a tie or too
    few votes stays open for someone to settle with Advance Winner)."""
    counts: dict[int, int] = {}
    for user_id, movie_id in matchup["votes"].items():
        if user_id in participant_ids:
            counts[movie_id] = counts.get(movie_id, 0) + 1
    for movie_id, count in counts.items():
        if count * 2 > len(participant_ids):
            return movie_id
    return None


def seed_from_watchlist(
    session: Session, user_id: str, rng: random.Random | None = None
) -> list[int]:
    """16 distinct random films from the user's synced Letterboxd watchlist."""
    ids = sorted(
        {
            row.movie_id
            for row in session.exec(
                select(LetterboxdWatchlist).where(LetterboxdWatchlist.user_id == user_id)
            ).all()
        }
    )
    if len(ids) < BRACKET_SIZE:
        raise RunSetupError(
            f"Your Letterboxd watchlist has {len(ids)} films - sync at least {BRACKET_SIZE} "
            "(or pick the films by hand) to seed a bracket"
        )
    return (rng or random).sample(ids, BRACKET_SIZE)


class MarchMadnessEngine(TrackerEngine):
    queue_policy = "none"
    modifier_scopes = frozenset()
    tagline = "16 films enter, one is crowned"
    tags: ClassVar[list[str]] = ["Tournament", "Watchlist", "Partner voting"]
    rulebook: ClassVar[RuleSection] = RuleSection(
        "Crown a champion from a 16-film bracket.",
        ["Compare two films and choose a winner.", "Repeat until one film wins the final."],
        ["Each decision eliminates one film; the final winner is the champion."],
        ["Eliminated films leave the tournament; there is no points-based loss."],
        [
            "Agree on what makes a winner before voting.",
            "Compare the pair in front of you, not a favourite from another branch.",
        ],
        [],
    )
    seed_policy = "none"
    game_type = MARCH_MADNESS
    supports_bounty_board = False
    display_name = "Watchlist March Madness"
    description = (
        "A 16-film single-elimination tournament: pit watchlist films head to head, advance the "
        "winners round by round and crown a champion."
    )

    def validate_rules_config(self, rules: dict | None) -> list[str]:
        problems = super().validate_rules_config(rules)
        rules = rules or {}
        ids = rules.get(IDS_KEY)
        if rules.get(SEED_FLAG):
            return problems
        if (
            not isinstance(ids, list)
            or len(ids) != BRACKET_SIZE
            or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids)
        ):
            problems.append(
                f"{IDS_KEY} must list exactly {BRACKET_SIZE} film ids (or set {SEED_FLAG}: true)"
            )
        elif len(set(ids)) != BRACKET_SIZE:
            problems.append(f"{IDS_KEY} must not repeat a film")
        return problems

    async def prepare_run(self, rules: dict, user_id: str) -> dict:
        ids = rules.get(IDS_KEY)
        if rules.get(SEED_FLAG):
            ids = seed_from_watchlist(self.session, user_id)
        films: dict[str, dict] = {}
        for movie_id in ids:
            try:
                movie = await cache_repo.get_movie(self.session, self.tmdb, movie_id)
                if movie.overview is None:
                    movie = await cache_repo.get_movie(
                        self.session, self.tmdb, movie_id, refresh=True
                    )
            except Exception as exc:
                raise RunSetupError(f"Film {movie_id} couldn't be looked up: {exc}") from exc
            films[str(movie_id)] = film_card(movie)
        rest = {k: v for k, v in rules.items() if k not in (IDS_KEY, SEED_FLAG)}
        return {**rest, "bracket": build_bracket(list(ids)), "bracket_films": films}

    async def validate_candidate(self, movie_id: int, rules: dict) -> ValidationResult:
        return ValidationResult(
            valid=False,
            blocked=True,
            reason="March Madness films are logged by deciding matchups in the bracket",
        )


def film_card(movie: CachedMovie) -> dict[str, Any]:
    return {
        "title": movie.title,
        "release_year": parse_release_year(movie.release_date),
        "poster_path": movie.poster_path,
        "runtime": movie.runtime,
        "overview": movie.overview or "",
        "tagline": movie.tagline or "",
    }
