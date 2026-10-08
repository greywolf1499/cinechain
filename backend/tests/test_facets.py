"""F5a evaluator boundaries, SQL parity, storage, API and resume gates."""

import random
from datetime import timedelta

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import ValidationError
from sqlalchemy import text
from sqlmodel import Session, col, select

from app.engines.predicates import FacetPredicate, MovieFacts, predicate
from app.facets import lexical, production, reception, store
from app.facets.micro_eras import MICRO_ERAS, micro_eras
from app.facets.models import MovieFacet, MovieFacetStatus
from app.facets.query import FacetQuery, compile, count, values_for
from app.facets.regions import regions_of
from app.facets.registry import CATALOGUE, FAMILY_VERSIONS
from app.models.cache import (
    CachedActor,
    CachedDirector,
    CachedMovie,
    CachedMovieCast,
    CachedMovieRating,
)
from app.models.run import Run, RunParticipant, RunStep
from app.models.system import SystemTask
from app.models.user import User
from app.services import cache_flush, feasibility, task_runner
from app.services.cache_repo import CacheRepo, _persist_person
from app.services.tmdb import _normalize_movie_detail
from app.utils.ids import utcnow
from tests.test_graph_mutators import client, db_engine

__all__ = ["client", "db_engine"]


def film(movie_id=1, **values):
    return CachedMovie(tmdb_id=movie_id, title="The Film", **values)


def test_facet_filters_use_stored_and_query_time_values(db_engine):
    from app.schemas.engine import FilterSpec

    with pytest.raises(ValidationError):
        FilterSpec(key="missing", label="Missing", kind="toggle", source="facet")
    with pytest.raises(ValidationError):
        FilterSpec(key="bad", label="Bad", kind="range", source="facet", facet="not_registered")
    with pytest.raises(ValidationError):
        FilterSpec(key="bad", label="Bad", kind="range", source="facet", facet="one_word_title")
    with Session(db_engine) as session:
        session.add(film(runtime=89, popularity=8, vote_count=100))
        session.add(film(2))
        session.commit()
        store.refresh(session, [1, 2])
        facets = [
            "runtime_verified",
            "popularity",
            "popularity_percentile",
            "vote_count_band",
            "canon",
            "text",
        ]
        values = values_for(session, [1, 2], facets)
        assert values[1] == {
            "runtime_verified": 89,
            "popularity": 8,
            "popularity_percentile": 1,
            "vote_count_band": "low",
            "canon": False,
            "text": "the film  ",
        }
        assert values[2].get("runtime_verified") is None
        assert values[2]["popularity"] is None
        for facet, value in values[1].items():
            op = "contains" if facet == "text" else "eq"
            query = FacetQuery(facet=facet, op=op, value=value)
            assert query.evaluate(values[1]) is True
            assert count(session, query, [1])["matches"] == 1


@pytest.mark.parametrize(
    "title, expected",
    [
        (
            "The Éve",
            {
                "title_first_letter": "E",
                "title_last_letter": "E",
                "title_length": 3,
                "title_word_count": 1,
                "one_word_title": True,
                "title_palindrome": True,
            },
        ),
        ("Spider-Man", {"one_word_title": True, "title_word_count": 1, "title_length": 9}),
        (
            "12 Angry Men",
            {"title_first_letter": "#", "title_last_letter": "N", "title_number": [12]},
        ),
        ("Rocky II", {"title_number": [2], "title_sequel_marker": True}),
        ("Blade Runner 2049", {"title_number": [], "title_year_token": [2049]}),
        ("Madam", {"title_palindrome": True}),
        ("AA", {"title_palindrome": False}),
        ("Film: Part Two", {"title_has_subtitle": True, "title_sequel_marker": True}),
        ("Film - Again", {"title_has_subtitle": True}),
        ("Civil War", {"title_number": [], "title_sequel_marker": False}),
        ("東京", {"title_length": 2, "title_word_count": 1, "title_palindrome": False}),
        ("", {"title_length": None, "one_word_title": None, "title_number": None}),
    ],
)
def test_lexical_boundaries(title, expected):
    values = lexical.evaluate(title)
    for key, value in expected.items():
        assert values[key] == value
    assert {
        f.id for f in CATALOGUE.values() if f.family == "lexical" and not f.relative
    } == values.keys()


