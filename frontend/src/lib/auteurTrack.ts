import type { AuteurFilm, RunStep } from "../types/api";

export const AUTEUR_MARATHON = "auteur_marathon";

export interface MarathonProgress {
  watched: number;
  total: number;
  percent: number;
}

/** `X / Y Films Watched · Z% Complete`: only logged-as-watched steps count. */
export function checklistProgress(movieIds: number[], steps: RunStep[]): MarathonProgress {
  const done = new Set(steps.filter((s) => s.status === "watched").map((s) => s.movie_id));
  const watched = movieIds.filter((id) => done.has(id)).length;
  const total = movieIds.length;
  return { watched, total, percent: total === 0 ? 0 : Math.round((watched / total) * 100) };
}

export function progressLabel({ watched, total, percent }: MarathonProgress): string {
  return `${watched} / ${total} Films Watched · ${percent}% Complete`;
}

export function auteurProgress(filmography: AuteurFilm[], steps: RunStep[]): MarathonProgress {
  return checklistProgress(
    filmography.map((film) => film.movie_id),
    steps,
  );
}

export function formatRuntime(minutes: number | null | undefined): string | null {
  if (!minutes) return null;
  const hours = Math.floor(minutes / 60);
  return hours > 0 ? `${hours}h ${minutes % 60}m` : `${minutes}m`;
}
