import { useState } from "react";
import { api } from "../lib/api";
import { useLogFilm } from "../lib/useLogFilm";
import type { RunDetail, RunStep, SplitCandidate } from "../types/api";
import HouseholdRatingModal from "./HouseholdRatingModal";
import MoviePoster from "./MoviePoster";
import QueuedFilmActions from "./QueuedFilmActions";

export default function UpNextShelf({ run }: { run: RunDetail }) {
  const planned = run.steps.filter((step) => step.status === "planned");
  const locked = run.status !== "active";
  const logFilm = useLogFilm(run.id);
  const [rating, setRating] = useState<{ step: RunStep; film: SplitCandidate } | null>(null);
  const [retry, setRetry] = useState<RunStep | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  async function rate(step: RunStep) {
    setChecking(true);
    setError(null);
    setRetry(step);
    try {
      const film = await api.post<SplitCandidate | { qualifies: false; reason: string }>(
        `/runs/${run.id}/split/ratings/${step.movie_id}/retry`,
      );
      if ("qualifies" in film) {
        setError(film.reason);
      } else {
        setRetry(null);
        setRating({ step, film });
      }
    } catch (error) {
      setError(error instanceof Error ? error.message : "Could not load ratings.");
    } finally {
      setChecking(false);
    }
  }

  if (!planned.length) return null;
  return (
    <section aria-label="Up next" className="mb-5 min-w-0 rounded-xl border border-sky-900/60 bg-sky-950/10 p-3">
      <h2 className="mb-2 text-sm font-semibold text-sky-300">Up next · {planned.length}</h2>
      <ul className="flex gap-3 overflow-x-auto pb-2">
        {planned.map((step) => (
          <li key={step.id} className="flex w-64 max-w-full shrink-0 items-center gap-2 rounded-lg border border-app-border p-2">
            <MoviePoster path={step.movie_poster_path} title={step.movie_title} className="w-9 shrink-0" />
            <div className="min-w-0 flex-1">
              <p className="truncate text-xs font-medium text-zinc-100" title={step.movie_title}>{step.movie_title}</p>
              <span className="text-[10px] text-sky-300">Up next</span>
              {!locked && (run.game_type === "rt_split" ? (
                <div className="mt-1 flex flex-wrap gap-2 text-[11px]">
                  <button type="button" disabled={checking || logFilm.isPending} onClick={() => void rate(step)}
                    className="rounded bg-accent px-2 py-1.5 font-semibold text-zinc-950 disabled:opacity-50">Log watched</button>
                  <button type="button" disabled={checking || logFilm.isPending}
                    onClick={() => void logFilm.unqueue(step).catch((error: Error) => setError(error.message))}
                    className="rounded border border-app-border px-2 py-1.5 text-zinc-300 disabled:opacity-50">Unqueue</button>
                </div>
              ) : <QueuedFilmActions runId={run.id} stepId={step.id} onError={setError} />)}
            </div>
          </li>
        ))}
      </ul>
      {checking && <p role="status" className="text-xs text-zinc-400">Checking ratings...</p>}
      {error && <p role="alert" className="text-xs text-amber-300">{error}</p>}
      {retry && !checking && !locked && (
        <div className="mt-2 flex flex-wrap gap-3 text-xs">
          <button type="button" disabled={logFilm.isPending} onClick={() => void rate(retry)} className="text-accent underline">Retry ratings</button>
          <button type="button" disabled={logFilm.isPending} className="text-zinc-300 underline"
            onClick={() => void logFilm.markWatched(retry, { no_contest: true }).then(() => {
              setRetry(null); setError(null);
            }).catch((error: Error) => setError(error.message))}>Log watched · no-contest</button>
        </div>
      )}
      <HouseholdRatingModal key={rating?.step.id ?? "empty"} runId={run.id}
        step={rating?.step} film={rating?.film ?? null} onClose={() => setRating(null)}
        onRatingsFailed={(_film, reason) => {
          setRetry(rating?.step ?? null); setError(reason); setRating(null);
        }} />
    </section>
  );
}