@pytest.mark.parametrize(
    "runtime,band",
    [(None, None), (0, None), (89, "short"), (90, "standard"), (150, "standard"), (151, "epic")],
)
def test_production_runtime_boundaries(runtime, band):
    assert production.evaluate(film(runtime=runtime))["runtime_band"] == band


def test_production_unknowns_and_known_empty_sets():
    values = production.evaluate(film())
    assert all(value is None for value in values.values())
    values = production.evaluate(
        film(
            release_date="2001-09-09",
            genre_ids=[],
            overview="",
            origin_country="[]",
            collection_id=None,
            narrative_year=-44,
            narrative_era_label="Ancient Rome",
        )
    )
    assert values["release_year"] == 2001 and values["release_decade"] == 2000
    assert values["origin_country"] == [] and values["genre"] == []
    assert values["in_collection"] is False
    assert values["setting_year"] == -44
    assert production.evaluate(film(collection_id=24, overview=""))["collection_id"] == "24"
    assert production.evaluate(film(collection_id=24, overview=""))["in_collection"] is True
    assert regions_of(["JP", "FR", "US"]) == [
        "northern_america",
        "eastern_asia",
        "western_europe",
        "anglosphere",
    ]
    assert production.evaluate(film(origin_country="SU"))["origin_country"] == ["RU"]


@pytest.mark.parametrize("name,bounds", list(MICRO_ERAS.items()))
def test_micro_era_membership(name, bounds):
    low, high, country = bounds
    countries = [country] if country else []
    for year in (low, high):
        if year is not None:
            assert name in micro_eras(year, countries)
    if low is not None:
        assert name not in micro_eras(low - 1, countries)
    if high is not None:
        assert name not in micro_eras(high + 1, countries)
    if country:
        assert name not in micro_eras(low, [])
    assert micro_eras(None, countries) is None
    assert micro_eras(1950, None) is None


@pytest.mark.parametrize(
    "raw,maximum,result",
    [
        ("7.5", 10, 7.5),
        ("0", 10, 0),
        ("100%", 100, 100),
        ("N/A", 10, None),
        ("nan", 10, None),
        ("inf", 10, None),
        ("11", 10, None),
        ("-1", 100, None),
    ],
)
def test_rating_parsing(raw, maximum, result):
    assert reception.parsed(raw, maximum) == result


def ratings(imdb="7", rt="50%", mc="60"):
    return CachedMovieRating(movie_id=1, imdb_rating=imdb, rotten_tomatoes=rt, metacritic=mc)


@pytest.mark.parametrize(
    "year,votes,imdb,rt,expected",
    [
        (2010, 1000, "7", "50%", True),
        (2011, 1000, "7", "50%", False),
        (2010, 999, "7", "50%", False),
        (2010, 1000, "7", "59%", True),
        (2010, 1000, "7", "60%", False),
        (2010, 1000, "6.9", "59%", False),
        (None, 1000, "7", "50%", None),
        (2010, None, "7", "50%", None),
        (2010, 1000, None, "50%", None),
        (2010, 1000, "7", None, None),
    ],
)
def test_cult_classic_boundaries_and_missing_inputs(year, votes, imdb, rt, expected):
    movie = film(release_date=f"{year}-01-01" if year else None, vote_count=votes)
    assert (
        reception.evaluate(movie, ratings(imdb, rt), current_year=2025)["cult_classic"] is expected
    )


