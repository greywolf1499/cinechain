"""Bonus link evidence for cast-style hops: Golden Reunions and Character Hops.

- *Golden Reunion*: the director of film A and one of A's top-5 billed actors both work on film B -
  the same pair, together again.
- *Character Hop*: the same character (by name) appears in both films, played by *different* actors
  (Sean Connery's James Bond -> Daniel Craig's James Bond). Where the films share no actor at all it
  is a link in its own right.

Both are pure functions over already-fetched credits, so they cost no extra TMDB calls of their own.
`character_hop` and `golden_reunion` are server-owned `transition_metadata` keys.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

GOLDEN_REUNION_KEY = "golden_reunion"
CHARACTER_HOP_KEY = "character_hop"
REUNION_TOP_BILLED = 5

# Roles that say nothing about *who* the character is: matching them would link half of cinema.
GENERIC_CHARACTERS = frozenset(
    {
        "self",
        "himself",
        "herself",
        "themselves",
        "themself",
        "extra",
        "extras",
        "uncredited",
        "unknown",
        "unnamed",
        "narrator",
        "voice",
        "cameo",
        "various",
        "others",
        "other",
        "background",
        "bystander",
        "crowd",
        "audience",
        "guest",
        "host",
        "hostess",
        "announcer",
        "reporter",
        "journalist",
        "newscaster",
        "anchor",
        "interviewer",
        "interviewee",
        "photographer",
        "driver",
        "taxi driver",
        "cab driver",
        "bartender",
        "waiter",
        "waitress",
        "bouncer",
        "clerk",
        "cashier",
        "receptionist",
        "secretary",
        "nurse",
        "doctor",
        "surgeon",
        "paramedic",
        "patient",
        "teacher",
        "student",
        "professor",
        "priest",
        "pastor",
        "minister",
        "judge",
        "lawyer",
        "jury foreman",
        "juror",
        "witness",
        "prisoner",
        "inmate",
        "guard",
        "prison guard",
        "security guard",
        "soldier",
        "officer",
        "cop",
        "policeman",
        "policewoman",
        "police officer",
        "police",
        "detective",
        "sheriff",
        "deputy",
        "agent",
        "fbi agent",
        "man",
        "woman",
        "boy",
        "girl",
        "kid",
        "child",
        "baby",
        "teenager",
        "old man",
        "old woman",
        "young man",
        "young woman",
        "father",
        "mother",
        "dad",
        "mom",
        "mum",
        "brother",
        "sister",
        "son",
        "daughter",
        "husband",
        "wife",
        "friend",
        "neighbor",
        "neighbour",
        "stranger",
        "passenger",
        "pilot",
        "captain",
        "sailor",
        "villager",
        "townsperson",
        "customer",
        "shopkeeper",
        "owner",
        "landlord",
        "maid",
        "butler",
        "chef",
        "thug",
        "henchman",
        "goon",
        "gangster",
        "bodyguard",
        "assistant",
        "director",
        "producer",
        "actor",
        "actress",
        "dancer",
        "singer",
        "musician",
        "boss",
        "manager",
        "worker",
        "bum",
        "homeless man",
        "drunk",
        "voices",
        "additional voices",
        "stunt double",
        "double",
    }
)
# A lone first name matches half the cast lists in cinema; a one-word hop needs a distinctive name.
COMMON_FIRST_NAMES = frozenset(
    {
        "john",
        "james",
        "michael",
        "david",
        "robert",
        "william",
        "richard",
        "thomas",
        "charles",
        "joseph",
        "mary",
        "jennifer",
        "linda",
        "sarah",
        "emily",
        "jessica",
        "ashley",
        "jack",
        "tom",
        "frank",
        "george",
        "henry",
        "peter",
        "paul",
        "mark",
        "steve",
        "steven",
        "bill",
        "bob",
        "joe",
        "mike",
        "dave",
        "dan",
        "daniel",
        "chris",
        "alex",
        "sam",
        "max",
        "ben",
        "nick",
        "tony",
        "anna",
        "maria",
        "lisa",
        "karen",
        "susan",
        "kate",
        "katie",
        "emma",
        "laura",
        "julia",
        "lucy",
        "alice",
        "eddie",
        "charlie",
        "billy",
        "johnny",
        "danny",
        "bobby",
        "jimmy",
        "tommy",
        "harry",
        "larry",
    }
)
MIN_SINGLE_WORD_LENGTH = 5

_PARENTHETICAL = re.compile(r"[\(\[].*?[\)\]]")
_NOISE_WORDS = re.compile(r"\b(uncredited|voice|archive footage|archival|credit only|as)\b")
_NON_WORD = re.compile(r"[^\w\s'-]", re.UNICODE)


def character_aliases(raw: str | None) -> set[str]:
    """The normalized, non-generic names a cast credit's character goes by ("Peter Parker /
    Spider-Man" is both); empty when the role is generic or too vague to match on."""
    if not raw:
        return set()
    aliases: set[str] = set()
    for part in re.split(r"\s*/\s*|\s+aka\s+|\s*;\s*", _PARENTHETICAL.sub(" ", raw)):
        name = _NON_WORD.sub(" ", _NOISE_WORDS.sub(" ", part.lower()))
        name = re.sub(r"\s+", " ", name).strip(" -'")
        if not name or name in GENERIC_CHARACTERS:
            continue
        words = name.split()
        if len(words) == 1 and (len(name) < MIN_SINGLE_WORD_LENGTH or name in COMMON_FIRST_NAMES):
            continue
        aliases.add(name)
    return aliases


