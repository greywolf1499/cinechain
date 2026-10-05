"""The Perfect Marathon Router: order a set of films so the tone never lurches.

A marathon is an open path through the films (a Traveling-Cinephile problem). The cost of a hop
is its "tonal whiplash" - a weighted blend of how far apart two films are in genre, release year,
runtime and rating. Up to `EXACT_MAX` films every ordering is tried; above that, simulated
annealing over 2-opt segment reversals finds a near-optimal one. Pure Python, no I/O.
"""

from __future__ import annotations

import itertools
import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

MIN_FILMS = 4
MAX_FILMS = 25
EXACT_MAX = 8

W_GENRE = 0.40
W_YEAR = 0.25
W_RUNTIME = 0.15
W_RATING = 0.20

YEAR_SPAN = 50.0
RUNTIME_SPAN = 60.0
RATING_SPAN = 5.0
# What a missing field costs: neither close nor far, so unknown data neither helps nor hurts.
NEUTRAL_DELTA = 0.5

T0 = 100.0
ALPHA = 0.995
ITERATIONS = 10_000
# Independent annealing chains (best kept): a single cooled chain lands 1-3% off the optimum,
# four land within ~1% at ~80ms for 25 films, well inside the 250ms budget.
RESTARTS = 4

SMOOTH_BELOW = 0.30
GENTLE_BELOW = 0.55
LABEL_SMOOTH = "Smooth Transition"
LABEL_GENTLE = "Gentle Shift"
LABEL_WHIPLASH = "Tonal Whiplash"

METHOD_EXACT = "exact"
METHOD_ANNEALING = "simulated_annealing"

_EPSILON = 1e-12


@dataclass(frozen=True)
class RouterFilm:
    movie_id: int
    title: str = ""
    genres: tuple[int, ...] = ()  # TMDB genre ids, primary genre first
    year: int | None = None
    runtime: int | None = None
    rating: float | None = None  # 0-10 (IMDb when known, else TMDB's score)


@dataclass(frozen=True)
class Weights:
    genre: float = W_GENRE
    year: float = W_YEAR
    runtime: float = W_RUNTIME
    rating: float = W_RATING

    def normalised(self) -> Weights:
        """Scaled to sum to 1 so scores stay comparable however the weights were chosen."""
        values = (self.genre, self.year, self.runtime, self.rating)
        if any(v < 0 or not math.isfinite(v) for v in values) or sum(values) <= 0:
            raise ValueError("Weights must be non-negative and not all zero")
        total = sum(values)
        return Weights(*(v / total for v in values))


@dataclass(frozen=True)
class Deltas:
    genre: float
    year: float
    runtime: float
    rating: float


@dataclass
class Transition:
    from_movie_id: int
    to_movie_id: int
    cost: float
    label: str
    summary: str
    deltas: Deltas


@dataclass
class RouterResult:
    ordered_movie_ids: list[int]
    initial_whiplash_score: float
    optimized_whiplash_score: float
    improvement_percentage: float
    transitions: list[Transition]
    method: str
    weights: Weights = field(default_factory=Weights)


def jaccard(a: Sequence[int], b: Sequence[int]) -> float:
    set_a, set_b = set(a), set(b)
    union = set_a | set_b
    return len(set_a & set_b) / len(union) if union else 0.0


def component_deltas(a: RouterFilm, b: RouterFilm) -> Deltas:
    """Each distance in 0..1 (0 = identical), `NEUTRAL_DELTA` where either film lacks the field."""
    genre = 1.0 - jaccard(a.genres, b.genres) if a.genres and b.genres else NEUTRAL_DELTA
    year = (
        min(abs(a.year - b.year) / YEAR_SPAN, 1.0)
        if a.year is not None and b.year is not None
        else NEUTRAL_DELTA
    )
    runtime = (
        min(abs(a.runtime - b.runtime) / RUNTIME_SPAN, 1.0)
        if a.runtime and b.runtime
        else NEUTRAL_DELTA
    )
    rating = (
        min(abs(a.rating - b.rating) / RATING_SPAN, 1.0)
        if a.rating is not None and b.rating is not None
        else NEUTRAL_DELTA
    )
    return Deltas(genre, year, runtime, rating)


