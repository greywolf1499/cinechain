"""Phase 26c: The Method Actor Marathon - career track, milestones, near-chronological order."""

import asyncio
from copy import deepcopy
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import numpy as np
import pytest
import respx
from sqlmodel import Session

from app.engines import method_actor
from app.engines.base import RunSetupError
from app.engines.method_actor import (
    age_at,
    build_career_track,
    enrich_career_track,
    flag_against_type,
    flag_comebacks,
    flag_first_theatrical,
    flag_genre_pivots,
    flag_language_crossover,
    group_career_eras,
)
from app.services import embeddings
from app.services.tmdb import TMDBError
from tests.test_graph_mutators import TMDB_BASE, client, db_engine

__all__ = ["client", "db_engine"]

TODAY = date(2026, 10, 4)
ACTOR = 500


def credit(
    movie_id,
    year,
    *,
    order=0,
    votes=1000,
    rating=7.0,
    character="Hero",
    genres=(),
    month="06-01",
    **extra,
):
    return {
        "id": movie_id,
        "title": f"Film {movie_id}",
        "release_date": f"{year}-{month}",
        "poster_path": f"/p{movie_id}.jpg",
        "character": character,
        "order": order,
        "vote_average": rating,
        "vote_count": votes,
        "genre_ids": list(genres),
        **extra,
    }


# A career in order: id 1 is the debut ... id 8 is the latest.
CAREER = [
    credit(1, 1985, order=9, votes=80, rating=5.5),  # debut
    credit(2, 1990, order=5, votes=300, rating=6.5),
    credit(3, 1994, order=1, votes=9000, rating=8.9),  # breakout + prestige peak
    credit(4, 1999, order=0, votes=6000, rating=8.1),
    credit(5, 2005, order=0, votes=1500, rating=7.0),
    credit(6, 2012, order=2, votes=900, rating=6.8),
    credit(7, 2018, order=6, votes=700, rating=7.2),
    credit(8, 2024, order=1, votes=2500, rating=7.9),  # modern resurgence
]


def milestones(track):
    return {f["movie_id"]: f["milestones"] for f in track if f["milestones"]}


# --- the career track ---


def test_the_track_is_chronological_and_flags_the_milestones():
    track = build_career_track(CAREER, "1960-03-10", TODAY)
    assert [f["movie_id"] for f in track] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert milestones(track) == {
        1: ["debut"],
        2: ["comeback"],
        3: ["breakout", "prestige_peak", "comeback"],
        4: ["first_lead", "comeback"],
        5: ["comeback"],
        6: ["comeback"],
        7: ["comeback"],
        8: ["modern_resurgence", "comeback"],
    }


def test_age_is_the_actors_age_at_release():
    track = build_career_track(CAREER, "1960-07-01", TODAY)
    assert [f["age"] for f in track][:3] == [24, 29, 33]  # 1985-06 (before the July birthday)
    assert age_at(None, "2000-01-01") is None
    assert age_at("1960-03-10", "1950-01-01") is None  # before they were born


def test_non_roles_and_unreleased_films_never_make_the_track():
    noisy = [
        *CAREER,
        credit(20, 1980, character="Himself"),  # earlier than the debut
        credit(21, 1981, character="Cop (uncredited)"),
        credit(22, 1982, genres=[99]),  # documentary
        credit(23, 1983, genres=[10770]),  # TV movie
        credit(24, 2030),  # not released yet
        {**credit(25, 1984), "release_date": ""},  # no date
        {**credit(26, 1984), "adult": True},
    ]
    track = build_career_track(noisy, None, TODAY)
    assert [f["movie_id"] for f in track] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert track[0]["milestones"] == ["debut"]


def test_the_track_is_curated_to_well_known_leading_roles_but_keeps_milestones():
    extras = [credit(100 + i, 1995 + i % 20, order=40, votes=3) for i in range(30)]
    cameos = [credit(200, 2001, order=30, votes=10)]
    track = build_career_track([*CAREER, *extras, *cameos], None, TODAY)
    ids = [f["movie_id"] for f in track]
    assert ids == [1, 2, 3, 4, 5, 6, 7, 8]  # unknown cameos are curated away, the debut stays
    many = [credit(300 + i, 1980 + i, order=1, votes=500 + i) for i in range(40)]
    capped = build_career_track(many, None, TODAY)
    assert len(capped) == 25
    assert [f["release_date"] for f in capped] == sorted(f["release_date"] for f in capped)


