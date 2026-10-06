"""Server-owned instructional copy; formatting fails on missing values."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RuleSection:
    goal: str
    turn: list[str]
    scoring: list[str]
    lose: list[str]
    tips: list[str]
    glossary: list[str]


GLOSSARY: dict[str, str] = {
    "wildcard": "Spend one to force a soft violation, such as a missing cast link. Hard mode rules and modifiers cannot be bought.",
    "life": "Rabbit Hole's allowance for a forced link or tier violation. At zero lives only legal moves remain.",
    "seed": "The starting film, not an inbound link. Some modes derive it, require two seeds, or use no seed.",
    "tier": "Rabbit Hole's current depth-based restriction. Look ahead to the next tier before choosing a film.",
    "bounty": "A side quest completed by a qualifying logged film. At most one completes per step and earns the mode's reward.",
    "hint": "Meet in the Middle's currency for inspecting the route between the two frontiers.",
    "star": "A Bounty Board achievement recorded with your victory; it never bypasses hard mode restrictions.",
    "raid": "Pick opposing territory. V3 moves the rope 2 and breaks their streak; legacy raids remove only available points. A bank doubles the pull.",
    "build": "Pick your own territory to add points. Momentum may increase the pull up to the configured cap.",
    "bank": "A neutral film banks a multiplier for your next scoring pull. V3 always scores 0 and resets only your own streak; v2 Sudden Death concedes 1.",
    "anchor": "The banked next-pull multiplier in Tug of War. It is consumed by that team's next scoring pull.",
    "streak": "V3 tracks each team's builds independently: a bank resets your streak, a raid breaks the defender's. Legacy v2 has one shared streak.",
    "sudden_death": "After the configured threshold, Tug's target shrinks. V3 settles rounds after both pulls and gives trailing teams initiative; v2 penalizes neutral pulls.",
    "veto": "Golden Veto spends your account's token to undo a partner's latest step or cancel their fork. One token refills every 30 days.",
    "fork": "Offer three legal films; another participant vetoes one and chooses one of the remaining two.",
    "checklist": "A fixed collection of films to watch. Regional expeditions can be completed in any order.",
    "track": "An ordered career or filmography. Advance along it within the allowed skip limit.",
    "no_contest": "A watched RT Split film logged without a household rating or points, so missing scores never trap the run.",
}


def render(section: RuleSection, values: Mapping[str, Any]) -> RuleSection:
    return RuleSection(
        goal=section.goal.format_map(values),
        turn=[line.format_map(values) for line in section.turn],
        scoring=[line.format_map(values) for line in section.scoring],
        lose=[line.format_map(values) for line in section.lose],
        tips=[line.format_map(values) for line in section.tips],
        glossary=list(section.glossary),
    )
