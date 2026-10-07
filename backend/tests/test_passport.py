"""Diary import (CSV/RSS -> import run) and Passport aggregation."""

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import get_session
from app.main import app
from app.models.cache import CachedMovie, CachedMovieDirector
from app.models.run import Run, RunParticipant, RunStep
from app.models.user import User
from app.services import letterboxd, passport, passport_import
from app.utils.ids import utcnow

TMDB = "https://api.themoviedb.org/3"

CATALOG = {
    496243: ("Parasite", "2019-05-30", ["KR"], [(21684, "Bong Joon Ho")]),
    10543: ("Stalker", "1979-05-25", ["SU"], [(2006, "Andrei Tarkovsky")]),
    1398: ("Solaris", "1972-03-20", ["SU", "usa", "RU"], [(2006, "Andrei Tarkovsky")]),
}
TITLES = {"parasite": 496243, "stalker": 10543, "solaris": 1398}


def _search(request: httpx.Request) -> httpx.Response:
    query = request.url.params["query"].lower()
    movie_id = TITLES.get(query)
    if movie_id is None:
        return httpx.Response(200, json={"results": []})
    title, release, _, _ = CATALOG[movie_id]
    return httpx.Response(
        200, json={"results": [{"id": movie_id, "title": title, "release_date": release}]}
    )


def _mock_tmdb():
    respx.get(f"{TMDB}/search/movie").mock(side_effect=_search)
    for movie_id, (title, release, countries, directors) in CATALOG.items():
        respx.get(f"{TMDB}/movie/{movie_id}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": movie_id,
                    "title": title,
                    "release_date": release,
                    "origin_country": countries,
                    "genres": [],
                    "runtime": 100,
                    "overview": "x",
                    "tagline": "t",
                },
            )
        )
        respx.get(f"{TMDB}/movie/{movie_id}/credits").mock(
            return_value=httpx.Response(
                200,
                json={
                    "cast": [{"id": 1, "name": "A", "order": 0}],
                    "crew": [
                        {"id": pid, "name": name, "job": "Director"} for pid, name in directors
                    ]
                    + [{"id": 99, "name": "Not A Director", "job": "Producer"}],
                },
            )
        )


@pytest.fixture()
def client(config_dir, monkeypatch):
    monkeypatch.setenv("TMDB_API_KEY", "test-key")
    from app.config import get_settings

    get_settings.cache_clear()
    engine = create_engine(
        f"sqlite:///{config_dir}/app_test.db", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)

    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        test_client.db_engine = engine
        test_client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password123", "display_name": "Alice"},
        )
        test_client.post("/api/auth/login", json={"username": "alice", "password": "password123"})
        yield test_client
    app.dependency_overrides.clear()


def _task(client, response, expected="completed"):
    assert response.status_code == 202, response.text
    task = client.get(f"/api/tasks/{response.json()['id']}").json()
    assert task["status"] == expected, task
    return task


DIARY_CSV = (
    "Date,Name,Year,Letterboxd URI,Rating,Rewatch,Tags,Watched Date\n"
    "2026-01-02,Parasite,2019,https://boxd.it/aaa,4.5,,,2026-01-01\n"
    "2026-02-02,Stalker,1979,https://boxd.it/bbb,5,,,2026-02-01\n"
    "2026-03-03,Parasite,2019,https://boxd.it/aaa,4,Yes,,2026-03-01\n"
    "2026-04-04,Solaris,1972,https://letterboxd.com/film/solaris/,4,,,2026-04-01\n"
    "2026-05-05,Totally Unknown Film,2001,https://boxd.it/ccc,3,,,2026-05-01\n"
)


# ---------------------------------------------------------------- pure parsing


def test_country_codes_are_normalized_and_legacy_codes_folded():
    assert passport.parse_country_codes('["us", "GB", "us"]') == [("US", None), ("GB", None)]
    assert passport.parse_country_codes('["SU", "RU"]') == [("RU", "SU")]
    assert passport.parse_country_codes("fr") == [("FR", None)]
    assert passport.parse_country_codes("US, de;JP") == [("US", None), ("DE", None), ("JP", None)]
    assert passport.parse_country_codes('["", "USA", "1", null]') == []
    assert passport.parse_country_codes(None) == []
    assert passport.parse_country_codes("[]") == []