@dataclass(frozen=True)
class CastCredit:
    """A cast entry reduced to what link-bonus detection needs."""

    person_id: int
    name: str
    character: str | None
    order: int | None = None


@dataclass(frozen=True)
class CharacterHop:
    character: str  # as it is credited on the earlier film
    actor_from: CastCredit
    actor_to: CastCredit


def find_character_hop(
    cast_from: Iterable[CastCredit], cast_to: Iterable[CastCredit]
) -> CharacterHop | None:
    """A character on both films played by different actors, preferring the most prominent
    (best-billed) one on the earlier film."""
    later: dict[str, CastCredit] = {}
    for credit in cast_to:
        for alias in character_aliases(credit.character):
            later.setdefault(alias, credit)
    ordered = sorted(cast_from, key=lambda c: c.order if c.order is not None else 9999)
    for credit in ordered:
        for alias in sorted(character_aliases(credit.character)):
            match = later.get(alias)
            if match is not None and match.person_id != credit.person_id:
                return CharacterHop((credit.character or alias).strip(), credit, match)
    return None


@dataclass(frozen=True)
class Person:
    person_id: int
    name: str


def find_golden_reunion(
    directors_from: Sequence[Person],
    cast_from: Iterable[CastCredit],
    directors_to: Sequence[Person],
    cast_to: Iterable[CastCredit],
) -> dict | None:
    """The director of the earlier film directing the later one with one of the earlier film's
    top-5 billed actors in its cast: {"director", "actor"} (plus their TMDB ids), else None."""
    returning = {t.person_id for t in directors_to}
    shared_directors = {d.person_id: d for d in directors_from if d.person_id in returning}
    if not shared_directors:
        return None
    later_cast = {c.person_id for c in cast_to}
    top = sorted(
        (c for c in cast_from if c.order is None or c.order < REUNION_TOP_BILLED),
        key=lambda c: c.order if c.order is not None else 9999,
    )[:REUNION_TOP_BILLED]
    for actor in top:
        if actor.person_id in later_cast and actor.person_id not in shared_directors:
            director = next(iter(shared_directors.values()))
            return {
                "director": director.name,
                "actor": actor.name,
                "director_id": director.person_id,
                "actor_id": actor.person_id,
            }
    return None
