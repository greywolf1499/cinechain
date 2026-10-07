"""F5b bidirectional sequence modifiers."""
# ruff: noqa: F811

import pytest
import respx
from sqlmodel import Session

from app.engines.base import RunSetupError
from app.engines.modifier_registry import (
    AlphabetParams,
    AscendingParams,
    ChainParams,
    CountDownParams,
    ModCtx,
    ObscurityParams,
    OrderParams,
    film_values,
    registry,
)
from app.engines.registry import ENGINE_REGISTRY
from app.facets import store
from app.facets.query import FacetQuery
from app.models.cache import CachedMovie
from app.models.run import Run, RunStep
from app.services import feasibility
from tests.test_composable_modifiers import (  # noqa: F401
    client,
    create_run,
    db_engine,
    film,
    log,
    mock_universe,
)


def step(title, *, movie_id=1, seed=False, year=None, **metadata):
    return RunStep(
        run_id="test",
        movie_id=movie_id,
        movie_title=title,
        movie_release_year=year,
        status="watched",
        transition_metadata={"seed": seed, **metadata},
    )


def attach(session, history):
    run = Run(name="Sequence")
    session.add(run)
    session.flush()
    for item in history:
        item.run_id = run.id
    session.add_all(history)
    session.flush()


def movie(movie_id, title, **fields):
    return CachedMovie(tmdb_id=movie_id, title=title, **fields)


def test_stored_values_decodes_current_facets_only(db_engine):
    with Session(db_engine) as session:
        session.add(movie(1, "The 2 Towers", release_date="2002-12-18", runtime=179))
        session.add(movie(2, "Heat"))
        session.flush()
        store.refresh(session, [1])
        values = store.stored_values(
            session,
            [1, 2, 3],
            ["title_first_letter", "title_number", "title_year_token", "runtime", "popularity"],
        )
        assert values == {
            1: {
                "title_first_letter": "#",
                "title_number": [2],
                "title_year_token": [],
                "runtime": 179,
            }
        }
        assert store.stored_values(session, [1])[1]["release_year"] == 2002
        store.invalidate(session, [1], ["lexical"])
        assert "title_number" not in store.stored_values(session, [1])[1]


def test_registry_exposes_new_sequence_keys_with_legacy_aliases():
    specs = registry()
    for key in ("count_down", "title_length", "rating_climb", "into_obscurity"):
        assert specs[key].scope == "sequence" and specs[key].overlay
    assert specs["last_letter_chain"].scope == "pair" and not specs["last_letter_chain"].overlay
    for key in ("alphabet_run", "ascending_numbers", "chrono_direction", "runtime_staircase"):
        assert specs[key].key == key
    assert specs["chrono_direction"].facet == "release_year"
    assert specs["runtime_staircase"].facet == "runtime"
    with pytest.raises(ValueError):
        CountDownParams(start=1, target=5)
    with pytest.raises(ValueError):
        CountDownParams(start=2000, target=0)
    assert CountDownParams(mode="decreasing", start=2000, target=0).start == 2000


def test_alphabet_both_directions_and_literal_articles():
    spec = registry()["alphabet_run"]
    az = ModCtx(AlphabetParams(wild_letters=[]), [step("Alien")])
    za = ModCtx(AlphabetParams(direction="za", wild_letters=[]), [step("Zodiac")])
    assert spec.check(az, movie(2, "Brazil")).ok and not spec.check(az, movie(2, "Yol")).ok
    assert spec.check(za, movie(2, "Yol")).ok and not spec.check(za, movie(2, "Brazil")).ok
    literal = ModCtx(AlphabetParams(ignore_articles=False, wild_letters=[]))
    assert spec.order(literal).facet == "title_first_letter_literal"
    assert not spec.check(literal, movie(2, "The Matrix")).ok
    assert spec.check(ModCtx(AlphabetParams(wild_letters=[])), movie(2, "An Apple")).ok


