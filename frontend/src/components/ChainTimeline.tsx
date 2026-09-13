import { useEffect, useState } from "react";
import MoviePoster from "./MoviePoster";
import ChainLink from "./ChainLink";
import MarkWatchedModal from "./MarkWatchedModal";
import MovieDetailModal from "./MovieDetailModal";
import { cn } from "../lib/cn";
import { isoToFlagEmoji, parseOriginCountries } from "../lib/countries";
import type { ActorClickPayload } from "./actorClickTypes";
import type { RunStep } from "../types/api";

type Order = "story" | "latest";
const ORDER_STORAGE_KEY = "cinechain:timeline-order";

type Row = { kind: "station"; step: RunStep } | { kind: "connector"; step: RunStep };

/** Each step's own transition_metadata always describes the connector INTO it
 * from the chronologically-previous step, regardless of display order - so
 * the connector renders on whichever side faces that earlier neighbor. */
function buildRows(orderedSteps: RunStep[], order: Order): Row[] {
  const rows: Row[] = [];
  orderedSteps.forEach((step, index) => {
    const isFirstChronological =
      order === "story" ? index === 0 : index === orderedSteps.length - 1;
    if (order === "story") {
      if (!isFirstChronological) rows.push({ kind: "connector", step });
      rows.push({ kind: "station", step });
    } else {
      rows.push({ kind: "station", step });
      if (!isFirstChronological) rows.push({ kind: "connector", step });
    }
  });
  return rows;
}

function isKeystoneConnector(step: RunStep, keystoneActorIds: Set<number>): boolean {
  const meta = step.transition_metadata as { actor_id?: number } | null;
  return !!meta?.actor_id && keystoneActorIds.has(meta.actor_id);
}

export default function ChainTimeline({
  runId,
  steps,
  keystoneActorIds,
  onActorClick,
  onRequestDeleteStep,
}: {
  runId: string;
  steps: RunStep[];
  keystoneActorIds: Set<number>;
  onActorClick: (actor: ActorClickPayload) => void;
  onRequestDeleteStep: (stepId: string) => void;
}) {
  const [order, setOrder] = useState<Order>(() => {
    if (typeof window === "undefined") return "story";
    return (localStorage.getItem(ORDER_STORAGE_KEY) as Order | null) ?? "story";
  });
  const [selectedStep, setSelectedStep] = useState<RunStep | null>(null);
  const [markWatchedStep, setMarkWatchedStep] = useState<RunStep | null>(null);

  useEffect(() => {
    localStorage.setItem(ORDER_STORAGE_KEY, order);
  }, [order]);

  if (steps.length === 0) return null;

  const orderedSteps = order === "story" ? steps : [...steps].reverse();
  const tailStepId = steps[steps.length - 1].id;
  const rows = buildRows(orderedSteps, order);

  return (
    <div>
      <div className="mb-4 inline-flex rounded-full border border-app-border bg-app-surface p-1 text-xs font-medium">
        <button
          type="button"
          onClick={() => setOrder("story")}
          className={cn(
            "rounded-full px-3 py-1.5 transition-colors",
            order === "story" ? "bg-accent text-zinc-950" : "text-zinc-400 hover:text-zinc-200",
          )}
        >
          Story Order (1 → N)
        </button>
        <button
          type="button"
          onClick={() => setOrder("latest")}
          className={cn(
            "rounded-full px-3 py-1.5 transition-colors",
            order === "latest" ? "bg-accent text-zinc-950" : "text-zinc-400 hover:text-zinc-200",
          )}
        >
          Latest First (N → 1)
        </button>
      </div>

      <div className="relative flex flex-col">
        <div className="pointer-events-none absolute inset-y-2 left-4 w-px bg-app-border" aria-hidden />
        {rows.map((row) =>
          row.kind === "station" ? (
            <StationRow
              key={`station-${row.step.id}`}
              step={row.step}
              onOpen={() => setSelectedStep(row.step)}
            />
          ) : (
            <ChainLink
              key={`connector-${row.step.id}`}
              step={row.step}
              isKeystone={isKeystoneConnector(row.step, keystoneActorIds)}
            />
          ),
        )}
      </div>

      {selectedStep && (
        <MovieDetailModal
          open={!!selectedStep}
          onClose={() => setSelectedStep(null)}
          runId={runId}
          step={selectedStep}
          isTailStep={selectedStep.id === tailStepId}
          onActorClick={onActorClick}
          onRequestMarkWatched={() => {
            setMarkWatchedStep(selectedStep);
            setSelectedStep(null);
          }}
          onRequestDelete={() => {
            onRequestDeleteStep(selectedStep.id);
            setSelectedStep(null);
          }}
        />
      )}

      {markWatchedStep && (
        <MarkWatchedModal
          open={!!markWatchedStep}
          onClose={() => setMarkWatchedStep(null)}
          runId={runId}
          stepId={markWatchedStep.id}
          movieTitle={markWatchedStep.movie_title}
        />
      )}
    </div>
  );
}

