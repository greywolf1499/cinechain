import type { ActiveChaos } from "../types/api";

/** "Time Machine: Pre-1970 only" -> { title: "Time Machine", rule: "Pre-1970 only" }. */
export function chaosParts(chaos: ActiveChaos): { title: string; rule: string } {
  const [title, ...rest] = chaos.label.split(":");
  return rest.length > 0 ? { title: title.trim(), rule: rest.join(":").trim() } : { title: "", rule: chaos.label };
}
