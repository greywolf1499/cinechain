import type { ConnectionKind, CraftRole } from "../types/api";

interface LinkLike {
  kind?: ConnectionKind;
  actor_id: number;
  actor_name: string;
  profile_path?: string | null;
  /** Crew & Craft Trail: what the person was on each film (candidate / destination side). */
  role_in_frontier?: CraftRole | null;
  role_in_candidate?: CraftRole | null;
  role_in_from?: CraftRole | null;
  role_in_to?: CraftRole | null;
}

/** `transition_metadata` for a hop. Director links are tagged so Auteur Relay can
 * alternate; actor links keep the original shape (the server stamps the rest). */
export function connectionMetadata(
  connection: LinkLike,
  characters: { from?: string | null; to?: string | null } = {},
): Record<string, unknown> {
  if (connection.kind === "craft") {
    // The server rebuilds these from its own validation; they just say which shared person and
    // role was picked when several connect the films.
    return {
      person_id: connection.actor_id,
      person_name: connection.actor_name,
      role: connection.role_in_candidate ?? connection.role_in_to ?? null,
      from_role: connection.role_in_frontier ?? connection.role_in_from ?? null,
      profile_path: connection.profile_path ?? null,
      character_in_from: characters.from ?? null,
      character_in_to: characters.to ?? null,
    };
  }
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
