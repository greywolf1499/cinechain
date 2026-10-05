import { useMemo, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Compass, Loader2, Search, X, Zap } from "lucide-react";
import PageHeading from "../components/PageHeading";
import MoviePoster from "../components/MoviePoster";
import ClampedLabel from "../components/ui/ClampedLabel";
import ExpandableText from "../components/ui/ExpandableText";
import { ApiError, api } from "../lib/api";
import { cn } from "../lib/cn";
import {
  DEFAULT_PRIORITIES,
  MAX_ROUTER_FILMS,
  MIN_ROUTER_FILMS,
  RANDOM_PICK_COUNT,
  TRANSITION_STYLES,
  bannerText,
  defaultRunName,
  pickRandom,
  priorityWeights,
  transitionText,
  type PriorityValues,
} from "../lib/marathonRouter";
import { useConvertMarathonToRun, useOptimizeMarathon } from "../lib/queries";
import { useDebouncedValue } from "../lib/useDebouncedValue";
import type { BingoWatchlist, MovieSummary, RouterResult } from "../types/api";

const MIN_CALCULATION_MS = 900; // long enough for the animation to read as "working"

interface PickedFilm {
  movie_id: number;
  title: string;
  year: number | null;
  poster_path: string | null;
}

type Source = "watchlist" | "search";