def test_a_thin_career_still_gets_a_track():
    thin = [credit(i, 2000 + i, order=20, votes=5) for i in range(1, 8)]
    track = build_career_track(thin, None, TODAY)
    assert len(track) >= 3 and track[0]["milestones"] == ["debut"]


def test_milestone_thresholds_fall_back_for_small_careers():
    modest = [
        credit(1, 1990, order=0, votes=150, rating=6.0),
        credit(2, 2024, order=0, votes=120, rating=7.5),
    ]
    marks = milestones(build_career_track(modest, None, TODAY))
    assert marks[1] == ["debut", "breakout", "first_lead"]
    assert marks[2] == ["prestige_peak", "modern_resurgence", "comeback"]


def test_no_feature_credits_is_a_setup_error():
    with pytest.raises(RunSetupError):
        build_career_track([credit(1, 2030)], None, TODAY)


def test_genre_pivot_requires_two_confirming_films_and_never_invents_missing_genres():
    credits = [credit(i, 2000 + i, genres=[35, 10749]) for i in range(1, 5)]
    credits += [credit(i, 2000 + i, genres=[18, 36]) for i in range(5, 8)]
    track = build_career_track(credits, None, TODAY)
    assert "genre_pivot" in track[4]["milestones"]
    assert "next two" in track[4]["evidence"]["genre_pivot"]
    original = deepcopy(track)
    flag_genre_pivots(track)
    assert track == original
    for last_genres in ([35, 10749], []):
        changed = [*credits[:-1], {**credits[-1], "genre_ids": last_genres}]
        assert not any(
            "genre_pivot" in f["milestones"] for f in build_career_track(changed, None, TODAY)
        )


def test_comeback_boundary_and_language_crossover_are_fact_backed():
    track = build_career_track(
        [
            credit(1, 2000, original_language="hi"),
            credit(2, 2003, original_language="hi"),
            credit(3, 2007, original_language="en"),
            credit(4, 2008, original_language="hi"),
        ],
        None,
        TODAY,
    )
    assert "comeback" not in track[1]["milestones"]
    assert "comeback" in track[2]["milestones"]
    assert "language_crossover" in track[2]["milestones"]
    assert "en" in track[2]["evidence"]["language_crossover"]
    boundary = [
        {**track[0], "release_date": "2000-06-02", "milestones": []},
        {**track[2], "release_date": "2004-06-01", "milestones": []},
    ]
    assert not any("comeback" in f["milestones"] for f in flag_comebacks(boundary))
    unknown = [{**film, "original_language": None} for film in track]
    assert not any(
        "language_crossover" in film["milestones"]
        for film in flag_language_crossover([{**f, "milestones": []} for f in unknown])
    )


def test_against_type_uses_career_distances_not_a_genre_guess():
    track = build_career_track(
        [credit(i, 2000 + i, genres=[18], overview="romance") for i in range(1, 13)], None, TODAY
    )
    vectors = {film["movie_id"]: np.array([1.0, 0.0]) for film in track}
    vectors[12] = np.array([0.0, 1.0])
    result = flag_against_type(track, vectors)
    assert "against_type" in result[-1]["milestones"]
    assert "against_type" not in track[-1]["milestones"]
    assert not any("genre_pivot" in film["milestones"] for film in result)
    assert result[-1]["evidence"]["against_type"]
    assert "against_type" not in flag_against_type(track[:3], vectors)[-1]["milestones"]
    eras = group_career_eras(result)
    assert eras[-1]["era_index"] == 1
    assert eras[-1]["era_label"] == "Drama era"


def release(kind, day):
    return {"release_date": f"{day}T00:00:00Z", "type": kind}


