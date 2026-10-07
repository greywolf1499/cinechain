import type { CareerContext, CareerMilestone, RulesConfig, RunStep } from "../types/api";

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
  first_theatrical: { emoji: "🎬", label: "First theatrical", className: "bg-sky-500/15 text-sky-200 ring-sky-400/40" },
  first_lead: { emoji: "⭐", label: "First lead", className: "bg-amber-400/15 text-amber-200 ring-amber-300/50" },
  genre_pivot: { emoji: "🔀", label: "Genre pivot", className: "bg-violet-500/15 text-violet-200 ring-violet-400/40" },
  against_type: { emoji: "🎭", label: "Against type", className: "bg-fuchsia-500/15 text-fuchsia-200 ring-fuchsia-400/40" },
  comeback: { emoji: "🔁", label: "Comeback", className: "bg-emerald-500/15 text-emerald-200 ring-emerald-400/40" },
  language_crossover: { emoji: "🌐", label: "Crossover", className: "bg-teal-500/15 text-teal-200 ring-teal-400/40" },
};

type ContextFilm = CareerContext & { movie_id: number; title: string; year: number };

export function careerGroup(track: ContextFilm[], index: number, rules: RulesConfig) {
  const film = track[index];
  const annotation = rules.career_eras?.find((era) => {
    const start = track.findIndex((entry) => entry.movie_id === era.start_movie_id);
    const end = track.findIndex((entry) => entry.movie_id === era.end_movie_id);
    return start >= 0 && start <= index && index <= end;
  });
  if (annotation) return { key: `player:${annotation.start_movie_id}`, label: annotation.label, annotation };
  if (film.era_index !== undefined && film.era_label) {
    return { key: `era:${film.era_index}`, label: film.era_label, annotation: undefined };
  }
  return { key: `decade:${decadeOf(film.year)}`, label: decadeOf(film.year), annotation: undefined };
}

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
      if (step.status === "watched") furthest = Math.max(furthest, index);
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
