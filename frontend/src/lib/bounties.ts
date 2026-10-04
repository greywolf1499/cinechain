import type { BountyId, RunStep } from "../types/api";

export interface BountyInfo {
  id: BountyId;
  title: string;
  icon: string;
  /** The criterion, as shown on the card (mirrors services/bounties.py). */
  criteria: string;
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

/** Modes that can't host a Bounty Board (the bracket logs films itself; the Rabbit Hole has lives). */
export const NO_BOUNTY_MODES = new Set(["march_madness", "rabbit_hole"]);

export const WILDCARD_REWARD = "+1 🎟️ Wildcard";

/** The bounty a logged step completed, if any. */
export function completedBountyOf(step: RunStep): BountyInfo | null {
  const id = (step.transition_metadata as { completed_bounty?: BountyId } | null)?.completed_bounty;
  return id ? (BOUNTIES[id] ?? null) : null;
}