const SLIDERS: { key: keyof PriorityValues; label: string; hint: string }[] = [
  { key: "genre", label: "Genre Harmony", hint: "Keep neighbouring films in the same genres" },
  { key: "decade", label: "Decade Proximity", hint: "Keep neighbouring films from the same era" },
  { key: "pacing", label: "Pacing / Runtime", hint: "Avoid jumping between short and epic-length films" },
];

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** The Perfect Marathon Router: pick films, get the order with the least tonal whiplash. */
export default function MarathonRouterPage() {
  const navigate = useNavigate();
  const [picked, setPicked] = useState<PickedFilm[]>([]);
  const [source, setSource] = useState<Source>("watchlist");
  const [filter, setFilter] = useState("");
  const [priorities, setPriorities] = useState<PriorityValues>(DEFAULT_PRIORITIES);
  const [result, setResult] = useState<RouterResult | null>(null);
  const [calculating, setCalculating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [runName, setRunName] = useState(defaultRunName);
  const optimize = useOptimizeMarathon();
  const convert = useConvertMarathonToRun();

  const watchlist = useQuery({
    queryKey: ["tools", "router", "watchlist"],
    queryFn: () => api.get<BingoWatchlist>("/tools/bingo/watchlist"),
    staleTime: 60_000,
  });
  const watchlistFilms = useMemo<PickedFilm[]>(
    () =>
      (watchlist.data?.films ?? []).map((f) => ({
        movie_id: f.movie_id,
        title: f.title,
        year: f.year,
        poster_path: f.poster_path,
      })),
    [watchlist.data],
  );

  const debouncedFilter = useDebouncedValue(filter, 300).trim();
  const search = useQuery({
    queryKey: ["movies", "search", debouncedFilter],
    queryFn: () => api.get<{ results: MovieSummary[] }>(`/movies/search?q=${encodeURIComponent(debouncedFilter)}`),
    enabled: source === "search" && debouncedFilter.length > 0,
    staleTime: 60_000,
  });

  const pickedIds = useMemo(() => new Set(picked.map((f) => f.movie_id)), [picked]);
  const candidates: PickedFilm[] =
    source === "watchlist"
      ? watchlistFilms.filter((f) => f.title.toLowerCase().includes(filter.trim().toLowerCase()))
      : (search.data?.results ?? []).map((m) => ({
          movie_id: m.tmdb_id,
          title: m.title,
          year: m.release_year,
          poster_path: m.poster_path,
        }));

  function changeSelection(next: PickedFilm[]) {
    setPicked(next);
    setResult(null);
    setError(null);
    setNotice(null);
  }

  function toggle(film: PickedFilm) {
    if (pickedIds.has(film.movie_id)) {
      changeSelection(picked.filter((f) => f.movie_id !== film.movie_id));
    } else if (picked.length >= MAX_ROUTER_FILMS) {
      setNotice(`The router takes at most ${MAX_ROUTER_FILMS} films - remove one first.`);
    } else {
      changeSelection([...picked, film]);
    }
  }

  function pickRandomTen() {
    if (watchlistFilms.length < MIN_ROUTER_FILMS) {
      setNotice(`Your watchlist needs at least ${MIN_ROUTER_FILMS} films - sync it from Settings first.`);
      return;
    }
    changeSelection(pickRandom(watchlistFilms, RANDOM_PICK_COUNT));
  }

  async function calculate() {
    setError(null);
    setNotice(null);
    setCalculating(true);
    try {
      const [optimized] = await Promise.all([
        optimize.mutateAsync({ movie_ids: picked.map((f) => f.movie_id), ...priorityWeights(priorities) }),
        sleep(MIN_CALCULATION_MS),
      ]);
      setResult(optimized);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't calculate the route.");
    } finally {
      setCalculating(false);
    }
  }

  async function queueRun() {
    if (!result) return;
    setError(null);
    try {
      const run = await convert.mutateAsync({
        run_name: runName.trim() || defaultRunName(),
        movie_ids: result.ordered_movie_ids,
      });
      navigate(`/runs/${run.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't queue the run.");
    }
  }

  const canCalculate = picked.length >= MIN_ROUTER_FILMS && !calculating;

  return (
    <div>
      <Link
        to="/tools"
        className="mb-3 inline-flex items-center gap-1 text-xs text-zinc-500 transition-colors hover:text-zinc-300"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> Tools
      </Link>
      <PageHeading
        title="Perfect Marathon Router"
        subtitle="Pick the films, and the router finds the order with the least tonal whiplash."
      />

      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <section className="rounded-xl border border-app-border bg-app-surface p-4" aria-label="Movie selector">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <div className="flex rounded-md border border-app-border p-0.5 text-xs" role="tablist">
              {(["watchlist", "search"] as const).map((tab) => (
                <button
                  key={tab}
                  type="button"
                  role="tab"
                  aria-selected={source === tab}
                  onClick={() => {
                    setSource(tab);
                    setFilter("");
                  }}
                  className={cn(
                    "rounded px-3 py-1 transition-colors",
                    source === tab ? "bg-accent text-zinc-950" : "text-zinc-400 hover:text-zinc-200",
                  )}
                >
                  {tab === "watchlist" ? "My Watchlist" : "Search Films"}
                </button>
              ))}
            </div>
            <button
              type="button"
              onClick={pickRandomTen}
              disabled={watchlist.isLoading}
              className="ml-auto rounded-md border border-app-border px-3 py-1.5 text-xs font-medium text-zinc-200 transition-colors hover:bg-app-surface-hover disabled:opacity-60"
            >
              {"\u{1F3B2}"} Pick {RANDOM_PICK_COUNT} Random from Watchlist
            </button>
          </div>

          <label className="relative mb-3 block">
            <Search className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-zinc-500" />
            <input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder={source === "watchlist" ? "Filter your watchlist..." : "Search any film by title..."}
              className="w-full rounded-md border border-app-border bg-app-bg py-2 pl-8 pr-3 text-sm text-zinc-100 outline-none focus:border-accent"
            />
          </label>

          {picked.length > 0 && (
            <ul className="mb-3 flex flex-wrap gap-1.5" aria-label="Selected films">
              {picked.map((film, index) => (
                <li
                  key={film.movie_id}
                  className="flex items-center gap-1.5 rounded-full border border-accent/40 bg-accent/10 py-0.5 pl-2.5 pr-1 text-xs text-accent"
                >
                  <span className="font-semibold tabular-nums">{index + 1}</span>
                  <span className="max-w-40 truncate">{film.title}</span>
                  <button
                    type="button"
                    onClick={() => toggle(film)}
                    aria-label={`Remove ${film.title}`}
                    className="rounded-full p-0.5 hover:bg-accent/20"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </li>
              ))}
            </ul>
          )}

          <FilmPicker
            films={candidates}
            pickedIds={pickedIds}
            onToggle={toggle}
            loading={source === "watchlist" ? watchlist.isLoading : search.isFetching}
            emptyMessage={
              source === "watchlist"
                ? watchlistFilms.length === 0
                  ? "No synced watchlist yet, or no films match your filter."
                  : "No watchlist film matches that filter."
                : debouncedFilter
                  ? "No films found."
                  : "Type a title to search."
            }
            emptyAction={
              source === "watchlist" && watchlistFilms.length === 0 ? (
                <Link
                  to="/settings/integrations#watchlist"
                  className="font-medium text-accent hover:underline"
                >
                  Sync your watchlist
                </Link>
              ) : undefined
            }
          />
        </section>

        <aside className="space-y-4 rounded-xl border border-app-border bg-app-surface p-4">
          <h2 className="text-sm font-semibold text-zinc-100">Priorities</h2>
          {SLIDERS.map((slider) => (
            <label key={slider.key} className="block">
              <span className="flex items-center justify-between text-xs text-zinc-300">
                {slider.label}
                <span className="tabular-nums text-zinc-500">{priorities[slider.key]}%</span>
              </span>
              <input
                type="range"
                min={0}
                max={100}
                step={5}
                value={priorities[slider.key]}
                onChange={(e) => setPriorities({ ...priorities, [slider.key]: Number(e.target.value) })}
                aria-label={slider.label}
                className="mt-1 w-full accent-accent"
              />
              <span className="text-[11px] text-zinc-500">{slider.hint}</span>
            </label>
          ))}
          <button
            type="button"
            onClick={() => setPriorities(DEFAULT_PRIORITIES)}
            className="text-[11px] text-zinc-500 underline hover:text-zinc-300"
          >
            Reset priorities
          </button>

          <button
            type="button"
            onClick={calculate}
            disabled={!canCalculate}
            className="flex w-full items-center justify-center gap-2 rounded-md bg-accent px-3.5 py-2.5 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-50"
          >
            {calculating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Zap className="h-4 w-4" />}
            {calculating ? "Calculating..." : "Calculate Smoothest Order"}
          </button>
          <p className="text-center text-[11px] text-zinc-500">
            {picked.length} / {MAX_ROUTER_FILMS} films selected
            {picked.length < MIN_ROUTER_FILMS && ` - pick at least ${MIN_ROUTER_FILMS}`}
          </p>
        </aside>
      </div>

      {notice && <p className="mt-3 text-xs text-amber-400">{notice}</p>}
      {error && (
        <p role="alert" className="mt-3 rounded-md border border-red-900/60 bg-red-950/30 px-3 py-2 text-xs text-red-300">
          {error}
        </p>
      )}

      {calculating && <CalculatingPanel count={picked.length} />}

      {result && !calculating && (
        <OptimizedSequence
          result={result}
          runName={runName}
          onRunNameChange={setRunName}
          onQueue={queueRun}
          queueing={convert.isPending}
        />
      )}
    </div>
  );
}

function FilmPicker({
  films,
  pickedIds,
  onToggle,
  loading,
  emptyMessage,
  emptyAction,
}: {
  films: PickedFilm[];
  pickedIds: Set<number>;
  onToggle: (film: PickedFilm) => void;
  loading: boolean;
  emptyMessage: string;
  emptyAction?: ReactNode;
}) {
  if (loading) {
    return (
      <p className="flex items-center gap-2 py-6 text-xs text-zinc-500" role="status">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading films...
      </p>
    );
  }
  if (films.length === 0) {
    return (
      <div className="py-6 text-center text-xs text-zinc-500">
        <p>{emptyMessage}</p>
        {emptyAction && <div className="mt-2">{emptyAction}</div>}
      </div>
    );
  }
  return (
    <ul className="grid max-h-96 grid-cols-3 gap-2 overflow-y-auto pr-1 sm:grid-cols-4 md:grid-cols-5 xl:grid-cols-6">
      {films.map((film) => {
        const on = pickedIds.has(film.movie_id);
        return (
          <li key={film.movie_id}>
            <button
              type="button"
              onClick={() => onToggle(film)}
              aria-pressed={on}
              title={`${film.title}${film.year ? ` (${film.year})` : ""}`}
              className={cn(
                "group relative block w-full overflow-hidden rounded-md border text-left transition-colors",
                on ? "border-accent ring-2 ring-accent" : "border-app-border hover:border-zinc-500",
              )}
            >
              <MoviePoster path={film.poster_path} title={film.title} className="w-full" />
              <span className="block truncate bg-app-bg px-1.5 py-1 text-[11px] text-zinc-300">{film.title}</span>
              {on && (
                <span className="absolute right-1 top-1 rounded-full bg-accent px-1.5 text-[10px] font-bold text-zinc-950">
                  {"\u2713"}
                </span>
              )}
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function CalculatingPanel({ count }: { count: number }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="mt-5 flex flex-col items-center gap-3 rounded-xl border border-accent/30 bg-accent/5 px-6 py-8 text-sm text-accent"
    >
      <Compass className="h-8 w-8 animate-spin" style={{ animationDuration: "1.6s" }} />
      <span>Weighing {count} films against each other...</span>
      <div className="h-1 w-48 overflow-hidden rounded bg-app-surface-hover">
        <div className="h-full w-1/3 animate-pulse rounded bg-accent" />
      </div>
    </div>
  );
}

function OptimizedSequence({
  result,
  runName,
  onRunNameChange,
  onQueue,
  queueing,
}: {
  result: RouterResult;
  runName: string;
  onRunNameChange: (name: string) => void;
  onQueue: () => void;
  queueing: boolean;
}) {
  return (
    <section className="mt-5" aria-label="Optimized sequence">
      <div
        role="status"
        className="mb-4 rounded-lg border border-accent/50 bg-accent/10 px-4 py-3 text-sm font-semibold text-accent"
      >
        {bannerText(result)}
      </div>

      <ol className="mx-auto max-w-2xl">
        {result.films.map((film, index) => {
          const transition = result.transitions[index];
          return (
            <li key={film.movie_id}>
              <article className="flex gap-3 rounded-xl border border-app-border bg-app-surface p-3">
                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-bold text-zinc-950">
                  {index + 1}
                </span>
                <MoviePoster path={film.poster_path} title={film.title} className="w-16 shrink-0" />
                <div className="min-w-0">
                  <h3 className="min-w-0 text-sm font-semibold text-zinc-100">
                    <ClampedLabel text={film.title} lines={1} as="span" />
                    {film.year ? <span className="font-normal text-zinc-500"> ({film.year})</span> : null}
                  </h3>
                  <p className="mt-0.5 text-xs text-zinc-400">
                    {[
                      film.genres.slice(0, 3).join(" / ") || null,
                      film.runtime ? `${film.runtime} min` : null,
                      film.rating ? `\u2605 ${film.rating.toFixed(1)}` : null,
                    ]
                      .filter(Boolean)
                      .join("  \u00B7  ")}
                  </p>
                  {film.overview && (
                    <ExpandableText
                      text={film.overview}
                      lines={2}
                      className="mt-1 text-xs text-zinc-500"
                    />
                  )}
                </div>
              </article>
              {transition && (
                <div className="flex flex-col items-center py-1.5">
                  <span className="h-2 w-px bg-app-border" />
                  <span
                    className={cn(
                      "rounded-full border px-3 py-1 text-[11px] font-medium",
                      TRANSITION_STYLES[transition.label].chip,
                    )}
                  >
                    {transitionText(transition)}
                  </span>
                  <span className="h-2 w-px bg-app-border" />
                </div>
              )}
            </li>
          );
        })}
      </ol>

      <div className="mx-auto mt-5 flex max-w-2xl flex-wrap items-center gap-3 rounded-xl border border-app-border bg-app-surface p-3">
        <input
          value={runName}
          onChange={(e) => onRunNameChange(e.target.value)}
          maxLength={128}
          aria-label="Run name"
          className="min-w-0 flex-1 rounded-md border border-app-border bg-app-bg px-3 py-2 text-sm text-zinc-100 outline-none focus:border-accent"
        />
        <button
          type="button"
          onClick={onQueue}
          disabled={queueing}
          className="flex items-center gap-2 rounded-md bg-accent px-3.5 py-2 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:opacity-60"
        >
          {queueing && <Loader2 className="h-4 w-4 animate-spin" />}
          {"\u{1F37F}"} Queue as Challenge Run
        </button>
      </div>
    </section>
  );
}
