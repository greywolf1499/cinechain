import type { RabbitPredicate, RulesConfig, RunStep } from "../types/api";

export const RABBIT_HOLE = "rabbit_hole";

export interface RabbitTier {
  number: number;
  name: string;
  /** Short rule label for badges and warnings. */
  rule: string;
  startDepth: number;
  curses?: RabbitPredicate[];
}

/** Mirrors backend `engines/rabbit_hole.py`: the tier follows the number of films already logged. */
export const RABBIT_TIERS: RabbitTier[] = [
  { number: 1, name: "Freefall", rule: "No extra constraints", startDepth: 0 },
  { number: 2, name: "The Retro Lock", rule: "Released before 2000", startDepth: 5 },
  { number: 3, name: "Tower of Babel", rule: "Non-English", startDepth: 10 },
  { number: 4, name: "The Micro-Clock", rule: "Under 100 mins", startDepth: 15 },
  { number: 5, name: "The B-Movie Abyss", rule: "Rated under 6.0", startDepth: 20 },
];

const DEFAULT_LIVES = 3;
const WARNING_WINDOW = 2;

export function rabbitTiers(rules?: RulesConfig): RabbitTier[] {
  if (rules?.rh_rules_version !== 2) return RABBIT_TIERS;
  return (rules.tier_deck ?? []).map((tier) => ({
    number: tier.number, name: tier.name, rule: tier.rule,
    startDepth: tier.start_depth, curses: tier.curses,
  }));
}

export function tierForDepth(
  depth: number,
  rules?: RulesConfig,
): RabbitTier {
  const tiers = rabbitTiers(rules);
  const scheduled = [...tiers].reverse().find((tier) => depth >= tier.startDepth) ?? tiers[0];
  const override = rules?.tier_override;
  if (rules?.rh_rules_version === 2) {
    const current = override?.depth === depth && override.predicate
      ? { ...scheduled, name: override.predicate.name, rule: override.predicate.rule }
      : scheduled;
    return { ...current, curses: rules.curse_skip === depth ? current.curses?.slice(0, -1) : current.curses };
  }
  if (
    override &&
    override.depth === depth &&
    override.tier !== undefined &&
    Number.isInteger(override.tier) &&
    override.tier >= 2 &&
    override.tier <= RABBIT_TIERS.length
  ) {
    return RABBIT_TIERS[override.tier - 1];
  }
  return scheduled;
}

export interface RabbitHud {
  depth: number;
  tier: RabbitTier;
  next: RabbitTier | null;
  /** Hops until the next tier's rule applies (1 = the very next hop). */
  hopsUntilNext: number | null;
  warning: string | null;
  lives: number;
  maxLives: number;
  tierOverride: number | null;
}

export function rabbitHud(
  rules: RulesConfig,
  depth: number,
): RabbitHud {
  const tier = tierForDepth(depth, rules);
  const tiers = rabbitTiers(rules);
  const scheduledTier = tierForDepth(depth, { ...rules, tier_override: undefined, curse_skip: undefined });
  const next = tiers[scheduledTier.number] ?? null;
  const hopsUntilNext = next ? next.startDepth - depth : null;
  const maxLives = rules.max_lives ?? DEFAULT_LIVES;
  const lives = Math.max(0, Math.min(rules.lives_remaining ?? maxLives, maxLives));
  const warning =
    next && hopsUntilNext !== null && hopsUntilNext <= WARNING_WINDOW
      ? `⚠️ Warning: Tier ${next.number} (${next.rule}) begins ${
          hopsUntilNext === 1 ? "on the next hop" : `in ${hopsUntilNext} hops`
        }!`
      : null;
  return {
    depth,
    tier,
    next,
    hopsUntilNext,
    warning,
    lives,
    maxLives,
    tierOverride: rules.tier_override?.depth === depth ? tier.number : null,
  };
}

export interface RabbitSummary {
  maxDepth: number;
  tiersConquered: number;
  moviesWatched: number;
  tierReached: RabbitTier;
}

/** The Game Over tally: a tier counts as conquered once every film of its depth range is logged. */
export function rabbitSummary(steps: RunStep[], rules?: RulesConfig): RabbitSummary {
  const maxDepth = steps.length;
  const tierReached = tierForDepth(maxDepth, rules ? { ...rules, tier_override: undefined } : undefined);
  return {
    maxDepth,
    tiersConquered: tierReached.number - 1,
    moviesWatched: steps.filter((step) => step.status === "watched").length,
    tierReached,
  };
}