def test_count_down_default_and_configurable_completion():
    spec = registry()["count_down"]
    titles = ["Ten", "Nine", "8", "Seven", "Six", "Five", "Four", "3", "Two", "One"]
    ctx = ModCtx(CountDownParams(), [step(t) for t in titles[:9]])
    assert spec.progress(ctx)["next"] == "1" and spec.outcome(ctx) is None
    assert not spec.check(ctx, movie(2, "Two")).ok
    done = ModCtx(CountDownParams(), [step(t) for t in titles])
    assert spec.outcome(done).reason == "Count down from 10 to 1 conquered in 10 films"
    short = ModCtx(CountDownParams(start=3, target=2), [step("Three"), step("2 Guns")])
    assert spec.outcome(short).reason == "Count down from 3 to 2 conquered in 2 films"
    skipped = ModCtx(
        CountDownParams(start=3, target=2),
        [step("Three"), step("Heat", overlay_skips=["count_down"])],
    )
    assert spec.outcome(skipped) is not None


def test_numbers_increasing_and_decreasing_pick_extremes():
    up = registry()["ascending_numbers"]
    inc = ModCtx(AscendingParams(mode="increasing"), [step("2 Fast 4 Furious")])
    assert up.progress(inc)["next"] == "3"
    assert up.check(inc, movie(2, "Ocean's 11")).ok and not up.check(inc, movie(2, "1917")).ok
    down = registry()["count_down"]
    dec = ModCtx(CountDownParams(mode="decreasing", start=20), [step("12 Angry Men 3")])
    assert down.progress(dec)["next"] == "11"
    assert down.check(dec, movie(2, "Seven")).ok and not down.check(dec, movie(2, "13")).ok
    years = ModCtx(CountDownParams(mode="decreasing", start=3000, allow_years=True))
    assert down.check(years, movie(2, "1917")).ok


@pytest.mark.parametrize(
    ("key", "params", "earlier", "higher", "lower"),
    [
        ("title_length", OrderParams, {"title": "Heat"}, {"title": "Alien"}, {"title": "Up"}),
        (
            "rating_climb",
            OrderParams,
            {"title": "A", "vote_average": 6.0, "vote_count": 500},
            {"title": "B", "vote_average": 7.5, "vote_count": 500},
            {"title": "C", "vote_average": 5.0, "vote_count": 500},
        ),
        (
            "into_obscurity",
            ObscurityParams,
            {"title": "A", "popularity": 50.0},
            {"title": "B", "popularity": 80.0},
            {"title": "C", "popularity": 5.0},
        ),
    ],
)
def test_scalar_sequences_in_both_directions(db_engine, key, params, earlier, higher, lower):
    spec = registry()[key]
    with Session(db_engine) as session:
        session.add(CachedMovie(tmdb_id=10, **earlier))
        session.commit()
        history = [step(earlier["title"], movie_id=10)]
        attach(session, history)
        for direction, good, bad in (("asc", higher, lower), ("desc", lower, higher)):
            ctx = ModCtx(params(direction=direction), history)
            assert spec.check(ctx, CachedMovie(tmdb_id=11, **good)).ok is True
            verdict = spec.check(ctx, CachedMovie(tmdb_id=12, **bad))
            assert verdict.ok is False and spec.label in verdict.reason
            assert spec.progress(ctx)["next"] is not None
        session.expunge_all()
    assert ObscurityParams().direction == "desc"


def test_non_strict_scalar_and_unknown_candidate():
    spec = registry()["title_length"]
    ctx = ModCtx(OrderParams(strict=False), [step("Heat")])
    assert spec.check(ctx, movie(2, "Jaws")).ok
    assert not spec.check(ModCtx(OrderParams(), [step("Heat")]), movie(2, "Jaws")).ok
    assert registry()["rating_climb"].check(ModCtx(OrderParams()), movie(2, "X")).ok is None


def test_letter_chain_both_directions():
    spec = registry()["last_letter_chain"]
    earlier = movie(1, "The Matrix")
    forward = ModCtx(ChainParams(), earlier=earlier)
    assert spec.check(forward, movie(2, "Xanadu")).ok
    verdict = spec.check(forward, movie(2, "Heat"))
    assert verdict.ok is False and "start with X" in verdict.reason
    assert spec.progress(forward)["label"].endswith("start with X")
    backward = ModCtx(ChainParams(direction="first_last"), earlier=earlier)
    assert spec.check(backward, movie(2, "Gattaca")).ok is False
    assert spec.check(backward, movie(2, "Atom")).ok
    assert spec.check(forward, movie(2, "2012")).ok
    assert spec.check(ModCtx(ChainParams(), earlier=None), movie(2, "Heat")).ok