function StationRow({ step, onOpen }: { step: RunStep; onOpen: () => void }) {
  const decade = step.movie_release_year ? Math.floor(step.movie_release_year / 10) * 10 : null;
  const countries = parseOriginCountries(step.movie_origin_country);

  return (
    <div className="relative flex items-start gap-3 py-1.5">
      <div className="flex w-8 shrink-0 items-center justify-center pt-3">
        <span
          className={cn(
            "h-3 w-3 rounded-full border-2 border-app-bg",
            step.status === "watched" ? "bg-accent" : "bg-zinc-600",
          )}
        />
      </div>

      <button
        type="button"
        onClick={onOpen}
        className={cn(
          "flex min-w-0 flex-1 items-start gap-3 rounded-lg border border-app-border bg-app-surface p-2.5 text-left transition-colors hover:border-accent/50",
          step.status === "planned" && "border-dashed",
        )}
      >
        <div className="relative w-16 shrink-0">
          <MoviePoster
            path={step.movie_poster_path}
            title={step.movie_title}
            className={cn("w-16", step.status === "planned" && "opacity-60")}
          />
          {step.status === "planned" && (
            <span className="absolute left-1 top-1 rounded-full bg-accent px-1.5 py-0.5 text-[9px] font-semibold text-zinc-950">
              Up Next
            </span>
          )}
        </div>

        <div className="min-w-0 flex-1">
          <p className="line-clamp-2 text-sm font-medium text-zinc-100">{step.movie_title}</p>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-zinc-500">
            <span>{step.movie_release_year ?? "—"}</span>
            {decade !== null && (
              <span className="rounded-full bg-app-surface-hover px-1.5 py-0.5 text-zinc-400">
                {decade}s
              </span>
            )}
            {countries.map((country) => (
              <span key={country} className="rounded-full bg-app-surface-hover px-1.5 py-0.5">
                {isoToFlagEmoji(country)} {country}
              </span>
            ))}
            <span
              className={cn(
                "rounded-full px-1.5 py-0.5 font-medium",
                step.status === "watched"
                  ? "bg-emerald-950 text-emerald-400"
                  : "bg-app-surface-hover text-zinc-500",
              )}
            >
              {step.status === "watched" ? "Watched" : "Planned"}
            </span>
          </div>

          {step.status === "watched" && step.watched_at && (
            <p className="mt-1 text-[10px] text-zinc-600">
              Watched {new Date(step.watched_at).toLocaleDateString()}
            </p>
          )}
          {step.user_notes && (
            <p className="mt-1 line-clamp-2 text-[10px] italic text-zinc-500">“{step.user_notes}”</p>
          )}

          <RuleFlags meta={step.transition_metadata} />
        </div>
      </button>
    </div>
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

