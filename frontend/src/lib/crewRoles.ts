import type { CraftRole, DiscoveryCandidate, DiscoveryConnection, RunStep } from "../types/api";

/** How each craft is shown on a link: emoji, label and a tinted pill. Classes are spelled out in
 * full so Tailwind keeps them. */
export const ROLE_STYLES: Record<CraftRole, { emoji: string; label: string; className: string }> = {
  composer: { emoji: "🎼", label: "Composer", className: "bg-violet-500/15 text-violet-300" },
  cinematographer: { emoji: "🎥", label: "Cinematographer", className: "bg-sky-500/15 text-sky-300" },
  writer: { emoji: "✍️", label: "Screenplay", className: "bg-emerald-500/15 text-emerald-300" },
  director: { emoji: "🎬", label: "Director", className: "bg-orange-500/15 text-orange-300" },
  actor: { emoji: "👥", label: "Actor", className: "bg-amber-500/15 text-amber-300" },
};

export function isCraftRole(value: unknown): value is CraftRole {
  return typeof value === "string" && value in ROLE_STYLES;
}

/** The text of a role badge: "🎼 Composer: Hans Zimmer", or "🎬 Director → 👥 Actor: Jordan Peele"
 * when the person held a different role on each film. */
export function roleBadgeText(
  role: CraftRole,
  name: string,
  fromRole?: CraftRole | null,
): string {
  const to = ROLE_STYLES[role];
  if (fromRole && fromRole !== role) {
    const from = ROLE_STYLES[fromRole];
    return `${from.emoji} ${from.label} → ${to.emoji} ${to.label}: ${name}`;
  }
  return `${to.emoji} ${to.label}: ${name}`;
}

/** The craft link recorded on a step (`person_*` metadata), if it has one. */
export function stepCraftLink(
  step: RunStep,
): { role: CraftRole; fromRole: CraftRole | null; name: string; personId: number | null } | null {
  const meta = step.transition_metadata as Record<string, unknown> | null;
  if (!meta || !isCraftRole(meta.role) || typeof meta.person_name !== "string") return null;
  return {
    role: meta.role,
    fromRole: isCraftRole(meta.from_role) ? meta.from_role : null,
    name: meta.person_name,
    personId: typeof meta.person_id === "number" ? meta.person_id : null,
  };
}

export function connectionRole(connection: DiscoveryConnection): CraftRole | null {
  return connection.kind === "craft" && isCraftRole(connection.role_in_candidate)
    ? connection.role_in_candidate
    : null;
}

export function hasCraftLinks(candidate: DiscoveryCandidate): boolean {
  return candidate.connections.some((c) => c.kind === "craft");
}
