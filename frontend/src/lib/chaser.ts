import { useEngines } from "./queries";
import type { RulesConfig } from "../types/api";

export function useNeedsChaser(rules: RulesConfig) {
  const engines = useEngines();
  const settings = engines.data?.find((engine) => engine.vibe)?.vibe;
  const vibe = rules.vibe_state;
  const comfort = rules.modifiers?.find((modifier) => modifier.key === "vibe_control")?.params.comfort;
  const setpoint = settings?.setpoints[
    comfort === "gentle" || comfort === "brave" ? comfort : "balanced"
  ];
  return {
    heavy:
      settings !== undefined &&
      (vibe?.chaser_recommended === true ||
        (vibe?.rolling_load != null && setpoint !== undefined && vibe.rolling_load > setpoint)),
    error: engines.error,
  };
}
