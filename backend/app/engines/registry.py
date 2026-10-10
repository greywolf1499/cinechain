from fastapi import HTTPException, status
from sqlmodel import Session

from app.engines.algorithms import AestheticGradientEngine, SemanticTropeEngine
from app.engines.auteur_marathon import AuteurMarathonEngine
from app.engines.base import BaseChallengeEngine
from app.engines.canon_infiltration import CanonInfiltrationEngine
from app.engines.canon_island import CanonIslandEngine
from app.engines.cinechain import CineChainEngine
from app.engines.connect_canon import ConnectCanonEngine
from app.engines.crew_craft import CrewCraftEngine
from app.engines.genre_pendulum import GenrePendulumEngine
from app.engines.grid_crawler import GridCrawlerEngine
from app.engines.historical_time_travel import HistoricalTimeTravelEngine
from app.engines.march_madness import MarchMadnessEngine
from app.engines.meet_in_middle import MeetInTheMiddleEngine
from app.engines.method_actor import MethodActorEngine
from app.engines.mutators import AuteurRelayEngine, ChronoClimbEngine, WorldPassportEngine
from app.engines.rabbit_hole import RabbitHoleEngine
from app.engines.regional_deep_dive import RegionalDeepDiveEngine
from app.engines.rt_split import RottenTomatoesSplitEngine
from app.engines.trackers import DecadeSieveEngine, RouletteEngine
from app.engines.tug_of_war import TugOfWarEngine
from app.services.tmdb import TMDBClient

ENGINE_REGISTRY: dict[str, type[BaseChallengeEngine]] = {
    engine.game_type: engine
    for engine in (
        CineChainEngine,
        CanonIslandEngine,
        GridCrawlerEngine,
        ConnectCanonEngine,
        CanonInfiltrationEngine,
        CrewCraftEngine,
        GenrePendulumEngine,
        MeetInTheMiddleEngine,
        TugOfWarEngine,
        RabbitHoleEngine,
        MarchMadnessEngine,
        MethodActorEngine,
        AuteurMarathonEngine,
        RegionalDeepDiveEngine,
        RottenTomatoesSplitEngine,
        DecadeSieveEngine,
        RouletteEngine,
        ChronoClimbEngine,
        HistoricalTimeTravelEngine,
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
