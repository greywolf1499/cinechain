import { useState } from "react";
import { Check, Loader2 } from "lucide-react";
import MoviePoster from "./MoviePoster";
import { ApiError } from "../lib/api";
import { cn } from "../lib/cn";
import { MILESTONES, decadeOf, trackStatuses } from "../lib/careerTrack";
import { useCreateStep } from "../lib/queries";
import type { CareerFilm, CareerMilestone, RunDetail } from "../types/api";

/** The milestone pill: `[ 🐣 Debut ]`. */
export function MilestoneBadge({ milestone }: { milestone: CareerMilestone }) {
  const info = MILESTONES[milestone];
  return (
    <span
      className={cn(
        "rounded-full px-2 py-0.5 text-[10px] font-semibold ring-1 ring-inset",
        info.className,
      )}
    >
      [ {info.emoji} {info.label} ]
    </span>
  );
}

/** The actor's career, oldest film first: age and decade at each film, milestone badges and the
 * log buttons that walk the marathon forward. */
export default function CareerTrack({ run }: { run: RunDetail }) {
  const track = (run.rules_config.filmography ?? []) as CareerFilm[];
  const actorName = run.rules_config.actor?.name ?? "the actor";
  const locked = run.status !== "active";
  const createStep = useCreateStep(run.id);
  const [pendingId, setPendingId] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const { statuses } = trackStatuses(track, run.steps);
  const watched = [...statuses.values()].filter((s) => s === "watched").length;

  async function log(film: CareerFilm, watchedNow: boolean) {
    setPendingId(film.movie_id);
    setMessage(null);
    try {
      await createStep.mutateAsync({
        movie_id: film.movie_id,
        status: watchedNow ? "watched" : "planned",
        watched_at: watchedNow ? new Date().toISOString() : null,
        force: false,
      });
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : "Could not log that film.");
    } finally {
      setPendingId(null);
    }
  }

  return (
    <section aria-label="Career progression track" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-zinc-100">
          🎭 {actorName}: Career Progression Track
        </h2>
        <p className="text-xs text-zinc-500">
          {watched} of {track.length} watched · skip at most {run.rules_config.max_skip ?? 2} between films
        </p>
      </div>
      {message && (
        <p role="alert" className="rounded-md border border-amber-900/50 bg-amber-950/20 px-3 py-2 text-xs text-amber-300">
          {message}
        </p>
      )}
      <ol className="relative flex flex-col gap-3 border-l border-app-border pl-5">
        {track.map((film, index) => {
          const state = statuses.get(film.movie_id) ?? "upcoming";
          const previous = track[index - 1];
          const newDecade = !previous || decadeOf(previous.year) !== decadeOf(film.year);
          return (
            <li key={film.movie_id} className="flex flex-col gap-1.5">
              {newDecade && (
                <p className="-ml-5 mt-1 text-[10px] font-bold uppercase tracking-widest text-zinc-600">
                  {decadeOf(film.year)}
                </p>
              )}
              <div
                className={cn(
                  "relative flex items-center gap-3 rounded-xl border p-2.5",
                  state === "watched" && "border-emerald-700/50 bg-emerald-950/20",
                  state === "planned" && "border-sky-800/50 bg-sky-950/20",
                  state === "next" && "border-accent/70 bg-app-surface shadow-[0_0_0_1px_rgba(251,191,36,0.25)]",
                  state === "upcoming" && "border-app-border bg-app-bg/60",
                )}
              >
                <span
                  aria-hidden
                  className={cn(
                    "absolute -left-[1.62rem] top-1/2 h-2.5 w-2.5 -translate-y-1/2 rounded-full ring-2 ring-app-bg",
                    state === "watched" ? "bg-emerald-400" : state === "next" ? "bg-accent" : "bg-zinc-700",
                  )}
                />
                <MoviePoster path={film.poster_path} title={film.title} className="w-12 shrink-0" />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <p className="truncate text-sm font-medium text-zinc-100">{film.title}</p>
                    {film.milestones.map((milestone) => (
                      <MilestoneBadge key={milestone} milestone={milestone} />
                    ))}
                  </div>
                  <p className="text-[11px] text-zinc-500">
                    {film.year} · {decadeOf(film.year)}
                    {film.age !== null && ` · age ${film.age}`}
                    {film.character ? ` · as ${film.character}` : ""}
                  </p>
                </div>
                {state === "watched" || state === "planned" ? (
                  <span
                    className={cn(
                      "flex shrink-0 items-center gap-1 text-[11px] font-medium",
                      state === "watched" ? "text-emerald-300" : "text-sky-300",
                    )}
                  >
                    <Check className="h-3.5 w-3.5" /> {state === "watched" ? "Watched" : "Queued"}
                  </span>
                ) : (
                  !locked && (
                    <div className="flex shrink-0 gap-1.5">
                      <button
                        type="button"
                        disabled={pendingId !== null}
                        onClick={() => void log(film, false)}
                        className="rounded-md border border-app-border px-2.5 py-1.5 text-[11px] font-medium text-zinc-300 hover:bg-app-surface-hover disabled:opacity-50"
                      >
                        Queue
                      </button>
                      <button
                        type="button"
                        disabled={pendingId !== null}
                        onClick={() => void log(film, true)}
                        className="flex items-center gap-1 rounded-md bg-accent px-2.5 py-1.5 text-[11px] font-semibold text-zinc-950 hover:bg-accent-strong disabled:opacity-50"
                      >
                        {pendingId === film.movie_id && <Loader2 className="h-3 w-3 animate-spin" />}
                        Log Watched
                      </button>
                    </div>
                  )
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
