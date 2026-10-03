"""Async multi-pass TMDB resolver: year passes, title-only, slug fallback, fuzzy guard."""

import httpx
import respx

from app.services.tmdb import TMDBClient
from app.services.tmdb_resolver import resolve_movie_id

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
        return httpx.Response(200, json={"results": [
            _movie(4, "Completely Different", "2019-01-01"), _movie(5, "Roma", "1990-01-01")]})

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