def whiplash_cost(a: RouterFilm, b: RouterFilm, weights: Weights | None = None) -> float:
    """Transition cost between two films: 0 = seamless, 1 = maximum tonal shock (weights sum to 1)."""
    w = weights or Weights()
    d = component_deltas(a, b)
    return w.genre * d.genre + w.year * d.year + w.runtime * d.runtime + w.rating * d.rating


def cost_matrix(films: Sequence[RouterFilm], weights: Weights | None = None) -> list[list[float]]:
    n = len(films)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            matrix[i][j] = matrix[j][i] = whiplash_cost(films[i], films[j], weights)
    return matrix


def path_cost(matrix: Sequence[Sequence[float]], order: Sequence[int]) -> float:
    return sum(matrix[a][b] for a, b in itertools.pairwise(order))


def _exact(matrix: list[list[float]], n: int) -> list[int]:
    best = list(range(n))
    best_cost = path_cost(matrix, best)
    for perm in itertools.permutations(range(n)):
        if perm[0] > perm[-1]:  # the reverse costs the same (symmetric) and is also enumerated
            continue
        cost = path_cost(matrix, perm)
        if cost < best_cost - _EPSILON:
            best, best_cost = list(perm), cost
    return best


def _reversal_delta(matrix: list[list[float]], order: list[int], i: int, j: int) -> float:
    """Cost change of reversing order[i..j]; only the two boundary edges differ."""
    last = len(order) - 1
    delta = 0.0
    if i > 0:
        delta += matrix[order[i - 1]][order[j]] - matrix[order[i - 1]][order[i]]
    if j < last:
        delta += matrix[order[i]][order[j + 1]] - matrix[order[j]][order[j + 1]]
    return delta


def _relocate_improves(matrix: list[list[float]], order: list[int]) -> bool:
    """Moves one film to a better spot if that lowers the cost (in place); True when it did."""
    n = len(order)
    base = path_cost(matrix, order)
    for i in range(n):
        film = order[i]
        rest = order[:i] + order[i + 1 :]
        for j in range(n):
            if j == i:
                continue
            candidate = rest[:j] + [film] + rest[j:]
            if path_cost(matrix, candidate) < base - _EPSILON:
                order[:] = candidate
                return True
    return False


def _local_descent(matrix: list[list[float]], order: list[int]) -> None:
    """Applies improving 2-opt reversals and single-film relocations until none is left (in place)."""
    n = len(order)
    while True:
        improved = False
        for i in range(n - 1):
            for j in range(i + 1, n):
                if _reversal_delta(matrix, order, i, j) < -_EPSILON:
                    order[i : j + 1] = reversed(order[i : j + 1])
                    improved = True
        if not improved and not _relocate_improves(matrix, order):
            return


def _anneal(
    matrix: list[list[float]],
    n: int,
    rng: random.Random,
    *,
    t0: float = T0,
    alpha: float = ALPHA,
    iterations: int = ITERATIONS,
) -> list[int]:
    order = list(range(n))
    cost = path_cost(matrix, order)
    best, best_cost = order[:], cost
    temperature = t0
    for _ in range(iterations):
        i, j = sorted(rng.sample(range(n), 2))
        delta = _reversal_delta(matrix, order, i, j)
        if delta < 0 or rng.random() < math.exp(-delta / temperature):
            order[i : j + 1] = reversed(order[i : j + 1])
            cost += delta
            if cost < best_cost - _EPSILON:
                best, best_cost = order[:], cost
        temperature *= alpha
    _local_descent(matrix, best)  # polish: annealing needn't end at a local optimum
    return best


def label_for(cost: float) -> str:
    if cost < SMOOTH_BELOW:
        return LABEL_SMOOTH
    return LABEL_GENTLE if cost < GENTLE_BELOW else LABEL_WHIPLASH


def _decade(year: int) -> str:
    return f"{year // 10 * 10}s"


