from pydantic import BaseModel

from app.schemas.discovery import DiscoveryConnection


class LinkFilm(BaseModel):
    movie_id: int
    title: str
    year: int | None = None
    genres: list[str] = []
    vibe: str | None = None
    plot: str | None = None


class LinkContext(BaseModel):
    mode: str
    mode_name: str
    mode_goal: str
    previous: LinkFilm
    candidate: LinkFilm
    shared_people: list[DiscoveryConnection] = []
    shared_tropes: list[str] = []
    similarity_percent: int | None = None
    bridge_facts: list[str] = []
    rule_payoffs: list[str] = []
    unknowns: list[str] = []


class LinkPitch(BaseModel):
    bridge: str
    candidate_hook: str
