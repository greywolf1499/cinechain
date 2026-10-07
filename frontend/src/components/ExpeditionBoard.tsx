import { useState } from "react";
import { Check } from "lucide-react";
import CanonBadge from "./CanonBadge";
import LogFilmButtons from "./LogFilmButtons";
import MarathonProgressBar from "./MarathonProgressBar";
import MoviePoster from "./MoviePoster";
import { QueuedFilm } from "./CareerTrack";
import { checklistProgress, formatRuntime } from "../lib/auteurTrack";
import CountryFlags from "./CountryFlags";
import { cn } from "../lib/cn";
import { sliceLabel } from "../lib/expedition";
import { useLogFilm } from "../lib/useLogFilm";
import type { RunDetail } from "../types/api";

/** The expedition checklist: every canon film in the slice, in list order, with its badge,
 * country flag and rank; watched titles are ticked off. */
export default function ExpeditionBoard({ run }: { run: RunDetail }) {
  const expedition = run.rules_config.expedition;
  const locked = run.status !== "active";
  const logFilm = useLogFilm(run.id);
  const pendingId = logFilm.pendingMovieId;
  const [message, setMessage] = useState<string | null>(null);
  if (!expedition) return null;

  const progress = checklistProgress(expedition.movie_ids, run.steps);
  const stepFor = (movieId: number) => run.steps.find((s) => s.movie_id === movieId);
  const flag = expedition.country ? <CountryFlags codes={[expedition.country]} /> : "🧭";


  return (
    <section aria-label="Expedition board" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-zinc-100">
          {flag} Expedition Board: {sliceLabel(expedition)} on {expedition.list_title}
        </h2>
        <p className="text-xs text-zinc-500">Watch them in any order</p>
      </div>
      <MarathonProgressBar progress={progress} barClassName="bg-lime-400" />
      {message && (
        <p role="alert" className="rounded-md border border-amber-900/50 bg-amber-950/20 px-3 py-2 text-xs text-amber-300">
          {message}
        </p>
      )}
      <ul className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {expedition.films.map((film) => {
          const step = stepFor(film.movie_id);
          const state = step ? (step.status === "watched" ? "watched" : "planned") : "todo";
          const runtime = formatRuntime(film.runtime);
          return (
            <li
              key={film.movie_id}
              className={cn(
                "flex items-center gap-3 rounded-xl border p-2.5",
                state === "watched" && "border-emerald-700/50 bg-emerald-950/20",
                state === "planned" && "border-sky-800/50 bg-sky-950/20",
                state === "todo" && "border-app-border bg-app-bg/60",
              )}
            >
              <MoviePoster movieId={film.movie_id} detailOptions={{ runId: run.id, step }} path={film.poster_path} title={film.title} className="w-12 shrink-0" />
              <div className="flex min-w-0 flex-1 flex-col gap-1">
                <p className="truncate text-sm font-medium text-zinc-100">{film.title}</p>
                <div className="flex flex-wrap items-center gap-1.5">
                  <CanonBadge
                    badge={{ badge_label: film.badge_label, badge_color: expedition.badge_color }}
                  />
                  {expedition.country && (
                    <span title={expedition.country_name ?? expedition.country} className="text-sm">
                      {flag}
                    </span>
                  )}
                  {film.rank !== null && (
                    <span className="text-[11px] text-zinc-500">Rank #{film.rank}</span>
                  )}
                </div>
                <p className="text-[11px] text-zinc-500">
                  {[film.year, runtime].filter(Boolean).join(" · ")}
                </p>
              </div>
              {state === "todo" ? (
                !locked && (
                  <LogFilmButtons
                    busy={pendingId === film.movie_id}
                    disabled={pendingId !== null}
                    onQueue={() => void logFilm.queue(film.movie_id).catch((error: Error) => setMessage(error.message))}
                    onWatch={() => void logFilm.logWatched(film.movie_id).catch((error: Error) => setMessage(error.message))}
                  />
                )
              ) : state === "watched" ? (
                <span className="flex shrink-0 items-center gap-1 text-[11px] font-medium text-emerald-300">
                  <Check className="h-3.5 w-3.5" /> Watched
                </span>
              ) : (
                <QueuedFilm
                  run={run}
                  movieId={film.movie_id}
                  locked={locked}
                  disabled={pendingId !== null}
                  onError={setMessage}
                />
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
