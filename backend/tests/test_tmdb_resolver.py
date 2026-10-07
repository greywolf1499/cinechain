"""Async multi-pass TMDB resolver: year passes, title-only, slug fallback, fuzzy guard."""

import httpx
import pytest
import respx

from app.services.tmdb import TMDBClient, TMDBError
from app.services.tmdb_resolver import (
    normalize_title,
    rank_candidates,
    resolve_movie,
    resolve_movie_id,
)

SEARCH = "https://api.themoviedb.org/3/search/movie"


def _movie(movie_id, title, release):
    return {"id": movie_id, "title": title, "release_date": release}


async def _resolve(handler, title, year, slug=None):
    with respx.mock:
        route = respx.get(SEARCH).mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            found = await resolve_movie_id(TMDBClient(client), title, year, slug=slug)
    return found, route


async def test_exact_year_match_stops_after_one_search():
    def handler(request):
        assert request.url.params["primary_release_year"] == "2019"
        return httpx.Response(200, json={"results": [_movie(1, "Parasite", "2019-05-30")]})

    found, route = await _resolve(handler, "Parasite", 2019)
    assert found == 1 and route.call_count == 1


async def test_falls_back_to_adjacent_year_then_title_only():
    years = []

    def handler(request):
        year = request.url.params.get("primary_release_year")
        years.append(year)
        if year == "2018":  # Letterboxd and TMDB disagree by a year
            return httpx.Response(200, json={"results": [_movie(2, "Roma", "2018-08-25")]})
        return httpx.Response(200, json={"results": []})

    found, _ = await _resolve(handler, "Roma", 2019)
    assert found == 2 and years == ["2019", "2018"]

    def title_only(request):
        if "primary_release_year" in request.url.params:
            return httpx.Response(200, json={"results": []})
        return httpx.Response(200, json={"results": [_movie(3, "Roma", "2019-01-01")]})

    found, _ = await _resolve(title_only, "Roma", 2019)
    assert found == 3


async def test_rejects_weak_title_matches_and_far_years():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "results": [
                    _movie(4, "Completely Different", "2019-01-01"),
                    _movie(5, "Roma", "1990-01-01"),
                ]
            },
        )

    found, _ = await _resolve(handler, "Roma", 2019)
    assert found is None


async def test_slug_fallback_is_tried_last_and_skipped_when_it_repeats_the_title():
    def handler(request):
        if request.url.params["query"] == "amelie from montmartre":
            return httpx.Response(200, json={"results": [_movie(6, "Amélie", "2001-04-25")]})
        return httpx.Response(200, json={"results": []})

    found, route = await _resolve(handler, "Amelie", 2001, slug="amelie-from-montmartre-2001")
    assert found == 6
    queries = [call.request.url.params["query"] for call in route.calls]
    assert queries[-1] == "amelie from montmartre"

    found, route = await _resolve(handler, "Nothing Here", None, slug="nothing-here")
    assert found is None and route.call_count == 1  # slug == title: no second search


class Matcher:
    def __init__(self, search=None, find=None, directors=None):
        self.search = search or (lambda query, year: [])
        self.find = find or {}
        self.directors = directors or {}
        self.calls = []

    async def search_movies(self, query, page=1, year=None):
        self.calls.append(("search", query, year))
        return {"results": self.search(query, year)}

    async def find_by_imdb_id(self, imdb_id):
        self.calls.append(("find", imdb_id))
        return self.find

    async def get_movie_directors(self, movie_id):
        self.calls.append(("directors", movie_id))
        return [{"name": name} for name in self.directors.get(movie_id, [])]


@pytest.mark.parametrize(
    "source,target",
    [
        ("Amélie", "Amelie"),
        ("The Cook, the Thief & His Wife", "Cook the Thief and His Wife"),
        ("Thing, The", "The Thing"),
        ("Rocky II", "Rocky 2"),
        ("Star Wars: Episode IV", "Star Wars Episode 4"),
        ("Chapter XXIV", "Chapter 24"),
        ("七人の侍", "七人の侍"),
    ],
)
def test_normalization_is_shared_and_equivalent(source, target):
    assert normalize_title(source) == normalize_title(target)


async def test_non_latin_titles_do_not_collapse_to_empty_matching_strings():
    matcher = Matcher(search=lambda query, year: [_movie(1, "別の映画", "2000-01-01")])
    outcome = await resolve_movie(matcher, "七人の侍", 2000)
    assert outcome.tmdb_id is None and outcome.status == "unmatched"


@pytest.mark.parametrize("media_type,expected", [("movie", "matched"), ("tv", "tv_title")])
async def test_inline_ids_respect_type_without_network(media_type, expected):
    matcher = Matcher()
    outcome = await resolve_movie(matcher, "Film", 2020, inline_id=123, tmdb_type=media_type)
    assert outcome.status == expected and outcome.tier == "inline"
    assert outcome.tmdb_id == (123 if media_type == "movie" else None)
    assert not matcher.calls


@pytest.mark.parametrize(
    "source,target,query",
    [
        ("Amélie", "Amelie", "amelie"),
        ("The Salt & Pepper", "Salt and Pepper", "salt and pepper"),
        ("Rocky II", "Rocky 2", "rocky 2"),
    ],
)
async def test_normalized_tier_handles_title_variants_and_adjacent_year(source, target, query):
    matcher = Matcher(
        search=lambda text, year: (
            [_movie(17, target, "2001-01-01")] if text == query and year == 2001 else []
        )
    )
    outcome = await resolve_movie(matcher, source, 2002)
    assert outcome == (17, "normalized", "matched", None)