@pytest.mark.parametrize(
    "budget,revenue,bomb,sleeper",
    [
        (None, None, None, None),
        (0, 1, None, None),
        (1_000_000, 0, None, None),
        (999_999, 1, False, False),
        (1_000_000, 499_999, True, False),
        (1_000_000, 500_000, False, False),
        (1_000_000, 4_999_999, False, False),
        (1_000_000, 5_000_000, False, True),
    ],
)
def test_financial_boundaries(budget, revenue, bomb, sleeper):
    values = reception.evaluate(film(budget=budget, revenue=revenue), None)
    assert values["box_office_bomb"] is bomb and values["sleeper_hit"] is sleeper


def test_reception_precedence_and_darling():
    movie = film(vote_average=8, vote_count=9)
    assert reception.evaluate(movie, None)["rating"] is None
    movie.vote_count = 10
    assert reception.evaluate(movie, None)["rating"] == 8
    assert reception.evaluate(movie, ratings("6.5", "85%"))["critic_darling"] is True
    assert reception.evaluate(movie, ratings("6.6", "85%"))["critic_darling"] is False
    assert reception.evaluate(movie, ratings("6", "84%"))["critic_darling"] is False
    assert reception.evaluate(movie, ratings("7"))["rating"] == 7
    assert reception.evaluate(movie, ratings("N/A"))["rating"] == 8


def test_posthumous_evidence():
    assert production.posthumous("2000-01-02", ["2000-01-01", None], False) is True
    assert production.posthumous("2000-01-01", ["2000-01-01"], True) is False
    assert production.posthumous("2000-01-01", ["2001-01-01"], True) is False
    assert production.posthumous("2000-01-01", [None], True) is None
    assert production.posthumous(None, ["1999-01-01"], True) is None
    assert production.posthumous("2000-01-01", [], True) is None


def test_director_index_uses_feature_curation():
    movies = [
        film(1, release_date="1980-01-01", runtime=90),
        film(2, release_date="1981-01-01", runtime=90),
        film(3, release_date="1970-01-01", runtime=39),
        film(4, release_date="1971-01-01", runtime=90, genre_ids=[99]),
        film(5, release_date="1972-01-01", runtime=90, genre_ids=[10770]),
    ]
    assert production.director_indexes(movies[0], [None]) == (None, None)
    assert production.director_indexes(movies[0], [movies]) == (True, 1)
    assert production.director_indexes(movies[1], [movies]) == (False, 2)
    assert production.director_indexes(movies[1], [movies, None]) == (None, None)
    assert production.director_indexes(movies[0], [movies, None]) == (True, None)


@pytest.mark.parametrize(
    "query",
    [
        {},
        {"all": [], "any": []},
        {"facet": "missing", "op": "eq", "value": 1},
        {"facet": "runtime", "op": "contains", "value": 1},
        {"facet": "runtime", "op": "lt", "value": True},
        {"facet": "one_word_title", "op": "eq", "value": 1},
        {"facet": "genre", "op": "has_all", "value": 18},
        {"facet": "runtime", "op": "eq", "value": float("nan")},
        {"facet": "original_language", "op": "eq", "value": store.EMPTY_SET},
        {"all": [], "value": 1},
    ],
)
def test_ast_validation(query):
    with pytest.raises(ValidationError):
        FacetQuery.model_validate(query)


def test_ast_complexity_and_kleene():
    q = {"facet": "runtime", "op": "lt", "value": 90}
    for _ in range(8):
        q = {"not": q}
    with pytest.raises(ValidationError):
        FacetQuery.model_validate(q)
    for op, empty in (("all", True), ("any", False)):
        assert FacetQuery.model_validate({op: []}).evaluate({}) is empty
    q = FacetQuery.model_validate(
        {
            "all": [
                {"facet": "runtime", "op": "lt", "value": 90},
                {"facet": "release_year", "op": "lt", "value": 2000},
            ]
        }
    )
    assert q.evaluate({"runtime": 100}) is False
    assert q.evaluate({"runtime": 80}) is None
    assert (
        FacetQuery.model_validate({"not": q.model_dump(by_alias=True, exclude_none=True)}).evaluate(
            {"runtime": 80}
        )
        is None
    )


