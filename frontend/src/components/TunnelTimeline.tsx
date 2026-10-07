import { useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Loader2, Swords, Trash2 } from "lucide-react";
import MoviePoster from "./MoviePoster";
import ClampedLabel from "./ui/ClampedLabel";
import PitchButton from "./PitchButton";
import { cn } from "../lib/cn";
import { useTunnelHint, useTunnelState } from "../lib/queries";
import { SIDE_LABELS, splitSides } from "../lib/tunnel";
import type { RunStep, TunnelSide, TunnelState } from "../types/api";

const SIDE_STYLES: Record<TunnelSide, { text: string; ring: string; chip: string }> = {
  head: {
    text: "text-sky-300",
    ring: "border-sky-400/40",
    chip: "bg-sky-500/10 text-sky-300",
  },
  tail: {
    text: "text-orange-300",
    ring: "border-orange-400/40",
    chip: "bg-orange-500/10 text-orange-300",
  },
};

/** Meet in the Middle: the chain as two converging tracks - Partner A's steps extend from the
 * left, Partner B's from the right - with the distance between the open ends in the trench. */
export default function TunnelTimeline({
  runId,
  steps,
  locked,
  onRequestDeleteStep,
}: {
  runId: string;
  steps: RunStep[];
  locked: boolean;
  onRequestDeleteStep: (stepId: string) => void;
}) {
  const { head, tail } = splitSides(steps);
  const lastStepId = steps[steps.length - 1]?.id;
  const tunnel = useTunnelState(runId);
  const titles = new Map(steps.map((step) => [step.movie_id, step.movie_title]));

  return (
    <div className="rounded-xl border border-app-border bg-app-surface p-4">
      <div className="grid grid-cols-1 items-stretch gap-4 lg:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)]">
        <Track side="head" steps={head} titles={titles} lastStepId={lastStepId} locked={locked} onRemove={onRequestDeleteStep} />
        <Trench runId={runId} state={tunnel.data} loading={tunnel.isFetching} locked={locked} />
        <Track side="tail" steps={tail} titles={titles} lastStepId={lastStepId} locked={locked} onRemove={onRequestDeleteStep} />
      </div>
    </div>
  );
}

function Track({
  side,
  steps,
  titles,
  lastStepId,
  locked,
  onRemove,
}: {
  side: TunnelSide;
  steps: RunStep[];
  titles: Map<number, string>;
  lastStepId: string | undefined;
  locked: boolean;
  onRemove: (stepId: string) => void;
}) {
  const style = SIDE_STYLES[side];
  // Partner B's track reads right-to-left: the seed sits at the far right, newest by the trench.
  const ordered = side === "tail" ? [...steps].reverse() : steps;
  const Arrow = side === "tail" ? ChevronLeft : ChevronRight;

  return (
    <section aria-label={`${SIDE_LABELS[side]}'s track`} className="min-w-0">
      <p className={cn("mb-2 text-xs font-semibold uppercase tracking-wide", style.text, side === "tail" && "lg:text-right")}>
        {SIDE_LABELS[side]}
        <span className="ml-1.5 font-normal normal-case text-zinc-500">
          {steps.length} film{steps.length === 1 ? "" : "s"}
        </span>
      </p>
      <div className={cn("flex items-stretch gap-1.5 overflow-x-auto pb-2", side === "tail" && "lg:flex-row")}>
        {ordered.map((step, index) => {
          const chronologicalIndex = side === "tail" ? steps.length - 1 - index : index;
          const previous = chronologicalIndex > 0 ? steps[chronologicalIndex - 1] : null;
          return (
            <div key={step.id} className="flex items-center gap-1.5">
              {index > 0 && <Arrow className="h-4 w-4 shrink-0 text-zinc-600" aria-hidden />}
              <TunnelCard
                step={step}
                previous={previous}
                side={side}
                titles={titles}
                isSeed={chronologicalIndex === 0}
                removable={!locked && step.id === lastStepId && chronologicalIndex > 0}
                onRemove={() => onRemove(step.id)}
              />
            </div>
          );
        })}
      </div>
    </section>
  );
}

