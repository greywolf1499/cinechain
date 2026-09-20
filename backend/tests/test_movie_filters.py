"""Pure unit tests for the reality filter (no network/DB needed beyond a
plain in-memory CachedMovie instance).
"""

from datetime import UTC, datetime, timedelta

from app.models.cache import CachedMovie
from app.services.movie_filters import is_reality_eligible


def _movie(**overrides) -> CachedMovie:
    defaults = {"tmdb_id": 1, "title": "Test Film",
                "release_date": "2000-01-01", "status": "Released"}
    defaults.update(overrides)
    return CachedMovie(**defaults)


def test_released_movie_is_eligible():
    assert is_reality_eligible(_movie()) is True


def test_missing_release_date_is_ineligible():
    assert is_reality_eligible(_movie(release_date=None)) is False


def test_future_release_date_is_ineligible():
    future = (datetime.now(UTC).date() + timedelta(days=30)).isoformat()
    assert is_reality_eligible(_movie(release_date=future)) is False


def test_planned_status_is_ineligible_even_with_past_release_date():
    assert is_reality_eligible(_movie(status="Planned")) is False


def test_in_production_status_is_ineligible():
    assert is_reality_eligible(_movie(status="In Production")) is False


def test_canceled_status_is_ineligible():
    assert is_reality_eligible(_movie(status="Canceled")) is False


def test_unknown_status_does_not_block_an_already_released_movie():
    """Stub movies (discovered via person credits) never carry a `status` -
    absence of status must never be treated as a rejection on its own."""
    assert is_reality_eligible(_movie(status=None)) is True


def test_low_popularity_indie_film_is_never_filtered():
    """The reality filter is release-date/status ONLY - never popularity,
    vote_count, or rating. This is the core philosophy guardrail."""
    assert is_reality_eligible(
        _movie(popularity=0.001, status="Released")) is True
