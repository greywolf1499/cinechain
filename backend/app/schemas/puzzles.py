from typing import Literal

from pydantic import BaseModel

from app.schemas.engine import SharedActorConnection
from app.schemas.movies import MovieSummary

AttemptStatus = Literal["not_started", "in_progress", "solved", "forfeited"]


class PuzzleHop(BaseModel):
    """A film in a chain plus the link that led into it (None for the starting film)."""

    movie: MovieSummary
    link: SharedActorConnection | None = None


class AttemptState(BaseModel):
    status: AttemptStatus = "not_started"
    chain: list[PuzzleHop] = []  # the accepted hops after the start film
    hops: int = 0
    # One per hop once solved: "green" got closer to the target, "yellow" didn't; the last is the target.
    grades: list[str] | None = None
    share_text: str | None = None
    run_id: str | None = None


class DailyPuzzleOut(BaseModel):
    puzzle_number: int
    date: str
    start_movie: MovieSummary
    target_movie: MovieSummary
    par_hops: int
    attempt: AttemptState
    # The shortest route; only revealed once the attempt is solved or forfeited.
    optimal_path: list[PuzzleHop] | None = None


class ValidateHopRequest(BaseModel):
    current_movie_id: int
    next_movie_id: int


class ValidateHopResult(BaseModel):
    valid: bool
    reason: str | None = None
    connections: list[SharedActorConnection] = []
    # True when the hop extended the user's attempt (it continues from the chain's tip).
    recorded: bool = False
    solved: bool = False
    next_movie: MovieSummary | None = None
    attempt: AttemptState


class ForfeitResult(BaseModel):
    puzzle_number: int
    par_hops: int
    optimal_path: list[PuzzleHop]
    attempt: AttemptState


class ConvertToRunResult(BaseModel):
    run_id: str
    movies: int
