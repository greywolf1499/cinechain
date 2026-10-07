import re

from app.facets.registry import FacetValue
from app.utils.title_tokens import ARTICLES, ROMANS, first_letter, normalized, title_numbers


def evaluate(title: str) -> dict[str, FacetValue]:
    text = normalized(title)
    words = re.findall(r"[^\W_]+(?:[-'][^\W_]+)*", text)
    while words and words[0].casefold() in ARTICLES:
        words.pop(0)
    letters = "".join(c for c in " ".join(words) if c.isalpha()).casefold()
    last = words[-1][-1].upper() if words else None
    numbers = title_numbers(title)
    with_years = title_numbers(title, allow_years=True)
    years = [n for n in with_years if 1900 <= n <= 2099]
    last_token = re.findall(r"[^\W_]+", text)[-1:]
    sequel = bool(last_token and (last_token[0].isdigit() or last_token[0] in ROMANS))
    sequel = sequel or bool(re.search(r"\bpart\b", text, re.IGNORECASE))
    values: dict[str, FacetValue] = {
        "title_first_letter": first_letter(title),
        "title_first_letter_literal": first_letter(title, ignore_articles=False),
        "title_last_letter": "#" if last and last.isdigit() else last,
        "title_length": len(letters) if words else None,
        "title_word_count": len(words) if words else None,
        "one_word_title": len(words) == 1 if words else None,
        "title_number": numbers if title.strip() else None,
        "title_year_token": years if title.strip() else None,
        "title_number_with_years": with_years if title.strip() else None,
        "title_palindrome": len(letters) >= 3 and letters == letters[::-1] if words else None,
        "title_has_subtitle": bool(re.search(r":|[–—]|\s-\s", title)) if words else None,
        "title_sequel_marker": sequel if words else None,
    }
    for suffix, tokens in (("", numbers), ("_with_years", with_years)):
        values[f"title_number_count{suffix}"] = len(tokens) if title.strip() else None
        values[f"title_number_min{suffix}"] = min(tokens) if tokens else None
        values[f"title_number_max{suffix}"] = max(tokens) if tokens else None
    return values