def test_first_theatrical_needs_verified_release_types():
    track = build_career_track([credit(1, 2000), credit(2, 2001), credit(3, 2002)], None, TODAY)
    dates = {
        1: [{"release_dates": [release(1, "2000-06-01")]}],
        2: [{"release_dates": [release(3, "2001-06-01")]}],
    }
    result = flag_first_theatrical(track, dates)
    assert "first_theatrical" in result[1]["milestones"]
    assert "2001-06-01" in result[1]["evidence"]["first_theatrical"]
    assert not any("first_theatrical" in f["milestones"] for f in flag_first_theatrical(track, {}))
    dates[1] = [{"release_dates": [release(3, "2000-06-01")]}]
    assert not any(
        "first_theatrical" in f["milestones"] for f in flag_first_theatrical(track, dates)
    )


@pytest.mark.anyio
async def test_career_postpass_is_bounded_and_embeddings_unavailable_is_optional(
    db_engine, monkeypatch
):
    track = build_career_track(
        [credit(i, 2000 + i, overview="role") for i in range(1, 5)], None, TODAY
    )
    tmdb = SimpleNamespace(get_movie_release_dates=AsyncMock(return_value=[]))

    async def offline(*args):
        raise embeddings.EmbeddingUnavailable("offline")

    monkeypatch.setattr(embeddings, "embed_batch", offline)
    with Session(db_engine) as session:
        result = await enrich_career_track(track, tmdb, session)
        assert tmdb.get_movie_release_dates.await_count == 3
        assert not any("against_type" in f["milestones"] for f in result)
        tmdb.get_movie_release_dates = AsyncMock(side_effect=TMDBError("offline", 500))
        result = await enrich_career_track(track, tmdb, session)
        assert not any("first_theatrical" in f["milestones"] for f in result)


@pytest.mark.anyio
async def test_optional_release_lookup_times_out_without_blocking_the_track(db_engine, monkeypatch):
    track = build_career_track([credit(i, 2000 + i) for i in range(1, 4)], None, TODAY)

    async def slow(movie_id):
        await asyncio.sleep(60)
        return []

    tmdb = SimpleNamespace(get_movie_release_dates=slow)
    monkeypatch.setattr(method_actor, "RELEASE_EVIDENCE_SECONDS", 0.01)
    with Session(db_engine) as session:
        result = await enrich_career_track(track, tmdb, session)
    assert [f["movie_id"] for f in result] == [1, 2, 3]
    assert not any("first_theatrical" in f["milestones"] for f in result)


@pytest.mark.anyio
async def test_postpass_uses_local_jit_vectors_and_villain_is_only_a_suggestion(
    db_engine, monkeypatch
):
    track = build_career_track(
        [credit(i, 2000 + i, genres=[18], overview="romance") for i in range(1, 13)], None, TODAY
    )
    track[-1].update(overview="Raj is a scientist helping his rural village.", character="Raj")
    tmdb = SimpleNamespace(
        get_movie_release_dates=AsyncMock(return_value=[]),
        get_movie_keywords=AsyncMock(return_value=["villain"]),
    )

    async def embed(config, texts):
        assert config.provider == embeddings.PROVIDER_LOCAL
        return embeddings.EmbeddingBatch(
            [
                np.array([0.0, 1.0]) if "scientist" in text else np.array([1.0, 0.0])
                for text in texts
            ],
            config.fingerprint,
        )

    monkeypatch.setattr(embeddings, "embed_batch", embed)
    with Session(db_engine) as session:
        result = await enrich_career_track(track, tmdb, session)
    assert "against_type" in result[-1]["milestones"]
    assert result[-1]["suggestions"] == ["villain"]
    assert "villain" not in result[-1]["milestones"]
    assert tmdb.get_movie_keywords.await_count == 1


def test_every_milestone_has_evidence():
    assert all(
        film["evidence"].get(milestone)
        for film in build_career_track(CAREER, None, TODAY)
        for milestone in film["milestones"]
    )


