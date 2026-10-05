import type { BountyId, RulesConfig, RunStep } from "../types/api";

export interface BountyInfo {
  id: BountyId;
  title: string;
  icon: string;
  /** The criterion, as shown on the card (mirrors services/bounties.py). */
  criteria: string;
  /** Written by the AI rather than part of the fixed catalogue. */
  ai?: boolean;
}

export const BOUNTIES: Record<BountyId, BountyInfo> = {
  short_king: { id: "short_king", title: "Short King", icon: "⏱️", criteria: "Runtime under 90 minutes" },
  time_capsule: { id: "time_capsule", title: "Time Capsule", icon: "📼", criteria: "Released before 1960" },
  hidden_gem: { id: "hidden_gem", title: "Hidden Gem", icon: "💎", criteria: "Obscure: TMDB popularity under 12" },
  foreign_horizon: {
    id: "foreign_horizon",
    title: "Foreign Horizon",
    icon: "🌍",
    criteria: "Non-English language and not a US production",
  },
  female_gaze: { id: "female_gaze", title: "Female Gaze", icon: "🎥", criteria: "Directed by a woman" },
  epic_odyssey: { id: "epic_odyssey", title: "Epic Odyssey", icon: "🏔️", criteria: "Runtime over 150 minutes" },
};

/** The bracket logs films itself, so it cannot host a Bounty Board. */
export const NO_BOUNTY_MODES = new Set(["march_madness"]);

export const WILDCARD_REWARD = "+1 🎟️ Wildcard";
export const LIFE_REWARD = "❤️ +1 life";

/** A bounty's display info: the static catalogue, or the run's stored AI definition. */
export function bountyInfo(id: BountyId, rules: Pick<RulesConfig, "custom_bounties">): BountyInfo | null {
  if (BOUNTIES[id]) return BOUNTIES[id];
  const custom = rules.custom_bounties?.[id];
  return custom ? { id, title: custom.title, icon: custom.icon, criteria: custom.description, ai: true } : null;
}

/** The bounty a logged step completed, if any. */
export function completedBountyOf(step: RunStep, rules: Pick<RulesConfig, "custom_bounties">): BountyInfo | null {
  const id = (step.transition_metadata as { completed_bounty?: BountyId } | null)?.completed_bounty;
  return id ? bountyInfo(id, rules) : null;
}
