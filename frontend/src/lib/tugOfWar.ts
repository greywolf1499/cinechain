import type { DiscoveryCandidate, RulesConfig, TugDimension } from "../types/api";

export const TUG_OF_WAR = "tug_of_war";
export const DEFAULT_TARGET_LEAD = 4;
export const DEFAULT_ERA_A_BEFORE = 1975;
export const DEFAULT_ERA_B_AFTER = 2005;

export const TUG_DIMENSIONS: Record<
  TugDimension,
  { label: string; detail: string; teamA: (rules: RulesConfig) => string; teamB: (rules: RulesConfig) => string }
> = {
  era: {
    label: "Era",
    detail: "Old cinema against new: films in between set a neutral anchor.",
    teamA: (rules) => `Pre-${rules.era_a_before ?? DEFAULT_ERA_A_BEFORE}`,
    teamB: (rules) => `Post-${rules.era_b_after ?? DEFAULT_ERA_B_AFTER}`,
  },
  geography: {
    label: "Geography",
    detail: "The West against the rest of the world, by the film's first production country.",
    teamA: () => "Western (US & Europe)",
    teamB: () => "Rest of World",
  },
};

export function tugTarget(rules: RulesConfig): number {
  return rules.tug_momentum?.effective_target ?? rules.target_lead ?? DEFAULT_TARGET_LEAD;
}

/** Momentum on the number line: positive pulls towards Team A, negative towards Team B. */
export function tugMomentum(rules: RulesConfig): number {
  if (rules.tug_rules_version === 3 && rules.tug_momentum?.rope != null) return rules.tug_momentum.rope;
  const scores = rules.tug_scores;
  return scores ? scores.team_a - scores.team_b : 0;
}

export function tugNextTeam(rules: RulesConfig): "team_a" | "team_b" {
  return rules.tug_momentum?.next_team ?? "team_a";
}

export function tugBankMultiplier(rules: RulesConfig): number {
  const team = tugNextTeam(rules);
  return (rules.tug_rules_version === 3 ? rules.tug_momentum?.banks?.[team] : rules.tug_momentum?.anchor === team) ? 2 : 1;
}

export function tugEffectLabel(
  effect: NonNullable<DiscoveryCandidate["tug_effect"]>,
  points: number,
  multiplier?: number,
): string {
  if (effect === "invasion") return multiplier === undefined
    ? `⚔️ Raid (${points}-point swing)`
    : `⚔️ Raid (+${multiplier} you · −${points - multiplier} them)`;
  if (effect === "sudden_neutral") return "☠️ Sudden neutral · opponent +1";
  if (effect === "neutral") return "⚓ Bank (next pull ×2)";
  return `🔥 Build (+${points} you)`;
}
