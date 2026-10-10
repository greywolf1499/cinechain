"""Grid Crawler: claim adjacent facet-query cells with logged films."""

from __future__ import annotations

import random
from collections.abc import Sequence
from typing import Any, ClassVar, Literal

from sqlmodel import Session, select

from app.engines.base import RunSetupError
from app.engines.conditions import RunOutcome
from app.engines.rulebook import RuleSection
from app.engines.trackers import TrackerEngine
from app.engines.traversal import get_policy
from app.facets.query import FacetQuery
from app.models.cache import CachedMovie
from app.models.curated import CanonMovieBadge, LetterboxdWatchlist
from app.models.run import RUN_STATUS_COMPLETED, Run, RunStep
from app.schemas.discovery import DiscoveryCandidate
from app.schemas.engine import RuleField, RunStats, Suggestion, SuggestionFilters, ValidationResult
from app.services import feasibility
from app.utils.dates import parse_release_year
from app.utils.ids import utcnow

GRID_CRAWLER = "grid_crawler"
GRID_TOOL_MODE = "tool"
GRID_SIZE = 5
GRID_RULES_KEY = "grid"
GRID_SEED_KEY = "grid_seed"
GRID_REVEALED_KEY = "grid_revealed"

_VARIANTS = (
    ("short", "Short", "⏱️"),
    ("epic", "Epic", "🎞️"),
    ("classic", "Classic", "🏛️"),
    ("modern", "Modern", "🛰️"),
    ("crowd_pleaser", "Crowd-pleaser", "🍿"),
    ("female_director", "Directed by a woman", "🎬"),
    ("one_word", "One-word title", "🔠"),
    ("non_english", "Non-English", "🌍"),
    ("canon", "Canon", "🏅"),
    ("cult_classic", "Cult classic", "🕶️"),
)


def _cells(size: int) -> list[str]:
    return [f"{r}:{c}" for r in range(size) for c in range(size)]


def _parse(cell_id: str) -> tuple[int, int]:
    row, col = cell_id.split(":", 1)
    return int(row), int(col)


def _neighbours(cell_id: str, size: int) -> list[str]:
    row, col = _parse(cell_id)
    pairs = [(row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1)]
    return [f"{r}:{c}" for r, c in pairs if 0 <= r < size and 0 <= c < size]


def _edge_cells(size: int) -> set[str]:
    return {f"0:{col}" for col in range(size)}


def _lines(size: int) -> list[list[str]]:
    lines: list[list[str]] = []
    for index in range(size):
        lines.append([f"{index}:{col}" for col in range(size)])
        lines.append([f"{row}:{index}" for row in range(size)])
    lines.append([f"{i}:{i}" for i in range(size)])
    lines.append([f"{i}:{size - 1 - i}" for i in range(size)])
    return lines


def _has_crossing(claimed: set[str], size: int) -> bool:
    starts = [f"{row}:0" for row in range(size) if f"{row}:0" in claimed]
    if not starts:
        return False
    queue = list(starts)
    seen = set(starts)
    while queue:
        current = queue.pop(0)
        _, col = _parse(current)
        if col == size - 1:
            return True
        for neighbour in _neighbours(current, size):
            if neighbour in claimed and neighbour not in seen:
                seen.add(neighbour)
                queue.append(neighbour)
    return False


def _distinct_cover(candidates: dict[str, list[int]]) -> bool:
    assigned: dict[int, str] = {}
    occupied: dict[str, int] = {}
    for cell in candidates:
        queue = [cell]
        parents: dict[int, str] = {}
        free_movie: int | None = None
        while queue and free_movie is None:
            current = queue.pop()
            for movie_id in candidates[current]:
                if movie_id in parents:
                    continue
                parents[movie_id] = current
                if movie_id not in assigned:
                    free_movie = movie_id
                    break
                queue.append(assigned[movie_id])
        if free_movie is None:
            return False
        while free_movie is not None:
            current = parents[free_movie]
            displaced = occupied.get(current)
            assigned[free_movie] = current
            occupied[current] = free_movie
            free_movie = displaced
    return True


