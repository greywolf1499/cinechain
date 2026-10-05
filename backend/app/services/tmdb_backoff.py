"""Shared 429 handling for callers that make many TMDB calls in a row (the
bridge solver, the diary importer): pause with `asyncio.sleep` - never block
the event loop - and retry, until a caller-defined deadline."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from app.services.tmdb import TMDBNotFoundError, TMDBRateLimitError

logger = logging.getLogger(__name__)

MAX_RATE_LIMIT_PAUSE_SECONDS = 30.0


class DeadlineReached(Exception):
    """The caller's time limit expired while waiting on TMDB."""


async def fetch_with_backoff[T](
    fetch: Callable[[], Awaitable[T]],
    deadline: float,
    on_pause: Callable[[float], None] | None = None,
) -> T | None:
    """Runs one TMDB fetch. On a 429 it pauses (escalating, honoring Retry-After,
    capped at the remaining time) and retries; it raises `DeadlineReached` only
    when `deadline` (a `time.monotonic()` value) passes. A 404 returns None."""
    pause = 2.0
    while True:
        if time.monotonic() >= deadline:
            raise DeadlineReached
        try:
            return await fetch()
        except TMDBRateLimitError as exc:
            wait = min(
                pause if exc.retry_after is None else exc.retry_after, MAX_RATE_LIMIT_PAUSE_SECONDS
            )
            logger.warning("TMDB rate limit hit; pausing %.1fs", wait)
            if on_pause is not None:
                on_pause(wait)
            await asyncio.sleep(max(0.0, min(wait, deadline - time.monotonic())))
            pause = min(pause * 2, MAX_RATE_LIMIT_PAUSE_SECONDS)
        except TMDBNotFoundError:
            return None
