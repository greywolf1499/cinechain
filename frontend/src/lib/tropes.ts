/** "time-loop" -> "Time Loop". */
export function tropeLabel(trope: string): string {
  return trope
    .split("-")
    .filter(Boolean)
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(" ");
}

/** The discrete trope a Semantic Trope Web hop was linked by, if any. */
export function sharedTropeOf(meta: Record<string, unknown> | null | undefined): string | null {
  const value = meta?.shared_trope;
  return typeof value === "string" && value ? value : null;
}
