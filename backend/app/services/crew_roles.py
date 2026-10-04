"""The only crew jobs CineChain ingests (the Crew & Craft Trail), and the role each stands for.

Storage is deliberately narrow: a film's crew list runs to hundreds of people, but only these
craft roles ever become links between films. `Director` is included so directors live in the same
unified table as the other crafts (Auteur Relay keeps its own director tables)."""

ROLE_ACTOR = "actor"
ROLE_COMPOSER = "composer"
ROLE_CINEMATOGRAPHER = "cinematographer"
ROLE_WRITER = "writer"
ROLE_DIRECTOR = "director"

# Most specific craft first: when one person fills several roles on a film and the two films
# share none of them, this decides which role represents them.
ROLE_PRIORITY = (
    ROLE_DIRECTOR, ROLE_COMPOSER, ROLE_CINEMATOGRAPHER, ROLE_WRITER, ROLE_ACTOR)

# TMDB job -> (role, department)
CRAFT_JOBS: dict[str, tuple[str, str]] = {
    "Original Music Composer": (ROLE_COMPOSER, "Sound"),
    "Director of Photography": (ROLE_CINEMATOGRAPHER, "Camera"),
    "Screenplay": (ROLE_WRITER, "Writing"),
    "Writer": (ROLE_WRITER, "Writing"),
    "Director": (ROLE_DIRECTOR, "Directing"),
}

ROLE_LABELS = {
    ROLE_ACTOR: "Actor",
    ROLE_COMPOSER: "Composer",
    ROLE_CINEMATOGRAPHER: "Cinematographer",
    ROLE_WRITER: "Writer",
    ROLE_DIRECTOR: "Director",
}


def role_for_job(job: str | None) -> str | None:
    entry = CRAFT_JOBS.get(job or "")
    return entry[0] if entry else None
