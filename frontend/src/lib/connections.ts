import type { ConnectionKind } from "../types/api";

interface LinkLike {
  kind?: ConnectionKind;
  actor_id: number;
  actor_name: string;
  profile_path?: string | null;
}

/** `transition_metadata` for a hop. Director links are tagged so Auteur Relay can
 * alternate; actor links keep the original shape (the server stamps the rest). */
export function connectionMetadata(
  connection: LinkLike,
  characters: { from?: string | null; to?: string | null } = {},
): Record<string, unknown> {
  if (connection.kind === "director") {
    return {
      connection_type: "director",
      director_id: connection.actor_id,
      director_name: connection.actor_name,
    };
  }
  return {
    actor_id: connection.actor_id,
    actor_name: connection.actor_name,
    profile_path: connection.profile_path ?? null,
    character_in_from: characters.from ?? null,
    character_in_to: characters.to ?? null,
  };
}
