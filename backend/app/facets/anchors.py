"""Versioned, culture-neutral language anchors for semantic vibe projections."""

ANCHOR_VERSION = 1

ANCHORS = {
    "valence_positive": (
        "A hopeful story brings relief, kindness, connection, joy and a positive outcome.",
        "A warm and affirming experience leaves its characters and audience feeling encouraged.",
        "A joyful story celebrates friendship, care and the possibility of a better future.",
    ),
    "valence_negative": (
        "A tragic story centers on grief, loss, suffering, despair and painful consequences.",
        "A bleak and sorrowful experience leaves its characters facing hardship and emotional pain.",
        "A somber story focuses on failure, separation, fear and an unhappy outcome.",
    ),
    "arousal_high": (
        "A tense, urgent story moves quickly through danger, conflict, pursuit and sudden action.",
        "An intense experience is filled with suspense, excitement, energy and high stakes.",
        "A forceful story creates pressure through rapid events, confrontation and immediate threats.",
    ),
    "arousal_low": (
        "A calm, quiet story unfolds slowly through reflection, routine and gentle observation.",
        "A peaceful experience has a relaxed pace, little danger and subdued emotional energy.",
        "A meditative story gives space to ordinary life, stillness and thoughtful conversation.",
    ),
    "heaviness": (
        "A grave and emotionally weighty story examines grief, injustice, trauma or human suffering.",
        "A serious experience dwells on difficult choices, painful loss and lasting consequences.",
        "An emotionally heavy story confronts mortality, hardship and the cost of conflict.",
    ),
    "spectacle": (
        "A light, playful spectacle emphasizes wonder, humor, visual excitement and entertainment.",
        "A lively adventure delights through colorful action, comic surprises and escapist fun.",
        "A buoyant story favors playful invention, exciting set pieces and an entertaining mood.",
    ),
}
