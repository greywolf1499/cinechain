import { useEffect, useState } from "react";
import { Check, Clock, EyeOff, Loader2, Minus, Plus, Sparkles, ThumbsUp, Ticket } from "lucide-react";
import MoviePoster from "./MoviePoster";
import { cn } from "../lib/cn";
import type { RouletteMovie } from "../types/api";

// Posters and titles stay masked behind a heavy blur until a card is chosen.
const BLIND_MASK = "backdrop-blur-md filter blur-md";

function decadeLabel(year: number | null): string | null {
  return year ? `${Math.floor(year / 10) * 10}s` : null;
}

/** Movie Night Roulette's Blind Draft: three masked candidates, a vibe vote, then a sharp reveal.
 * Only runtime, decade, tagline and plot logline show until a card is chosen. */
export default function BlindDraft({
  movies,
  poolSize,
  logging,
  onLog,
}: {
  movies: RouletteMovie[];
  poolSize: number;
  logging: boolean;
  onLog: (movie: RouletteMovie, watched: boolean) => void;
}) {
  const [votes, setVotes] = useState<Record<number, number>>({});
  const [chosenId, setChosenId] = useState<number | null>(null);
  const [sharp, setSharp] = useState(false); // flips a frame after the choice so the unblur animates

  useEffect(() => {
    if (chosenId === null) return;
    const timer = window.setTimeout(() => setSharp(true), 60);
    return () => window.clearTimeout(timer);
  }, [chosenId]);

  function vote(movieId: number, delta: 1 | -1) {
    setVotes((current) => ({
      ...current,
      [movieId]: Math.max(0, (current[movieId] ?? 0) + delta),
    }));
  }

  function revealTopVibe() {
    const best = Math.max(...movies.map((m) => votes[m.tmdb_id] ?? 0));
    const leaders = movies.filter((m) => (votes[m.tmdb_id] ?? 0) === best);
    setChosenId(leaders[Math.floor(Math.random() * leaders.length)].tmdb_id);
  }

  const totalVotes = movies.reduce((sum, m) => sum + (votes[m.tmdb_id] ?? 0), 0);
  const chosen = movies.find((m) => m.tmdb_id === chosenId) ?? null;
  const topVotes = Math.max(...movies.map((m) => votes[m.tmdb_id] ?? 0));
  const tied = movies.filter((m) => (votes[m.tmdb_id] ?? 0) === topVotes).length > 1;

  return (
    <div className="flex flex-col gap-3">
      <p className="flex items-center gap-1.5 text-xs text-zinc-400">
        <EyeOff className="h-3.5 w-3.5 text-accent" />
        {chosen
          ? "The reveal"
          : "Blind Draft: vote on the vibe, then reveal. No peeking at posters or titles."}
      </p>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {movies.map((movie, index) => {
          const isChosen = movie.tmdb_id === chosenId;
          const masked = !isChosen || !sharp;
          const count = votes[movie.tmdb_id] ?? 0;
          const decade = decadeLabel(movie.release_year);
          const logline = movie.overview;
          return (
            <div
              key={movie.tmdb_id}
              className={cn(
                "flex flex-col gap-2 rounded-lg border bg-app-bg p-3 transition-all duration-700 ease-out",
                isChosen
                  ? "scale-[1.03] border-accent shadow-[0_0_28px_-6px] shadow-accent/60"
                  : "border-app-border",
                chosen && !isChosen && "scale-95 opacity-40",
              )}
            >
              <div className="relative overflow-hidden rounded-md">
                <MoviePoster
                  path={movie.poster_path}
                  title={masked ? "Hidden poster" : movie.title}
                  className={cn(
                    "w-full transition-all duration-700 ease-out",
                    masked ? BLIND_MASK : "scale-100 blur-0",
                    masked && "scale-110",
                  )}
                />
                <div
                  aria-hidden
                  className={cn(
                    "pointer-events-none absolute inset-0 flex items-center justify-center bg-zinc-950/40 transition-opacity duration-700",
                    masked ? "opacity-100 backdrop-blur-md" : "opacity-0",
                  )}
                >
                  <span className="text-3xl font-black text-zinc-200/80">?</span>
                </div>
              </div>

              <div className="min-w-0">
                <span className="sr-only">{masked ? `Mystery film ${index + 1}` : movie.title}</span>
                <p
                  aria-hidden
                  className={cn(
                    "select-none truncate text-sm font-semibold text-zinc-100 transition-all duration-700 ease-out",
                    masked && BLIND_MASK,
                  )}
                >
                  {movie.title}
                </p>
                <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-zinc-500">
                  {decade && <span>{decade}</span>}
                  {movie.runtime ? (
                    <span className="flex items-center gap-1">
                      <Clock className="h-3 w-3" />
                      {movie.runtime} min
                    </span>
                  ) : null}
                </div>
                {movie.tagline && (
                  <p className="mt-1.5 text-xs font-medium italic text-zinc-300">&ldquo;{movie.tagline}&rdquo;</p>
                )}
                {logline && <p className="mt-1 line-clamp-4 text-[11px] leading-relaxed text-zinc-500">{logline}</p>}
              </div>

              {!chosen && (
                <div className="mt-auto flex flex-col gap-1.5 pt-1">
                  <div className="flex items-center justify-between rounded-md border border-app-border px-1.5 py-1">
                    <button
                      type="button"
                      aria-label={`Remove a vote from film ${index + 1}`}
                      disabled={count === 0}
                      onClick={() => vote(movie.tmdb_id, -1)}
                      className="rounded p-1 text-zinc-400 hover:bg-app-surface-hover disabled:opacity-30"
                    >
                      <Minus className="h-3 w-3" />
                    </button>
                    <span className="flex items-center gap-1.5 text-xs tabular-nums text-zinc-200">
                      <ThumbsUp className="h-3 w-3 text-accent" />
                      {count} vibe{count === 1 ? "" : "s"}
                    </span>
                    <button
                      type="button"
                      aria-label={`Vote for film ${index + 1}`}
                      onClick={() => vote(movie.tmdb_id, 1)}
                      className="rounded p-1 text-accent hover:bg-app-surface-hover"
                    >
                      <Plus className="h-3 w-3" />
                    </button>
                  </div>
                  <button
                    type="button"
                    onClick={() => setChosenId(movie.tmdb_id)}
                    className="rounded-md border border-app-border px-2 py-1 text-[11px] font-medium text-zinc-300 transition-colors hover:border-accent hover:text-accent"
                  >
                    Choose this one
                  </button>
                </div>
              )}

              {isChosen && sharp && (
                <div className="mt-auto flex flex-col gap-1.5 pt-1">
                  <p className="flex items-center gap-1 text-xs font-semibold text-accent">
                    <Sparkles className="h-3.5 w-3.5" />
                    {movie.title}
                    {movie.release_year ? ` (${movie.release_year})` : ""}
                  </p>
                  <button
                    type="button"
                    disabled={logging}
                    onClick={() => onLog(movie, true)}
                    className="flex items-center justify-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:opacity-60"
                  >
                    {logging ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
                    Log as Watched
                  </button>
                  <button
                    type="button"
                    disabled={logging}
                    onClick={() => onLog(movie, false)}
                    className="flex items-center justify-center gap-1.5 rounded-md border border-app-border px-3 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover disabled:opacity-60"
                  >
                    <Ticket className="h-3.5 w-3.5" />
                    Plan for Later
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {!chosen && (
        <button
          type="button"
          disabled={totalVotes === 0}
          onClick={revealTopVibe}
          className="flex items-center justify-center gap-1.5 rounded-md border border-accent/50 bg-accent/10 px-3 py-2 text-xs font-semibold text-accent transition-colors hover:bg-accent/20 disabled:cursor-not-allowed disabled:opacity-40"
        >
          <Sparkles className="h-3.5 w-3.5" />
          {totalVotes === 0
            ? "Cast a vote to reveal the winner"
            : tied
              ? "Reveal the top vibe (ties are drawn at random)"
              : "Reveal the top vibe"}
        </button>
      )}

      <p className="text-[11px] text-zinc-600">
        3 of {poolSize} cached film{poolSize === 1 ? "" : "s"} matching your filters
      </p>
    </div>
  );
}
