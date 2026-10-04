import type { RunStep } from "../types/api";

export const HISTORICAL_TIME_TRAVEL = "historical_time_travel";
export const MIN_NARRATIVE_YEAR = -10_000;
export const MAX_NARRATIVE_YEAR = 10_000;

/** "400 BC", "180 AD", "1300 AD" - and a bare "1945" once the year is modern. */
export function formatNarrativeYear(year: number): string {
  if (year < 0) return `${(-year).toLocaleString("en-US")} BC`;
  return year < 1500 ? `${year} AD` : String(year);
}

/** A pictogram for the era a story is set in. */
export function eraEmoji(year: number, label: string | null | undefined): string {
  if (year >= 2030) return "🚀";
  if (label === "Contemporary") return "🎬";
  if (year < 500) return "🏛️";
  if (year < 1600) return "⚔️";
  if (year < 1900) return "🎩";
  if (year >= 1914 && year <= 1953) return "💣";
  return "📅";
}

/** "🏛️ 180 AD · Ancient Rome" */
export function narrativeSettingText(year: number, label: string | null | undefined): string {
  return `${eraEmoji(year, label)} ${formatNarrativeYear(year)} · ${label || "Unlabelled era"}`;
}

/** "+1,120 years forward" / "−300 years backward" (a zero leap reads "same year"). */
export function leapText(delta: number): string {
  if (delta === 0) return "same year";
  const years = Math.abs(delta);
  const noun = years === 1 ? "year" : "years";
  return `${delta > 0 ? "+" : "−"}${years.toLocaleString("en-US")} ${noun} ${delta > 0 ? "forward" : "backward"}`;
}

/** The leap into `step` from the step before it, from the films' *current* setting years
 * (so a manual edit updates every hop), else from the delta recorded when it was logged. */
export function stepLeap(previous: RunStep | undefined, step: RunStep): number | null {
  if (
    previous?.movie_narrative_year != null &&
    step.movie_narrative_year != null
  ) {
    return step.movie_narrative_year - previous.movie_narrative_year;
  }
  const recorded = (step.transition_metadata as { narrative_delta?: unknown } | null)?.narrative_delta;
  return typeof recorded === "number" ? recorded : null;
}

/** Parses the modal's year field: "180", "-400", "400 BC", "1,945 AD". null = not a valid year. */
export function parseNarrativeYear(raw: string): number | null {
  const text = raw.trim().replace(/,/g, "");
  const match = text.match(/^(-?\d{1,5})\s*(bce?|ad|ce)?$/i);
  if (!match) return null;
  let year = parseInt(match[1], 10);
  if (match[2] && /^bce?$/i.test(match[2])) year = -Math.abs(year);
  return year >= MIN_NARRATIVE_YEAR && year <= MAX_NARRATIVE_YEAR ? year : null;
}