def test_career_eras_are_player_editable_but_career_evidence_is_not(client):
    eras = [{"start_movie_id": 1, "end_movie_id": 3, "label": "My early favourites"}]
    with respx.mock:
        mock_actor()
        run_id = make_run(
            client, career_eras=eras, filmography=[{"movie_id": 99, "milestones": ["villain"]}]
        )
    before = detail(client, run_id)["rules_config"]
    assert before["career_eras"] == eras
    response = client.patch(
        f"/api/runs/{run_id}/rules",
        json={
            "career_eras": [{**eras[0], "label": "Villain era"}],
            "filmography": [{"movie_id": 99, "era_index": 99}],
        },
    )
    assert response.status_code == 200, response.text
    after = response.json()["rules_config"]
    assert after["career_eras"][0]["label"] == "Villain era"
    assert after["filmography"] == before["filmography"]
    assert after["wildcards_budget"] == 1  # An annotation-only PATCH must not reset gameplay.
    for invalid in (
        [{**eras[0], "end_movie_id": 99}],
        [eras[0], eras[0]],
        [{**eras[0], "label": " "}],
        [{**eras[0], "start_movie_id": 8}],
    ):
        assert (
            client.patch(f"/api/runs/{run_id}/rules", json={"career_eras": invalid}).status_code
            == 422
        )


@pytest.mark.parametrize("start,status_code", [(1, 422), (2, 201)])
def test_creation_eras_reference_the_final_number_filtered_track(client, start, status_code):
    credits = [
        {**film, "title": "Unnumbered Debut" if film["id"] == 1 else film["title"]}
        for film in CAREER
    ]
    with respx.mock:
        mock_actor(credits)
        response = client.post(
            "/api/runs",
            json={
                "name": "Numbered career",
                "game_type": "method_actor",
                "rules_config": {
                    "actor_id": ACTOR,
                    "modifiers": [{"key": "number_in_title", "params": {}}],
                    "career_eras": [
                        {"start_movie_id": start, "end_movie_id": 3, "label": "Early era"}
                    ],
                },
            },
        )
    assert response.status_code == status_code, response.text
    if status_code == 201:
        assert 1 not in {
            film["movie_id"] for film in response.json()["rules_config"]["filmography"]
        }
    else:
        assert "on-track span" in response.json()["detail"]


# --- the API ---


def mock_actor(credits=CAREER, birthday="1960-03-10"):
    respx.get(f"{TMDB_BASE}/person/{ACTOR}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": ACTOR,
                "name": "Method Man",
                "birthday": birthday,
                "profile_path": "/m.jpg",
            },
        )
    )
    respx.get(f"{TMDB_BASE}/person/{ACTOR}/movie_credits").mock(
        return_value=httpx.Response(200, json={"id": ACTOR, "cast": credits, "crew": []})
    )
    for entry in credits:
        respx.get(f"{TMDB_BASE}/movie/{entry['id']}/release_dates").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        respx.get(f"{TMDB_BASE}/movie/{entry['id']}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": entry["id"],
                    "title": entry["title"],
                    "release_date": entry["release_date"],
                    "poster_path": entry["poster_path"],
                    "overview": "",
                    "origin_country": ["US"],
                    "original_language": "en",
                    "runtime": 100,
                    "genres": [],
                    "popularity": 5.0,
                    "status": "Released",
                },
            )
        )
    respx.get(f"{TMDB_BASE}/movie/99").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 99,
                "title": "Off Track",
                "release_date": "2000-01-01",
                "poster_path": None,
                "overview": "",
                "origin_country": ["US"],
                "original_language": "en",
                "runtime": 100,
                "genres": [],
                "popularity": 5.0,
                "status": "Released",
            },
        )
    )


