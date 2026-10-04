/** Bonus evidence the server stamps on a hop's `transition_metadata`. */
export interface GoldenReunion {
  director: string;
  actor: string;
}

export function goldenReunionOf(meta: Record<string, unknown> | null | undefined): GoldenReunion | null {
  const value = meta?.golden_reunion as Partial<GoldenReunion> | undefined;
  return value && typeof value.director === "string" && typeof value.actor === "string"
    ? { director: value.director, actor: value.actor }
    : null;
}

export function characterHopOf(meta: Record<string, unknown> | null | undefined): string | null {
  const value = meta?.character_hop;
  return typeof value === "string" && value ? value : null;
}
