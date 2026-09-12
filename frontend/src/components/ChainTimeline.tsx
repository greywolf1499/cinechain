import { useState } from "react";
import { Check } from "lucide-react";
import MoviePoster from "./MoviePoster";
import ChainLink from "./ChainLink";
import MovieCastStrip from "./MovieCastStrip";
import MarkWatchedModal from "./MarkWatchedModal";
import { cn } from "../lib/cn";
import type { ActorClickPayload } from "./actorClickTypes";
import type { RunStep } from "../types/api";

export default function ChainTimeline({
  runId,
  steps,
  onActorClick,
}: {
  runId: string;
  steps: RunStep[];
  onActorClick: (actor: ActorClickPayload) => void;
}) {
  const [expandedStepId, setExpandedStepId] = useState<string | null>(null);
  const [markWatchedStep, setMarkWatchedStep] = useState<RunStep | null>(null);

  if (steps.length === 0) return null;

  return (
    <>
      <div className="flex items-start gap-1 overflow-x-auto pb-3">
        {steps.map((step, index) => (
          <div key={step.id} className="flex items-stretch gap-1">
            {index > 0 && <ChainLink step={step} />}
            <div
              className={cn(
                "w-32 shrink-0 rounded-md",
                step.status === "planned" && "border border-dashed border-accent/40 bg-accent/5 p-1.5",
              )}
            >
              <div className="relative">
                <button
                  type="button"
                  onClick={() => setExpandedStepId(expandedStepId === step.id ? null : step.id)}
                  className="block w-full"
                >
                  <MoviePoster
                    path={step.movie_poster_path}
                    title={step.movie_title}
                    className={cn(step.status === "planned" && "opacity-60")}
                  />
                </button>
                {step.status === "planned" && (
                  <span className="absolute left-1 top-1 rounded-full bg-accent px-1.5 py-0.5 text-[9px] font-semibold text-zinc-950">
                    Up Next
                  </span>
                )}
              </div>

              <p className="mt-1.5 line-clamp-2 text-xs font-medium text-zinc-200">
                {step.movie_title}
              </p>
              <p className="text-[11px] text-zinc-500">{step.movie_release_year ?? "—"}</p>

              {step.status === "watched" && step.watched_at && (
                <p className="text-[10px] text-zinc-600">
                  Watched {new Date(step.watched_at).toLocaleDateString()}
                </p>
              )}
              {step.user_notes && (
                <p className="mt-1 line-clamp-2 text-[10px] italic text-zinc-500">
                  “{step.user_notes}”
                </p>
              )}

              <RuleFlags meta={step.transition_metadata} />

              {step.status === "planned" && (
                <button
                  type="button"
                  onClick={() => setMarkWatchedStep(step)}
                  className="mt-1.5 flex w-full items-center justify-center gap-1 rounded-md bg-accent/10 px-2 py-1 text-[10px] font-medium text-accent transition-colors hover:bg-accent/20"
                >
                  <Check className="h-3 w-3" />
                  Mark as Watched
                </button>
              )}

              {expandedStepId === step.id && (
                <MovieCastStrip movieId={step.movie_id} onActorClick={onActorClick} />
              )}
            </div>
          </div>
        ))}
      </div>

      {markWatchedStep && (
        <MarkWatchedModal
          open={!!markWatchedStep}
          onClose={() => setMarkWatchedStep(null)}
          runId={runId}
          stepId={markWatchedStep.id}
          movieTitle={markWatchedStep.movie_title}
        />
      )}
    </>
  );
}

function RuleFlags({ meta }: { meta: Record<string, unknown> | null }) {
  if (!meta) return null;
  const flags: string[] = [];
  if (meta.wildcard_used) flags.push("Wildcard");
  if (meta.repeat_penalty) flags.push("Repeat");
  if (meta.runtime_flagged) flags.push("Short runtime");
  if (flags.length === 0) return null;

  return (
    <div className="mt-1 flex flex-wrap gap-1">
      {flags.map((flag) => (
        <span
          key={flag}
          className="rounded-full bg-amber-950 px-1.5 py-0.5 text-[9px] font-medium text-amber-400"
        >
          ⚡ {flag}
        </span>
      ))}
    </div>
  );
}
