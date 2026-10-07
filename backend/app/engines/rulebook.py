"""Server-owned instructional copy; formatting fails on missing values."""

import re
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


def glossary(rules: Mapping[str, Any] | None = None) -> dict[str, str]:
    terms = {
        "wildcard": "Spend one to skip a missing cast link. You must still meet the film's other rules.",
        "life": "Rabbit Hole's allowance for a forced link or tier violation. At zero lives only legal moves remain.",
        "seed": "The starting film, not an inbound link. Some modes derive it, require two seeds, or use no seed.",
        "tier": "Rabbit Hole's current depth-based restriction. Look ahead to the next tier before choosing a film.",
        "bounty": "A side quest completed by a qualifying logged film. At most one completes per step and earns the mode's reward.",
        "hint": "Meet in the Middle's currency for inspecting the route between the two frontiers.",
        "star": "A Bounty Board achievement recorded with your victory; it never bypasses hard mode restrictions.",
        "raid": "Pick their home film. Pull the rope by 2 and break their streak. A bank doubles the pull.",
        "build": "Pick your own territory to add points. Momentum may increase the pull up to the configured cap.",
        "bank": "Pick a neutral film. Score no points, reset your streak and double your next scoring pull.",
        "anchor": "The banked next-pull multiplier in Tug of War. It is consumed by that team's next scoring pull.",
        "streak": "Each team has its own run of builds. A bank resets yours. A raid breaks theirs.",
        "sudden_death": "The target shrinks after both teams pull. The team behind goes first next round.",
        "veto": "Golden Veto spends your account's token to undo a partner's latest step or cancel their fork. One token refills every 30 days.",
        "fork": "Offer three legal films; another participant vetoes one and chooses one of the remaining two.",
        "checklist": "A fixed collection of films to watch. Regional expeditions can be completed in any order.",
        "track": "An ordered career or filmography. Advance along it within the allowed skip limit.",
        "no_contest": "A watched RT Split film logged without a household rating or points, so missing scores never trap the run.",
    }
    if (rules or {}).get("tug_rules_version") == 1:
        terms.update(
            build="Each watched home film gives that side 1 point, no matter who logs it.",
            raid="There are no raids in this run. Their home films give them 1 point.",
            bank="Neutral films give neither side points. They do not double later pulls.",
            streak="There is no streak bonus in this run.",
            sudden_death="The target stays fixed in this run.",
        )
    elif (rules or {}).get("tug_rules_version") == 2:
        terms.update(
            raid="Gain 1 point and take up to 1 of their points. A bank doubles both.",
            bank="Double your next scoring pull. During Sudden Death, a neutral film gives them 1 point.",
            streak="Build again to grow the shared streak. A raid or bank resets it.",
            sudden_death="The target shrinks as pulls pass. Neutral films give the other team 1 point.",
        )
    return terms


def bullets(lines: list[str], values: Mapping[str, Any]) -> list[str]:
    return [
        part.strip() if part.strip().endswith((".", "!", "?")) else part.strip() + "."
        for line in lines
        for part in re.split(r"(?<=[.!?])\s+(?=[A-Z])|;\s+|\n", line.format_map(values))
        if part.strip()
    ]


def render(section: RuleSection, values: Mapping[str, Any]) -> RuleSection:
    return RuleSection(
        goal=section.goal.format_map(values),
        turn=bullets(section.turn, values),
        scoring=bullets(section.scoring, values),
        lose=bullets(section.lose, values),
        tips=bullets(section.tips, values),
        glossary=list(section.glossary),
    )