def test_inline_atomic_store_refresh_and_unknown_sets(db_engine, monkeypatch):
    with Session(db_engine) as session:
        repo = CacheRepo(session)
        movie = repo.upsert_movie(
            _normalize_movie_detail({"id": 1, "title": "Madam", "budget": 0, "revenue": 0})
        )
        assert movie.budget == 0 and movie.revenue == 0
        assert session.get(MovieFacetStatus, (1, "lexical")).status == "ok"
        assert (
            count(session, FacetQuery(facet="title_palindrome", op="eq", value=True))["matches"]
            == 1
        )
        assert (
            count(session, FacetQuery(facet="box_office_bomb", op="eq", value=False))["unknown"]
            == 1
        )
        assert count(session, FacetQuery(facet="genre", op="has_all", value=[]))["matches"] == 1
        before = list(session.exec(select(MovieFacet).where(MovieFacet.movie_id == 1)).all())
        repo.upsert_ratings(1, {"imdb_rating": "7", "rotten_tomatoes": "50%", "metacritic": "60"})
        assert count(session, FacetQuery(facet="imdb_rating", op="eq", value=7))["matches"] == 1

        def fail():
            raise RuntimeError("commit interrupted")

        monkeypatch.setattr(session, "commit", fail)
        with pytest.raises(RuntimeError, match="interrupted"):
            repo.upsert_movie(_normalize_movie_detail({"id": 1, "title": "Changed"}))
        session.rollback()
        assert session.get(CachedMovie, 1).title == "Madam"
        assert (
            count(session, FacetQuery(facet="title_palindrome", op="eq", value=True))["matches"]
            == 1
        )
        assert before


def test_person_joins_invalidation_and_flush(db_engine):
    with Session(db_engine) as session:
        repo = CacheRepo(session)
        repo.upsert_movie(
            _normalize_movie_detail({"id": 1, "title": "Movie", "release_date": "2000-01-01"})
        )
        repo.upsert_directors(1, [{"id": 9, "name": "Director", "gender": 1}])
        assert (
            count(session, FacetQuery(facet="female_director", op="eq", value=True))["matches"] == 1
        )
        _persist_person(session, 9, {"name": "Director", "deathday": "1999-12-31"})
        repo.upsert_director_credits(9, "Director", [{"id": 1, "title": "Movie"}])
        assert session.get(CachedDirector, 9).deathday == "1999-12-31"
        assert (
            count(session, FacetQuery(facet="posthumous_release", op="eq", value=True))["matches"]
            == 1
        )
        repo.upsert_ratings(1, {"imdb_rating": "7", "rotten_tomatoes": None, "metacritic": None})
        rating = session.get(CachedMovieRating, 1)
        rating.fetched_at = utcnow() - timedelta(days=10)
        session.add(rating)
        session.commit()
        with db_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            try:
                connection.execute(
                    text("INSERT INTO movie_facets VALUES ('runtime','',90,999,'cache',1)")
                )
                connection.execute(
                    text(
                        "INSERT INTO movie_facet_status VALUES (999,'production',1,'ok',CURRENT_TIMESTAMP)"
                    )
                )
            finally:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        cache_flush.flush_stale_cache(session)
        assert count(session, FacetQuery(facet="imdb_rating", op="ge", value=0))["unknown"] == 1
        assert not session.exec(select(MovieFacet).where(MovieFacet.movie_id == 999)).all()
        assert session.get(MovieFacetStatus, (999, "production")) is None


