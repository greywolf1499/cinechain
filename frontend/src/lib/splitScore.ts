import type { RulesConfig, RunStep } from "../types/api";

export const RT_SPLIT = "rt_split";
export const DEFAULT_TARGET_POINTS = 3;

export type SplitTeam = "team_a" | "team_b";

export const SPLIT_TEAMS: Record<SplitTeam, { label: string; short: string; emoji: string }> = {
  team_a: { label: "Team Critic 🍅", short: "Critic", emoji: "🍅" },
  team_b: { label: "Team Audience 🍿", short: "Audience", emoji: "🍿" },
};

export function targetPoints(rules: RulesConfig): number {
  return rules.target_points ?? DEFAULT_TARGET_POINTS;
}

/** Who a household rating scores for: strictly closer to the critics, otherwise the audience. */
export function settlePoint(household: number, critic: number, audience: number): SplitTeam {
  return Math.abs(household - critic) < Math.abs(household - audience) ? "team_a" : "team_b";
}

export function isValidHousehold(value: number): boolean {
  return Number.isInteger(value) && value >= 1 && value <= 100;
}

export interface SplitSettlement {
  household_score: number;
  critic_score: number;
  audience_score: number;
  divergence: number;
  point_to: SplitTeam;
}

/** The server's settlement of a step (absent on non-split steps). */
export function settlementOf(step: RunStep): SplitSettlement | null {
  const meta = step.transition_metadata as Partial<SplitSettlement> | null;
  return meta && meta.point_to && meta.household_score !== undefined ? (meta as SplitSettlement) : null;
}