function TunnelCard({
  step,
  previous,
  side,
  titles,
  isSeed,
  removable,
  onRemove,
}: {
  step: RunStep;
  previous: RunStep | null;
  side: TunnelSide;
  titles: Map<number, string>;
  isSeed: boolean;
  removable: boolean;
  onRemove: () => void;
}) {
  const style = SIDE_STYLES[side];
  const meta = step.transition_metadata;
  const collided = !!meta?.collision;
  const via = typeof meta?.actor_name === "string" ? meta.actor_name : null;
  const nearMissId = typeof meta?.near_miss_with === "number" ? meta.near_miss_with : null;

  return (
    <div
      className={cn(
        "flex w-28 shrink-0 flex-col gap-1.5 rounded-lg border bg-app-bg p-2",
        collided ? "border-fuchsia-400 shadow-[0_0_24px_-8px] shadow-fuchsia-500/60" : style.ring,
      )}
    >
      <MoviePoster path={step.movie_poster_path} title={step.movie_title} className="w-full" />
      <div className="min-w-0">
        <ClampedLabel
          text={step.movie_title}
          lines={2}
          as="p"
          className="text-[11px] font-medium leading-tight text-zinc-100"
        />
        <p className="text-[10px] text-zinc-500">{step.movie_release_year ?? "—"}</p>
      </div>
      <div className="flex flex-wrap items-center gap-1">
        {isSeed && (
          <span className={cn("rounded-full px-1.5 py-0.5 text-[9px] font-semibold", style.chip)}>Seed</span>
        )}
        {step.status === "planned" && (
          <span className="rounded-full bg-app-surface-hover px-1.5 py-0.5 text-[9px] text-zinc-400">Up next</span>
        )}
        {collided && (
          <span className="rounded-full bg-fuchsia-500/20 px-1.5 py-0.5 text-[9px] font-semibold text-fuchsia-300">
            💥 Collision
          </span>
        )}
        {nearMissId !== null && (
          <span
            title={`Almost connected to ${titles.get(nearMissId) ?? `film ${nearMissId}`}`}
            aria-label={`Near miss with ${titles.get(nearMissId) ?? `film ${nearMissId}`}`}
            className="rounded-full bg-amber-500/15 px-1.5 py-0.5 text-[9px] font-semibold text-amber-300"
          >
            💫 Near miss
          </span>
        )}
      </div>
      {previous && (
        <div className="flex flex-col gap-1 border-t border-dashed border-app-border pt-1.5">
          {via && <p className="truncate text-[10px] text-zinc-400" title={`Linked via ${via}`}>via {via}</p>}
          <PitchButton previousMovieId={previous.movie_id} candidateMovieId={step.movie_id} linkLabel={via} />
        </div>
      )}
      {removable && (
        <button
          type="button"
          onClick={onRemove}
          title="Remove this step"
          className="flex items-center justify-center gap-1 rounded-md border border-app-border py-1 text-[10px] text-zinc-500 transition-colors hover:bg-app-surface-hover hover:text-red-300"
        >
          <Trash2 className="h-3 w-3" />
          Undo
        </button>
      )}
    </div>
  );
}