def test_500_films_200_random_queries_python_sql_parity(db_engine):
    rng = random.Random(501)
    with Session(db_engine) as session:
        movies = [
            film(
                i,
                release_date=f"{rng.randint(1920, 2025)}-01-01" if rng.random() > 0.2 else None,
                runtime=rng.choice([None, 0, 80, 90, 150, 151]),
                genre_ids=rng.choice([None, [], [18], [18, 35]]),
                original_language=rng.choice([None, "en", "ja"]),
                origin_country=rng.choice([None, "[]", '["US"]', '["FR","JP"]']),
                overview="",
                vote_count=rng.choice([None, 9, 10, 1000]),
                vote_average=7,
                budget=rng.choice([None, 0, 1_000_000]),
                revenue=rng.choice([None, 0, 499_999, 5_000_000]),
            )
            for i in range(1, 501)
        ]
        session.add_all(movies)
        session.flush()
        store.refresh(session, range(1, 501))
        session.commit()
        facts = store.movie_facts(session, movies)
        leaves = [
            {"facet": "runtime", "op": "lt", "value": 90},
            {"facet": "runtime", "op": "ge", "value": 150},
            {"facet": "original_language", "op": "ne", "value": "en"},
            {"facet": "origin_country", "op": "contains", "value": "US"},
            {"facet": "genre", "op": "has_all", "value": [18, 35]},
            {"facet": "genre", "op": "has_any", "value": [18]},
            {"facet": "genre", "op": "has_any", "value": []},
            {"facet": "title_number", "op": "contains", "value": 0},
            {"facet": "release_year", "op": "le", "value": 1960},
            {"facet": "box_office_bomb", "op": "eq", "value": True},
            {"facet": "one_word_title", "op": "eq", "value": False},
            {"facet": "micro_era", "op": "has_all", "value": []},
            {"facet": "runtime", "op": "eq", "value": 0},
        ]

        def random_query(depth=0):
            if depth == 3 or rng.random() < 0.5:
                return rng.choice(leaves)
            op = rng.choice(["all", "any", "not"])
            return {
                op: random_query(depth + 1)
                if op == "not"
                else [random_query(depth + 1) for _ in range(rng.randrange(4))]
            }

        for _ in range(200):
            query = FacetQuery.model_validate(random_query())
            sql, params = compile(query)
            actual = {
                row.movie_id: None if row.verdict is None else bool(row.verdict)
                for row in session.execute(text(sql), params)
            }
            expected = {i: query.evaluate(values) for i, values in facts.items()}
            assert actual == expected, query
            totals = count(session, query)
            assert totals["matches"] == sum(v is True for v in expected.values())
            assert totals["unknown"] == sum(v is None for v in expected.values())
        sql, params = compile(FacetQuery(facet="runtime", op="eq", value=90))
        plan = session.execute(text("EXPLAIN QUERY PLAN " + sql), params).all()
        assert any("USING PRIMARY KEY" in row[-1] for row in plan)
        assert any("ix_movie_facets_movie_family" in row[-1] for row in plan)
        facet_plan = session.execute(
            text("""EXPLAIN QUERY PLAN SELECT movie_id FROM movie_facets
            WHERE facet_id='runtime' AND value_text='' AND value_num=90""")
        ).all()
        assert any("USING PRIMARY KEY" in row[-1] for row in facet_plan)
        table_sql = (
            session.execute(
                text(
                    "SELECT sql FROM sqlite_master WHERE name IN ('movie_facets','movie_facet_status')"
                )
            )
            .scalars()
            .all()
        )
        assert len(table_sql) == 2 and all("WITHOUT ROWID" in ddl.upper() for ddl in table_sql)
        assert (
            session.execute(text("PRAGMA index_info(ix_movie_facets_movie_family)")).all()[0][2]
            == "movie_id"
        )


