"""Phase 28b: narrative setting years and the Historical Time-Travel engine."""

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.engines.historical_time_travel import format_year
from app.main import app
from app.models.cache import CachedMovie
from app.services import historical_era as he
from app.services import llm

TMDB_BASE = "https://api.themoviedb.org/3"


def film(release="2000-06-01", **kw):
    return CachedMovie(tmdb_id=kw.pop("tmdb_id", 1), title=kw.pop("title", "T"), release_date=release, **kw)


# --- keyword taxonomy ---------------------------------------------------------


@pytest.mark.parametrize("slug, year, label", [
    ("ancient-rome", 100, "Ancient Rome"),
    ("ancient-greece", -400, "Ancient Greece"),
    ("ancient-egypt", -1200, "Ancient Egypt"),
    ("middle-ages", 1250, "Middle Ages"),
    ("medieval", 1250, "Middle Ages"),
    ("renaissance", 1500, "Renaissance"),
    ("samurai", 1600, "Samurai Era"),
    ("18th-century", 1750, "18th Century"),
    ("victorian-era", 1870, "Victorian Era"),
    ("19th-century", 1870, "19th Century"),
    ("wild-west", 1880, "Wild West"),
    ("world-war-i", 1916, "World War I"),
    ("roaring-twenties", 1925, "Roaring Twenties"),
    ("world-war-ii", 1943, "World War II"),
    ("cold-war", 1965, "Cold War"),
    ("1970s", 1975, "1970s"),
    ("1980s", 1985, "1980s"),
    ("1990s", 1995, "1990s"),
    ("post-apocalyptic", 2060, "Post-Apocalyptic Future"),
    ("cyberpunk", 2080, "Cyberpunk Future"),
    ("space-travel", 2150, "Space Age Future"),
    ("dystopia", 2150, "Dystopian Future"),
])
def test_taxonomy_matches_the_spec(slug, year, label):
    assert he.TAXONOMY[slug] == (year, label)
    assert he.resolve_narrative_era(film(), "", [slug.replace("-", " ")]) == (year, label)


def test_keywords_are_slugified_and_aliased():
    assert he.resolve_narrative_era(film(), "", ["World War II"]) == (1943, "World War II")
    assert he.resolve_narrative_era(film(), "", ["Roman Empire"])[0] == 100
    assert he.resolve_narrative_era(film(), "", ["post-apocalyptic future"])[0] == 2060
    assert he.resolve_narrative_era(film(), "", ["Based on Novel", "WWII"])[0] == 1943


def test_the_most_concrete_era_wins_over_generic_futures():
    assert he.resolve_narrative_era(film(), "", ["dystopia", "world war ii"])[0] == 1943
    assert he.resolve_narrative_era(film(), "", ["space travel", "cyberpunk"])[0] == 2080


def test_keywords_beat_the_overview():
    assert he.resolve_narrative_era(film(), "In 1066, a king.", ["samurai"]) == (1600, "Samurai Era")


# --- overview scanner ---------------------------------------------------------


def test_overview_century_references():
    assert he.resolve_narrative_era(film(), "A sailor in the 18th century.", []) == (1750, "18th Century")
    assert he.resolve_narrative_era(film(), "Set in the 21st-century.", [])[0] == 2050
    assert he.resolve_narrative_era(film(), "A story from the 3rd century BC", []) == (-250, "3rd Century BC")
    assert he.resolve_narrative_era(film(), "The 12th Century monks...", [])[1] == "12th Century"


def test_overview_years():
    assert he.resolve_narrative_era(film(), "In 1943 a spy parachutes in.", []) == (1943, "1940s")
    assert he.resolve_narrative_era(film("2005-01-01"), "By 2077 the oceans rose.", []) == (2077, "Future")
    assert he.resolve_narrative_era(film(), "Rome, 44 BC, the Ides.", []) == (-44, "Ancient World")
    assert he.resolve_narrative_era(film(), "The year is 800 AD.", []) == (800, "Middle Ages")


def test_nothing_found_means_contemporary_release_year():
    assert he.resolve_narrative_era(film("1994-10-14"), "Two friends open a cafe.", ["friendship"]) == (
        1994, "Contemporary")


def test_missing_release_date_still_returns_a_year():
    year, label = he.resolve_narrative_era(film(None), "", [])
    assert isinstance(year, int) and label == "Contemporary"


def test_format_year():
    assert format_year(-400) == "400 BC"
    assert format_year(180) == "180 AD"
    assert format_year(1945) == "1945"


