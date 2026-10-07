import { useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Loader2, RefreshCw, X } from "lucide-react";
import MoviePoster from "./MoviePoster";
import MovieSearchAutocomplete from "./MovieSearchAutocomplete";
import { api } from "../lib/api";
import type { MovieSummary, SeedSuggestionResponse, RulesConfig, RawRulesConfig } from "../types/api";

/** Search for a starting film, or let the local cache pick one: "Recommend Seed
 * Movie" rolls a well-regarded film and "Re-roll" never repeats one until they're used up. */
export default function SeedMoviePicker({
  value,
  onChange,
  gameType,
  rules,
  excludeIds = [],
  allowedIds,
  recommendFirst = false,
}: {
  value: MovieSummary | null;
  onChange: (movie: MovieSummary | null) => void;
  gameType: string;
  rules: RulesConfig | RawRulesConfig;
  excludeIds?: number[];
  allowedIds?: number[] | null;
  /** Give the recommendation action primary placement in large seed previews. */
  recommendFirst?: boolean;
}) {
  const contextKey = JSON.stringify([gameType, rules, excludeIds]);
  const activeContext = useRef<string | null>(contextKey);
  useEffect(() => {
    activeContext.current = contextKey;
    return () => { activeContext.current = null; };
  }, [contextKey]);
  const [rotation, setRotation] = useState<{
    key: string; seen: number[]; reason: string | null; emptyReason: string | null;
  }>({ key: contextKey, seen: [], reason: null, emptyReason: null });
  const seen = [...new Set([
    ...(rotation.key === contextKey ? rotation.seen : []),
    ...(value ? [value.tmdb_id] : []),
  ])];
  const reason = rotation.key === contextKey ? rotation.reason : null;
  const emptyReason = rotation.key === contextKey ? rotation.emptyReason : null;

  const roll = useMutation({
    mutationFn: async () => {
      const query = (exclude: number[]) =>
        api.post<SeedSuggestionResponse>("/movies/seed-suggestion", {
          game_type: gameType,
          rules_config: rules,
          exclude: [...excludeIds, ...exclude],
        });
      const first = await query(seen);
      if (first.suggestion || seen.length === 0) return { response: first, key: contextKey, previous: seen };
      // Every candidate has been shown: start the rotation over.
      return { response: await query([]), key: contextKey, previous: [] };
    },
    onSuccess: ({ response, key, previous }) => {
      if (key !== activeContext.current) return;
      const suggestion = response.suggestion;
      setRotation({
        key,
        seen: suggestion ? [...previous, suggestion.tmdb_id] : previous,
        reason: suggestion ? response.reason : null,
        emptyReason: suggestion ? null : response.reason,
      });
      if (!suggestion) return;
      onChange(suggestion);
    },
  });

  function clear() {
    setRotation((current) => ({ ...current, reason: null }));
    onChange(null);
  }

  if (value) {
    return (
      <div className="flex items-center gap-3 rounded-lg border border-app-border bg-app-bg p-2.5">
        <MoviePoster movieId={value.tmdb_id} path={value.poster_path} title={value.title} className="w-11 shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-zinc-100">
            {value.title}
            {value.release_year && <span className="ml-1.5 text-zinc-500">({value.release_year})</span>}
          </p>
          {reason && <p className="text-[11px] text-accent">🎲 {reason}</p>}
          {(emptyReason || roll.isError) && <p role="alert" className="text-[11px] text-amber-400">{roll.isError ? roll.error.message : emptyReason}</p>}
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
      allowedMovieIds={allowedIds}
      excludedMovieIds={excludeIds}
      onSelect={(movie) => {
        setRotation((current) => ({ ...current, reason: null }));
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
      {(emptyReason || roll.isError) && (
        <p role="alert" className="text-[11px] text-amber-400">
          {roll.isError
            ? roll.error.message
            : emptyReason}
        </p>
      )}
    </div>
  );
}
