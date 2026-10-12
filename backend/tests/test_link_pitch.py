"""Mode-aware, cache-only link explanations and candidate identity regressions."""

import json

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.engines.mutators import candidate_from_row
from app.engines.registry import get_engine
from app.facets import semantic, store
from app.facets.tropes import replace_source
from app.main import app
from app.models.cache import CachedActor, CachedGenre, CachedMovie, CachedMovieCast
from app.models.curated import CanonMovieBadge, CuratedList
from app.models.run import Run, RunStep
from app.services import embeddings, llm
from app.services.tmdb import TMDBClient


@pytest.fixture
def db_engine(config_dir, monkeypatch):
    engine = create_engine(
        f"sqlite:///{config_dir}/pitch.db", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                CachedMovie(
                    tmdb_id=1,
                    title="Jab We Met",
                    release_date="2007-01-01",
                    overview="A cheerful traveler rescues a heartbroken passenger on a road trip.",
                    tagline="",
                    origin_country='["IN"]',
                    genre_ids=[10749, 35],
                    runtime=140,
                    overview_embedding=embeddings.encode_embedding(np.array([1.0, 0.0])),
                ),
                CachedMovie(
                    tmdb_id=2,
                    title="36 China Town",
                    release_date="2006-01-01",
                    overview="A detective investigates suspects after a casino owner is murdered.",
                    tagline="",
                    origin_country='["IN"]',
                    genre_ids=[9648, 35],
                    runtime=110,
                    overview_embedding=embeddings.encode_embedding(np.array([0.9, 0.1])),
                ),
                CachedActor(tmdb_id=10, name="Shahid Kapoor"),
                CachedGenre(id=10749, name="Romance"),
                CachedGenre(id=35, name="Comedy"),
                CachedGenre(id=9648, name="Mystery"),
            ]
        )
        session.commit()
        session.add_all(
            [
                CachedMovieCast(movie_id=1, actor_id=10, cast_order=0, character_name="Aditya"),
                CachedMovieCast(movie_id=2, actor_id=10, cast_order=0, character_name="Raj"),
            ]
        )
        replace_source(session, 1, "manual", {"romance": 1.0})
        replace_source(session, 2, "manual", {"romance": 1.0})
        monkeypatch.setattr(
            semantic,
            "cached_projection",
            lambda movie: {"vibe_quadrant": "euphoric" if movie.tmdb_id == 1 else "tense"},
        )
        session.commit()
    yield engine
    engine.dispose()


@pytest.fixture
def client(db_engine):
    def session():
        with Session(db_engine) as db:
            yield db

    app.dependency_overrides[get_session] = session
    with TestClient(app) as client:
        registered = client.post(
            "/api/auth/register",
            json={
                "username": "alice",
                "password": "fixture-pass",
                "display_name": "Alice",
            },
        )
        assert registered.status_code == 201, registered.text
        client.post("/api/auth/login", json={"username": "alice", "password": "fixture-pass"})
        yield client
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "mode,rules,expected",
    [
        ("cinechain", {}, ["Shahid Kapoor", "Romance", "Mystery"]),
        ("semantic_trope", {}, ["plot similarity", "Romance", "Euphoric", "Tense"]),
        (
            "tug_of_war",
            {
                "tug_rules_version": 3,
                "dimension": "era_classic",
                "tug_players": {"team_a": "alice"},
                "target_lead": 7,
            },
            ["Raid", "points", "target pole", "Shared cast"],
        ),
        ("rabbit_hole", {}, ["Tier 1", "satisfies the active rule", "Shahid Kapoor"]),
        ("chrono_climb", {"direction": "descent"}, ["-1", "descent"]),
        ("world_passport", {}, ["country crossing", "IN"]),
        ("genre_pendulum", {"genre_cycle": ["Comedy", "Mystery"]}, ["pendulum target", "Comedy"]),
        ("historical_time_travel", {}, ["story setting leap"]),
    ],
)
async def test_mode_context_and_offline_template(db_engine, mode, rules, expected):
    async with httpx.AsyncClient() as http:
        with Session(db_engine) as session:
            previous, candidate = session.get(CachedMovie, 1), session.get(CachedMovie, 2)
            engine = get_engine(mode, session, TMDBClient(http))
            context = engine.describe_link(
                session, previous, candidate, candidate_from_row(candidate), rules, []
            )
            result = await llm.link_pitch(llm.LlmConfig(), context)
            assert result.source == "template"
            for phrase in expected:
                assert phrase in result.value.bridge
            assert "36 China Town (2006)" in result.value.candidate_hook
            assert "detective investigates" in result.value.candidate_hook
            assert "cheerful traveler" not in result.value.candidate_hook
            if mode == "cinechain":
                assert context.shared_people[0].character_in_frontier == "Aditya"
                assert context.shared_people[0].character_in_candidate == "Raj"


