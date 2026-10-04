import type { BridgeRoute } from "../types/api";

/** The films strictly between a route's endpoints. */
export function intermediateMovieIds(route: Pick<BridgeRoute, "path">): number[] {
  return route.path.slice(1, -1).map((node) => node.movie_id);
}

/** Every intermediate film already used by the routes shown: feeding these back as
 * `exclude_movie_ids` forces the solver into a corridor that avoids all of them. */
export function usedIntermediateIds(routes: Pick<BridgeRoute, "path">[]): number[] {
  return [...new Set(routes.flatMap(intermediateMovieIds))];
}

/** A "Scenic Detour": a route that deliberately takes a longer corridor than the shortest one
 * found, or the backend's own "Underdog / International Pick" (less mainstream films). */
export function isScenicRoute(route: Pick<BridgeRoute, "hops" | "label">, routes: Pick<BridgeRoute, "hops">[]): boolean {
  const shortest = routes.reduce((min, r) => Math.min(min, r.hops), Number.POSITIVE_INFINITY);
  return route.hops > shortest || /underdog/i.test(route.label);
}
