"""Deterministic title tokens, without substring Roman-numeral false positives."""

import re
import unicodedata

ARTICLES = frozenset(
    {"the", "a", "an", "le", "la", "les", "der", "die", "das", "el", "los", "las", "il", "lo"}
)
UNITS = dict(
    zip(
        [
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
            "twenty",
        ],
        range(21),
        strict=True,
    )
)
ORDINALS = dict(
    zip(
        [
            "zeroth",
            "first",
            "second",
            "third",
            "fourth",
            "fifth",
            "sixth",
            "seventh",
            "eighth",
            "ninth",
            "tenth",
            "eleventh",
            "twelfth",
            "thirteenth",
            "fourteenth",
            "fifteenth",
            "sixteenth",
            "seventeenth",
            "eighteenth",
            "nineteenth",
            "twentieth",
        ],
        range(21),
        strict=True,
    )
)
TENS = {
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
ORDINALS.update(
    {
        "thirtieth": 30,
        "fortieth": 40,
        "fiftieth": 50,
        "sixtieth": 60,
        "seventieth": 70,
        "eightieth": 80,
        "ninetieth": 90,
    }
)
SCALES = {"hundred": 100, "thousand": 1000, "million": 1_000_000}
ROMANS = dict(
    zip(
        [
            "II",
            "III",
            "IV",
            "V",
            "VI",
            "VII",
            "VIII",
            "IX",
            "X",
            "XI",
            "XII",
            "XIII",
            "XIV",
            "XV",
            "XVI",
            "XVII",
            "XVIII",
            "XIX",
            "XX",
        ],
        range(2, 21),
        strict=True,
    )
)


def normalized(title: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", title) if not unicodedata.combining(char)
    )


def first_letter(title: str, ignore_articles: bool = True) -> str | None:
    tokens = re.findall(r"[^\W_]+", normalized(title))
    while tokens and ignore_articles and tokens[0].lower() in ARTICLES:
        tokens.pop(0)
    if not tokens:
        return None
    first = tokens[0][0]
    return "#" if first.isdigit() else first.upper()


def title_numbers(title: str, allow_years: bool = False) -> list[int]:
    text = normalized(title)
    matches = list(re.finditer(r"\d+|[A-Za-z]+", text))
    tokens = [match.group() for match in matches]
    numbers: list[int] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        word = token.lower()
        if token.isdigit():
            number = int(token)
            if allow_years or not (len(token) == 4 and 1900 <= number <= 2099):
                numbers.append(number)
        elif word in UNITS or word in ORDINALS or word in TENS or word in SCALES:
            number = 0
            total = 0
            previous = ""
            start = index
            while index < len(tokens):
                word = tokens[index].lower()
                if index > start and not re.fullmatch(
                    r"[\s-]+", text[matches[index - 1].end() : matches[index].start()]
                ):
                    break
                if word == "and" and previous in SCALES and index + 1 < len(tokens):
                    index += 1
                    previous = "hundred"
                    continue
                if word in UNITS or word in ORDINALS or word in TENS:
                    value = (UNITS | ORDINALS | TENS)[word]
                    if (
                        previous
                        and previous not in SCALES
                        and not (
                            previous in (UNITS | TENS)
                            and (UNITS | TENS)[previous] >= 20
                            and (UNITS | TENS)[previous] % 10 == 0
                            and value < 10
                        )
                    ):
                        break
                    number += value
                elif word in SCALES:
                    scale = SCALES[word]
                    number = max(number, 1) * scale
                    if scale >= 1000:
                        total += number
                        number = 0
                else:
                    break
                previous = word
                index += 1
            number += total
            if allow_years or not 1900 <= number <= 2099:
                numbers.append(number)
            continue
        elif token in ROMANS:
            numbers.append(ROMANS[token])
        index += 1
    return list(dict.fromkeys(numbers))
