import type { RulesConfig } from "../types/api";

export const GENRE_PENDULUM = "genre_pendulum";
export const DEFAULT_GENRE_CYCLE = ["Horror", "Thriller", "Crime", "Comedy"];
export const DEFAULT_SWING_FREQUENCY = 2;
export const MAX_CYCLE_LENGTH = 12;

/** TMDB's movie genres - the only names the backend accepts in a cycle. */
export const TMDB_GENRE_NAMES = [
  "Action",
  "Adventure",
  "Animation",
  "Comedy",
  "Crime",
  "Documentary",
  "Drama",
  "Family",
  "Fantasy",
  "History",
  "Horror",
  "Music",
  "Mystery",
  "Romance",
  "Science Fiction",
  "TV Movie",
  "Thriller",
  "War",
  "Western",
];

const ALIASES: Record<string, string> = {
  "sci-fi": "Science Fiction",
  scifi: "Science Fiction",
  "sci fi": "Science Fiction",
  "science-fiction": "Science Fiction",
  tv: "TV Movie",
  doc: "Documentary",
};

/** TMDB's spelling of a typed genre ("sci-fi" -> "Science Fiction"), or null if it isn't one. */
export function canonicalGenre(input: string): string | null {
  const key = input.trim().toLowerCase().replace(/\s+/g, " ");
  return TMDB_GENRE_NAMES.find((name) => name.toLowerCase() === key) ?? ALIASES[key] ?? null;
}

export interface PendulumState {
  target: string;
  nextTarget: string;
  /** 1-based step within the current swing. */
  position: number;
  frequency: number;
}

/** Where the pendulum is for the next film: the same state machine as the backend,
 * `cycle[(steps // frequency) % cycle.length]`. */
export function pendulumState(rules: RulesConfig, stepsLogged: number): PendulumState {
  const cycle = rules.genre_cycle?.length ? rules.genre_cycle : DEFAULT_GENRE_CYCLE;
  const frequency = rules.swing_frequency && rules.swing_frequency > 0 ? rules.swing_frequency : DEFAULT_SWING_FREQUENCY;
  const swing = Math.floor(stepsLogged / frequency);
  return {
    target: cycle[swing % cycle.length],
    nextTarget: cycle[(swing + 1) % cycle.length],
    position: (stepsLogged % frequency) + 1,
    frequency,
  };
}
