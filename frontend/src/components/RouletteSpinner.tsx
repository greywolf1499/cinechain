import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, Clock, Dices, Loader2, Star, Ticket } from "lucide-react";
import MoviePoster from "./MoviePoster";
import { ApiError, api } from "../lib/api";
import { cn } from "../lib/cn";
import { useCreateStep } from "../lib/queries";
import type { GenreOut, RouletteMovie, RouletteSpinResult } from "../types/api";

const SPIN_MIN_MS = 1400; // the suspense is the point - never reveal instantly

const MAX_RUNTIME_OPTIONS = [90, 105, 120, 150];
const MIN_RATING_OPTIONS = [6, 7, 7.5, 8];

type Phase = "idle" | "spinning" | "revealed";

const selectClass =
  "w-full rounded-md border border-app-border bg-app-bg px-2.5 py-1.5 text-xs text-zinc-200 focus:border-accent focus:outline-none";

/** Movie Night Roulette: filter, spin, watch the pick come into focus, then log it. */
export default function RouletteSpinner({ runId }: { runId: string }) {
  const createStep = useCreateStep(runId);
  const { data: genres } = useQuery({
    queryKey: ["movies", "genres"],
    queryFn: () => api.get<GenreOut[]>("/movies/genres"),
  });

  const [maxRuntime, setMaxRuntime] = useState("");
  const [minRating, setMinRating] = useState("");
  const [genre, setGenre] = useState("");

  const [phase, setPhase] = useState<Phase>("idle");
  const [pick, setPick] = useState<RouletteMovie | null>(null);
  const [poolSize, setPoolSize] = useState(0);
  const [focused, setFocused] = useState(false); // drives the blur -> sharp reveal transition
  const [message, setMessage] = useState<string | null>(null);
  const spinToken = useRef(0);

  // Flip to "focused" one frame after the card mounts so the CSS transition runs.
  useEffect(() => {
    if (phase !== "revealed") return;
    const timer = window.setTimeout(() => setFocused(true), 60);
    return () => window.clearTimeout(timer);
  }, [phase, pick]);

  async function spin() {
    const token = ++spinToken.current;
    setPhase("spinning");
    setFocused(false);
    setMessage(null);

    const params = new URLSearchParams({ run_id: runId });
    if (maxRuntime) params.set("max_runtime", maxRuntime);
    if (minRating) params.set("min_rating", minRating);
    if (genre) params.set("genre", genre);

    const delay = new Promise((resolve) => window.setTimeout(resolve, SPIN_MIN_MS));
    try {
      const [result] = await Promise.all([
        api.get<RouletteSpinResult>(`/engine/roulette/spin?${params.toString()}`),
        delay,
      ]);
      if (token !== spinToken.current) return;
      setPick(result.movie);
      setPoolSize(result.pool_size);
      setPhase("revealed");
    } catch (err) {
      await delay;
      if (token !== spinToken.current) return;
      setPick(null);
      setPhase("idle");
      setMessage(err instanceof ApiError ? err.message : "The wheel jammed - try again.");
    }
  }

  async function log(watched: boolean) {
    if (!pick) return;
    setMessage(null);
    try {
      await createStep.mutateAsync({
        movie_id: pick.tmdb_id,
        status: watched ? "watched" : "planned",
        watched_at: watched ? new Date().toISOString() : null,
      });
      setPick(null);
      setPhase("idle");
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : "Couldn't log this film.");
    }
  }

  const spinning = phase === "spinning";

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-3 gap-2">
        <label className="flex flex-col gap-1 text-[11px] font-medium text-zinc-500">
          Max runtime
          <select value={maxRuntime} onChange={(e) => setMaxRuntime(e.target.value)} className={selectClass}>
            <option value="">Any</option>
            {MAX_RUNTIME_OPTIONS.map((m) => (
              <option key={m} value={m}>
                &le; {m} min
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[11px] font-medium text-zinc-500">
          Min IMDb
          <select value={minRating} onChange={(e) => setMinRating(e.target.value)} className={selectClass}>
            <option value="">Any</option>
            {MIN_RATING_OPTIONS.map((r) => (
              <option key={r} value={r}>
                &ge; {r}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[11px] font-medium text-zinc-500">
          Genre
          <select value={genre} onChange={(e) => setGenre(e.target.value)} className={selectClass}>
            <option value="">Any</option>
            {genres?.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      <button
        type="button"
        onClick={spin}
        disabled={spinning}
        className="flex items-center justify-center gap-2 rounded-md bg-accent px-3.5 py-2.5 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:opacity-60"
      >
        <Dices className={cn("h-4 w-4", spinning && "animate-spin")} />
        {phase === "revealed" ? "Spin Again" : spinning ? "Spinning..." : "Spin Roulette"}
      </button>

      {spinning && (
        <div
          role="status"
          className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-accent/40 bg-accent/5 py-8"
        >
          <div className="h-24 w-16 animate-pulse rounded-md bg-app-surface-hover blur-sm" />
          <p className="text-xs font-medium text-accent">The reel is spinning...</p>
        </div>
      )}

      {phase === "revealed" && pick && (
        <div
          className={cn(
            "rounded-lg border border-accent/40 bg-app-bg p-3 transition-all duration-700 ease-out",
            focused ? "scale-100 rotate-0 opacity-100 blur-0" : "scale-75 -rotate-6 opacity-40 blur-xl",
          )}
        >
          <div className="flex gap-3">
            <MoviePoster path={pick.poster_path} title={pick.title} className="w-20 shrink-0" />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold leading-tight text-zinc-100">{pick.title}</p>
              <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs text-zinc-500">
                {pick.release_year && <span>{pick.release_year}</span>}
                {pick.runtime ? (
                  <span className="flex items-center gap-1">
                    <Clock className="h-3 w-3" />
                    {pick.runtime} min
                  </span>
                ) : null}
                {pick.imdb_rating && (
                  <span className="flex items-center gap-1 text-amber-400">
                    <Star className="h-3 w-3 fill-current" />
                    {pick.imdb_rating}
                  </span>
                )}
              </div>
              {(pick.tagline || pick.overview) && (
                <p className="mt-1.5 line-clamp-3 text-xs italic text-zinc-400">
                  {pick.tagline || pick.overview}
                </p>
              )}
            </div>
          </div>
          <p className="mt-2 text-[11px] text-zinc-600">
            1 of {poolSize} cached film{poolSize === 1 ? "" : "s"} matching your filters
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              disabled={!focused || createStep.isPending}
              onClick={() => log(true)}
              className="flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:opacity-60"
            >
              {createStep.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
              Log as Watched
            </button>
            <button
              type="button"
              disabled={!focused || createStep.isPending}
              onClick={() => log(false)}
              className="flex items-center gap-1.5 rounded-md border border-app-border px-3 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover disabled:opacity-60"
            >
              <Ticket className="h-3.5 w-3.5" />
              Plan for Later
            </button>
          </div>
        </div>
      )}

      {message && <p className="text-xs text-amber-400">{message}</p>}
    </div>
  );
}
