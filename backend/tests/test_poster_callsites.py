"""Keep every poster explicitly actionable or concealed, without a JS test runner."""

import re
from pathlib import Path


def poster_tags(text):
    for match in re.finditer(r"<MoviePoster\b", text):
        depth = 0
        quote = None
        for index in range(match.end(), len(text)):
            char = text[index]
            if quote:
                if char == quote and text[index - 1] != "\\":
                    quote = None
            elif char in "\"'`":
                quote = char
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            elif char == ">" and depth == 0:
                yield text[match.start() : index + 1]
                break


def test_all_movie_posters_have_detail_or_concealment():
    root = Path(__file__).resolve().parents[2] / "frontend" / "src"
    failures = [
        f"{path.relative_to(root)}: {tag}"
        for path in root.rglob("*.tsx")
        for tag in poster_tags(path.read_text())
        if not re.search(r"\bmovieId\s*=|\bconcealed(?:\s|=|/|>)", tag)
    ]
    assert not failures, "\n".join(failures)


def test_poster_guard_handles_nested_props():
    assert list(poster_tags("<MoviePoster detailOptions={{ step }} movieId={id} />")) == [
        "<MoviePoster detailOptions={{ step }} movieId={id} />"
    ]
