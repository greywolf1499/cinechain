from fastapi import HTTPException, status
from sqlmodel import Session

from app.engines.algorithms import AestheticGradientEngine, SemanticTropeEngine
from app.engines.base import BaseChallengeEngine
from app.engines.canon_island import CanonIslandEngine
from app.engines.cinechain import CineChainEngine
from app.engines.crew_craft import CrewCraftEngine
from app.engines.genre_pendulum import GenrePendulumEngine
from app.engines.meet_in_middle import MeetInTheMiddleEngine
from app.engines.mutators import AuteurRelayEngine, ChronoClimbEngine, WorldPassportEngine
from app.engines.trackers import DecadeSieveEngine, RouletteEngine
from app.engines.tug_of_war import TugOfWarEngine
from app.services.tmdb import TMDBClient

ENGINE_REGISTRY: dict[str, type[BaseChallengeEngine]] = {
    engine.game_type: engine
    for engine in (
        CineChainEngine,
        CanonIslandEngine,
        CrewCraftEngine,
        GenrePendulumEngine,
        MeetInTheMiddleEngine,
        TugOfWarEngine,
        DecadeSieveEngine,
        RouletteEngine,
        ChronoClimbEngine,
        WorldPassportEngine,
        AuteurRelayEngine,
        AestheticGradientEngine,
        SemanticTropeEngine,
    )
}


def get_engine_class(game_type: str) -> type[BaseChallengeEngine]:
    try:
        return ENGINE_REGISTRY[game_type]
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown game_type: {game_type}"
        ) from None


def get_engine(game_type: str, session: Session, tmdb: TMDBClient) -> BaseChallengeEngine:
    """Resolve `game_type` to a ready-to-use engine instance.

    Takes `session`/`tmdb` too (beyond just `game_type`) because engines are
    bound to their infrastructure at construction time - see BaseChallengeEngine.
    """
    engine_class = get_engine_class(game_type)
    return engine_class(session, tmdb)
