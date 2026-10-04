export const DRAMA_GENRE_ID = 18;
export const CHASER_TRIGGER_RUNTIME = 135;

/** A heavy film (135+ minutes, or a drama) earns a palate cleanser (mirrors services/pool_options.py). */
export function needsChaser(runtime: number | null | undefined, genreIds: number[] | null | undefined): boolean {
  return (runtime ?? 0) >= CHASER_TRIGGER_RUNTIME || (genreIds ?? []).includes(DRAMA_GENRE_ID);
}
