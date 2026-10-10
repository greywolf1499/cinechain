"""Phase F10: deterministic, facts-only matchup tape generation."""

import json

from sqlmodel import Session, SQLModel, create_engine

from app.models.cache import CachedMovie
from app.services import llm, tale_of_the_tape


def cards():
    return (
        tale_of_the_tape.TapeCard(
            movie_id=1,
            title="Heat",
            year=1995,
            runtime=170,
            rating=8.3,
            genres=[80],
            language="en",
            countries=["US"],
        ),
        tale_of_the_tape.TapeCard(
            movie_id=2,
            title="Collateral",
            year=2004,
            runtime=120,
            rating=7.5,
            genres=[53],
            language="en",
            countries=["US"],
        ),
    )


def test_axis_selection_is_deterministic_and_uses_three_contrasts_and_common_ground():
    left, right = cards()
    first = tale_of_the_tape.select_axes(left, right, "round_of_16-1")
    assert first == tale_of_the_tape.select_axes(left, right, "round_of_16-1")
    assert len(first) == 4
    assert sum(axis["contrast"] for axis in first) == 3
    assert any(not axis["contrast"] for axis in first)


def test_headline_validator_rejects_invented_names_and_digits():
    left, right = cards()
    axes = tale_of_the_tape.select_axes(left, right, "m1")
    assert tale_of_the_tape.validate_headline(
        "Heat faces Collateral across the Era divide.", axes, left, right
    )
    assert not tale_of_the_tape.validate_headline(
        "Heat faces Collateral and Jordan Peterson across the Era divide.", axes, left, right
    )
    assert not tale_of_the_tape.validate_headline(
        "Heat faces Collateral as Spielberg wins the Era divide.", axes, left, right
    )
    assert not tale_of_the_tape.validate_headline(
        "Heat faces Collateral in 2045 across the Era divide.", axes, left, right
    )


async def test_llm_off_uses_the_validated_template_fallback(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/tape.db")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                CachedMovie(
                    tmdb_id=1,
                    title="Heat",
                    release_date="1995-12-15",
                    runtime=170,
                    genre_ids=[80],
                    original_language="en",
                ),
                CachedMovie(
                    tmdb_id=2,
                    title="Collateral",
                    release_date="2004-08-04",
                    runtime=120,
                    genre_ids=[53],
                    original_language="en",
                ),
            ]
        )
        session.commit()
        result = await tale_of_the_tape.build_tape(
            session,
            "round_of_16-1",
            {"movie_id": 1, "title": "Heat"},
            {"movie_id": 2, "title": "Collateral"},
            llm.LlmConfig(),
        )
    assert result["source"] == "template"
    assert len(result["axes"]) == 4
    assert "Heat" in result["headline"] and "Collateral" in result["headline"]


def test_headline_template_mentions_axis_and_both_titles():
    left, right = cards()
    axes = tale_of_the_tape.select_axes(left, right, "m1")
    headline = tale_of_the_tape.template_headline(left, right, axes)
    assert tale_of_the_tape.validate_headline(headline, axes, left, right)


def test_forged_tape_rules_are_server_owned():
    from app.services.blind_fork import strip_server_rules

    clean = strip_server_rules(
        {"bracket_tape": {"finals-1": {"headline": "forged"}}, "vibe_state": {"state": "fatigued"}}
    )
    assert clean == {}


def test_tape_axis_selection_depends_on_matchup_seed():
    left, right = cards()
    first = tale_of_the_tape.select_axes(left, right, "m1")
    second = tale_of_the_tape.select_axes(left, right, "m2")
    assert json.dumps(first) != json.dumps(second)
