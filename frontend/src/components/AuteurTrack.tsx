import { useState } from "react";
import { Check } from "lucide-react";
import LogFilmButtons from "./LogFilmButtons";
import MarathonProgressBar from "./MarathonProgressBar";
import MoviePoster from "./MoviePoster";
import { ApiError } from "../lib/api";
import { auteurProgress, formatRuntime } from "../lib/auteurTrack";
import { trackStatuses } from "../lib/careerTrack";
import { cn } from "../lib/cn";
import { useCreateStep } from "../lib/queries";
import type { AuteurFilm, RunDetail } from "../types/api";

/** The director's filmography in release order: year, poster and runtime per film, a progress
 * bar, and the log buttons that walk the marathon forward. */
export default function AuteurTrack({ run }: { run: RunDetail }) {
  const films = (run.rules_config.filmography ?? []) as AuteurFilm[];
  const directorName = run.rules_config.director?.name ?? "the director";
  const locked = run.status !== "active";
  const createStep = useCreateStep(run.id);
  const [pendingId, setPendingId] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const { statuses } = trackStatuses(films, run.steps);
  const progress = auteurProgress(films, run.steps);

  async function log(film: AuteurFilm, watchedNow: boolean) {
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
    <section aria-label="Auteur track" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-zinc-100">🎬 {directorName}: Auteur Track</h2>
        <p className="text-xs text-zinc-500">
          Release order · skip at most {run.rules_config.max_skip ?? 1} between films
        </p>
      </div>
      <MarathonProgressBar progress={progress} barClassName="bg-teal-400" />
      {message && (
        <p role="alert" className="rounded-md border border-amber-900/50 bg-amber-950/20 px-3 py-2 text-xs text-amber-300">
          {message}
        </p>
      )}
      <ol className="relative flex flex-col gap-3 border-l border-app-border pl-5">
        {films.map((film) => {
          const state = statuses.get(film.movie_id) ?? "upcoming";
          const runtime = formatRuntime(film.runtime);
          return (
            <li
              key={film.movie_id}
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
              <span className="w-11 shrink-0 text-center text-sm font-bold tabular-nums text-zinc-400">
                {film.year}
              </span>
              <MoviePoster path={film.poster_path} title={film.title} className="w-12 shrink-0" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-zinc-100">{film.title}</p>
                <p className="text-[11px] text-zinc-500">{runtime ?? "Runtime unknown"}</p>
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
                  <LogFilmButtons
                    busy={pendingId === film.movie_id}
                    disabled={pendingId !== null}
                    onQueue={() => void log(film, false)}
                    onWatch={() => void log(film, true)}
                  />
                )
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
