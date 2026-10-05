import { useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Loader2, RefreshCw, X } from "lucide-react";
import MoviePoster from "./MoviePoster";
import MovieSearchAutocomplete from "./MovieSearchAutocomplete";
import { api } from "../lib/api";
import type { MovieSummary, SeedSuggestion } from "../types/api";

/** Search for a starting film, or let the local cache pick one: "Recommend Seed
 * Movie" rolls a well-regarded film and "Re-roll" never repeats one until they're used up. */
export default function SeedMoviePicker({
  value,
  onChange,
  gameType,
  recommendFirst = false,
}: {
  value: MovieSummary | null;
  onChange: (movie: MovieSummary | null) => void;
  gameType: string;
  /** Give the recommendation action primary placement in large seed previews. */
  recommendFirst?: boolean;
}) {
  const [reason, setReason] = useState<string | null>(null);
  const [emptyCache, setEmptyCache] = useState(false);
  const seen = useRef<number[]>([]);

  const roll = useMutation({
    mutationFn: async () => {
      const query = (exclude: number[]) =>
        api.get<SeedSuggestion | null>(
          `/movies/seed-suggestion?game_type=${encodeURIComponent(gameType)}&exclude=${exclude.join(",")}`,
        );
      const first = await query(seen.current);
      if (first || seen.current.length === 0) return first;
      // Every candidate has been shown: start the rotation over.
      seen.current = [];
      return query([]);
    },
    onSuccess: (suggestion) => {
      setEmptyCache(!suggestion);
      if (!suggestion) return;
      seen.current.push(suggestion.tmdb_id);
      setReason(suggestion.reason);
      onChange(suggestion);
    },
  });

  function clear() {
    setReason(null);
    onChange(null);
  }

  if (value) {
    return (
      <div className="flex items-center gap-3 rounded-lg border border-app-border bg-app-bg p-2.5">
        <MoviePoster path={value.poster_path} title={value.title} className="w-11 shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-zinc-100">
            {value.title}
            {value.release_year && <span className="ml-1.5 text-zinc-500">({value.release_year})</span>}
          </p>
          {reason && <p className="text-[11px] text-accent">🎲 {reason}</p>}
        </div>
        <button
          type="button"
          onClick={() => roll.mutate()}
          disabled={roll.isPending}
          className="flex shrink-0 items-center gap-1.5 rounded-md border border-app-border px-2.5 py-1.5 text-xs font-medium text-zinc-200 transition-colors hover:bg-app-surface-hover disabled:opacity-60"
        >
          {roll.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" />
          )}
          Re-roll
        </button>
        <button
          type="button"
          onClick={clear}
          aria-label="Remove seed movie"
          className="shrink-0 rounded p-1 text-zinc-500 transition-colors hover:text-zinc-200"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
    );
  }

  const search = (
    <MovieSearchAutocomplete
      onSelect={(movie) => {
        setReason(null);
        onChange(movie);
      }}
      placeholder="Search for a starting film..."
    />
  );
  const recommend = (
    <button
      type="button"
      onClick={() => roll.mutate()}
      disabled={roll.isPending}
      className={`flex min-h-[38px] items-center justify-center gap-1.5 rounded-md px-3 text-xs font-semibold transition-colors disabled:opacity-60 ${
        recommendFirst
          ? "border border-accent bg-accent text-zinc-950 hover:bg-accent-strong"
          : "shrink-0 border border-accent/40 bg-accent/10 text-accent hover:bg-accent/20"
      }`}
    >
      {roll.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
      🎲 Recommend Seed Movie
    </button>
  );

  return (
    <div className="flex flex-col gap-1.5">
      {recommendFirst ? (
        <>
          {recommend}
          <div className="min-w-0">{search}</div>
        </>
      ) : (
        <div className="flex items-start gap-2">
          <div className="min-w-0 flex-1">{search}</div>
          {recommend}
        </div>
      )}
      {(emptyCache || roll.isError) && (
        <p role="alert" className="text-[11px] text-amber-400">
          {roll.isError
            ? "Couldn't fetch a suggestion - try searching instead."
            : "Your movie cache has nothing to recommend yet - search for a film instead."}
        </p>
      )}
    </div>
  );
}
