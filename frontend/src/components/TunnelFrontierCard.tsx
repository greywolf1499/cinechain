import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { GitBranch, Lock, Plus } from "lucide-react";
import MoviePoster from "./MoviePoster";
import PickNextHub from "./PickNextHub";
import { cn } from "../lib/cn";
import { MEET_IN_THE_MIDDLE, SIDE_LABELS, splitSides } from "../lib/tunnel";
import type { RulesConfig, RunStep, TunnelSide } from "../types/api";

const BUTTON_STYLES: Record<TunnelSide, string> = {
  head: "bg-sky-400 text-zinc-950 hover:bg-sky-300",
  tail: "bg-orange-400 text-zinc-950 hover:bg-orange-300",
};

/** Meet in the Middle's "Active Frontier": both open ends and one Pick Next button per side. */
export default function TunnelFrontierCard({
  runId,
  steps,
  rulesConfig,
  locked,
}: {
  runId: string;
  steps: RunStep[];
  rulesConfig: RulesConfig;
  locked: boolean;
}) {
  const navigate = useNavigate();
  const { head, tail } = splitSides(steps);
  const [hubSide, setHubSide] = useState<TunnelSide | null>(null);
  const frontiers: Record<TunnelSide, RunStep | undefined> = {
    head: head[head.length - 1],
    tail: tail[tail.length - 1],
  };
  const hubFrontier = hubSide ? frontiers[hubSide] : undefined;

  return (
    <div className="rounded-xl border border-app-border bg-app-surface p-4">
      <p className="mb-3 text-xs font-medium uppercase tracking-wide text-zinc-500">Open ends</p>

      <div className="flex flex-col gap-3">
        {(["head", "tail"] as const).map((side) => {
          const frontier = frontiers[side];
          return (
            <div key={side} className="flex items-center gap-3">
              {frontier ? (
                <MoviePoster path={frontier.movie_poster_path} title={frontier.movie_title} className="w-10 shrink-0" />
              ) : (
                <div className="aspect-[2/3] w-10 shrink-0 rounded-md bg-app-surface-hover" />
              )}
              <div className="min-w-0 flex-1">
                <p className="text-[10px] uppercase tracking-wide text-zinc-500">{SIDE_LABELS[side]}&apos;s end</p>
                <p className="truncate text-sm font-medium text-zinc-100">{frontier?.movie_title ?? "Not started"}</p>
              </div>
              {!locked && (
                <button
                  type="button"
                  disabled={!frontier}
                  onClick={() => setHubSide(side)}
                  className={cn(
                    "flex shrink-0 items-center gap-1.5 rounded-md px-3 py-2 text-xs font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50",
                    BUTTON_STYLES[side],
                  )}
                >
                  <Plus className="h-3.5 w-3.5" />
                  Extend {SIDE_LABELS[side]}&apos;s Side
                </button>
              )}
            </div>
          );
        })}
      </div>

      {locked ? (
        <div className="mt-4 flex items-center gap-2 rounded-md border border-app-border bg-app-bg px-3 py-2.5 text-sm text-zinc-500">
          <Lock className="h-4 w-4 shrink-0" />
          This run is over - logging is locked.
        </div>
      ) : (
        frontiers.head &&
        frontiers.tail && (
          <button
            type="button"
            onClick={() =>
              navigate(`/tools/bridge?from=${frontiers.head?.movie_id}&to=${frontiers.tail?.movie_id}`)
            }
            className="mt-4 flex w-full items-center justify-center gap-1.5 rounded-md border border-app-border px-3.5 py-2 text-xs font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover"
          >
            <GitBranch className="h-3.5 w-3.5" />
            Bridge Solver: find a route between the ends
          </button>
        )
      )}

      {!locked && hubSide && hubFrontier && (
        <PickNextHub
          // Remount per side so each end starts from a clean Pick Next stack.
          key={hubSide}
          open
          onClose={() => setHubSide(null)}
          runId={runId}
          frontierStep={hubFrontier}
          rulesConfig={rulesConfig}
          steps={steps}
          gameType={MEET_IN_THE_MIDDLE}
          tunnelSide={hubSide}
        />
      )}
    </div>
  );
}
