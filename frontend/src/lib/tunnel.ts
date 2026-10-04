import type { RunStep, TunnelSide } from "../types/api";

export const MEET_IN_THE_MIDDLE = "meet_in_the_middle";

export function stepSide(step: RunStep): TunnelSide {
  return step.transition_metadata?.tunnel_side === "tail" ? "tail" : "head";
}

/** The two tracks of a Meet in the Middle run, each in the order it was logged. */
export function splitSides(steps: RunStep[]): { head: RunStep[]; tail: RunStep[] } {
  const head: RunStep[] = [];
  const tail: RunStep[] = [];
  for (const step of steps) (stepSide(step) === "tail" ? tail : head).push(step);
  return { head, tail };
}

export const SIDE_LABELS: Record<TunnelSide, string> = {
  head: "Partner A",
  tail: "Partner B",
};
