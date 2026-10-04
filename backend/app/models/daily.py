from datetime import datetime
from typing import Any

from sqlmodel import JSON, Column, Field, SQLModel

from app.utils.ids import utcnow

ATTEMPT_IN_PROGRESS = "in_progress"
ATTEMPT_SOLVED = "solved"
ATTEMPT_FORFEITED = "forfeited"
FINISHED_ATTEMPT_STATUSES = frozenset({ATTEMPT_SOLVED, ATTEMPT_FORFEITED})


class DailyPuzzle(SQLModel, table=True):
    """The verified pair of one UTC day, computed on the first request of that day and then
    served from here (no cron, no repeated BFS). `optimal_path` is the shortest route:
    `[{"movie_id": int, "link": SharedActorConnection | None}, ...]` start -> target."""

    __tablename__ = "daily_puzzles"

    puzzle_date: str = Field(primary_key=True)  # ISO date
    puzzle_number: int
    start_movie_id: int
    target_movie_id: int
    par_hops: int
    optimal_path: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)


class DailyPuzzleAttempt(SQLModel, table=True):
    """One user's attempt at one day's puzzle. `chain` holds the accepted hops after the start
    film: `[{"movie_id": int, "link": SharedActorConnection}, ...]`."""

    __tablename__ = "daily_puzzle_attempts"

    puzzle_date: str = Field(primary_key=True, foreign_key="daily_puzzles.puzzle_date")
    user_id: str = Field(primary_key=True, foreign_key="users.id")
    status: str = Field(default=ATTEMPT_IN_PROGRESS)
    chain: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    # Per-hop emoji grade ("green" progressed towards the target, "yellow" didn't), set on a solve.
    grades: list[str] | None = Field(default=None, sa_column=Column(JSON))
    run_id: str | None = None  # the challenge run this attempt was converted into
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
