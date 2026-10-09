import type { RabbitPredicate, RulesConfig, RunStep } from "../types/api";

export const RABBIT_HOLE = "rabbit_hole";

export interface RabbitTier {
  number: number;
  name?: string;
  /** Short rule label for badges and warnings. */
  rule?: string;
  startDepth: number;
  curses?: RabbitPredicate[];
  hidden?: boolean;
  emoji?: string;
  difficulty?: number;
}

const DEFAULT_LIVES = 3;

export function rabbitTiers(
  rules?: RulesConfig,
  metadata?: { rabbit_tiers?: { number: number; name: string; rule: string; start_depth: number; emoji: string }[] | null },
): RabbitTier[] {
  if (rules?.tier_deck) {
    return rules.tier_deck.map((tier) => ({
      number: tier.number,
      name: tier.name,
      rule: tier.rule,
      startDepth: tier.start_depth,
      curses: tier.curses,
      hidden: tier.hidden,
      emoji: tier.emoji,
      difficulty: tier.difficulty,
    }));
  }
  return (metadata?.rabbit_tiers ?? []).map((tier) => ({
    number: tier.number,
    name: tier.name,
    rule: tier.rule,
    startDepth: tier.start_depth,
    emoji: tier.emoji,
  }));
}

export function tierForDepth(
  depth: number,
  rules?: RulesConfig,
  metadata?: Parameters<typeof rabbitTiers>[1],
): RabbitTier {
  const tiers = rabbitTiers(rules, metadata);
  const scheduled = [...tiers].reverse().find((tier) => depth >= tier.startDepth) ??
    tiers[0] ?? { number: 1, name: "Tier 1", rule: "", startDepth: 0 };
  const override = rules?.tier_override;
  if (rules?.rh_rules_version === 2 || rules?.rh_rules_version === 3) {
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
    override.tier <= tiers.length
  ) {
    return tiers[override.tier - 1] ?? scheduled;
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
  metadata?: Parameters<typeof rabbitTiers>[1],
  warningWindow?: number | null,
): RabbitHud {
  const tier = tierForDepth(depth, rules, metadata);
  const tiers = rabbitTiers(rules, metadata);
  const scheduledTier = tierForDepth(
    depth,
    { ...rules, tier_override: undefined, curse_skip: undefined },
    metadata,
  );
  const next = tiers[scheduledTier.number] ?? null;
  const hopsUntilNext = next ? next.startDepth - depth : null;
  const maxLives = rules.max_lives ?? DEFAULT_LIVES;
  const lives = Math.max(0, Math.min(rules.lives_remaining ?? maxLives, maxLives));
  const warning =
    rules.fog !== "off" || warningWindow == null || !next || hopsUntilNext === null || hopsUntilNext > warningWindow
        ? null
        : next
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
export function rabbitSummary(
  steps: RunStep[],
  rules?: RulesConfig,
  metadata?: Parameters<typeof rabbitTiers>[1],
): RabbitSummary {
  const maxDepth = steps.length;
  const tierReached = tierForDepth(
    maxDepth,
    rules ? { ...rules, tier_override: undefined } : undefined,
    metadata,
  );
  return {
    maxDepth,
    tiersConquered: tierReached.number - 1,
    moviesWatched: steps.filter((step) => step.status === "watched").length,
    tierReached,
  };
}