def test_predicate_aliases_compiled_counts_without_evidence(db_engine, monkeypatch):
    with Session(db_engine) as session:
        session.add_all([film(1, runtime=80), film(2, runtime=100), film(3)])
        session.commit()
        test = predicate("runtime_lt", value=90)
        assert isinstance(test, FacetPredicate)
        assert feasibility.pass_rate(session, test, [1, 2, 3]) == pytest.approx(2 / 3)
        store.refresh(session, [1, 2, 3])
        session.commit()

        def no_evidence(*args):
            raise AssertionError("whole-cache evidence load")

        monkeypatch.setattr(feasibility, "movies", no_evidence)
        assert feasibility.pass_rate(session, test, [1, 2, 3]) == pytest.approx(2 / 3)
        assert feasibility.cache_pass_rate(session, test) == pytest.approx(2 / 3)
        assert feasibility.pass_rate(session, test, [1, 1, 2, 3]) == pytest.approx(3 / 4)
        for language in (None, "", "en", "ja"):
            for countries in (None, [], ["US"], ["JP"]):
                facts = MovieFacts(None, None, None, language, countries, [])
                expected = (
                    None
                    if not language
                    else False
                    if language == "en"
                    else None
                    if countries is None
                    else "US" not in countries
                )
                assert predicate("non_us_non_english").check(None, facts) is expected
        assert (
            predicate("female_director").check(
                None, MovieFacts(None, None, None, None, None, [1, None])
            )
            is True
        )


def test_relative_facets_are_query_time_only(db_engine):
    with Session(db_engine) as session:
        session.add_all([film(i, popularity=i, vote_count=i) for i in range(1, 5)])
        session.commit()
        store.refresh(session, range(1, 5))
        session.commit()
        assert count(session, FacetQuery(facet="popularity", op="lt", value=3))["matches"] == 2
        assert (
            count(session, FacetQuery(facet="vote_count_band", op="eq", value="very_high"))[
                "matches"
            ]
            == 1
        )
        assert (
            count(session, FacetQuery(facet="popularity_percentile", op="ge", value=3))["matches"]
            == 2
        )
        assert not session.exec(
            select(MovieFacet).where(
                col(MovieFacet.facet_id).in_(
                    ["popularity", "popularity_percentile", "vote_count_band"]
                )
            )
        ).all()


def test_api_catalogue_count_validation_scope_and_backfill(client, db_engine):
    with Session(db_engine) as session:
        session.add_all([film(i, runtime=80 if i % 2 else None) for i in range(1, 10)])
        session.commit()
    body = {"query": {"facet": "runtime", "op": "lt", "value": 90}}
    assert client.post("/api/facets/count", json=body).json()["unknown"] == 9
    response = client.post("/api/system/facets/backfill")
    assert response.status_code == 200
    task_id = response.json()["id"]
    with Session(db_engine) as session:
        assert session.get(SystemTask, task_id).status == "completed"
        assert session.get(SystemTask, task_id).progress_data["cursor"] == 9
        user = session.exec(select(User).where(User.username == "alice")).one()
        run = Run(name="Scoped")
        session.add(run)
        session.flush()
        session.add(RunParticipant(run_id=run.id, user_id=user.id))
        session.add(RunStep(run_id=run.id, movie_id=1, movie_title="Film"))
        session.commit()
        run_id = run.id
    result = client.post("/api/facets/count", json=body).json()
    assert result["matches"] == 5 and result["unknown"] == 4 and result["pass_rate"] == 1
    assert len(result["sample"]) <= 6
    assert client.post("/api/facets/count", json={**body, "run_id": run_id}).json()["matches"] == 4
    assert client.post("/api/facets/count", json={**body, "run_id": "missing"}).status_code == 404
    assert (
        client.post(
            "/api/facets/count", json={"query": {"facet": "invalid", "op": "eq", "value": 1}}
        ).status_code
        == 422
    )
    catalogue = client.get("/api/facets").json()
    assert catalogue["cached_movies"] == 9
    runtime = next(f for f in catalogue["facets"] if f["id"] == "runtime")
    assert runtime["known"] == 5 and runtime["coverage"] == pytest.approx(500 / 9)
    with Session(db_engine) as session:
        user = session.exec(select(User).where(User.username == "alice")).one()
        user.is_admin = False
        session.add(user)
        session.commit()
    assert client.post("/api/system/facets/backfill").status_code == 403
    client.post("/api/auth/logout")
    assert client.get("/api/facets").status_code == 401
    assert client.post("/api/facets/count", json=body).status_code == 401


