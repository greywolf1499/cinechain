import { Check, Loader2, Shuffle, X } from "lucide-react";
import MoviePoster from "./MoviePoster";
import ClampedLabel from "./ui/ClampedLabel";
import OnServerBadge, { onServerCardClass } from "./OnServerBadge";
import { cn } from "../lib/cn";
import type { JellyfinItemSummary, SwapCandidate, SwapMode } from "../types/api";

export type SwapTabState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; candidates: SwapCandidate[]; total: number };

/** One open swap: which path film, which tab is showing, and each tab's lookup. */
export interface SwapState {
  index: number;
  tab: SwapMode;
  same: SwapTabState;
  broad: SwapTabState;
}

function readyTotal(tab: SwapTabState): number | null {
  return tab.status === "ready" ? tab.total : null;
}

const TABS: { mode: SwapMode; label: string }[] = [
  { mode: "same", label: "👥 Same Actors" },
  { mode: "broad", label: "🔀 Broad Detour" },
];

/** Alternatives for one path film: "Same Actors" keeps both of its actor links, "Broad Detour"
 * reconnects its neighbours through any other cast members. */
export default function BridgeSwapPanel({
  movieTitle,
  actorNames,
  state,
  onServerMap,
  applying,
  onPick,
  onTabChange,
  onClose,
}: {
  movieTitle: string;
  actorNames: [string, string];
  state: SwapState;
  onServerMap?: Record<number, JellyfinItemSummary>;
  applying: boolean;
  onPick: (candidate: SwapCandidate) => void;
  onTabChange: (mode: SwapMode) => void;
  onClose: () => void;
}) {
  const tab = state[state.tab];
  return (
    <section
      aria-label="Swap movie"
      className="mt-4 rounded-lg border border-accent/30 bg-accent/5 p-4"
    >
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 text-sm font-medium text-zinc-200">
            <Shuffle className="h-4 w-4 text-accent" />
            Swap{" "}
            <ClampedLabel
              text={movieTitle}
              lines={1}
              as="span"
              className="max-w-40 text-accent"
            />
          </p>
          <p className="mt-0.5 text-xs text-zinc-500">
            {state.tab === "same"
              ? `Other films starring both ${actorNames[0]} and ${actorNames[1]} - the rest of the path stays put.`
              : "Films that share at least one actor with the film before and one with the film after - even if they're completely different actors."}
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

      <div role="tablist" aria-label="Swap type" className="mb-3 inline-flex rounded-full border border-app-border bg-app-surface p-1 text-xs font-medium">
        {TABS.map(({ mode, label }) => (
          <button
            key={mode}
            type="button"
            role="tab"
            aria-selected={state.tab === mode}
            onClick={() => onTabChange(mode)}
            className={cn(
              "rounded-full px-3 py-1.5 transition-colors",
              state.tab === mode ? "bg-accent text-zinc-950" : "text-zinc-400 hover:text-zinc-200",
            )}
          >
            {label}
            {readyTotal(state[mode]) !== null && (
              <span className="ml-1.5 opacity-70">{readyTotal(state[mode])}</span>
            )}
          </button>
        ))}
      </div>

      {tab.status === "loading" && (
        <p className="flex items-center gap-2 text-sm text-zinc-400">
          <Loader2 className="h-4 w-4 animate-spin text-accent" />
          {state.tab === "same"
            ? "Finding films with the same two actors..."
            : "Searching the casts of both neighbours for a detour..."}
        </p>
      )}

      {tab.status === "error" && <p className="text-sm text-red-400">{tab.message}</p>}

      {tab.status === "ready" &&
        (tab.candidates.length === 0 ? (
          <p className="text-sm text-zinc-400">
            {state.tab === "same"
              ? `No other film stars both ${actorNames[0]} and ${actorNames[1]}.`
              : "No film in the cache links these two neighbours through different cast."}
          </p>
        ) : (
          <>
            <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {tab.candidates.map((candidate) => {
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
                      <ClampedLabel
                        text={candidate.node.title}
                        lines={2}
                        as="p"
                        className="text-sm font-medium leading-tight text-zinc-100"
                      />
                      <p className="text-xs text-zinc-500">{candidate.node.release_year ?? "—"}</p>
                      {state.tab === "broad" && (
                        <p
                          className="truncate text-[10px] text-accent"
                          title={`${candidate.connection_in.actor_name} / ${candidate.connection_out.actor_name}`}
                        >
                          {candidate.connection_in.actor_name} → {candidate.connection_out.actor_name}
                        </p>
                      )}
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
            {tab.total > tab.candidates.length && (
              <p className="mt-2 text-xs text-zinc-500">
                Showing the {tab.candidates.length} most popular of {tab.total} matches.
              </p>
            )}
          </>
        ))}
    </section>
  );
}
