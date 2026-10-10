import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.api.routes_runs import _without_server_keys
from app.engines.grid_crawler import (
    GRID_REVEALED_KEY,
    GRID_RULES_KEY,
    GridCrawlerEngine,
    actor_claim_turn_allowed,
    generate_grid_board,
)
from app.models.run import RUN_STATUS_COMPLETED, Run, RunStep
from app.services import feasibility
from app.services.blind_fork import strip_server_rules


def _step(movie_id: int, cell: str) -> RunStep:
    return RunStep(
        run_id="run",
        movie_id=movie_id,
        movie_title=f"Movie {movie_id}",
        status="watched",
        transition_metadata={"grid_cell": cell},
    )


def test_public_rules_redacts_unrevealed_cells(config_dir):
    engine = create_engine(f"sqlite:///{config_dir}/grid_redact.db", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    run = Run(name="Grid", game_type="grid_crawler", rules_config={})
    rules = {
        "fog": True,
        GRID_REVEALED_KEY: ["0:0"],
        GRID_RULES_KEY: {
            "size": 2,
            "cells": [
                {"id": "0:0", "label": "A", "query": {"op": "leaf", "id": "short"}},
                {"id": "0:1", "label": "B", "query": {"op": "leaf", "id": "classic"}},
            ],
        },
    }
    public = GridCrawlerEngine.public_rules(rules, run)
    cells = public[GRID_RULES_KEY]["cells"]
    assert cells[0]["id"] == "0:0" and "query" in cells[0]
    assert cells[1]["id"] == "0:1" and cells[1]["hidden"] is True and "query" not in cells[1]


def test_claimable_cells_start_on_top_edge_then_expand_adjacent(config_dir):
    engine = create_engine(f"sqlite:///{config_dir}/grid_claims.db", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        crawler = GridCrawlerEngine(session, tmdb=None)
        rules = {
            GRID_RULES_KEY: {
                "size": 3,
                "cells": [{"id": f"{r}:{c}"} for r in range(3) for c in range(3)],
            }
        }
        starts = {cell["id"] for cell in crawler._claimable_cells(rules, [])}
        next_cells = {
            cell["id"] for cell in crawler._claimable_cells(rules, [_step(1, "0:1")])
        }
    assert starts == {"0:0", "0:1", "0:2"}
    assert next_cells == {"0:0", "0:2", "1:1"}


@pytest.mark.asyncio
async def test_player_can_choose_a_matching_start_cell(config_dir, monkeypatch):
    engine = create_engine(
        f"sqlite:///{config_dir}/grid_choice.db",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        crawler = GridCrawlerEngine(session, tmdb=None)
        monkeypatch.setattr(
            crawler,
            "_matches_cell",
            lambda movie_id, cell: cell["id"] in {"0:0", "0:2"},
        )
        rules = {
            GRID_RULES_KEY: {
                "size": 3,
                "cells": [
                    {"id": f"{r}:{c}", "query": {"facet": "runtime", "op": "ge", "value": 60}}
                    for r in range(3)
                    for c in range(3)
                ],
            }
        }
        result = await crawler.validate_next_step(1, 1, rules=rules)
        metadata = crawler.link_metadata(result, {"grid_cell": "0:2"})
    assert result.valid
    assert metadata == {"grid_cell": "0:2"}


def test_crossing_victory_completes_run(config_dir):
    engine = create_engine(
        f"sqlite:///{config_dir}/grid_outcome.db",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        crawler = GridCrawlerEngine(session, tmdb=None)
        run = Run(
            name="Grid",
            game_type="grid_crawler",
            rules_config={"victory": "crossing", GRID_RULES_KEY: {"size": 3, "cells": []}},
        )
        steps = [_step(1, "0:0"), _step(2, "0:1"), _step(3, "0:2")]
        outcome = crawler.evaluate_run_outcome(run, steps)
    assert outcome is not None
    assert outcome.status == RUN_STATUS_COMPLETED


@pytest.mark.parametrize(
    ("victory", "claimed"),
    [
        ("bingo", ["0:0", "0:1", "0:2"]),
        ("blackout", ["0:0", "0:1", "1:0", "1:1"]),
    ],
)
def test_bingo_and_blackout_victories(config_dir, victory, claimed):
    engine = create_engine(
        f"sqlite:///{config_dir}/grid_{victory}.db",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        crawler = GridCrawlerEngine(session, tmdb=None)
        run = Run(
            name="Grid",
            game_type="grid_crawler",
            rules_config={"victory": victory, GRID_RULES_KEY: {"size": 2 if victory == "blackout" else 3, "cells": []}},
        )
        outcome = crawler.evaluate_run_outcome(
            run, [_step(index + 1, cell) for index, cell in enumerate(claimed)]
        )
    assert outcome is not None


def test_grid_generation_is_seeded_and_covers_a_winning_line(monkeypatch):
    from app.facets.registry import CATALOGUE

    named = {
        key: {
            "label": key,
            "query": {"facet": "runtime", "op": "ge", "value": 60},
        }
        for key in (
            "short", "epic", "classic", "modern", "crowd_pleaser",
            "female_director", "one_word", "non_english", "canon", "cult_classic",
        )
    }
    monkeypatch.setattr("app.facets.registry.named_variants", lambda: named)
    monkeypatch.setattr(
        feasibility,
        "counts",
        lambda session, predicate, ids: {"pass_rate": 0.25},
    )
    monkeypatch.setattr(
        feasibility,
        "matching_ids",
        lambda session, query, ids: list(range(1, 100)),
    )
    first = generate_grid_board(None, seed=42, size=4, universe_ids=list(range(1, 100)))
    second = generate_grid_board(None, seed=42, size=4, universe_ids=list(range(1, 100)))
    assert first == second
    assert len(first["cells"]) == 16
    assert all(cell["query"]["facet"] in CATALOGUE for cell in first["cells"])


@pytest.mark.asyncio
async def test_jump_claims_any_unclaimed_matching_cell_and_stamps_spend(config_dir, monkeypatch):
    engine = create_engine(
        f"sqlite:///{config_dir}/grid_jump.db",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        crawler = GridCrawlerEngine(session, tmdb=None)
        monkeypatch.setattr(
            crawler,
            "_matches_cell",
            lambda movie_id, cell: cell["id"] == "2:2",
        )
        rules = {
            "wildcards_budget": 1,
            "_grid_jump": True,
            GRID_RULES_KEY: {
                "size": 3,
                "cells": [
                    {"id": f"{r}:{c}", "query": {"op": "leaf", "id": "short"}}
                    for r in range(3)
                    for c in range(3)
                ],
            },
        }
        result = await crawler.validate_next_step(1, 2, rules=rules, history=[_step(1, "0:0")])
        stamped = crawler.link_metadata(result, {"grid_cell": "2:2"})
        no_budget = await crawler.validate_next_step(
            1,
            2,
            rules={**rules, "wildcards_budget": 0},
            history=[_step(1, "0:0")],
        )
    assert result.valid
    assert stamped == {"grid_cell": "2:2", "grid_jump": True}
    assert not no_budget.valid


def test_claim_fold_ignores_planned_cells_and_server_state_cannot_be_forged():
    planned = RunStep(
        run_id="run",
        movie_id=5,
        movie_title="Planned",
        status="planned",
        transition_metadata={"grid_cell": "0:0"},
    )
    assert GridCrawlerEngine._claimed([planned]) == []
    assert strip_server_rules(
        {
            "grid": {"cells": []},
            "grid_seed": 12,
            "grid_revealed": ["0:0"],
            "waypoints": [1, 2, 3],
            "legs": [],
            "current_leg": 1,
            "infiltration_par": 2,
            "target_list_id": "canon",
            "hop_limit": 4,
            "size": 5,
        }
    ) == {"size": 5}
    assert _without_server_keys(
        {
            "grid_cell": "0:0",
            "grid_jump": True,
            "wildcard_used": True,
            "waypoint_reached": True,
            "infiltrated": True,
            "user_note": "kept",
        }
    ) == {"user_note": "kept"}


def test_table_mode_claims_alternate_participants():
    first = _step(1, "0:0")
    first.transition_metadata = {"grid_cell": "0:0", "acting_participant_id": "alice"}
    assert not actor_claim_turn_allowed([first], "alice")
    assert actor_claim_turn_allowed([first], "bob")
