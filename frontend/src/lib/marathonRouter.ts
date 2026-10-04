import type { RouterResult, RouterTransition, WhiplashLabel } from "../types/api";

export const MAX_ROUTER_FILMS = 20;
export const MIN_ROUTER_FILMS = 4;
export const RANDOM_PICK_COUNT = 10;

/** Priority sliders (0-100). The defaults mirror the router's own 40 / 25 / 15 split. */
export interface PriorityValues {
  genre: number;
  decade: number;
  pacing: number;
}

export const DEFAULT_PRIORITIES: PriorityValues = { genre: 40, decade: 25, pacing: 15 };
/** Rating / mood has no slider: it keeps its default share (0.20 of the 1.00 total). */
const RATING_WEIGHT = 0.2;

export function priorityWeights(p: PriorityValues) {
  return {
    weight_genre: p.genre / 100,
    weight_year: p.decade / 100,
    weight_runtime: p.pacing / 100,
    weight_rating: RATING_WEIGHT,
  };
}

/** Fisher-Yates over a copy; `random` is injectable for tests. */
export function pickRandom<T>(items: readonly T[], count: number, random: () => number = Math.random): T[] {
  const pool = [...items];
  for (let i = pool.length - 1; i > 0; i--) {
    const j = Math.floor(random() * (i + 1));
    [pool[i], pool[j]] = [pool[j], pool[i]];
  }
  return pool.slice(0, count);
}

export function bannerText(result: RouterResult): string {
  const scores = `Initial Score: ${result.initial_whiplash_score.toFixed(1)} \u2500\u2500\u25ba Optimized: ${result.optimized_whiplash_score.toFixed(1)}`;
  if (result.improvement_percentage <= 0) {
    return `\u2728 Already as smooth as it gets - no reordering beats your list (${scores})`;
  }
  return `\u2728 Tonal Whiplash Reduced by ${Math.round(result.improvement_percentage)}%! (${scores})`;
}

export const TRANSITION_STYLES: Record<WhiplashLabel, { icon: string; chip: string }> = {
  "Smooth Transition": { icon: "\u{1F30A}", chip: "border-emerald-700/60 bg-emerald-950/40 text-emerald-300" },
  "Gentle Shift": { icon: "\u3030\uFE0F", chip: "border-sky-700/60 bg-sky-950/40 text-sky-300" },
  "Tonal Whiplash": { icon: "\u26A1", chip: "border-amber-600/60 bg-amber-950/40 text-amber-300" },
};

export function transitionText(t: RouterTransition): string {
  return `${TRANSITION_STYLES[t.label].icon} ${t.label}: ${t.summary}`;
}

export function defaultRunName(now: Date = new Date()): string {
  return `Smooth Marathon - ${now.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}`;
}
