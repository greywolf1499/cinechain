import { Check, Loader2, Shuffle, X } from "lucide-react";
import MoviePoster from "./MoviePoster";
import OnServerBadge, { onServerCardClass } from "./OnServerBadge";
import { cn } from "../lib/cn";
import type { JellyfinItemSummary, SwapCandidate } from "../types/api";

export type SwapState =
  | { index: number; status: "loading" }
  | { index: number; status: "error"; message: string }
  | { index: number; status: "ready"; candidates: SwapCandidate[]; total: number };

/** Alternatives for one path film that keep both of its actor links (the "Same-Actor Swap"). */
export default function BridgeSwapPanel({
  movieTitle,
  actorNames,
  state,
  onServerMap,
  applying,
  onPick,
  onClose,
}: {
  movieTitle: string;
  actorNames: [string, string];
  state: SwapState;
  onServerMap?: Record<number, JellyfinItemSummary>;
  applying: boolean;
  onPick: (candidate: SwapCandidate) => void;
  onClose: () => void;
}) {
  return (
    <section
      aria-label="Swap movie"
      className="mt-4 rounded-lg border border-accent/30 bg-accent/5 p-4"
    >
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 text-sm font-medium text-zinc-200">
            <Shuffle className="h-4 w-4 text-accent" />
            Swap <span className="truncate text-accent">{movieTitle}</span>
          </p>
          <p className="mt-0.5 text-xs text-zinc-500">
            Other films starring both {actorNames[0]} and {actorNames[1]} - the rest of the path stays put.
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close swap panel"
          className="shrink-0 rounded-md p-1 text-zinc-500 transition-colors hover:bg-app-surface-hover hover:text-zinc-200"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {state.status === "loading" && (
        <p className="flex items-center gap-2 text-sm text-zinc-400">
          <Loader2 className="h-4 w-4 animate-spin text-accent" />
          Finding films with the same two actors...
        </p>
      )}

      {state.status === "error" && <p className="text-sm text-red-400">{state.message}</p>}

      {state.status === "ready" &&
        (state.candidates.length === 0 ? (
          <p className="text-sm text-zinc-400">
            No other film stars both {actorNames[0]} and {actorNames[1]}.
          </p>
        ) : (
          <>
            <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {state.candidates.map((candidate) => {
                const onServer = onServerMap?.[candidate.node.movie_id]?.on_server;
                return (
                  <li
                    key={candidate.node.movie_id}
                    className={cn(
                      "flex items-center gap-3 rounded-lg border p-2",
                      onServer ? onServerCardClass(true) : "border-app-border bg-app-surface",
                    )}
                  >
                    <MoviePoster
                      path={candidate.node.poster_path}
                      title={candidate.node.title}
                      className="w-12 shrink-0"
                    />
                    <div className="min-w-0 flex-1">
                      <p className="line-clamp-2 text-sm font-medium leading-tight text-zinc-100">
                        {candidate.node.title}
                      </p>
                      <p className="text-xs text-zinc-500">{candidate.node.release_year ?? "—"}</p>
                      <OnServerBadge onServer={onServer} />
                    </div>
                    <button
                      type="button"
                      disabled={applying}
                      onClick={() => onPick(candidate)}
                      className="flex shrink-0 items-center gap-1 rounded-md bg-accent px-2.5 py-1.5 text-xs font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:opacity-60"
                    >
                      <Check className="h-3.5 w-3.5" />
                      Use
                    </button>
                  </li>
                );
              })}
            </ul>
            {state.total > state.candidates.length && (
              <p className="mt-2 text-xs text-zinc-500">
                Showing the {state.candidates.length} most popular of {state.total} matches.
              </p>
            )}
          </>
        ))}
    </section>
  );
}