def test_legacy_parity_for_chrono_and_staircase():
    specs = registry()
    earlier = movie(1, "A", release_date="2000-01-01", runtime=100)
    chrono = ModCtx(specs["chrono_direction"].params(direction="descent"), earlier=earlier)
    assert not specs["chrono_direction"].check(chrono, movie(2, "B", release_date="2001-01-01")).ok
    assert specs["chrono_direction"].query(chrono).model_dump(exclude_none=True) == {
        "facet": "release_year",
        "op": "lt",
        "value": 2000,
    }
    stair = ModCtx(specs["runtime_staircase"].params(direction="ascending"), earlier=earlier)
    assert specs["runtime_staircase"].check(stair, movie(2, "B", runtime=120)).ok
    assert specs["runtime_staircase"].query(stair).evaluate({"runtime": 90}) is False


def test_history_prefers_stored_facets_over_snapshot(db_engine):
    spec = registry()["alphabet_run"]
    with Session(db_engine) as session:
        session.add(movie(5, "Alien"))
        session.commit()
        store.refresh(session, [5])
        history = [step("Snapshot Renamed", movie_id=5)]
        attach(session, history)
        assert spec.state(ModCtx(AlphabetParams(wild_letters=[]), history))[0] == 1
        facts = {5: {"title_first_letter": "S"}}
        assert spec.state(ModCtx(AlphabetParams(wild_letters=[]), history, facts=facts))[0] == 0
        session.expunge_all()


CORPUS = [
    "Alien",
    "The Matrix",
    "Brazil",
    "Ocean's 11",
    "2 Fast 2 Furious",
    "Ten",
    "1917",
    "Se7en",
    "Zodiac",
    "Up",
    "Rocky II",
    "Heat",
]


def test_every_sequence_query_matches_sql_and_check(db_engine):
    """Each coverage query's SQL verdict equals its live AST verdict and the modifier check."""
    with Session(db_engine) as session:
        for movie_id, title in enumerate(CORPUS, start=1):
            session.add(
                movie(
                    movie_id,
                    title,
                    release_date=f"{1950 + movie_id * 3}-01-01",
                    runtime=80 + movie_id * 7,
                    vote_average=4 + movie_id * 0.4,
                    vote_count=500,
                    popularity=float(movie_id * 3),
                )
            )
        session.commit()
        ids = list(range(1, len(CORPUS) + 1))
        store.refresh(session, ids)
        rows = [session.get(CachedMovie, i) for i in ids]
        history = [step("Heat", movie_id=12)]
        attach(session, history)
        specs = registry()
        cases = [
            ("alphabet_run", AlphabetParams(wild_letters=[]), ()),
            ("alphabet_run", AlphabetParams(direction="za", wild_letters=[]), ()),
            ("alphabet_run", AlphabetParams(strict=False, wild_letters=[]), history),
            ("alphabet_run", AlphabetParams(ignore_articles=False, wild_letters=[]), ()),
            ("ascending_numbers", AscendingParams(), ()),
            ("ascending_numbers", AscendingParams(mode="increasing", start=3), ()),
            ("count_down", CountDownParams(start=11), ()),
            ("count_down", CountDownParams(mode="decreasing", start=8), ()),
            ("count_down", CountDownParams(mode="decreasing", start=3000, allow_years=True), ()),
            ("number_in_title", specs["number_in_title"].params(), ()),
            ("number_in_title", specs["number_in_title"].params(allow_years=True), ()),
            ("title_length", OrderParams(), history),
            ("title_length", OrderParams(direction="desc", strict=False), history),
            ("rating_climb", OrderParams(), history),
            ("rating_climb", OrderParams(direction="desc"), history),
            ("into_obscurity", ObscurityParams(), history),
            ("into_obscurity", ObscurityParams(direction="asc"), history),
        ]
        earlier = session.get(CachedMovie, 12)
        pair_cases = [
            ("chrono_direction", specs["chrono_direction"].params(direction="climb")),
            ("chrono_direction", specs["chrono_direction"].params(direction="descent")),
            ("runtime_staircase", specs["runtime_staircase"].params(direction="ascending")),
            ("runtime_staircase", specs["runtime_staircase"].params(direction="descending")),
            ("last_letter_chain", ChainParams()),
            ("last_letter_chain", ChainParams(direction="first_last")),
        ]
        contexts = [(key, ModCtx(params, history)) for key, params, history in cases]
        contexts += [(key, ModCtx(params, earlier=earlier)) for key, params in pair_cases]
        for key, ctx in contexts:
            spec = specs[key]
            query = spec.coverage(ctx).query
            assert isinstance(feasibility.query_of(spec.coverage(ctx)), FacetQuery)
            sql = set(feasibility.matching_ids(session, query, ids))
            live = {row.tmdb_id for row in rows if query.evaluate(film_values(row)) is True}
            checked = {row.tmdb_id for row in rows if spec.check(ctx, row).ok is True}
            assert sql == live == checked, (key, ctx.params, sql, live, checked)
        session.expunge_all()