def test_csv_entries_stream_and_header_variants(tmp_path):
    diary = tmp_path / "diary.csv"
    diary.write_text(DIARY_CSV, encoding="utf-8-sig")
    entries = list(passport_import.iter_csv_entries(diary))
    assert [e.title for e in entries][:2] == ["Parasite", "Stalker"]
    assert entries[0].watched_on.isoformat() == "2026-01-01"  # Watched Date, not the log Date
    assert entries[3].slug == "solaris" and entries[0].slug is None  # boxd.it links carry no slug
    assert passport_import.count_csv_rows(diary) == 5

    watched = tmp_path / "watched.csv"
    watched.write_text("Date,Name,Year,Letterboxd URI\n2026-06-06,Stalker,1979,https://boxd.it/x\n")
    assert next(passport_import.iter_csv_entries(watched)).watched_on.isoformat() == "2026-06-06"


def test_csv_header_validation(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("foo,bar\n1,2\n")
    with pytest.raises(ValueError, match="Name"):
        passport_import.validate_csv_header(bad)
    nodate = tmp_path / "nodate.csv"
    nodate.write_text("Name,Year\nX,2000\n")
    with pytest.raises(ValueError, match="Date"):
        passport_import.validate_csv_header(nodate)
    empty = tmp_path / "empty.csv"
    empty.write_text("")
    with pytest.raises(ValueError, match="empty"):
        passport_import.validate_csv_header(empty)


# ---------------------------------------------------------------- CSV import API


def test_cancelled_import_keeps_finished_entries_and_can_resume(client, monkeypatch):
    from app.models.system import SystemTask
    from app.services import task_runner

    monkeypatch.setattr(task_runner, "PROGRESS_FLUSH_SECONDS", 0)
    original = passport_import.DiaryImporter._entry_to_step
    stopped = False

    async def cancel_after_first(importer, *args):
        nonlocal stopped
        step = await original(importer, *args)
        if step is not None and not stopped:
            stopped = True
            with importer.ctx.session() as session:
                task = session.get(SystemTask, importer.ctx.task_id)
                task.cancel_requested = True
                session.add(task)
                session.commit()
        return step

    monkeypatch.setattr(passport_import.DiaryImporter, "_entry_to_step", cancel_after_first)
    with respx.mock:
        _mock_tmdb()
        failed = _task(
            client,
            client.post(
                "/api/passport/import/csv", files={"file": ("diary.csv", DIARY_CSV, "text/csv")}
            ),
            expected="failed",
        )
    assert failed["status"] == "failed"
    assert failed["progress_data"]["error"]["code"] == "cancelled"
    assert failed["progress_data"]["progress"]["current"] == 1
    with Session(client.db_engine) as session:
        assert len(session.exec(select(RunStep)).all()) == 1
    with respx.mock:
        _mock_tmdb()
        resumed = _task(
            client,
            client.post(
                "/api/passport/import/csv", files={"file": ("diary.csv", DIARY_CSV, "text/csv")}
            ),
        )
    assert resumed["status"] == "completed"
    assert resumed["progress_data"]["result"]["skipped_duplicates"] >= 1


def test_csv_import_creates_hidden_import_run_and_passport(client):
    with respx.mock:
        _mock_tmdb()
        resp = client.post(
            "/api/passport/import/csv", files={"file": ("diary.csv", DIARY_CSV, "text/csv")}
        )
        task = _task(client, resp)

    result = task["progress_data"]["result"]
    assert result["total_rows"] == 5
    assert result["imported"] == 4  # Parasite x2 (different dates), Stalker, Solaris
    assert result["unresolved_count"] == 1
    assert result["unresolved"] == [
        {"title": "Totally Unknown Film", "year": 2001, "reason": "no_tmdb_match"}
    ]

    with Session(client.db_engine) as session:
        run = session.get(Run, result["run_id"])
        assert (run.name, run.game_type) == ("Letterboxd Import", "import")
        assert (
            session.exec(select(RunParticipant).where(RunParticipant.run_id == run.id)).one().role
            == "owner"
        )
        steps = session.exec(select(RunStep).where(RunStep.run_id == run.id)).all()
        assert len(steps) == 4 and {s.status for s in steps} == {"watched"}
        stalker = next(s for s in steps if s.movie_id == 10543)
        assert stalker.watched_at.date().isoformat() == "2026-02-01"
        assert stalker.movie_release_year == 1979 and json.loads(stalker.movie_origin_country) == [
            "SU"
        ]
        assert stalker.logged_by_user_id == client.get("/api/auth/me").json()["id"]

    me = client.get("/api/passport/me").json()
    assert me["total_movies_watched"] == 3 and me["total_watches"] == 4
    assert me["decades_distribution"] == {"1970s": 2, "2010s": 1}
    assert {c["code"]: c["count"] for c in me["countries"]} == {"RU": 2, "KR": 1}
    russia = next(c for c in me["countries"] if c["code"] == "RU")
    assert russia["merged_from"] == ["SU"]
    assert me["top_directors"][0] == {"person_id": 2006, "name": "Andrei Tarkovsky", "count": 2}
    assert [d["name"] for d in me["top_directors"]] == ["Andrei Tarkovsky", "Bong Joon Ho"]
    assert me["directors_coverage"] == {"movies_with_directors": 3, "movies_total": 3}

    # The hidden run never appears in run lists and can't be created by hand.
    assert client.get("/api/runs").json() == []
    bad = client.post("/api/runs", json={"name": "x", "game_type": "import"})
    assert bad.status_code == 400


def test_reimporting_the_same_csv_is_idempotent(client):
    with respx.mock:
        _mock_tmdb()
        first = _task(
            client,
            client.post(
                "/api/passport/import/csv", files={"file": ("diary.csv", DIARY_CSV, "text/csv")}
            ),
        )
        second = _task(
            client,
            client.post(
                "/api/passport/import/csv", files={"file": ("diary.csv", DIARY_CSV, "text/csv")}
            ),
        )

    assert first["progress_data"]["result"]["run_id"] == second["progress_data"]["result"]["run_id"]
    again = second["progress_data"]["result"]
    assert (again["imported"], again["skipped_duplicates"]) == (0, 4)
    assert client.get("/api/passport/me").json()["total_watches"] == 4


def test_csv_upload_validation_and_cleanup(client, config_dir):
    assert (
        client.post(
            "/api/passport/import/csv", files={"file": ("x.csv", "foo,bar\n1,2\n", "text/csv")}
        ).status_code
        == 422
    )
    assert not list((config_dir / "imports").glob("*"))  # rejected upload leaves nothing behind
    with respx.mock:
        _mock_tmdb()
        _task(
            client,
            client.post(
                "/api/passport/import/csv", files={"file": ("d.csv", DIARY_CSV, "text/csv")}
            ),
        )
    assert not list((config_dir / "imports").glob("*"))  # spooled file removed after the job


def test_import_requires_login_and_a_tmdb_key(client, monkeypatch):
    anon = TestClient(app)
    assert (
        anon.post("/api/passport/import/rss", json={"letterboxd_username": "x"}).status_code == 401
    )
    assert anon.get("/api/passport/me").status_code == 401

    from app.config import get_settings

    monkeypatch.setenv("TMDB_API_KEY", "")
    get_settings.cache_clear()
    with TestClient(app) as keyless:
        keyless.post("/api/auth/login", json={"username": "alice", "password": "password123"})
        resp = keyless.post(
            "/api/passport/import/csv", files={"file": ("d.csv", DIARY_CSV, "text/csv")}
        )
        assert resp.status_code == 409 and "TMDB API key" in resp.json()["detail"]


# ---------------------------------------------------------------- RSS import


def test_rss_import_uses_feed_tmdb_ids_and_skips_non_diary_items(client, monkeypatch):
    feed = {
        "films": [
            {
                "title": "Parasite",
                "year": 2019,
                "slug": "parasite-2019",
                "watched_at": "2026-09-01",
                "tmdb_id": 496243,
            },
            {
                "title": "Some list item",
                "year": 2000,
                "slug": "x",
                "watched_at": None,
                "tmdb_id": 5,
            },
            {
                "title": "Stalker",
                "year": 1979,
                "slug": "stalker",
                "watched_at": "2026-09-02",
                "tmdb_id": None,
            },
        ]
    }
    monkeypatch.setattr(letterboxd, "ingest_rss_diary", lambda username: feed)
    with respx.mock:
        _mock_tmdb()
        search = respx.routes[0]  # the /search/movie route registered first
        task = _task(
            client,
            client.post("/api/passport/import/rss", json={"letterboxd_username": "alice_lb"}),
        )

    result = task["progress_data"]["result"]
    assert (result["source"], result["total_rows"], result["imported"]) == ("rss", 2, 2)
    assert (
        search.call_count == 1
    )  # only Stalker needed a title search; Parasite came with its TMDB id
    assert (
        client.post("/api/passport/import/rss", json={"letterboxd_username": "../x"}).status_code
        == 400
    )


# ---------------------------------------------------------------- rate limits


def test_import_pauses_on_429_then_succeeds_without_blocking(client):
    calls = {"n": 0}

    def flaky_search(request):
        calls["n"] += 1
        if (
            calls["n"] <= 30
        ):  # outlasts TMDBClient's own 5 retries per call, so the importer must pause
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})
        return _search(request)

    with respx.mock:
        _mock_tmdb()
        respx.get(f"{TMDB}/search/movie").mock(side_effect=flaky_search)
        task = _task(
            client,
            client.post(
                "/api/passport/import/csv", files={"file": ("d.csv", DIARY_CSV, "text/csv")}
            ),
        )

    result = task["progress_data"]["result"]
    assert result["imported"] == 4
    assert result["rate_limit_pauses"] >= 1


