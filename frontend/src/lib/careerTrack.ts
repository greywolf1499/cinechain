import type { CareerMilestone, RulesConfig, RunStep } from "../types/api";

export const METHOD_ACTOR = "method_actor";

export function marathonPacing(rules: RulesConfig): string {
  if (rules.order === "free" || rules.max_skip === null) return "Free order · any unwatched on-track film";
  const skip = rules.order === "strict" ? 0 : rules.order === "relaxed" ? 2 : rules.max_skip ?? 2;
  return `Release order · skip at most ${skip} between films`;
}

export const MILESTONES: Record<CareerMilestone, { emoji: string; label: string; className: string }> = {
  debut: { emoji: "🐣", label: "Debut", className: "bg-sky-500/15 text-sky-200 ring-sky-400/40" },
  breakout: { emoji: "🚀", label: "Breakout", className: "bg-fuchsia-500/15 text-fuchsia-200 ring-fuchsia-400/40" },
  prestige_peak: { emoji: "🏆", label: "Prestige Peak", className: "bg-amber-400/15 text-amber-200 ring-amber-300/50" },
  modern_resurgence: { emoji: "👑", label: "Modern Resurgence", className: "bg-emerald-500/15 text-emerald-200 ring-emerald-400/40" },
};

export function decadeOf(year: number): string {
  return `${Math.floor(year / 10) * 10}s`;
}

export type TrackStatus = "watched" | "planned" | "next" | "upcoming";

/** Where each track film stands: logged (watched/planned), the next one in line, or still ahead. */
export function trackStatuses(
  track: { movie_id: number }[],
  steps: RunStep[],
): { statuses: Map<number, TrackStatus>; nextIndex: number } {
  const statuses = new Map<number, TrackStatus>();
  let furthest = -1;
  track.forEach((film, index) => {
    const step = steps.find((s) => s.movie_id === film.movie_id);
    if (step) {
      statuses.set(film.movie_id, step.status === "watched" ? "watched" : "planned");
      furthest = Math.max(furthest, index);
    }
  });
  const nextIndex = Math.min(furthest + 1, track.length - 1);
  track.forEach((film, index) => {
    if (!statuses.has(film.movie_id)) {
      statuses.set(film.movie_id, index === nextIndex && furthest < track.length - 1 ? "next" : "upcoming");
    }
  });
  return { statuses, nextIndex };
}