def test_count_down_checklist_feasibility_uses_distinct_films(db_engine):
    from app.services.tmdb import TMDBClient

    engine = ENGINE_REGISTRY["auteur_marathon"](Session(db_engine), TMDBClient("x"))
    rules = {
        "modifiers": [{"key": "count_down", "params": {"start": 3, "target": 1}}],
        "filmography": [
            {"movie_id": 1, "title": "Three Kings"},
            {"movie_id": 2, "title": "2 Guns"},
            {"movie_id": 3, "title": "1 2 3"},
        ],
    }
    assert engine.prepare_overlays(rules) == rules
    rules["filmography"] = rules["filmography"][1:]
    with pytest.raises(RunSetupError, match="infeasible in the checklist"):
        engine.prepare_overlays(rules)


def test_api_count_down_victory_and_undo(client):
    run = create_run(
        client,
        "chrono_climb",
        modifiers=[{"key": "count_down", "params": {"start": 3, "target": 2}}],
    )
    with respx.mock:
        mock_universe({1: film("Three", 2000), 2: film("Two", 2001), 3: film("Five", 2002)})
        assert log(client, run, 3, force=True).status_code == 409
        assert log(client, run, 1).status_code == 201
        detail = client.get(f"/api/runs/{run}").json()
        assert detail["status"] == "active"
        last = log(client, run, 2)
        assert last.status_code == 201, last.text
        detail = client.get(f"/api/runs/{run}").json()
        assert detail["status"] == "completed"
        assert detail["status_reason"] == "Count down from 3 to 2 conquered in 2 films"
        assert client.delete(f"/api/runs/{run}/steps/{last.json()['id']}").status_code == 204
        assert client.get(f"/api/runs/{run}").json()["status"] == "active"


def test_api_z_to_a_blocks_wrong_letter(client):
    run = create_run(
        client,
        "chrono_climb",
        modifiers=[{"key": "alphabet_run", "params": {"direction": "za", "wild_letters": []}}],
    )
    with respx.mock:
        mock_universe({1: film("Zodiac", 2000), 2: film("Alien", 2001), 3: film("Yol", 2002)})
        assert log(client, run, 1).status_code == 201
        blocked = log(client, run, 2, force=True)
        assert blocked.status_code == 409 and "Next: Y" in blocked.text
        assert log(client, run, 3).status_code == 201


def test_api_rejects_invalid_count_down_and_reports_schema(client):
    create_run(
        client,
        "chrono_climb",
        expect=422,
        modifiers=[{"key": "count_down", "params": {"start": 1, "target": 5}}],
    )
    meta = client.get("/api/engines").json()
    engines = meta if isinstance(meta, list) else meta.get("engines", meta)
    entry = next(e for e in engines if e["game_type"] == "chrono_climb")
    spec = next(m for m in entry["modifiers"] if m["key"] == "count_down")
    assert spec["params_schema"]["properties"]["mode"]["enum"] == ["count_down", "decreasing"]
