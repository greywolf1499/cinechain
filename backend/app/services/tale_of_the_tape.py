"""Facet-grounded matchup axes and schema-checked Tale of the Tape narration."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from app.facets.genres import TMDB_GENRE_IDS
from app.facets.store import movie_facts
from app.models.cache import CachedMovie
from app.services import llm
from app.utils.countries import parse_countries

TAPE_VERSION = 1
AXES = ("Tone", "Era", "Reception", "Scale", "Theme", "Origin", "Pedigree")
_DIGITS = re.compile(r"\d+")
_CAPITALIZED_WORD = re.compile(r"\b[A-Z][a-z]{2,}\b")
_GENRE_NAMES = {genre_id: name for name, genre_id in TMDB_GENRE_IDS.items()}


class TapeCard(BaseModel):
    movie_id: int
    title: str
    year: int | None = None
    runtime: int | None = None
    rating: float | None = None
    genres: list[int] = Field(default_factory=list)
    language: str | None = None
    countries: list[str] = Field(default_factory=list)
    collection_id: int | None = None
    overview: str = ""
    facts: dict[str, Any] = Field(default_factory=dict)


class TapeLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    headline: str = Field(min_length=1, max_length=220)


def card_from_cache(session: Session, movie_id: int, supplied: dict[str, Any]) -> TapeCard:
    movie = session.get(CachedMovie, movie_id)
    if movie is None:
        movie = CachedMovie(
            tmdb_id=movie_id, title=str(supplied.get("title") or f"Film {movie_id}")
        )
    facts = movie_facts(session, [movie])[movie_id]
    rating = facts.get("rating")
    year_text = (movie.release_date or "")[:4]
    try:
        year = int(year_text) if year_text else None
    except ValueError:
        year = None
    return TapeCard(
        movie_id=movie_id,
        title=movie.title or str(supplied.get("title") or f"Film {movie_id}"),
        year=year,
        runtime=movie.runtime,
        rating=float(rating) if isinstance(rating, (int, float)) else None,
        genres=list(movie.genre_ids or []),
        language=movie.original_language,
        countries=parse_countries(movie.origin_country),
        collection_id=movie.collection_id,
        overview=(movie.overview or supplied.get("overview") or "")[:600],
        facts=facts,
    )


def axis_values(card: TapeCard) -> dict[str, str | None]:
    """Build readable axis evidence exclusively from cached facts."""
    quadrant = card.facts.get("vibe_quadrant")
    return {
        "Tone": str(quadrant) if quadrant else None,
        "Era": f"{card.year // 10 * 10}s" if card.year else None,
        "Reception": _rating_band(card.rating),
        "Scale": _runtime_band(card.runtime),
        "Theme": (
            ",".join(_GENRE_NAMES.get(value, f"Genre {value}") for value in card.genres)
            if card.genres
            else None
        ),
        "Origin": card.countries[0] if card.countries else card.language,
        "Pedigree": f"collection:{card.collection_id}" if card.collection_id else None,
    }


def select_axes(left: TapeCard, right: TapeCard, matchup_id: str) -> list[dict[str, Any]]:
    """Choose the three strongest contrasts and one shared axis deterministically."""
    left_values, right_values = axis_values(left), axis_values(right)
    seed = int.from_bytes(hashlib.sha256(matchup_id.encode()).digest()[:8], "big")
    ties = {axis: (seed >> (index * 3)) & 7 for index, axis in enumerate(AXES)}
    contrasts = [
        axis
        for axis in AXES
        if left_values[axis] is not None
        and right_values[axis] is not None
        and left_values[axis] != right_values[axis]
    ]
    contrasts.sort(
        key=lambda axis: (-_distance(left_values[axis], right_values[axis]), ties[axis], axis)
    )
    chosen = contrasts[:3]
    common = [
        axis
        for axis in AXES
        if axis not in chosen
        and left_values[axis] is not None
        and left_values[axis] == right_values[axis]
    ]
    common.sort(key=lambda axis: (ties[axis], axis))
    if common:
        chosen.append(common[0])
    for axis in AXES:
        if len(chosen) == 4:
            break
        if axis not in chosen:
            chosen.append(axis)
    return [
        {
            "name": axis,
            "left": left_values[axis],
            "right": right_values[axis],
            "contrast": left_values[axis] != right_values[axis],
        }
        for axis in chosen
    ]


def validate_headline(
    text: str, axes: list[dict[str, Any]], left: TapeCard, right: TapeCard
) -> bool:
    folded = text.casefold()
    allowed_name_tokens = {
        token.casefold()
        for title in (left.title, right.title)
        for token in _CAPITALIZED_WORD.findall(title)
    }
    allowed_name_tokens.update(axis.casefold() for axis in AXES)
    allowed_name_tokens.update(
        token.casefold()
        for axis in axes
        for value in (axis["left"], axis["right"])
        if isinstance(value, str)
        for token in _CAPITALIZED_WORD.findall(value)
    )
    if any(
        token.casefold() not in allowed_name_tokens for token in _CAPITALIZED_WORD.findall(text)
    ):
        return False
    fact_digits = set(_DIGITS.findall(json.dumps(axes)))
    fact_digits.update(_DIGITS.findall(left.title))
    fact_digits.update(_DIGITS.findall(right.title))
    referenced_axis = any(axis["name"].casefold() in folded for axis in axes)
    return (
        5 <= len(text.split()) <= 35
        and referenced_axis
        and set(_DIGITS.findall(text)) <= fact_digits
        and left.title.casefold() in folded
        and right.title.casefold() in folded
    )


async def build_tape(
    session: Session,
    matchup_id: str,
    left_data: dict[str, Any],
    right_data: dict[str, Any],
    config: llm.LlmConfig,
) -> dict[str, Any]:
    left = card_from_cache(session, int(left_data["movie_id"]), left_data)
    right = card_from_cache(session, int(right_data["movie_id"]), right_data)
    axes = select_axes(left, right, matchup_id)
    fallback = TapeLine(headline=template_headline(left, right, axes))
    result = await llm.generate_structured(
        config,
        "Write a concise, grounded sports-broadcast headline from the facts. Do not invent facts.",
        {
            "left_title": left.title,
            "right_title": right.title,
            "left_overview": left.overview,
            "right_overview": right.overview,
            "axes": axes,
        },
        TapeLine,
        retries=1,
        fallback=fallback,
        validator=lambda value: validate_headline(value.headline, axes, left, right),
    )
    return {
        "axes": axes,
        "headline": result.value.headline,
        "source": result.source,
        "version": TAPE_VERSION,
    }


def template_headline(left: TapeCard, right: TapeCard, axes: list[dict[str, Any]]) -> str:
    contrast = next((axis for axis in axes if axis["contrast"]), axes[0])
    shared = next((axis for axis in axes if not axis["contrast"]), None)
    if shared and shared["left"]:
        line = (
            f"{left.title} and {right.title} share {shared['name']}, "
            f"but split on {contrast['name']}."
        )
    else:
        line = f"{left.title} faces {right.title} across the {contrast['name']} divide."
    return line[:220]


def _distance(left: str | None, right: str | None) -> int:
    if left is None or right is None:
        return 0
    return (
        2
        if "," not in left and "," not in right
        else len(set(left.split(",")) ^ set(right.split(",")))
    )


def _rating_band(value: float | None) -> str | None:
    if value is None:
        return None
    return "low" if value < 5.5 else "mid" if value < 7.0 else "high"


def _runtime_band(value: int | None) -> str | None:
    if value is None:
        return None
    return "compact" if value < 90 else "long" if value > 150 else "feature-length"
