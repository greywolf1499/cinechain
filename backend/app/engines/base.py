"""Strategy-pattern interface for pluggable challenge game rules."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import ClassVar

from sqlmodel import Session

from app.models.run import RunStep
from app.schemas.discovery import DiscoveryCandidate
from app.schemas.engine import RunStats, Suggestion, SuggestionFilters, ValidationResult
from app.services.tmdb import TMDBClient


class BaseChallengeEngine(ABC):
    """A challenge's game rules, bound to the current DB session and TMDB client."""

    game_type: str
    display_name: str
    description: str
    capabilities: ClassVar[list[str]]

    def __init__(self, session: Session, tmdb: TMDBClient) -> None:
        self.session = session
        self.tmdb = tmdb

    @abstractmethod
    async def validate_next_step(self, from_movie_id: int, to_movie_id: int) -> ValidationResult:
        """Is `to_movie_id` a legal next film after `from_movie_id`?"""

    @abstractmethod
    async def get_suggestions(
        self, current_movie_id: int, exclude_movie_ids: list[int], filters: SuggestionFilters
    ) -> list[Suggestion]:
        """Candidate next films reachable from `current_movie_id`."""

    @abstractmethod
    async def compute_stats(self, steps: list[RunStep]) -> RunStats:
        """Passport-style stats (countries/decades/keystone actors) for a run."""

    @abstractmethod
    def solve_bridge(
        self, from_movie_id: int, to_movie_id: int, max_depth: int
    ) -> AsyncIterator[dict]:
        """Bidirectional-BFS bridge solve as a stream of events.

        Event shape (progress/partial/result/error/done) is finalized in
        Phase 6 - this signature only reserves the contract.
        """

    async def discover_candidates(
        self,
        frontier_movie_id: int,
        mode: str = "or",
        cast_limit: int | None = None,
    ) -> list[DiscoveryCandidate]:
        """Unified cast-aggregation "Pick Next" pool (Phase 13) - an OPTIONAL
        capability, not part of the required Strategy contract (e.g. a future
        DirectorLadderEngine would aggregate by director, not cast overlap).
        Concrete engines that support it should override this; the route
        layer catches NotImplementedError and reports it as an unsupported
        capability rather than a 500.
        """
        raise NotImplementedError