def test_backfill_batch_resume_and_cancellation(db_engine, monkeypatch):
    with Session(db_engine) as session:
        session.add_all([film(i, runtime=80) for i in range(1, 7)])
        task = SystemTask(name="facets_backfill", progress_data={"cursor": 2, "processed": 2})
        session.add(task)
        session.commit()
        task_id = task.id
    ctx = task_runner.TaskContext(db_engine, task_id, {"cursor": 2, "processed": 2})
    monkeypatch.setattr(task_runner, "FACETS_BATCH_SIZE", 2)
    original = task_runner.TaskContext.progress

    def cancel_after_batch(self, payload):
        original(self, payload)
        with self.session() as session:
            task = session.get(SystemTask, task_id)
            task.cancel_requested = True
            session.add(task)
            session.commit()

    monkeypatch.setattr(task_runner.TaskContext, "progress", cancel_after_batch)
    with pytest.raises(task_runner.TaskCancelled):
        task_runner.facets_backfill(ctx)
    with Session(db_engine) as session:
        task = session.get(SystemTask, task_id)
        assert task.progress_data["cursor"] == 4
        assert session.get(MovieFacetStatus, (4, "lexical"))
        assert session.get(MovieFacetStatus, (5, "lexical")) is None
        task.cancel_requested = False
        session.add(task)
        session.commit()
        data = task.progress_data
    monkeypatch.setattr(task_runner.TaskContext, "progress", original)
    result = task_runner.facets_backfill(task_runner.TaskContext(db_engine, task_id, data))
    assert result == {"cursor": 6, "processed": 6}


def test_migration_roundtrip_preserves_raw_cache(config_dir, monkeypatch):
    monkeypatch.setenv("CONFIG_DIR", str(config_dir))
    config = Config("alembic.ini")
    assert ScriptDirectory.from_config(config).get_heads() == ["e6f7a8b9c0d1"]
    command.upgrade(config, "c4d5e6f7a8b9")
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{config_dir}/cinechain.db")
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO cached_movies(tmdb_id,title) VALUES (1,'Preserved')"))
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT title,budget,revenue,collection_id FROM cached_movies")
        ).one() == ("Preserved", None, None, None)
        assert (
            "WITHOUT ROWID"
            in connection.execute(text("SELECT sql FROM sqlite_master WHERE name='movie_facets'"))
            .scalar_one()
            .upper()
        )
    command.downgrade(config, "c4d5e6f7a8b9")
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT title FROM cached_movies")).scalar_one() == "Preserved"
        )
        assert connection.execute(text("SELECT COUNT(*) FROM movie_facets")).scalar_one() == 0
    engine.dispose()


def test_stale_family_and_empty_set_do_not_become_false_evidence(db_engine):
    with Session(db_engine) as session:
        session.add_all([film(1, genre_ids=[]), film(2, genre_ids=None)])
        session.commit()
        store.refresh(session, [1, 2])
        session.commit()
        query = FacetQuery(facet="genre", op="contains", value=0)
        assert count(session, query) == {"matches": 0, "unknown": 1, "pass_rate": 0.5}
        status = session.get(MovieFacetStatus, (1, "production"))
        status.version = 0
        session.add(status)
        session.commit()
        assert count(session, query)["unknown"] == 2
        store.refresh(session, [1])
        session.commit()
        assert count(session, query)["unknown"] == 1
        store.delete_movies(session, [2])
        session.delete(session.get(CachedMovie, 2))
        session.commit()
        assert session.get(MovieFacetStatus, (2, "lexical")) is None


