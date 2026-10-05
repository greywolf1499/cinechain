import { useNavigate } from "react-router-dom";
import type { ComponentType } from "react";
import { AUTEUR_MARATHON } from "../../lib/auteurTrack";
import { MARCH_MADNESS } from "../../lib/bracket";
import { METHOD_ACTOR } from "../../lib/careerTrack";
import { REGIONAL_DEEP_DIVE } from "../../lib/expedition";
import { GENRE_PENDULUM } from "../../lib/pendulum";
import { RT_SPLIT } from "../../lib/splitScore";
import { TUG_OF_WAR } from "../../lib/tugOfWar";
import { ModeConfigProps } from "./mode-config/types";
import TugConfig from "./mode-config/TugConfig";
import SplitConfig from "./mode-config/SplitConfig";
import CanonListConfig from "./mode-config/CanonListConfig";
import DecadeConfig from "./mode-config/DecadeConfig";
import BracketConfig from "./mode-config/BracketConfig";
import { DirectorConfig, MethodActorConfig } from "./mode-config/ActorConfig";
import DiveConfig from "./mode-config/DiveConfig";
import PendulumConfig from "./mode-config/PendulumConfig";
import RabbitHoleConfig from "./mode-config/RabbitHoleConfig";
import { RABBIT_HOLE } from "../../lib/rabbitHole";

const MODE_CONFIG: Record<string, ComponentType<ModeConfigProps>> = {
  [TUG_OF_WAR]: TugConfig,
  [RT_SPLIT]: SplitConfig,
  canon_island: CanonListConfig,
  decade_sieve: DecadeConfig,
  [MARCH_MADNESS]: BracketConfig,
  [METHOD_ACTOR]: MethodActorConfig,
  [AUTEUR_MARATHON]: DirectorConfig,
  [REGIONAL_DEEP_DIVE]: DiveConfig,
  [GENRE_PENDULUM]: PendulumConfig,
  [RABBIT_HOLE]: RabbitHoleConfig,
};

export default function ModeConfigPanel({
  gameType,
  draft,
  update,
  curatedLists,
}: Omit<ModeConfigProps, "onGoLists"> & { gameType: string }) {
  const navigate = useNavigate();
  const Config = MODE_CONFIG[gameType];
  if (!Config) {
    return <p className="rounded-lg border border-app-border bg-app-bg/60 p-3 text-xs text-zinc-500">No extra setup needed for this mode.</p>;
  }
  return <Config draft={draft} update={update} curatedLists={curatedLists} onGoLists={() => navigate("/lists")} />;
}