def test_persistent_rate_limit_aborts_but_keeps_progress(client, monkeypatch):
    monkeypatch.setattr(passport_import, "PER_CALL_DEADLINE_SECONDS", 0.2)
    monkeypatch.setattr("app.services.tmdb_resolver.PER_CALL_DEADLINE_SECONDS", 0.2)
    csv_text = "Name,Year,Watched Date\n" + "".join(
        f"Unknown {i},2000,2026-01-{i + 1:02d}\n" for i in range(6)
    )

    with respx.mock:
        respx.get(f"{TMDB}/search/movie").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "0"}, json={})
        )
        task = _task(
            client,
            client.post(
                "/api/passport/import/csv", files={"file": ("d.csv", csv_text, "text/csv")}
            ),
            expected="failed",
        )

    assert task["progress_data"]["error"]["code"] == "import_aborted"
    assert "run the import again" in task["error"]


# ---------------------------------------------------------------- aggregation


def _seed_watch(
    session, run, user_id, movie_id, *, year, origin, status="watched", transition_metadata=None
):
    session.add(
        RunStep(
            run_id=run.id,
            movie_id=movie_id,
            movie_title=f"M{movie_id}",
            movie_release_year=year,
            movie_origin_country=origin,
            status=status,
            transition_metadata=transition_metadata,
            watched_at=datetime(2026, 1, 1, tzinfo=UTC),
            logged_by_user_id=user_id,
        )
    )


