import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Dices, Loader2, Search, X } from "lucide-react";
import MoviePoster from "./MoviePoster";
import { ApiError, api } from "../lib/api";
import { BRACKET_SIZE } from "../lib/bracket";
import { cn } from "../lib/cn";
import { useDebouncedValue } from "../lib/useDebouncedValue";
import type { MovieSummary } from "../types/api";

/** Pick the 16 contenders by hand, or roll them from the synced Letterboxd watchlist. */
export default function BracketSeedPicker({
  films,
  onChange,
}: {
  films: MovieSummary[];
  onChange: (films: MovieSummary[]) => void;
}) {
  const [query, setQuery] = useState("");
  const debounced = useDebouncedValue(query, 300);
  const [seeding, setSeeding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { data, isFetching } = useQuery({
    queryKey: ["movies", "search", debounced],
    queryFn: () => api.get<{ results: MovieSummary[] }>(`/movies/search?q=${encodeURIComponent(debounced)}`),
    enabled: debounced.trim().length > 1,
  });
  const full = films.length >= BRACKET_SIZE;

  async function seedRandom() {
    setSeeding(true);
    setError(null);
    try {
      onChange(await api.get<MovieSummary[]>("/tools/march-madness/seed"));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not seed from the watchlist.");
    } finally {
      setSeeding(false);
    }
  }

  function add(movie: MovieSummary) {
    if (full || films.some((f) => f.tmdb_id === movie.tmdb_id)) return;
    onChange([...films, movie]);
    setQuery("");
  }

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-amber-400/30 bg-amber-500/5 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-medium text-zinc-400">
          The contenders{" "}
          <span className={cn("tabular-nums", full ? "text-emerald-300" : "text-zinc-500")}>
            ({films.length} / {BRACKET_SIZE})
          </span>
        </span>
        <button
          type="button"
          onClick={() => void seedRandom()}
          disabled={seeding}
          className="flex items-center gap-1.5 rounded-md border border-amber-400/50 px-3 py-1.5 text-xs font-semibold text-amber-200 transition-colors hover:bg-amber-400/10 disabled:opacity-60"
        >
          {seeding ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Dices className="h-3.5 w-3.5" />}
          🎲 Seed Random 16 from Watchlist
        </button>
      </div>
      <p className="text-[11px] text-zinc-500">
        Need a watchlist?{" "}
        <Link
          to="/settings/integrations#watchlist"
          className="font-medium text-accent hover:underline"
        >
          Sync it from Integrations
        </Link>
      </p>
      {error && <p className="text-[11px] text-red-300">{error}</p>}

      {!full && (
        <div className="relative">
          <div className="flex items-center gap-2 rounded-md border border-app-border bg-app-bg px-3 py-2 focus-within:border-accent">
            <Search className="h-4 w-4 shrink-0 text-zinc-500" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search a film to add..."
              aria-label="Add a contender"
              className="w-full bg-transparent text-sm text-zinc-100 placeholder:text-zinc-600 focus:outline-none"
            />
            {isFetching && <Loader2 className="h-4 w-4 animate-spin text-zinc-500" />}
          </div>
          {query.trim().length > 1 && data && data.results.length > 0 && (
            <ul className="mt-1 max-h-56 overflow-y-auto rounded-md border border-app-border bg-app-surface">
              {data.results.slice(0, 6).map((movie) => (
                <li key={movie.tmdb_id}>
                  <button
                    type="button"
                    onClick={() => add(movie)}
                    className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm text-zinc-100 hover:bg-app-surface-hover"
                  >
                    <MoviePoster path={movie.poster_path} title={movie.title} className="w-6 shrink-0" />
                    <span className="truncate">{movie.title}</span>
                    <span className="ml-auto text-xs text-zinc-500">{movie.release_year ?? ""}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {films.length > 0 && (
        <ol className="grid grid-cols-1 gap-1.5 sm:grid-cols-2" aria-label="Seeded films">
          {films.map((film, index) => (
            <li
              key={film.tmdb_id}
              className="flex items-center gap-2 rounded-md border border-app-border bg-app-bg px-2 py-1 text-xs text-zinc-200"
            >
              <span className="w-4 text-right tabular-nums text-zinc-600">{index + 1}</span>
              <span className="min-w-0 flex-1 truncate">{film.title}</span>
              <span className="text-zinc-500">{film.release_year ?? ""}</span>
              <button
                type="button"
                aria-label={`Remove ${film.title}`}
                onClick={() => onChange(films.filter((f) => f.tmdb_id !== film.tmdb_id))}
                className="text-zinc-600 hover:text-zinc-200"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ol>
      )}
      <p className="text-[11px] text-zinc-500">
        Neighbours meet in the Round of 16: films 1 &amp; 2, 3 &amp; 4... in the order listed.
      </p>
    </div>
  );
}