def _board_universe(session: Session, rules: dict, user_id: str) -> list[int]:
    universe = rules.get("universe", "cache")
    if universe == "watchlist":
        return list(
            session.exec(
                select(LetterboxdWatchlist.movie_id).where(LetterboxdWatchlist.user_id == user_id)
            ).all()
        )
    if universe == "canon" and rules.get("curated_list_id"):
        return list(
            session.exec(
                select(CanonMovieBadge.movie_id).where(
                    CanonMovieBadge.curated_list_id == rules["curated_list_id"]
                )
            ).all()
        )
    return list(session.exec(select(CachedMovie.tmdb_id)).all())


def actor_claim_turn_allowed(history: Sequence[RunStep], actor_id: str) -> bool:
    previous = next((step for step in reversed(history) if step.status == "watched"), None)
    if previous is None:
        return True
    previous_actor = (previous.transition_metadata or {}).get("acting_participant_id")
    return (previous_actor or previous.logged_by_user_id) != actor_id


def generate_grid_board(
    session: Session,
    *,
    seed: int,
    size: int,
    universe_ids: list[int],
    layout: Literal["gradient", "siege"] = "gradient",
    mode: Literal["run", "tool"] = "run",
) -> dict[str, Any]:
    variants = feasibility.named_variants() if hasattr(feasibility, "named_variants") else None
    if variants is None:
        from app.facets.registry import named_variants

        variants = named_variants()
    rng = random.Random(seed)
    picks = list(_VARIANTS)
    rng.shuffle(picks)
    selected = (picks * ((size * size + len(picks) - 1) // len(picks)))[: size * size]
    cells = []
    for index, (variant_id, label, emoji) in enumerate(selected):
        definition = variants[variant_id]
        query = FacetQuery.model_validate(definition["query"])
        counts = feasibility.counts(
            session, type("P", (), {"query": query, "id": variant_id})(), universe_ids
        )
        rate = counts["pass_rate"] if counts["pass_rate"] is not None else 0.0
        if not (0.02 <= rate <= 0.60):
            continue
        row, col = divmod(index, size)
        distance_to_edge = min(row, col, size - 1 - row, size - 1 - col)
        cell = {
            "id": f"{row}:{col}",
            "label": label,
            "emoji": emoji,
            "query": query.model_dump(by_alias=True, exclude_none=True),
            "difficulty": feasibility.difficulty(rate),
            "revealed": mode == GRID_TOOL_MODE,
            "distance_to_edge": distance_to_edge,
        }
        cells.append(cell)
    if len(cells) < size * size:
        raise ValueError("Not enough feasible facet cells to build a board")
    if layout == "gradient":
        cells.sort(key=lambda cell: (cell["difficulty"], cell["distance_to_edge"], cell["id"]))
    else:
        cells.sort(key=lambda cell: (-cell["difficulty"], -cell["distance_to_edge"], cell["id"]))
    ordered = sorted(cells, key=lambda cell: cell["id"])
    line_candidates: dict[str, list[int]] = {}
    for cell in ordered:
        query = FacetQuery.model_validate(cell["query"])
        line_candidates[cell["id"]] = feasibility.matching_ids(session, query, universe_ids)
    if not any(
        _distinct_cover({cell_id: line_candidates[cell_id] for cell_id in line})
        for line in _lines(size)
    ):
        raise ValueError("No winning line has distinct-film coverage")
    return {
        "size": size,
        "layout": layout,
        "cells": [
            {key: value for key, value in cell.items() if key != "distance_to_edge"}
            for cell in ordered
        ],
    }


class GridCrawlerEngine(TrackerEngine):
    game_type = GRID_CRAWLER
    capabilities: ClassVar[list[str]] = [
        "discover_candidates",
        "validate_next_step",
        "compute_stats",
    ]
    display_name = "Grid Crawler"
    description = "Claim adjacent board cells by logging films that satisfy each cell's facet rule."
    queue_policy = "slot"
    seed_policy = "none"
    tagline = "Fill the grid, one adjacent claim at a time"
    tags: ClassVar[list[str]] = ["Board", "Facet cells", "Adjacency"]
    rulebook: ClassVar[RuleSection] = RuleSection(
        "Claim adjacent grid cells by logging films that match each cell's rule.",
        [
            "Your first watched film claims a top-edge cell.",
            "Later films claim a matching cell next to your last claim.",
            "Spend a wildcard to Jump anywhere, and take turns in Table Mode.",
        ],
        ["Complete your selected board victory pattern to win."],
        ["If you exceed the hop cap without infiltrating the target, the run fails."],
        ["Prefer films that satisfy multiple neighbours so you keep route options open."],
        ["seed", "wildcard"],
    )
    rule_fields: ClassVar[list[RuleField]] = [
        *TrackerEngine.rule_fields,
        RuleField(
            key="size",
            kind="int",
            label="Board size",
            min=4,
            max=6,
            default=5,
        ),
        RuleField(
            key="layout",
            kind="enum",
            label="Difficulty layout",
            options=["gradient", "siege"],
            default="gradient",
        ),
        RuleField(
            key="victory",
            kind="enum",
            label="Victory pattern",
            options=["bingo", "crossing", "blackout"],
            default="bingo",
        ),
        RuleField(
            key="universe",
            kind="enum",
            label="Cell universe",
            options=["cache", "watchlist", "canon"],
            default="cache",
        ),
        RuleField(
            key="fog",
            kind="bool",
            label="Fog of war",
            default=False,
        ),
        RuleField(
            key="link",
            kind="enum",
            label="Hop link policy",
            options=["none", "shared_cast", "shared_director", "shared_any_person"],
            default="none",
        ),
    ]

    @classmethod
    def public_rules(cls, rules: dict | None, run: Run) -> dict[str, Any]:
        public = dict(rules or {})
        grid = public.get(GRID_RULES_KEY)
        if not isinstance(grid, dict):
            return public
        if not public.get("fog"):
            return public
        revealed = set(public.get(GRID_REVEALED_KEY) or [])
        cells = []
        for cell in grid.get("cells", []):
            if cell.get("id") in revealed:
                cells.append(cell)
            else:
                redacted = {k: v for k, v in cell.items() if k != "query"}
                redacted["hidden"] = True
                cells.append(redacted)
        public[GRID_RULES_KEY] = {**grid, "cells": cells}
        return public

    def prepare_rules_config(self, rules: dict) -> dict:
        prepared = dict(rules)
        prepared.setdefault("size", GRID_SIZE)
        prepared.setdefault("layout", "gradient")
        prepared.setdefault("victory", "bingo")
        prepared.setdefault("universe", "cache")
        prepared.setdefault("fog", False)
        prepared.setdefault("link", "none")
        prepared.setdefault(GRID_SEED_KEY, random.randint(1, 2_147_483_647))
        prepared.setdefault(GRID_REVEALED_KEY, [])
        return prepared

    async def prepare_run(self, rules: dict, user_id: str) -> dict:
        prepared = self.prepare_rules_config(rules)
        universe_ids = _board_universe(self.session, prepared, user_id)
        attempts = 24
        for offset in range(attempts):
            seed = int(prepared[GRID_SEED_KEY]) + offset
            try:
                board = generate_grid_board(
                    self.session,
                    seed=seed,
                    size=int(prepared["size"]),
                    universe_ids=universe_ids,
                    layout=prepared["layout"],
                )
                prepared[GRID_SEED_KEY] = seed
                prepared[GRID_RULES_KEY] = board
                return prepared
            except ValueError:
                continue
        raise RunSetupError("Grid Crawler could not draw a feasible board from this universe")

    @staticmethod
    def _claimed(history: Sequence[RunStep]) -> list[str]:
        return [
            cell
            for step in history
            if step.status == "watched"
            for cell in [(step.transition_metadata or {}).get("grid_cell")]
            if isinstance(cell, str)
        ]

    def _claimable_cells(
        self, rules: dict, history: Sequence[RunStep], *, jump: bool = False
    ) -> list[dict[str, Any]]:
        grid = rules.get(GRID_RULES_KEY) or {}
        cells = list(grid.get("cells") or [])
        size = int(grid.get("size") or rules.get("size") or GRID_SIZE)
        claimed = set(self._claimed(history))
        remaining = [cell for cell in cells if cell.get("id") not in claimed]
        if jump:
            return remaining
        if not claimed:
            starts = _edge_cells(size)
            return [cell for cell in remaining if cell.get("id") in starts]
        allowed = {neighbour for cell in claimed for neighbour in _neighbours(cell, size)}
        return [cell for cell in remaining if cell.get("id") in allowed]

    def _matches_cell(self, movie_id: int, cell: dict[str, Any]) -> bool:
        query = FacetQuery.model_validate(cell["query"])
        return movie_id in feasibility.matching_ids(self.session, query, [movie_id])

    async def validate_primary(
        self,
        from_movie_id: int,
        to_movie_id: int,
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
    ) -> ValidationResult:
        return ValidationResult(valid=True)

    async def validate_next_step(
        self,
        from_movie_id: int,
        to_movie_id: int,
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
        history: Sequence[RunStep] | None = None,
    ) -> ValidationResult:
        rules = rules or {}
        history = history or []
        adjacent = self._claimable_cells(rules, history)
        jump_available = bool(rules.get("_grid_jump")) and (
            int(rules.get("wildcards_budget", 0)) == -1 or int(rules.get("wildcards_budget", 0)) > 0
        )
        claimable = self._claimable_cells(rules, history, jump=True) if jump_available else adjacent
        matching = [cell["id"] for cell in claimable if self._matches_cell(to_movie_id, cell)]
        adjacent_matching = [
            cell["id"] for cell in adjacent if self._matches_cell(to_movie_id, cell)
        ]
        if not matching:
            reason = (
                "A Jump needs an available wildcard and an unclaimed matching cell."
                if rules.get("_grid_jump")
                else "This film does not satisfy any currently claimable grid cell."
            )
            return ValidationResult(
                valid=False,
                blocked=True,
                reason=reason,
            )
        policy_key = rules.get("link", "none")
        result = ValidationResult(
            valid=True,
            mechanic={
                "grid_claimable_cells": matching,
                "grid_adjacent_cells": adjacent_matching,
            },
        )
        if history and policy_key != "none":
            policy = get_policy(policy_key)
            link = await policy.validate(
                self,
                from_movie_id,
                to_movie_id,
                cast_limit,
                rules,
                previous_transition,
                history,
            )
            if not link.valid:
                return link
            result.connections = link.connections
            result.mechanic = {
                **(result.mechanic or {}),
                **({"connection_type": policy_key} if policy_key != "none" else {}),
            }
        return result

    def link_metadata(self, result: ValidationResult, client_metadata: dict | None) -> dict | None:
        metadata = super().link_metadata(result, client_metadata) or {}
        claimable = (result.mechanic or {}).get("grid_claimable_cells") or []
        adjacent = (result.mechanic or {}).get("grid_adjacent_cells") or []
        requested = (client_metadata or {}).get("grid_cell")
        chosen = requested if requested in claimable else (claimable[0] if claimable else None)
        metadata.pop("grid_jump", None)
        metadata.pop("wildcard_used", None)
        if chosen is not None:
            metadata["grid_cell"] = chosen
            if chosen not in adjacent:
                metadata["grid_jump"] = True
        metadata.pop("grid_claimable_cells", None)
        metadata.pop("grid_adjacent_cells", None)
        return metadata or None

    async def discover_candidates(
        self,
        frontier_movie_id: int,
        mode: str = "or",
        cast_limit: int | None = None,
        rules: dict | None = None,
        previous_transition: dict | None = None,
        history: Sequence[RunStep] | None = None,
    ) -> list[DiscoveryCandidate]:
        rules = rules or {}
        history = history or []
        claimable = self._claimable_cells(rules, history)
        remaining = self._claimable_cells(rules, history, jump=True)
        if not claimable:
            return []
        allowed: dict[int, list[str]] = {}
        jump_allowed: dict[int, list[str]] = {}
        universe = _board_universe(
            self.session, rules, str(rules.get("_grid_discovery_user_id") or "")
        )
        for cell in claimable:
            query = FacetQuery.model_validate(cell["query"])
            for movie_id in feasibility.matching_ids(self.session, query, universe):
                allowed.setdefault(movie_id, []).append(cell["id"])
        if int(rules.get("wildcards_budget", 0)) != 0:
            regular_ids = {cell["id"] for cell in claimable}
            for cell in remaining:
                if cell["id"] in regular_ids:
                    continue
                query = FacetQuery.model_validate(cell["query"])
                for movie_id in feasibility.matching_ids(self.session, query, universe):
                    jump_allowed.setdefault(movie_id, []).append(cell["id"])
        rows = {
            row.tmdb_id: row
            for row in self.session.exec(
                select(CachedMovie).where(
                    CachedMovie.tmdb_id.in_(list(set(allowed) | set(jump_allowed)))  # type: ignore[attr-defined]
                )
            ).all()
        }
        candidates: list[DiscoveryCandidate] = []
        policy_key = rules.get("link", "none")
        policy = get_policy(policy_key) if history and policy_key != "none" else None
        for movie_id in sorted(set(allowed) | set(jump_allowed)):
            row = rows.get(movie_id)
            if row is None:
                continue
            cell_ids = allowed.get(movie_id, [])
            jump_cell_ids = jump_allowed.get(movie_id, [])
            if policy is not None:
                link = await policy.validate(
                    self,
                    frontier_movie_id,
                    movie_id,
                    cast_limit,
                    rules,
                    previous_transition,
                    history,
                )
                if not link.valid:
                    continue
                connections = [
                    {
                        "kind": connection.kind,
                        "actor_id": connection.actor_id,
                        "actor_name": connection.actor_name,
                        "profile_path": connection.profile_path,
                        "character_in_frontier": connection.character_in_from,
                        "character_in_candidate": connection.character_in_to,
                        "role_in_frontier": connection.role_in_from,
                        "role_in_candidate": connection.role_in_to,
                    }
                    for connection in link.connections
                ]
            else:
                connections = []
            candidates.append(
                DiscoveryCandidate(
                    movie_id=movie_id,
                    title=row.title,
                    poster_path=row.poster_path,
                    release_year=parse_release_year(row.release_date),
                    origin_country=row.origin_country,
                    genre_ids=list(row.genre_ids or []),
                    popularity=row.popularity,
                    grid_cells=sorted(set(cell_ids)),
                    grid_jump_cells=sorted(set(jump_cell_ids)),
                    connections=connections,
                )
            )
        candidates.sort(key=lambda candidate: (-(candidate.popularity or 0.0), candidate.title))
        return candidates[:250]

    async def get_suggestions(
        self,
        current_movie_id: int,
        exclude_movie_ids: list[int],
        filters: SuggestionFilters,
        rules: dict | None = None,
        history: Sequence[RunStep] | None = None,
    ) -> list[Suggestion]:
        return []

    async def compute_stats(self, steps: list[RunStep]) -> RunStats:
        from app.engines.cinechain import compute_run_stats

        return compute_run_stats(steps)

    def sync_run_state(self, run: Run, steps: Sequence[RunStep]) -> None:
        rules = dict(run.rules_config or {})
        grid = rules.get(GRID_RULES_KEY)
        if not isinstance(grid, dict):
            return
        size = int(grid.get("size") or rules.get("size") or GRID_SIZE)
        if not rules.get("fog"):
            rules[GRID_REVEALED_KEY] = _cells(size)
            run.rules_config = rules
            return
        claimed = set(self._claimed(steps))
        revealed = {cell for claimed_cell in claimed for cell in _neighbours(claimed_cell, size)}
        revealed |= claimed
        rules[GRID_REVEALED_KEY] = sorted(revealed)
        run.rules_config = rules

    def evaluate_run_outcome(self, run: Run, steps: list[RunStep]) -> RunOutcome | None:
        own = super().evaluate_run_outcome(run, steps)
        if own is not None:
            return own
        rules = run.rules_config or {}
        grid = rules.get(GRID_RULES_KEY) or {}
        size = int(grid.get("size") or rules.get("size") or GRID_SIZE)
        victory = rules.get("victory", "bingo")
        claimed = set(self._claimed(steps))
        if victory == "blackout":
            done = len(claimed) == size * size
        elif victory == "crossing":
            done = _has_crossing(claimed, size)
        else:
            done = any(all(cell in claimed for cell in line) for line in _lines(size))
        if done:
            return RunOutcome(RUN_STATUS_COMPLETED, "Grid objective completed!")
        return None


def tool_board(session: Session, user_id: str, *, seed: int | None = None) -> dict[str, Any]:
    rules = {"size": 5, "layout": "gradient", "universe": "watchlist"}
    universe_ids = _board_universe(session, rules, user_id)
    chosen_seed = seed if seed is not None else int(utcnow().timestamp())
    board = generate_grid_board(
        session, seed=chosen_seed, size=5, universe_ids=universe_ids, layout="gradient", mode="tool"
    )
    board["seed"] = chosen_seed
    board["victory"] = "bingo"
    return board