async def test_exact_tier_requires_equal_year():
    matcher = Matcher(search=lambda query, year: [_movie(17, "Roma", "2018-01-01")])
    outcome = await resolve_movie(matcher, "Roma", 2019)
    assert outcome == (17, "normalized", "matched", None)


async def test_search_tier_and_slug_fallback_are_explicit():
    matcher = Matcher(
        search=lambda query, year: (
            [_movie(17, "Amélie", "2001-01-01")] if query == "amelie from montmartre" else []
        )
    )
    assert await resolve_movie(matcher, "Amélie", 2001, slug="amelie-from-montmartre-2001") == (
        17,
        "search",
        "matched",
        None,
    )


async def test_ambiguous_candidates_are_not_guessed_and_director_breaks_tie():
    movies = [_movie(1, "Solaris", "1972-01-01"), _movie(2, "Solaris", "2002-01-01")]
    matcher = Matcher(
        search=lambda query, year: movies,
        directors={
            1: ["Andrei Tarkovsky"],
            2: ["Steven Soderbergh"],
        },
    )
    outcome = await resolve_movie(matcher, "Solaris", None)
    assert outcome.tmdb_id is None and outcome.status == "ambiguous" and outcome.reason
    matched = await resolve_movie(matcher, "Solaris", None, directors=["Steven Soderbergh"])
    assert matched.tmdb_id == 2 and matched.status == "matched"


async def test_imdb_lookup_is_lazy_and_disambiguates():
    calls = []

    async def load_imdb():
        calls.append("deep")
        return "tt1234567"

    matcher = Matcher(find={"movie_results": [_movie(91, "Different spelling", "2020-01-01")]})
    outcome = await resolve_movie(matcher, "Unlisted", 2020, load_imdb=load_imdb)
    assert outcome == (91, "imdb", "matched", None) and calls == ["deep"]
    assert matcher.calls[-1] == ("find", "tt1234567")
    calls.clear()
    matcher.search = lambda query, year: [_movie(17, "Unlisted", "2020-01-01")]
    assert (await resolve_movie(matcher, "Unlisted", 2020, load_imdb=load_imdb)).tier == "exact"
    assert not calls


async def test_imdb_tv_only_and_multiple_movies_are_not_movie_matches():
    matcher = Matcher(find={"tv_results": [{"id": 99}]})
    outcome = await resolve_movie(matcher, "TV", None, imdb_id="tt1234567")
    assert outcome.tmdb_id is None and outcome.status == "tv_title"
    matcher.find = {"movie_results": [{"id": 1}, {"id": 2}]}
    assert (await resolve_movie(matcher, "Film", None, imdb_id="tt1234567")).status == "ambiguous"


async def test_find_by_imdb_uses_shared_retry_and_pacing(monkeypatch):
    waits = []

    async def sleep(delay):
        waits.append(delay)

    monkeypatch.setattr("app.services.tmdb.asyncio.sleep", sleep)
    with respx.mock:
        respx.get(SEARCH).respond(200, json={"results": []})
        find = respx.get("https://api.themoviedb.org/3/find/tt1234567").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "0.25"}),
                httpx.Response(200, json={"movie_results": [_movie(91, "Film", "2020-01-01")]}),
            ]
        )
        async with httpx.AsyncClient() as client:
            outcome = await resolve_movie(TMDBClient(client), "Film", 2020, imdb_id="tt1234567")
    assert outcome.tier == "imdb" and outcome.tmdb_id == 91
    assert find.call_count == 2 and waits and 0.25 in waits
    assert find.calls[0].request.url.params["external_source"] == "imdb_id"


async def test_provider_failure_propagates_to_diary_import():
    with respx.mock:
        respx.get(SEARCH).respond(401, json={"error": "invalid key"})
        async with httpx.AsyncClient() as client:
            with pytest.raises(TMDBError):
                await resolve_movie_id(TMDBClient(client), "Film", 2020)


@pytest.mark.parametrize(
    "threshold,slack,normalized",
    [
        (0.92, 0, False),
        (0.85, 1, True),
        (0.80, 2, False),
    ],
)
def test_tier_thresholds_and_year_bounds_are_inclusive(threshold, slack, normalized):
    query = "a" * 100
    equal = _movie(
        1, "a" * int(threshold * 100) + "b" * (100 - int(threshold * 100)), f"{2000 + slack}-01-01"
    )
    below = _movie(2, equal["title"][:-1] + "b", f"{2000 + slack + 1}-01-01")
    # One fewer matching character is below the threshold, at the same eligible year.
    below["title"] = "a" * (int(threshold * 100) - 1) + "b" * (101 - int(threshold * 100))
    assert rank_candidates(
        [equal, below], query, 2000, threshold=threshold, normalized=normalized, year_slack=slack
    ) == [equal]
    below["release_date"] = equal["release_date"]
    assert (
        rank_candidates(
            [below], query, 2000, threshold=threshold, normalized=normalized, year_slack=slack
        )
        == []
    )
    outside_year = {**equal, "release_date": f"{2000 + slack + 1}-01-01"}
    assert (
        rank_candidates(
            [outside_year],
            query,
            2000,
            threshold=threshold,
            normalized=normalized,
            year_slack=slack,
        )
        == []
    )


async def test_an_ambiguous_exact_match_cannot_be_replaced_by_a_near_year_guess():
    def search(query, year):
        if year == 2000:
            return [_movie(1, "Same Title", "2000-01-01"), _movie(2, "Same Title", "2000-05-01")]
        if year == 1999:
            return [_movie(3, "Same Title", "1999-01-01")]
        return []

    outcome = await resolve_movie(Matcher(search=search), "Same Title", 2000)
    assert outcome.tmdb_id is None and outcome.status == "ambiguous"
