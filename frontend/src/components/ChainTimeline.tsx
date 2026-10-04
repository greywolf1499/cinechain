import { useEffect, useState } from "react";
import { Check, Loader2 } from "lucide-react";
import MoviePoster from "./MoviePoster";
import ChainLink from "./ChainLink";
import ColorSwatch from "./ColorSwatch";
import MarkWatchedModal from "./MarkWatchedModal";
import MovieDetailModal from "./MovieDetailModal";
import { CanonBadgeList } from "./CanonBadge";
import { cn } from "../lib/cn";
import { isoToFlagEmoji, parseOriginCountries } from "../lib/countries";
import { useCanonBadgesBulk, useUpdateStep } from "../lib/queries";
import type { ActorClickPayload } from "./actorClickTypes";
import type { CanonBadge, RunStep } from "../types/api";

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
  locked = false,
  castLinked = true,
}: {
  runId: string;
  steps: RunStep[];
  keystoneActorIds: Set<number>;
  onActorClick: (actor: ActorClickPayload) => void;
  onRequestDeleteStep: (stepId: string) => void;
  /** Finished runs (completed/failed/forfeited) can't log or alter the chain. */
  locked?: boolean;
  /** False for standalone modes: links show the mode's rule evidence instead of a cast link. */
  castLinked?: boolean;
}) {
  const [order, setOrder] = useState<Order>(() => {
    if (typeof window === "undefined") return "story";
    return (localStorage.getItem(ORDER_STORAGE_KEY) as Order | null) ?? "story";
  });
  const [selectedStep, setSelectedStep] = useState<RunStep | null>(null);
  const [markWatchedStep, setMarkWatchedStep] = useState<RunStep | null>(null);
  const quickMarkWatched = useUpdateStep(runId);
  const { data: badgesMap } = useCanonBadgesBulk(steps.map((s) => s.movie_id));

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
              badges={badgesMap?.[String(row.step.movie_id)]}
              onOpen={() => setSelectedStep(row.step)}
              onQuickMarkWatched={() =>
                quickMarkWatched.mutate({ stepId: row.step.id, watched_at: new Date().toISOString() })
              }
              locked={locked}
              quickMarkWatchedPending={
                quickMarkWatched.isPending && quickMarkWatched.variables?.stepId === row.step.id
              }
            />
          ) : (
            <ChainLink
              key={`connector-${row.step.id}`}
              step={row.step}
              isKeystone={isKeystoneConnector(row.step, keystoneActorIds)}
              castLinked={castLinked}
              previousMovieId={steps[steps.findIndex((s) => s.id === row.step.id) - 1]?.movie_id}
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
          locked={locked}
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

function StationRow({
  step,
  badges,
  onOpen,
  onQuickMarkWatched,
  quickMarkWatchedPending,
  locked,
}: {
  step: RunStep;
  badges: CanonBadge[] | undefined;
  onOpen: () => void;
  onQuickMarkWatched: () => void;
  quickMarkWatchedPending: boolean;
  locked: boolean;
}) {
  const decade = step.movie_release_year ? Math.floor(step.movie_release_year / 10) * 10 : null;
  const countries = parseOriginCountries(step.movie_origin_country);
  const isPlanned = step.status === "planned";

  return (
    <div className="relative flex items-start gap-3 py-1.5">
      <div className="flex w-8 shrink-0 items-center justify-center pt-3">
        <span
          className={cn(
            "h-3 w-3 rounded-full border-2 border-app-bg",
            isPlanned ? "bg-zinc-600" : "bg-accent",
          )}
        />
      </div>

      <div
        className={cn(
          "flex min-w-0 flex-1 flex-col gap-2 rounded-lg border border-app-border bg-app-surface p-2.5",
          isPlanned && "border-dashed border-accent/40 bg-accent/5",
        )}
      >
        <button
          type="button"
          onClick={onOpen}
          className="flex min-w-0 items-start gap-3 text-left transition-opacity hover:opacity-90"
        >
          <div className="relative w-16 shrink-0">
            <MoviePoster
              path={step.movie_poster_path}
              title={step.movie_title}
              className={cn("w-16", isPlanned && "opacity-60")}
            />
            {step.movie_dominant_color && (
              <ColorSwatch color={step.movie_dominant_color} className="absolute -right-1.5 -top-1.5" />
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
              {isPlanned ? (
                <span className="rounded-full bg-accent/15 px-1.5 py-0.5 font-medium text-accent">
                  🎟️ Up Next
                </span>
              ) : (
                <span className="rounded-full bg-emerald-950 px-1.5 py-0.5 font-medium text-emerald-400">
                  {step.watched_at
                    ? `Watched on ${new Date(step.watched_at).toLocaleDateString()}`
                    : "Watched"}
                </span>
              )}
            </div>

            {step.user_notes && (
              <p className="mt-1 line-clamp-2 text-[10px] italic text-zinc-500">“{step.user_notes}”</p>
            )}

            <div className="mt-1">
              <CanonBadgeList badges={badges} />
            </div>

            <RuleFlags meta={step.transition_metadata} />
          </div>
        </button>

        {isPlanned && !locked && (
          <button
            type="button"
            onClick={onQuickMarkWatched}
            disabled={quickMarkWatchedPending}
            className="flex w-fit items-center gap-1.5 self-start rounded-md bg-accent/10 px-2.5 py-1 text-[11px] font-medium text-accent transition-colors hover:bg-accent/20 disabled:opacity-60"
          >
            {quickMarkWatchedPending ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Check className="h-3 w-3" />
            )}
            Mark as Watched
          </button>
        )}
      </div>
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