def test_passport_aggregates_across_runs_and_only_counts_my_watched_steps(client):
    me = client.get("/api/auth/me").json()["id"]
    with Session(client.db_engine) as session:
        run_a, run_b = Run(name="A"), Run(name="B")
        session.add_all([run_a, run_b])
        session.commit()
        _seed_watch(session, run_a, me, 1, year=1985, origin='["US"]')
        _seed_watch(session, run_b, me, 1, year=1985, origin='["US"]')  # same film in another run
        _seed_watch(session, run_a, me, 2, year=1999, origin='["US","GB"]')
        _seed_watch(session, run_a, me, 3, year=1999, origin="FR")
        _seed_watch(
            session, run_a, me, 4, year=2005, origin='["JP"]', status="planned"
        )  # not watched
        other = User(username="bob", display_name="Bob", password_hash="x")
        session.add(other)
        session.commit()
        _seed_watch(session, run_a, other.id, 5, year=1950, origin='["IT"]')  # not mine
        for movie_id, directors in (
            (1, [(7, "Dir Seven")]),
            (2, [(7, "Dir Seven"), (8, "Dir Eight")]),
            (3, [(8, "Dir Eight")]),
            (4, [(9, "Dir Nine")]),
        ):
            session.add(
                CachedMovie(tmdb_id=movie_id, title=f"M{movie_id}", directors_fetched_at=utcnow())
            )
            session.flush()
            session.add_all(
                [CachedMovieDirector(movie_id=movie_id, person_id=p, name=n) for p, n in directors]
            )
        session.add(CachedMovie(tmdb_id=1000, title="no-directors-yet"))
        session.commit()

    body = client.get("/api/passport/me").json()

    assert body["total_movies_watched"] == 3 and body["total_watches"] == 4
    assert body["decades_distribution"] == {"1980s": 1, "1990s": 2}
    assert {c["code"]: c["count"] for c in body["countries"]} == {"US": 2, "GB": 1, "FR": 1}
    assert body["countries"][0]["code"] == "US"
    assert [(d["name"], d["count"]) for d in body["top_directors"]] == [
        ("Dir Eight", 2),
        ("Dir Seven", 2),
    ]
    assert body["directors_coverage"] == {"movies_with_directors": 3, "movies_total": 3}


