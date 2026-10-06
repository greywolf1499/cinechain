import pytest

from app.utils.title_tokens import first_letter, title_numbers


@pytest.mark.parametrize(("title", "years", "expected"), [
    ("The Seventh Seal", False, [7]), ("Ocean's Eleven", False, [11]),
    ("Rocky II", False, [2]), ("10 Things I Hate About You", False, [10]),
    ("1917", False, []), ("1917", True, [1917]), ("I, Robot", False, []),
    ("Mix", False, []), ("MIX", False, []), ("Se7en", False, [7]),
    ("XX", False, [20]), ("Rocky ii", False, []), ("21st Jump Street", False, [21]),
    ("Twenty-One", False, [21]), ("One Hundred", False, [100]),
    ("One Thousand Two Hundred", False, [1200]), ("2 Fast 2 Furious", False, [2]),
    ("Room 1408", False, [1408]), ("2001: A Space Odyssey", False, []),
    ("2001: A Space Odyssey", True, [2001]), ("Zero Dark Thirty", False, [0, 30]),
    ("One, Two, Three", False, [1, 2, 3]), ("One Two Three", False, [1, 2, 3]),
    ("One Hundred and One", False, [101]), ("Twenty First", False, [21]),
    ("Nineteen Hundred", False, []), ("Nineteen Hundred", True, [1900]),
])
def test_number_tokens(title, years, expected):
    assert title_numbers(title, years) == expected


@pytest.mark.parametrize(("title", "articles", "expected"), [
    ("The Matrix", True, "M"), ("The Matrix", False, "T"),
    ("Élite Squad", True, "E"), ("2001: A Space Odyssey", True, "#"),
    ("... Le Samouraï", True, "S"), ("La La Land", True, "L"),
    ("", True, None), ("!!!", True, None), ("Ａlien", True, "A"),
])
def test_first_letter(title, articles, expected):
    assert first_letter(title, articles) == expected