def test_run_pitch_uses_server_mode_and_candidate_plot(client, db_engine):
    response = client.post(
        "/api/runs",
        json={
            "name": "Pitch fixture",
            "game_type": "cinechain",
            "rules_config": {"min_runtime": 0},
        },
    )
    assert response.status_code == 201, response.text
    run = response.json()
    with Session(db_engine) as session:
        session.add(
            RunStep(run_id=run["id"], movie_id=1, movie_title="Jab We Met", status="watched")
        )
        session.commit()
    response = client.post(
        f"/api/runs/{run['id']}/pitch",
        json={
            "previous_movie_id": 1,
            "candidate_movie_id": 2,
            "link_label": "Invented Actor",
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["candidate_movie_id"] == 2 and result["source"] == "template"
    assert "Shahid Kapoor" in result["bridge"] and "Invented Actor" not in result["bridge"]
    assert "detective investigates" in result["candidate_hook"]
    assert "cheerful traveler" not in result["candidate_hook"]
    with Session(db_engine) as session:
        stored = session.get(Run, run["id"])
        stored.game_type = "semantic_trope"
        session.add(stored)
        session.commit()
    response = client.post(
        f"/api/runs/{run['id']}/pitch",
        json={
            "previous_movie_id": 1,
            "candidate_movie_id": 2,
        },
    )
    assert "plot similarity" in response.json()["bridge"]
    assert "shared themes" in response.json()["bridge"]
    assert (
        client.post(
            f"/api/runs/{run['id']}/pitch",
            json={
                "previous_movie_id": 999,
                "candidate_movie_id": 2,
            },
        ).status_code
        == 422
    )
    with TestClient(app) as anonymous:
        assert (
            anonymous.post(
                f"/api/runs/{run['id']}/pitch",
                json={
                    "previous_movie_id": 1,
                    "candidate_movie_id": 2,
                },
            ).status_code
            == 401
        )


@pytest.mark.parametrize(
    "bad", ["previous_plot", "invented_number", "wrong_year", "extra_sentence"]
)
async def test_llm_swapped_synopsis_or_ungrounded_payoff_falls_back(db_engine, monkeypatch, bad):
    async with httpx.AsyncClient() as http:
        with Session(db_engine) as session:
            previous, candidate = session.get(CachedMovie, 1), session.get(CachedMovie, 2)
            context = get_engine("cinechain", session, TMDBClient(http)).describe_link(
                session,
                previous,
                candidate,
                candidate_from_row(candidate),
            )

            async def generate(*args, **kwargs):
                return json.dumps(
                    {
                        "bridge": "36 China Town follows Jab We Met through shared cast Shahid Kapoor.",
                        "candidate_hook": (
                            "In 36 China Town, a cheerful traveler rescues a heartbroken passenger."
                            if bad == "previous_plot"
                            else "In 36 China Town (2007), a detective investigates suspects after a casino owner is murdered."
                            if bad == "wrong_year"
                            else "In 36 China Town, a detective investigates suspects after a casino owner is murdered. Watch another film."
                            if bad == "extra_sentence"
                            else "In 36 China Town, 999 detectives investigate suspects after a casino owner is murdered."
                        ),
                    }
                )

            monkeypatch.setattr(llm, "generate", generate)
            result = await llm.link_pitch(llm.LlmConfig(provider="ollama"), context)
            assert result.source == "template"
            assert "detective investigates" in result.value.candidate_hook


async def test_goal_and_grid_link_facts_are_cache_grounded(db_engine):
    async with httpx.AsyncClient() as http:
        with Session(db_engine) as session:
            previous, candidate = session.get(CachedMovie, 1), session.get(CachedMovie, 2)
            store.refresh(session, [1, 2])
            canon = CuratedList(
                title="Target", url="https://fixture.example/list", badge_prefix="Target"
            )
            session.add(canon)
            session.commit()
            session.add(
                CanonMovieBadge(
                    movie_id=2,
                    curated_list_id=canon.id,
                    badge_label="Target",
                )
            )
            session.commit()
            for mode, rules in [
                ("connect_canon", {"legs": [{"from": 1, "to": 2}], "current_leg": 0}),
                ("canon_infiltration", {"target_list_id": canon.id}),
            ]:
                context = get_engine(mode, session, TMDBClient(http)).describe_link(
                    session,
                    previous,
                    candidate,
                    candidate_from_row(candidate),
                    rules,
                    [],
                )
                assert "cached target distance: 0 hops" in context.rule_payoffs
                assert "shaves 1 hops toward the target" in context.rule_payoffs
            context = get_engine("grid_crawler", session, TMDBClient(http)).describe_link(
                session,
                previous,
                candidate,
                candidate_from_row(candidate),
                {
                    "grid": {
                        "size": 3,
                        "cells": [
                            {
                                "id": "0:0",
                                "query": {"facet": "genre", "op": "contains", "value": 9648},
                            },
                            {
                                "id": "2:2",
                                "query": {"facet": "genre", "op": "contains", "value": 9648},
                            },
                        ],
                    }
                },
                [],
            )
            assert "0:0" in context.rule_payoffs[0]
            assert "2:2" not in context.rule_payoffs[0]
            fog = get_engine("canon_infiltration", session, TMDBClient(http)).describe_link(
                session,
                previous,
                candidate,
                candidate_from_row(candidate),
                {"target_list_id": canon.id, "fog": True},
                [],
            )
            assert fog.unknowns == ["Target distances are hidden by Fog"]
            assert not any("hops" in payoff for payoff in fog.rule_payoffs)


async def test_tug_v4_and_rabbit_tier_verdicts_are_computed_not_claimed(db_engine):
    async with httpx.AsyncClient() as http:
        with Session(db_engine) as session:
            previous, candidate = session.get(CachedMovie, 1), session.get(CachedMovie, 2)
            store.refresh(session, [1, 2])
            rules = {
                "tug_rules_version": 4,
                "tug_traversal": "genre_overlap",
                "tug_plane": {
                    "id": "runtime_poles",
                    "params": {"team_a_under": 120, "team_b_over": 150},
                },
                "tug_players": {"team_a": "alice"},
            }
            meta = candidate_from_row(candidate)
            meta.tug_points = 999
            context = get_engine("tug_of_war", session, TMDBClient(http)).describe_link(
                session,
                previous,
                candidate,
                meta,
                rules,
                [],
            )
            assert any("Build: 1 points" in item for item in context.rule_payoffs)
            assert any("Genre overlap" in item for item in context.rule_payoffs)
            history = [
                RunStep(run_id="fixture", movie_id=1, movie_title="Seed", status="watched")
                for _ in range(5)
            ]
            context = get_engine("rabbit_hole", session, TMDBClient(http)).describe_link(
                session,
                previous,
                candidate,
                candidate_from_row(candidate),
                {},
                history,
            )
            assert "Tier 2" in context.rule_payoffs[0]
            assert "does not satisfy" in context.rule_payoffs[0]


def test_cached_tone_projection_never_loads_a_model(monkeypatch, tmp_path):
    from app.config import get_settings
    from app.facets.anchors import ANCHOR_VERSION, ANCHORS

    movie = CachedMovie(
        tmdb_id=1,
        title="Cached",
        overview_embedding=embeddings.encode_embedding(np.array([1.0, 0.0])),
    )
    fingerprint = embeddings.LOCAL_FINGERPRINT
    monkeypatch.setattr(semantic, "_centroids", {})
    monkeypatch.setattr(
        semantic, "project_vector", lambda vector, anchors, model: {"vibe_quadrant": "tense"}
    )
    monkeypatch.setattr(get_settings(), "config_dir", tmp_path)
    path = semantic.centroid_cache_path(tmp_path, fingerprint)
    path.parent.mkdir()
    np.savez(
        path,
        fingerprint=np.array(fingerprint),
        version=np.array(ANCHOR_VERSION),
        **{name: np.array([1.0, 0.0]) for name in ANCHORS},
    )
    assert semantic.cached_projection(movie)["vibe_quadrant"] == "tense"