def test_seed_steps_are_excluded_until_their_watched_date_is_explicitly_edited(client):
    me = client.get("/api/auth/me").json()["id"]
    with Session(client.db_engine) as session:
        run = Run(name="Tug")
        session.add(run)
        session.commit()
        session.refresh(run)
        session.add(RunParticipant(run_id=run.id, user_id=me, role="owner"))
        _seed_watch(
            session,
            run,
            me,
            9001,
            year=1950,
            origin='["US"]',
            transition_metadata={"seed": True},
        )
        session.commit()
        seed_step = session.exec(select(RunStep).where(RunStep.run_id == run.id)).one()

    assert client.get("/api/passport/me").json()["total_movies_watched"] == 0
    updated = client.patch(
        f"/api/runs/{run.id}/steps/{seed_step.id}",
        json={"watched_at": "2026-01-02T00:00:00Z"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["transition_metadata"] is None
    assert client.get("/api/passport/me").json()["total_movies_watched"] == 1


def test_empty_passport_and_top_directors_capped_at_five(client):
    assert client.get("/api/passport/me").json() == {
        "total_movies_watched": 0,
        "total_watches": 0,
        "decades_distribution": {},
        "countries": [],
        "top_directors": [],
        "directors_coverage": {"movies_with_directors": 0, "movies_total": 0},
    }

    me = client.get("/api/auth/me").json()["id"]
    with Session(client.db_engine) as session:
        run = Run(name="Big")
        session.add(run)
        session.commit()
        for movie_id in range(1, 8):
            _seed_watch(session, run, me, movie_id, year=2000, origin='["US"]')
            session.add(CachedMovie(tmdb_id=movie_id, title="m", directors_fetched_at=utcnow()))
            session.flush()
            session.add(
                CachedMovieDirector(movie_id=movie_id, person_id=movie_id, name=f"D{movie_id}")
            )
        session.commit()
    assert len(client.get("/api/passport/me").json()["top_directors"]) == 5


def test_backfill_directors_fills_missing_cache_entries(client):
    me = client.get("/api/auth/me").json()["id"]
    with Session(client.db_engine) as session:
        run = Run(name="Plain")
        session.add(run)
        session.commit()
        _seed_watch(session, run, me, 496243, year=2019, origin='["KR"]')
        session.commit()
    assert client.get("/api/passport/me").json()["top_directors"] == []

    with respx.mock:
        _mock_tmdb()
        task = _task(client, client.post("/api/passport/backfill-directors"))

    assert task["progress_data"]["result"] == {"looked_up": 1, "failed": 0, "total": 1}
    body = client.get("/api/passport/me").json()
    assert body["top_directors"] == [{"person_id": 21684, "name": "Bong Joon Ho", "count": 1}]
    assert body["directors_coverage"]["movies_with_directors"] == 1


def test_backfill_counts_films_missing_from_tmdb_as_failed(client):
    me = client.get("/api/auth/me").json()["id"]
    with Session(client.db_engine) as session:
        run = Run(name="Plain")
        session.add(run)
        session.commit()
        _seed_watch(session, run, me, 496243, year=2019, origin='["KR"]')
        _seed_watch(session, run, me, 777, year=2000, origin='["US"]')  # not on TMDB
        session.commit()

    with respx.mock:
        _mock_tmdb()
        respx.get(f"{TMDB}/movie/777").mock(return_value=httpx.Response(404, json={}))
        task = _task(client, client.post("/api/passport/backfill-directors"))

    assert task["progress_data"]["result"] == {"looked_up": 1, "failed": 1, "total": 2}