def make_run(client, **rules):
    resp = client.post(
        "/api/runs",
        json={
            "name": "Marathon",
            "game_type": "method_actor",
            "rules_config": {"actor_id": ACTOR, "wildcards_budget": 1, **rules},
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def log(client, run_id, movie_id, **extra):
    return client.post(f"/api/runs/{run_id}/steps", json={"movie_id": movie_id, **extra})


def detail(client, run_id):
    return client.get(f"/api/runs/{run_id}").json()


def test_the_engine_is_registered(client):
    engines = {e["game_type"]: e for e in client.get("/api/engines").json()}
    assert engines["method_actor"]["display_name"] == "The Method Actor Marathon"


def test_creating_a_run_stores_the_actor_and_the_career_track(client):
    with respx.mock:
        mock_actor()
        run_id = make_run(client)
    rules = detail(client, run_id)["rules_config"]
    assert rules["actor"] == {"id": ACTOR, "name": "Method Man"}
    assert "actor_id" not in rules and rules["max_skip"] == 2
    track = rules["filmography"]
    assert [f["movie_id"] for f in track] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert track[0]["milestones"] == ["debut"] and track[0]["age"] == 25
    assert track[2]["milestones"] == ["breakout", "prestige_peak", "comeback"]


def test_preparation_attaches_first_theatrical_from_tmdb_release_dates(client):
    with respx.mock:
        mock_actor()
        respx.get(f"{TMDB_BASE}/movie/1/release_dates").mock(
            return_value=httpx.Response(
                200, json={"results": [{"release_dates": [release(1, "1985-06-01")]}]}
            )
        )
        respx.get(f"{TMDB_BASE}/movie/2/release_dates").mock(
            return_value=httpx.Response(
                200, json={"results": [{"release_dates": [release(3, "1990-06-01")]}]}
            )
        )
        run_id = make_run(client)
    film = detail(client, run_id)["rules_config"]["filmography"][1]
    assert "first_theatrical" in film["milestones"]
    assert "1990-06-01" in film["evidence"]["first_theatrical"]


def test_an_unknown_actor_or_a_missing_id_is_rejected(client):
    with respx.mock:
        respx.get(f"{TMDB_BASE}/person/{ACTOR}").mock(return_value=httpx.Response(404, json={}))
        respx.get(f"{TMDB_BASE}/person/{ACTOR}/movie_credits").mock(
            return_value=httpx.Response(404, json={})
        )
        missing = client.post(
            "/api/runs",
            json={"name": "x", "game_type": "method_actor", "rules_config": {"actor_id": ACTOR}},
        )
    assert missing.status_code == 422
    nothing = client.post("/api/runs", json={"name": "x", "game_type": "method_actor"})
    assert nothing.status_code == 422
    bad = client.post(
        "/api/runs",
        json={
            "name": "x",
            "game_type": "method_actor",
            "rules_config": {"actor_id": ACTOR, "max_skip": 99},
        },
    )
    assert bad.status_code == 422


def test_films_must_follow_the_career_order(client):
    with respx.mock:
        mock_actor()
        run_id = make_run(client)
        assert log(client, run_id, 1).status_code == 201  # the debut
        assert log(client, run_id, 2).status_code == 201  # next in line
        assert (
            log(client, run_id, 5).status_code == 201
        )  # skips films 3 and 4: still near-sequential
    assert [s["movie_id"] for s in detail(client, run_id)["steps"]] == [1, 2, 5]


def test_skipping_too_far_or_going_backwards_needs_a_wildcard(client):
    with respx.mock:
        mock_actor()
        run_id = make_run(client)
        log(client, run_id, 1)
        too_far = log(client, run_id, 6)  # skips four films
        assert too_far.status_code == 409 and not too_far.json()["detail"]["blocked"]
        assert "skips 4 films" in too_far.json()["detail"]["reason"]
        assert log(client, run_id, 3).status_code == 201
        back = log(client, run_id, 2)
        assert back.status_code == 409 and "comes before" in back.json()["detail"]["reason"]
        forced = log(client, run_id, 2, force=True)  # a wildcard buys it
        assert forced.status_code == 201 and forced.json()["transition_metadata"]["wildcard_used"]


def test_strict_mode_allows_no_skips(client):
    with respx.mock:
        mock_actor()
        run_id = make_run(client, max_skip=0)
        assert log(client, run_id, 2).status_code == 409  # the first film must be the debut
        assert log(client, run_id, 1).status_code == 201
        assert log(client, run_id, 3).status_code == 409
        assert log(client, run_id, 2).status_code == 201


def test_a_film_off_the_track_is_always_blocked(client):
    with respx.mock:
        mock_actor()
        run_id = make_run(client)
        for force in (False, True):
            resp = log(client, run_id, 99, force=force)
            assert resp.status_code == 409 and resp.json()["detail"]["blocked"] is True
        assert "career track" in resp.json()["detail"]["reason"]


def test_logging_the_final_film_completes_the_career(client):
    with respx.mock:
        mock_actor()
        run_id = make_run(client)
        for movie_id in range(1, 9):
            assert log(client, run_id, movie_id).status_code == 201
    done = detail(client, run_id)
    assert done["status"] == "completed" and done["status_reason"] == "Career complete: Method Man"


def test_person_search_lists_actors_first(client):
    with respx.mock:
        respx.get(f"{TMDB_BASE}/search/person").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": 1,
                            "name": "A Director",
                            "known_for_department": "Directing",
                            "profile_path": None,
                            "known_for": [{"title": "X"}],
                        },
                        {
                            "id": 2,
                            "name": "An Actor",
                            "known_for_department": "Acting",
                            "profile_path": "/a.jpg",
                            "known_for": [{"title": "Y"}, {"name": "Z"}],
                        },
                    ]
                },
            )
        )
        resp = client.get("/api/people/search", params={"q": "an"})
    assert resp.status_code == 200
    people = resp.json()
    assert [p["person_id"] for p in people] == [2, 1]
    assert people[0]["known_for"] == ["Y", "Z"] and people[0]["profile_path"] == "/a.jpg"