def describe_transition(
    a: RouterFilm,
    b: RouterFilm,
    deltas: Deltas,
    weights: Weights,
    label: str,
    genre_names: Mapping[int, str] | None = None,
) -> str:
    """A short reason for the hop: what the films share when it is smooth, else the biggest gap."""
    names = genre_names or {}

    def name(genre_id: int) -> str:
        return names.get(genre_id, "Genre")

    shared = [g for g in a.genres if g in set(b.genres)]
    if label == LABEL_SMOOTH:
        era = f"{_decade(b.year)} " if b.year is not None and deltas.year < 0.5 else ""
        if shared:
            return f"{era}{name(shared[0])} Harmony"
        return f"{era}Tonal Harmony".strip()

    contributions = {
        "genre": weights.genre * deltas.genre,
        "year": weights.year * deltas.year,
        "runtime": weights.runtime * deltas.runtime,
        "rating": weights.rating * deltas.rating,
    }
    biggest = max(contributions, key=lambda key: contributions[key])
    if biggest == "genre" and a.genres and b.genres:
        left = next((g for g in a.genres if g not in set(b.genres)), a.genres[0])
        right = next((g for g in b.genres if g not in set(a.genres)), b.genres[0])
        return f"{name(left)} \u2500\u2500\u25ba {name(right)}"
    if biggest == "year" and a.year is not None and b.year is not None:
        return f"{_decade(a.year)} \u2500\u2500\u25ba {_decade(b.year)}"
    if biggest == "runtime" and a.runtime and b.runtime:
        return f"{a.runtime} min \u2500\u2500\u25ba {b.runtime} min"
    if biggest == "rating" and a.rating is not None and b.rating is not None:
        return f"Rated {a.rating:.1f} \u2500\u2500\u25ba {b.rating:.1f}"
    return "Mixed signals"


def build_transitions(
    films: Sequence[RouterFilm],
    weights: Weights,
    genre_names: Mapping[int, str] | None = None,
) -> list[Transition]:
    transitions = []
    for a, b in itertools.pairwise(films):
        deltas = component_deltas(a, b)
        cost = (
            weights.genre * deltas.genre
            + weights.year * deltas.year
            + weights.runtime * deltas.runtime
            + weights.rating * deltas.rating
        )
        label = label_for(cost)
        transitions.append(
            Transition(
                from_movie_id=a.movie_id,
                to_movie_id=b.movie_id,
                cost=round(cost, 4),
                label=label,
                summary=describe_transition(a, b, deltas, weights, label, genre_names),
                deltas=deltas,
            )
        )
    return transitions


def optimize(
    films: Sequence[RouterFilm],
    weights: Weights | None = None,
    genre_names: Mapping[int, str] | None = None,
) -> RouterResult:
    """The smoothest order of `films` (4 to 25, distinct). Deterministic for a given input."""
    if not MIN_FILMS <= len(films) <= MAX_FILMS:
        raise ValueError(f"The router takes {MIN_FILMS} to {MAX_FILMS} films")
    if len({film.movie_id for film in films}) != len(films):
        raise ValueError("Each film can only appear once")
    w = (weights or Weights()).normalised()
    n = len(films)
    matrix = cost_matrix(films, w)
    initial_cost = path_cost(matrix, range(n))

    if n <= EXACT_MAX:
        order, method = _exact(matrix, n), METHOD_EXACT
    else:
        seed = sum(film.movie_id * (index + 1) for index, film in enumerate(films))
        chains = [_anneal(matrix, n, random.Random(seed + k)) for k in range(RESTARTS)]
        order, method = min(chains, key=lambda chain: path_cost(matrix, chain)), METHOD_ANNEALING
    optimized_cost = path_cost(matrix, order)
    if optimized_cost >= initial_cost - _EPSILON:  # never hand back something worse than the input
        order, optimized_cost = list(range(n)), initial_cost

    ordered = [films[i] for i in order]
    improvement = (
        (initial_cost - optimized_cost) / initial_cost * 100.0 if initial_cost > _EPSILON else 0.0
    )
    return RouterResult(
        ordered_movie_ids=[film.movie_id for film in ordered],
        initial_whiplash_score=round(initial_cost, 3),
        optimized_whiplash_score=round(optimized_cost, 3),
        improvement_percentage=round(max(improvement, 0.0), 1),
        transitions=build_transitions(ordered, w, genre_names),
        method=method,
        weights=w,
    )
