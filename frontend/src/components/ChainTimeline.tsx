import { useState } from "react";
import MoviePoster from "./MoviePoster";
import ChainLink from "./ChainLink";
import MovieCastStrip from "./MovieCastStrip";
import type { ActorClickPayload } from "./actorClickTypes";
import type { RunStep } from "../types/api";

export default function ChainTimeline({
  steps,
  onActorClick,
}: {
  steps: RunStep[];
  onActorClick: (actor: ActorClickPayload) => void;
}) {
  const [expandedStepId, setExpandedStepId] = useState<string | null>(null);

  if (steps.length === 0) return null;

  return (
    <div className="flex items-start gap-1 overflow-x-auto pb-3">
      {steps.map((step, index) => (
        <div key={step.id} className="flex items-stretch gap-1">
          {index > 0 && <ChainLink step={step} />}
          <div className="w-32 shrink-0">
            <button
              type="button"
              onClick={() => setExpandedStepId(expandedStepId === step.id ? null : step.id)}
              className="block w-full"
            >
              <MoviePoster path={step.movie_poster_path} title={step.movie_title} />
            </button>
            <p className="mt-1.5 line-clamp-2 text-xs font-medium text-zinc-200">
              {step.movie_title}
            </p>
            <p className="text-[11px] text-zinc-500">{step.movie_release_year ?? "—"}</p>
            {expandedStepId === step.id && (
              <MovieCastStrip movieId={step.movie_id} onActorClick={onActorClick} />
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