def test_deadline_interrupts_and_restores_connection(db_engine, monkeypatch):
    from fastapi import HTTPException

    from app.api import routes_facets

    with Session(db_engine) as session:
        monkeypatch.setattr(routes_facets, "QUERY_SECONDS", 0)
        with pytest.raises(HTTPException, match="deadline"), routes_facets.query_deadline(session):
            session.execute(
                text("""WITH RECURSIVE n(x) AS
                    (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<1000000)
                    SELECT SUM(x) FROM n""")
            ).scalar_one()
        assert session.execute(text("SELECT 1")).scalar_one() == 1


def test_top_five_cast_deathday_changes_refresh_inline(db_engine):
    with Session(db_engine) as session:
        session.add(film(1, release_date="2000-01-01"))
        session.add_all(
            [
                CachedActor(tmdb_id=10, name="Star", deathday="1999-01-01"),
                CachedActor(tmdb_id=11, name="Cameo", deathday="1998-01-01"),
            ]
        )
        session.flush()
        session.add_all(
            [
                CachedMovieCast(movie_id=1, actor_id=10, cast_order=0),
                CachedMovieCast(movie_id=1, actor_id=11, cast_order=5),
            ]
        )
        session.commit()
        store.refresh(session, [1])
        session.commit()
        query = FacetQuery(facet="posthumous_release", op="eq", value=True)
        assert count(session, query)["matches"] == 1
        _persist_person(session, 10, {"deathday": None})
        assert count(session, query)["unknown"] == 1
        assert production.posthumous("2000-01-01", ["invalid", "1999-01-01"], False) is True


def test_backfill_exact_2000_batch_size(db_engine, monkeypatch):
    with Session(db_engine) as session:
        session.add_all([film(i) for i in range(1, 2006)])
        task = SystemTask(name="facets_backfill")
        session.add(task)
        session.commit()
        task_id = task.id
    batches = []
    original = store.refresh

    def capture(session, ids, *args, **kwargs):
        batches.append(len(ids))
        return original(session, ids, *args, **kwargs)

    monkeypatch.setattr(store, "refresh", capture)
    assert (
        task_runner.facets_backfill(task_runner.TaskContext(db_engine, task_id))["cursor"] == 2005
    )
    assert batches == [2000, 5]


@pytest.mark.parametrize("expired", [False, True])
def test_failed_backfill_api_resumes_cursor(client, db_engine, expired):
    with Session(db_engine) as session:
        session.add_all([film(1), film(2)])
        key = "facets_backfill:" + ",".join(
            f"{family}:{version}" for family, version in FAMILY_VERSIONS.items()
        )
        session.add(
            SystemTask(
                name="facets_backfill",
                status="failed",
                dedupe_key=key,
                progress_data={"cursor": 1, "processed": 1},
                updated_at=utcnow() - timedelta(days=10) if expired else utcnow(),
            )
        )
        session.commit()
    response = client.post("/api/system/facets/backfill")
    assert response.status_code == 200
    with Session(db_engine) as session:
        task = session.get(SystemTask, response.json()["id"])
        assert task.status == "completed" and task.progress_data["processed"] == 2
        assert session.get(MovieFacetStatus, (1, "lexical")) is None
        assert session.get(MovieFacetStatus, (2, "lexical")) is not None


def test_backfill_api_deduplicates_active_work(client, db_engine):
    with Session(db_engine) as session:
        key = "facets_backfill:" + ",".join(
            f"{family}:{version}" for family, version in FAMILY_VERSIONS.items()
        )
        task = SystemTask(name="facets_backfill", status="pending", dedupe_key=key)
        session.add(task)
        session.commit()
        task_id = task.id
    assert client.post("/api/system/facets/backfill").json()["id"] == task_id
    assert client.post("/api/system/facets/backfill").json()["id"] == task_id
    with Session(db_engine) as session:
        assert len(session.exec(select(SystemTask).where(SystemTask.dedupe_key == key)).all()) == 1