function Trench({
  runId,
  state,
  loading,
  locked,
}: {
  runId: string;
  state: TunnelState | undefined;
  loading: boolean;
  locked: boolean;
}) {
  const hint = useTunnelHint(runId);
  const previousDistance = useRef<number | null>(null);
  const previousRunId = useRef(runId);
  const [trend, setTrend] = useState<"warmer" | "colder" | null>(null);
  useEffect(() => {
    if (previousRunId.current !== runId) {
      previousRunId.current = runId;
      previousDistance.current = null;
      setTrend(null);
    }
    const current = state?.distance_hops;
    if (current == null) return;
    if (previousDistance.current !== null && previousDistance.current !== current) {
      setTrend(current < previousDistance.current ? "warmer" : "colder");
    }
    previousDistance.current = current;
  }, [runId, state?.distance_hops]);

  let headline: React.ReactNode;
  let detail: string | null = null;

  if (state?.collided) {
    headline = <span className="text-fuchsia-300">💥 The chains collided!</span>;
  } else if (!state) {
    headline = (
      <span className="flex items-center justify-center gap-1.5 text-zinc-400">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        Measuring the gap...
      </span>
    );
  } else if (state.distance_hops === null) {
    headline = state.searched_depth > 0 ? (
      <span className="text-amber-300">Distance: ≥ {state.searched_depth} hops</span>
    ) : (
      <span className="text-amber-300">Distance: unknown</span>
    );
    detail = state.searched_depth > 0
      ? `Still searching deeper next time. ${state.message ?? ""}`.trim()
      : state.message ?? "No route found within the search limit.";
  } else {
    headline = (
      <span className="text-zinc-100">
        Distance: ~{state.distance_hops} hop{state.distance_hops === 1 ? "" : "s"} remaining
      </span>
    );
    detail =
      state.distance_hops <= 1
        ? "The two open ends already share cast - extend one with the other's film to collide."
        : state.distance_hops === 2
          ? "A single film connects both ends. Find it and the chains collide."
          : "Extend either side to close the gap.";
  }

  return (
    <div
      role="status"
      aria-label="Distance between the two ends"
      className="flex min-w-44 flex-col items-center justify-center gap-1.5 rounded-lg border border-dashed border-app-border bg-app-bg/70 px-3 py-4 text-center text-xs lg:max-w-52"
    >
      <Swords className="h-4 w-4 text-zinc-600" aria-hidden />
      <p className="flex items-center justify-center gap-1.5 font-semibold">
        {headline}
        {loading && state && (
          <Loader2 className="h-3 w-3 animate-spin text-zinc-500" aria-label="Updating distance" />
        )}
      </p>
      {trend && (
        <p className={cn("font-semibold", trend === "warmer" ? "text-emerald-300" : "text-sky-300")}>
          {trend === "warmer" ? "▲ Warmer" : "▼ Colder"}
        </p>
      )}
      {detail && <p className="text-[11px] leading-snug text-zinc-500">{detail}</p>}
      {state && !locked && !state.collided && (
        <>
          <p className="mt-1 text-[10px] text-zinc-500">{state.hints_remaining} hint tokens left</p>
          <div className="flex flex-wrap justify-center gap-1.5">
            {(["head", "tail"] as const).map((side) => (
              <div key={side} className="flex gap-1">
                <button
                  type="button"
                  disabled={hint.isPending || state.hints_remaining < 1}
                  onClick={() => hint.mutate({ side, level: "actor" })}
                  className="rounded-md border border-app-border px-2 py-1 text-[10px] text-zinc-300 hover:bg-app-surface-hover disabled:opacity-50"
                  title={`Reveal the next connecting actor from ${SIDE_LABELS[side]}'s side (1 token)`}
                >
                  💡 {side === "head" ? "A" : "B"} actor · 1
                </button>
                <button
                  type="button"
                  disabled={hint.isPending || state.hints_remaining < 2}
                  onClick={() => hint.mutate({ side, level: "film" })}
                  className="rounded-md border border-app-border px-2 py-1 text-[10px] text-zinc-300 hover:bg-app-surface-hover disabled:opacity-50"
                  title={`Reveal the next film from ${SIDE_LABELS[side]}'s side (2 tokens)`}
                >
                  {side === "head" ? "A" : "B"} film · 2
                </button>
              </div>
            ))}
          </div>
          {hint.data && (
            <p role="status" className="text-[11px] text-amber-200">
              {SIDE_LABELS[hint.variables?.side ?? "head"]}:{" "}
              {hint.data.actor?.actor_name ?? hint.data.film?.title ?? "Hint found"}
            </p>
          )}
          {hint.isError && (
            <p role="alert" className="text-[10px] text-red-300">
              {hint.error instanceof Error ? hint.error.message : "Couldn't get a hint."}
            </p>
          )}
        </>
      )}
    </div>
  );
}
