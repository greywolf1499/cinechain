import type { RulesConfig, RunStep } from "../types/api";

export const RABBIT_HOLE = "rabbit_hole";

export interface RabbitTier {
  number: number;
  name: string;
  /** Short rule label for badges and warnings. */
  rule: string;
  startDepth: number;
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

export function tierForDepth(depth: number): RabbitTier {
  return [...RABBIT_TIERS].reverse().find((tier) => depth >= tier.startDepth) ?? RABBIT_TIERS[0];
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
}

export function rabbitHud(rules: Pick<RulesConfig, "lives_remaining" | "max_lives">, depth: number): RabbitHud {
  const tier = tierForDepth(depth);
  const next = RABBIT_TIERS[tier.number] ?? null;
  const hopsUntilNext = next ? next.startDepth - depth : null;
  const maxLives = rules.max_lives ?? DEFAULT_LIVES;
  const lives = Math.max(0, Math.min(rules.lives_remaining ?? maxLives, maxLives));
  const warning =
    next && hopsUntilNext !== null && hopsUntilNext <= WARNING_WINDOW
      ? `⚠️ Warning: Tier ${next.number} (${next.rule}) begins ${
          hopsUntilNext === 1 ? "on the next hop" : `in ${hopsUntilNext} hops`
        }!`
      : null;
  return { depth, tier, next, hopsUntilNext, warning, lives, maxLives };
}

export interface RabbitSummary {
  maxDepth: number;
  tiersConquered: number;
  moviesWatched: number;
  tierReached: RabbitTier;
}

/** The Game Over tally: a tier counts as conquered once every film of its depth range is logged. */
export function rabbitSummary(steps: RunStep[]): RabbitSummary {
  const maxDepth = steps.length;
  const tierReached = tierForDepth(maxDepth);
  return {
    maxDepth,
    tiersConquered: tierReached.number - 1,
    moviesWatched: steps.filter((step) => step.status === "watched").length,
    tierReached,
  };
}