def test_parse_era_reply():
    assert he.parse_era_reply('{"year": 1888, "era": "Victorian London"}') == (1888, "Victorian London")
    assert he.parse_era_reply('Sure! {"year": -44, "era": "Late Roman Republic"} done') == (
        -44, "Late Roman Republic")
    for bad in ("no json", '{"year": "1900", "era": "x"}', '{"year": 1900}', '{"year": 99999, "era": "x"}',
                '{"year": true, "era": "x"}', '{"year": 1900, "era": "  "}', "[1, 2]"):
        assert he.parse_era_reply(bad) is None


# --- API plumbing -------------------------------------------------------------


@pytest.fixture()
def db_engine(config_dir):
    engine = create_engine(
        f"sqlite:///{config_dir}/app_test.db", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture()
def client(db_engine):
    def override_get_session():
        with Session(db_engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        test_client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password123", "display_name": "Alice"})
        test_client.post("/api/auth/login", json={"username": "alice", "password": "password123"})
        yield test_client
    app.dependency_overrides.clear()


UNIVERSE = {
    1: {"title": "Gladiator Days", "release": "2000-05-01", "keywords": ["ancient rome"], "pop": 9},
    2: {"title": "Knights", "release": "2001-01-01", "keywords": ["medieval"], "pop": 8},
    3: {"title": "The Front", "release": "1998-07-01", "keywords": ["world war ii"], "pop": 7},
    4: {"title": "Neon Rain", "release": "1982-06-01", "keywords": ["cyberpunk"], "pop": 6},
    5: {"title": "Cafe Society", "release": "2015-01-01", "keywords": [], "pop": 5,
        "overview": "Friends open a cafe."},
    6: {"title": "Pharaoh", "release": "1995-01-01", "keywords": ["ancient egypt"], "pop": 4},
}


def mock_universe(movies=UNIVERSE, keywords_status=200):
    for movie_id, m in movies.items():
        respx.get(f"{TMDB_BASE}/movie/{movie_id}").mock(return_value=httpx.Response(200, json={
            "id": movie_id, "title": m["title"], "release_date": m["release"],
            "poster_path": f"/p{movie_id}.jpg", "overview": m.get("overview", ""),
            "origin_country": ["US"], "original_language": "en", "runtime": 100, "genres": [],
            "popularity": m.get("pop", 1.0), "status": "Released",
        }))
        respx.get(f"{TMDB_BASE}/movie/{movie_id}/keywords").mock(
            return_value=httpx.Response(keywords_status, json={
                "id": movie_id, "keywords": [{"id": i, "name": k} for i, k in enumerate(m["keywords"])]}))
    respx.get(f"{TMDB_BASE}/discover/movie").mock(return_value=httpx.Response(
        200, json={"results": [], "page": 1, "total_pages": 1}))
    respx.get(f"{TMDB_BASE}/search/keyword").mock(
        return_value=httpx.Response(200, json={"results": []}))


def create_run(client, **rules):
    resp = client.post("/api/runs", json={"name": "Time", "game_type": "historical_time_travel", "rules_config": {
        "allow_repeats": "strict", "no_consecutive_actor": False, "min_runtime": 0,
        "wildcards_budget": 2, **rules}})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def log(client, run_id, movie_id, **extra):
    return client.post(f"/api/runs/{run_id}/steps", json={"movie_id": movie_id, **extra})


def steps(client, run_id):
    return client.get(f"/api/runs/{run_id}").json()["steps"]


def test_engine_is_registered(client):
    engines = {e["game_type"]: e for e in client.get("/api/engines").json()}
    meta = engines["historical_time_travel"]
    assert meta["display_name"] == "Historical Time-Travel"
    assert {"discover_candidates", "json_rules", "modifiers"} <= set(meta["capabilities"])


def test_direction_is_validated(client):
    resp = client.post("/api/runs", json={"name": "x", "game_type": "historical_time_travel",
                                          "rules_config": {"direction": "sideways"}})
    assert resp.status_code == 422 and "direction" in resp.text


def test_climb_enforces_a_later_setting_year_not_release_year(client, db_engine):
    run_id = create_run(client)
    with respx.mock:
        mock_universe()
        first = log(client, run_id, 1)  # set in 100 AD
        assert first.status_code == 201
        # Released 1982 (earlier than Gladiator Days' 2000) but set in 2080: legal.
        ok = log(client, run_id, 4)
        # Released 2001, set in 1250: moves backward from 2080.
        for force in (False, True):
            blocked = log(client, run_id, 2, force=force)
            assert blocked.status_code == 409
            assert blocked.json()["detail"]["blocked"] is True
    assert ok.status_code == 201
    meta = ok.json()["transition_metadata"]
    assert meta["narrative_delta"] == 1980
    assert meta["narrative_year"] == 2080 and meta["narrative_era_label"] == "Cyberpunk Future"
    assert meta["direction"] == "climb"
    assert "Historical Time-Travel" in blocked.json()["detail"]["reason"]
    assert "1250" in blocked.json()["detail"]["reason"] and "2080" in blocked.json()["detail"]["reason"]

    listed = steps(client, run_id)
    assert [(s["movie_narrative_year"], s["movie_narrative_era_label"]) for s in listed] == [
        (100, "Ancient Rome"), (2080, "Cyberpunk Future")]
    with Session(db_engine) as session:
        assert session.get(CachedMovie, 4).narrative_year == 2080


def test_the_same_setting_year_does_not_count(client):
    run_id = create_run(client)
    twin = {**UNIVERSE, 7: {"title": "Rome Again", "release": "2010-01-01", "keywords": ["ancient rome"]}}
    with respx.mock:
        mock_universe(twin)
        log(client, run_id, 1)
        assert log(client, run_id, 7).status_code == 409


def test_descent_walks_backward(client):
    run_id = create_run(client, direction="descent")
    with respx.mock:
        mock_universe()
        assert log(client, run_id, 3).status_code == 201  # 1943
        assert log(client, run_id, 4).status_code == 409  # 2080: forward
        back = log(client, run_id, 2)  # 1250
        assert back.status_code == 201
        assert back.json()["transition_metadata"]["narrative_delta"] == 1250 - 1943
        assert back.json()["transition_metadata"]["direction"] == "descent"
        assert log(client, run_id, 6).status_code == 201  # -1200


def test_descent_blocks_a_later_setting_with_a_clear_reason(client):
    run_id = create_run(client, direction="descent")
    with respx.mock:
        mock_universe()
        log(client, run_id, 2)
        reason = log(client, run_id, 3).json()["detail"]["reason"]
    assert "must be set before 1250" in reason


def test_no_period_indicator_is_contemporary_and_saved(client, db_engine):
    run_id = create_run(client)
    with respx.mock:
        mock_universe()
        assert log(client, run_id, 1).status_code == 201
        ok = log(client, run_id, 5)
    assert ok.status_code == 201
    assert ok.json()["transition_metadata"]["narrative_year"] == 2015
    assert ok.json()["transition_metadata"]["narrative_era_label"] == "Contemporary"
    with Session(db_engine) as session:
        assert session.get(CachedMovie, 5).narrative_era_label == "Contemporary"


def test_unreachable_keywords_do_not_save_a_guess(client, db_engine):
    run_id = create_run(client)
    with respx.mock:
        mock_universe(keywords_status=404)
        assert log(client, run_id, 1).status_code == 201
    with Session(db_engine) as session:
        assert session.get(CachedMovie, 1).narrative_year is None
    # The step still reads back with its (unsaved) default: nothing is invented for the UI.
    assert steps(client, run_id)[0]["movie_narrative_year"] is None


def test_a_saved_era_is_not_resolved_again(client):
    run_id = create_run(client)
    with respx.mock:
        mock_universe()
        log(client, run_id, 1)
        log(client, run_id, 4)
        keyword_routes = [r for r in respx.routes if "keywords" in str(r.pattern)]
        calls_after_first = sum(r.call_count for r in keyword_routes)
        client.post(f"/api/runs/{run_id}/validate", json={"movie_id": 4})
        assert sum(r.call_count for r in keyword_routes) == calls_after_first


def test_hybrid_mode_also_needs_a_shared_actor(client):
    run_id = create_run(client, require_cast_link=True)
    with respx.mock:
        mock_universe()
        for movie_id in UNIVERSE:
            respx.get(f"{TMDB_BASE}/movie/{movie_id}/credits").mock(return_value=httpx.Response(
                200, json={"id": movie_id, "cast": [], "crew": []}))
        log(client, run_id, 1)
        resp = log(client, run_id, 4)
    assert resp.status_code == 409  # forward in time, but no shared cast


# --- LLM fallback ---------------------------------------------------------------


@pytest.fixture()
def llm_config():
    return llm.LlmConfig(provider=llm.PROVIDER_OLLAMA, base_url="http://x", model="m")


def run_ensure(db_engine, movie_id, config, **kw):
    import asyncio

    class FakeTmdb:
        async def get_movie_keywords(self, _id):
            return []

    with Session(db_engine) as session:
        movie = session.get(CachedMovie, movie_id)
        return asyncio.run(he.ensure_narrative_era(session, FakeTmdb(), movie, config, **kw)), movie


def test_llm_resolves_an_ambiguous_overview(db_engine, llm_config, monkeypatch):
    with Session(db_engine) as session:
        session.add(film(tmdb_id=1, title="Odd", overview="A tailor and a ghost."))
        session.commit()

    async def fake_generate(config, system, prompt, max_tokens=96):
        assert "Odd" in prompt
        return '{"year": 1888, "era": "Victorian London"}'

    monkeypatch.setattr(llm, "generate", fake_generate)
    (result, movie) = run_ensure(db_engine, 1, llm_config)
    assert result == (1888, "Victorian London")
    assert (movie.narrative_year, movie.narrative_era_label) == (1888, "Victorian London")


def test_llm_failure_or_garbage_is_not_saved_as_contemporary(db_engine, llm_config, monkeypatch):
    with Session(db_engine) as session:
        session.add(film(tmdb_id=1, title="Odd", overview="A tailor and a ghost."))
        session.commit()

    async def broken(config, system, prompt, max_tokens=96):
        raise llm.LlmUnavailable("down")

    monkeypatch.setattr(llm, "generate", broken)
    (result, movie) = run_ensure(db_engine, 1, llm_config)
    assert result == (2000, "Contemporary") and movie.narrative_year is None

    async def garbage(config, system, prompt, max_tokens=96):
        return "I think it's old"

    monkeypatch.setattr(llm, "generate", garbage)
    (result, movie) = run_ensure(db_engine, 1, llm_config)
    assert result == (2000, "Contemporary") and movie.narrative_year == 2000  # model answered, nothing found


def test_llm_is_skipped_for_pool_films_and_never_for_keyword_hits(db_engine, llm_config, monkeypatch):
    with Session(db_engine) as session:
        session.add(film(tmdb_id=1, overview="A tailor and a ghost."))
        session.commit()
    calls = []

    async def spy(config, system, prompt, max_tokens=96):
        calls.append(prompt)
        return '{"year": 1888, "era": "x"}'

    monkeypatch.setattr(llm, "generate", spy)
    (result, movie) = run_ensure(db_engine, 1, llm_config, allow_llm=False)
    assert calls == [] and movie.narrative_year is None

    with Session(db_engine) as session:
        session.add(film(tmdb_id=2, overview="In 1943, a spy."))
        session.commit()
    (result, _) = run_ensure(db_engine, 2, llm_config)
    assert result == (1943, "1940s") and calls == []


# --- narrative-era endpoint -------------------------------------------------------


def test_endpoint_resolves_and_overrides(client, db_engine):
    with respx.mock:
        mock_universe()
        resolved = client.post("/api/movies/3/narrative-era")
        assert resolved.status_code == 200
        assert resolved.json() == {
            "tmdb_id": 3, "narrative_year": 1943, "narrative_era_label": "World War II",
            "source": "resolved"}

        manual = client.post("/api/movies/3/narrative-era", json={
            "narrative_year": 1944, "narrative_era_label": "D-Day"})
        assert manual.json() == {
            "tmdb_id": 3, "narrative_year": 1944, "narrative_era_label": "D-Day", "source": "manual"}

        bce = client.post("/api/movies/6/narrative-era", json={"narrative_year": -1300})
        assert bce.json()["narrative_year"] == -1300
        assert bce.json()["narrative_era_label"] == "Ancient World"

        label_only = client.post("/api/movies/3/narrative-era", json={"narrative_era_label": "Normandy"})
        assert label_only.json()["narrative_year"] == 1944 and label_only.json()["narrative_era_label"] == "Normandy"

        # A plain re-resolve replaces the manual edit.
        again = client.post("/api/movies/3/narrative-era")
        assert again.json()["narrative_year"] == 1943

    with Session(db_engine) as session:
        assert session.get(CachedMovie, 3).narrative_year == 1943


def test_manual_override_survives_logging_and_drives_the_rules(client):
    run_id = create_run(client)
    with respx.mock:
        mock_universe()
        client.post("/api/movies/1/narrative-era", json={"narrative_year": 2500, "narrative_era_label": "Far Future"})
        log(client, run_id, 1)
        assert log(client, run_id, 4).status_code == 409  # 2080 is no longer after 2500
        assert steps(client, run_id)[0]["movie_narrative_era_label"] == "Far Future"


def test_endpoint_validation(client):
    with respx.mock:
        mock_universe()
        assert client.post("/api/movies/1/narrative-era", json={"narrative_year": 50000}).status_code == 422
        assert client.post("/api/movies/1/narrative-era", json={"narrative_year": "soon"}).status_code == 422
        assert client.post("/api/movies/1/narrative-era", json={"narrative_era_label": "x" * 61}).status_code == 422


def test_endpoint_requires_login(db_engine):
    def override_get_session():
        with Session(db_engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(app) as anon:
            assert anon.post("/api/movies/1/narrative-era").status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_contemporary_label_is_reset_when_the_release_date_changes(client, db_engine):
    with respx.mock:
        mock_universe()
        client.post("/api/movies/5/narrative-era")
    with Session(db_engine) as session:
        assert session.get(CachedMovie, 5).narrative_era_label == "Contemporary"
    changed = {**UNIVERSE, 5: {**UNIVERSE[5], "release": "2016-01-01"}}
    with respx.mock:
        mock_universe(changed)
        client.get("/api/movies/5", params={"refresh": True})
    with Session(db_engine) as session:
        assert session.get(CachedMovie, 5).narrative_year is None


# --- Pick Next pool -----------------------------------------------------------------


def seed_dated(db_engine):
    rows = {
        10: ("Rome Epic", "2000-01-01", 100, "Ancient Rome", 5.0),
        11: ("Knights Tale", "2001-01-01", 1250, "Middle Ages", 6.0),
        12: ("Front Line", "1998-01-01", 1943, "World War II", 7.0),
        13: ("Neon Rain", "1982-01-01", 2080, "Cyberpunk Future", 8.0),
        14: ("Far Away", "2015-01-01", 2150, "Space Age Future", 9.0),
    }
    with Session(db_engine) as session:
        for movie_id, (title, release, year, label, pop) in rows.items():
            session.add(CachedMovie(
                tmdb_id=movie_id, title=title, release_date=release, overview="x", runtime=100,
                narrative_year=year, narrative_era_label=label, popularity=pop, status="Released",
                poster_path=f"/p{movie_id}.jpg", origin_country='["US"]'))
        session.commit()


def discover(client, run_id, frontier):
    resp = client.get(f"/api/runs/{run_id}/discover", params={"frontier_movie_id": frontier})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_pick_next_pool_only_holds_later_settings_nearest_first(client, db_engine):
    seed_dated(db_engine)
    run_id = create_run(client)
    with respx.mock:
        mock_universe({10: {"title": "Rome Epic", "release": "2000-01-01", "keywords": ["ancient rome"]}})
        log(client, run_id, 10)
        pool = discover(client, run_id, 10)
    pool = pool.get("candidates", pool) if isinstance(pool, dict) else pool
    assert [c["movie_id"] for c in pool] == [11, 12, 13, 14]
    first = pool[0]
    assert first["narrative_year"] == 1250 and first["narrative_era_label"] == "Middle Ages"
    assert first["narrative_delta"] == 1150


def test_pick_next_pool_on_a_descent_only_holds_earlier_settings(client, db_engine):
    seed_dated(db_engine)
    run_id = create_run(client, direction="descent")
    with respx.mock:
        mock_universe({13: {"title": "Neon Rain", "release": "1982-01-01", "keywords": ["cyberpunk"]}})
        log(client, run_id, 13)
        pool = discover(client, run_id, 13)
    pool = pool.get("candidates", pool) if isinstance(pool, dict) else pool
    assert [c["movie_id"] for c in pool] == [12, 11, 10]
    assert pool[0]["narrative_delta"] == 1943 - 2080


def test_pool_fetches_fresh_films_by_era_keyword(client, db_engine):
    run_id = create_run(client)
    with respx.mock:
        mock_universe({1: UNIVERSE[1], 4: UNIVERSE[4]})
        respx.get(f"{TMDB_BASE}/search/keyword").mock(
            return_value=httpx.Response(200, json={"results": [{"id": 321, "name": "medieval"}]}))
        respx.get(f"{TMDB_BASE}/discover/movie").mock(return_value=httpx.Response(200, json={
            "results": [{"id": 4, "title": "Neon Rain", "release_date": "1982-06-01",
                         "poster_path": "/p4.jpg", "genre_ids": [], "original_language": "en",
                         "popularity": 6.0}],
            "page": 1, "total_pages": 1}))
        log(client, run_id, 1)
        pool = discover(client, run_id, 1)
    pool = pool.get("candidates", pool) if isinstance(pool, dict) else pool
    assert [c["movie_id"] for c in pool] == [4]
    assert pool[0]["narrative_year"] == 2080
