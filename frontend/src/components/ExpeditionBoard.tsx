import { useState } from "react";
import { Check } from "lucide-react";
import CanonBadge from "./CanonBadge";
import LogFilmButtons from "./LogFilmButtons";
import MarathonProgressBar from "./MarathonProgressBar";
import MoviePoster from "./MoviePoster";
import { QueuedFilm } from "./CareerTrack";
import { api, ApiError } from "../lib/api";
import { checklistProgress, formatRuntime } from "../lib/auteurTrack";
import CountryFlags from "./CountryFlags";
import { cn } from "../lib/cn";
import { sliceLabel } from "../lib/expedition";
import { useCreateStep } from "../lib/queries";
import type { ExpeditionFilm, RunDetail, ValidationResult } from "../types/api";

/** The expedition checklist: every canon film in the slice, in list order, with its badge,
 * country flag and rank; watched titles are ticked off. */
export default function ExpeditionBoard({ run }: { run: RunDetail }) {
  const expedition = run.rules_config.expedition;
  const locked = run.status !== "active";
  const createStep = useCreateStep(run.id);
  const [pendingId, setPendingId] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [skipPick, setSkipPick] = useState<{ film: ExpeditionFilm; keys: string[] } | null>(null);
  if (!expedition) return null;

  const progress = checklistProgress(expedition.movie_ids, run.steps);
  const stepFor = (movieId: number) => run.steps.find((s) => s.movie_id === movieId);
  const flag = expedition.country ? <CountryFlags codes={[expedition.country]} /> : "🧭";

  async function log(film: ExpeditionFilm, watchedNow: boolean, skips: string[] = []) {
    setPendingId(film.movie_id);
    setMessage(null);
    try {
      if (run.rules_config.modifiers?.length && !skips.length) {
        const validation = await api.post<ValidationResult>(`/runs/${run.id}/validate`, { movie_id: film.movie_id });
        if (!validation.valid) {
          if (watchedNow && validation.overlay_skippable?.length && !validation.blocked) {
            setSkipPick({ film, keys: validation.overlay_skippable });
          }
          setMessage(validation.reason);
          return;
        }
      }
      await createStep.mutateAsync({
        movie_id: film.movie_id,
        status: watchedNow ? "watched" : "planned",
        watched_at: watchedNow ? new Date().toISOString() : null,
        force: skips.length > 0,
        ...(skips.length ? { skip_overlays: skips } : {}),
      });
      setSkipPick(null);
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : "Could not log that film.");
    } finally {
      setPendingId(null);
    }
  }

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
      {skipPick && (
        <div className="flex flex-wrap items-center gap-2 rounded border border-amber-800/50 p-3 text-xs text-amber-300">
          <span>Log {skipPick.film.title} as a substitute for the unreachable requirement?</span>
          <button type="button" disabled={pendingId !== null}
            onClick={() => void log(skipPick.film, true, skipPick.keys)}
            className="rounded border border-amber-500/50 px-3 py-2">
            Spend {skipPick.keys.length} wildcard{skipPick.keys.length === 1 ? "" : "s"} & log watched
          </button>
          <button type="button" onClick={() => setSkipPick(null)} className="px-3 py-2">Cancel</button>
        </div>
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
              <MoviePoster path={film.poster_path} title={film.title} className="w-12 shrink-0" />
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
                    onQueue={() => void log(film, false)}
                    onWatch={() => void log(film, true)}
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