@pytest.mark.parametrize(
    "length,size", [("short", 6), ("feature", 12), ("full", 25), ("endless", 60)]
)
def test_marathon_length_caps(length, size):
    credits = [credit(i, 1900 + i, votes=1000 + i) for i in range(1, 81)]
    track = build_career_track(credits, None, TODAY, length=length)
    assert len(track) == size
    assert len({film["movie_id"] for film in track}) == size
    assert [film["year"] for film in track] == sorted(film["year"] for film in track)


def test_milestones_length_keeps_marks_and_pads_collapsed_milestones():
    track = build_career_track(CAREER, None, TODAY, length="milestones")
    assert len(track) == 8  # New evidence-backed comebacks also belong on a milestones track.
    assert set(milestones(track)) == set(range(1, 9))
    collapsed = [credit(i, 1900 + i, votes=1000 - i, rating=9 - i / 10) for i in range(1, 9)]
    assert len(build_career_track(collapsed, None, TODAY, length="milestones")) == 3


def test_free_order_can_go_back_but_cannot_leave_track_or_finish_on_last_alone(client):
    with respx.mock:
        mock_actor()
        run_id = make_run(client, order="free")
        assert log(client, run_id, 8).status_code == 201
        assert detail(client, run_id)["status"] == "active"
        assert log(client, run_id, 1).status_code == 201
        assert log(client, run_id, 99).status_code == 409
        for movie_id in range(2, 8):
            assert log(client, run_id, movie_id).status_code == 201
    assert detail(client, run_id)["status"] == "completed"


def test_endless_wrap_counts_only_distinct_watched_on_track_films(client):
    with respx.mock:
        mock_actor()
        run_id = make_run(client, track_length="endless", order="free")
        assert log(client, run_id, 8).status_code == 201
        assert log(client, run_id, 1, status="planned").status_code == 201
    assert detail(client, run_id)["status"] == "active"
    result = client.post(f"/api/runs/{run_id}/wrap")
    assert result.status_code == 200
    assert result.json()["status"] == "completed"
    assert result.json()["completed_at"]
    assert result.json()["status_reason"] == "Marathon wrapped: 1 of 8 films (12%)"
    assert client.post(f"/api/runs/{run_id}/wrap").status_code == 409


def test_wrap_rejects_non_endless_marathons_and_other_modes(client):
    with respx.mock:
        mock_actor()
        run_id = make_run(client)
    assert client.post(f"/api/runs/{run_id}/wrap").status_code == 422
    other = client.post(
        "/api/runs", json={"name": "Graph", "rules_config": {"track_length": "endless"}}
    ).json()["id"]
    assert client.post(f"/api/runs/{other}/wrap").status_code == 422
    assert client.post("/api/runs/missing/wrap").status_code == 404


def test_order_maps_to_skips_and_length_is_creation_only(client):
    with respx.mock:
        mock_actor()
        run_id = make_run(client, order="strict", max_skip=10)
    assert detail(client, run_id)["rules_config"]["max_skip"] == 0
    changed = client.patch(f"/api/runs/{run_id}/rules", json={"order": "free"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["rules_config"]["max_skip"] is None
    assert (
        client.patch(f"/api/runs/{run_id}/rules", json={"track_length": "endless"}).status_code
        == 422
    )
