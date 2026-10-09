import type { DiscoveryCandidate, EngineMeta, RulesConfig } from "../types/api";

export const TUG_OF_WAR = "tug_of_war";
/** Only a stand-in until `/engines` loads: the real default is the backend `target_lead`
 * `RuleField`, which `tugTargetDefault` reads. */
export const TUG_TARGET_LEAD_FALLBACK = 7;
/** The winning target the backend would apply to a run that never stored one. */
export function tugTargetDefault(engines: EngineMeta[] | undefined): number {
  const field = engines
    ?.find((engine) => engine.game_type === TUG_OF_WAR)
    ?.rule_fields.find((rule) => rule.key === "target_lead");
  return typeof field?.default === "number" ? field.default : TUG_TARGET_LEAD_FALLBACK;
}

export function tugTarget(rules: RulesConfig, fallback = TUG_TARGET_LEAD_FALLBACK): number {
  return rules.tug_momentum?.effective_target ?? rules.target_lead ?? fallback;
}

/** Momentum on the number line: positive pulls towards Team A, negative towards Team B. */
export function tugMomentum(rules: RulesConfig): number {
  if ((rules.tug_rules_version === 3 || rules.tug_rules_version === 4) && rules.tug_momentum?.rope != null) return rules.tug_momentum.rope;
  const scores = rules.tug_scores;
  return scores ? scores.team_a - scores.team_b : 0;
}

export function tugNextTeam(rules: RulesConfig): "team_a" | "team_b" {
  return rules.tug_momentum?.next_team ?? "team_a";
}

export function tugBankMultiplier(rules: RulesConfig): number {
  const team = tugNextTeam(rules);
  return ((rules.tug_rules_version === 3 || rules.tug_rules_version === 4) ? rules.tug_momentum?.banks?.[team] : rules.tug_momentum?.anchor === team) ? 2 : 1;
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
