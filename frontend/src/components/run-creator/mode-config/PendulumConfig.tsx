import GenreCycleInput from "../../GenreCycleInput";
import type { ModeConfigProps } from "./types";

export default function PendulumConfig({ draft, update }: ModeConfigProps) {
  return (
    <GenreCycleInput
      cycle={draft.genreCycle}
      onCycleChange={(genreCycle) => update({ genreCycle })}
      frequency={draft.swingFrequency}
      onFrequencyChange={(swingFrequency) => update({ swingFrequency })}
    />
  );
}
