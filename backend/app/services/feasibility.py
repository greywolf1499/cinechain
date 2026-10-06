"""Cache-only, three-valued feasibility evidence, scoped to one request."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sqlmodel import Session, select

from app.engines.predicates import MovieFacts, Predicate, facts_of
from app.models.cache import CachedMovie, CachedMovieDirector, CachedMovieRating
from app.services.movie_filters import rating_from_cache


@dataclass(frozen=True)
class Feasibility:
    ok: bool
    reason: str
    pass_rate: float | None

    @property
    def drawable(self) -> bool:
        return self.ok and (self.pass_rate is None or self.pass_rate <= 0.8)


class Evidence:
    """Memoize facts, not conclusions: bounds/history can change within a request."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.movies = {movie.tmdb_id: movie for movie in session.exec(select(CachedMovie)).all()}
        directors: dict[int, list[CachedMovieDirector]] = {}
        for director in session.exec(select(CachedMovieDirector)).all():
            directors.setdefault(director.movie_id, []).append(director)
        ratings = {
            rating.movie_id: rating for rating in session.exec(select(CachedMovieRating)).all()
        }
        self.facts = {
            movie_id: facts_of(
                movie, directors.get(movie_id, []), rating_from_cache(movie, ratings.get(movie_id))
            )
            for movie_id, movie in self.movies.items()
        }

    def check(self, test: Predicate, ids: Iterable[int], *, exact: bool = True) -> Feasibility:
        results = [
            test.check(self.movies[movie_id], self.facts[movie_id])
            if movie_id in self.movies
            else None
            for movie_id in ids
        ]
        if not results:
            return Feasibility(
                not exact, "No remaining eligible films" if exact else "Cache has no evidence", None
            )
        possible = sum(result is not False for result in results)
        rate = possible / len(results)
        # Unknowns are possible, but cannot prove that a quest is trivial.
        observed_rate = None if any(result is None for result in results) else rate
        return Feasibility(
            possible > 0,
            "No remaining eligible film matches this quest"
            if not possible
            else "Too easy for the eligible pool"
            if observed_rate is not None and observed_rate > 0.8
            else "A matching film is possible",
            observed_rate,
        )


def evidence_for(session: Session) -> Evidence:
    if "bounty_feasibility" not in session.info:
        session.info["bounty_feasibility"] = Evidence(session)
    return session.info["bounty_feasibility"]


def invalidate(session: Session) -> None:
    session.info.pop("bounty_feasibility", None)


def pass_rate(session: Session, test: Predicate, ids: Iterable[int]) -> float | None:
    evidence = evidence_for(session)
    results = [
        test.check(evidence.movies[i], evidence.facts[i]) if i in evidence.movies else None
        for i in ids
    ]
    return sum(result is not False for result in results) / len(results) if results else None


def exists(session: Session, test: Predicate, ids: Iterable[int]) -> bool:
    rate = pass_rate(session, test, ids)
    return rate is not None and rate > 0


def cache_pass_rate(session: Session, test: Predicate) -> float | None:
    return pass_rate(session, test, session.exec(select(CachedMovie.tmdb_id)).all())


def ranges_of(test: Predicate) -> dict[str, tuple[float | None, float | None]]:
    return test.ranges


def contradicts(test: Predicate, bounds: dict[str, tuple[float | None, float | None]]) -> bool:
    for field, (low, high) in ranges_of(test).items():
        mode_low, mode_high = bounds.get(field, (None, None))
        lows = [value for value in (low, mode_low) if value is not None]
        highs = [value for value in (high, mode_high) if value is not None]
        if lows and highs and max(lows) > min(highs):
            return True
    return False


def within(facts: MovieFacts, bounds: dict[str, tuple[float | None, float | None]]) -> bool:
    for field, (low, high) in bounds.items():
        value = getattr(facts, field)
        if value is not None and (
            low is not None and value < low or high is not None and value > high
        ):
            return False
    return True
