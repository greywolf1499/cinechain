import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { AlertTriangle, GitBranch, Loader2, Lock, Search, X } from "lucide-react";
import { api } from "../lib/api";
import { useDebouncedValue } from "../lib/useDebouncedValue";
import { allowsMovieRepeats, findExistingStepNumber, forcePricing } from "../lib/rules";
import { connectionMetadata } from "../lib/connections";
import { roleBadgeText } from "../lib/crewRoles";
import LinkBonusBadges from "./LinkBonusBadges";
import { useCreateStep } from "../lib/queries";
import MoviePoster from "./MoviePoster";
import type { MovieSummary, RulesConfig, RunStep, ValidationResult } from "../types/api";

interface MovieSearchAutocompleteProps {
  onSelect?: (movie: MovieSummary) => void;
  placeholder?: string;
  /** When provided, picking a movie validates against the run's tail step and logs it. */
  runId?: string;
  tailMovieId?: number;
  rulesConfig?: RulesConfig;
  steps?: RunStep[];
  onLogged?: () => void;
}

export default function MovieSearchAutocomplete({
  onSelect,
  placeholder = "Search for a film...",
  runId,
  tailMovieId,
  rulesConfig,
  steps = [],
  onLogged,
}: MovieSearchAutocompleteProps) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebouncedValue(query, 300);
  const [picked, setPicked] = useState<MovieSummary | null>(null);
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [validating, setValidating] = useState(false);
  const [watchStatus, setWatchStatus] = useState<"watched" | "planned">("watched");
  const [watchedDate, setWatchedDate] = useState(() => new Date().toISOString().slice(0, 10));

  const createStep = useCreateStep(runId ?? "");

  const { data, isFetching } = useQuery({
    queryKey: ["movies", "search", debouncedQuery],
    queryFn: () =>
      api.get<{ results: MovieSummary[] }>(
        `/movies/search?q=${encodeURIComponent(debouncedQuery)}`,
      ),
    enabled: debouncedQuery.trim().length > 1 && !picked,
  });

  async function handlePick(movie: MovieSummary) {
    setQuery("");
    setPicked(movie);
    onSelect?.(movie);

    if (!runId) return; // plain picker mode (e.g. seed movie) - nothing more to do

    setValidating(true);
    try {
      // Run-scoped pre-flight: the run's own engine + rules, same check logging applies.
      const result = await api.post<ValidationResult>(`/runs/${runId}/validate`, {
        movie_id: movie.tmdb_id,
      });
      setValidation(result);
    } catch {
      setValidation({ valid: false, reason: "Could not validate this pick.", connections: [], blocked: false });
    } finally {
      setValidating(false);
    }
  }

  async function handleLog(force: boolean) {
    if (!picked) return;
    const connection = validation?.connections[0];
    await createStep.mutateAsync({
      movie_id: picked.tmdb_id,
      force,
      status: watchStatus,
      watched_at: watchStatus === "watched" ? new Date(watchedDate).toISOString() : null,
      transition_metadata: connection
        ? connectionMetadata(connection, {
            from: connection.character_in_from,
            to: connection.character_in_to,
          })
        : null,
    });
    reset();
    onLogged?.();
  }

  function reset() {
    setPicked(null);
    setValidation(null);
    setQuery("");
    setWatchStatus("watched");
    setWatchedDate(new Date().toISOString().slice(0, 10));
  }

  if (picked) {
    const existingStepNumber = findExistingStepNumber(steps, picked.tmdb_id);
    const allowRepeats = rulesConfig ? allowsMovieRepeats(rulesConfig) : true;
    const isLockedDuplicate = existingStepNumber !== null && !allowRepeats;
    const pricing = forcePricing(rulesConfig ?? { wildcards_budget: -1 });
    const wildcardsRemaining = pricing.remaining;
    const wildcardsExhausted = pricing.exhausted;

    return (
      <div className="rounded-lg border border-app-border bg-app-bg p-3">
        <div className="flex items-start gap-3">
          <MoviePoster path={picked.poster_path} title={picked.title} className="w-12" />
          <div className="min-w-0 flex-1">
            <div className="flex items-center justify-between gap-2">
              <p className="truncate text-sm font-medium text-zinc-100">
                {picked.title}
                {picked.release_year && (
                  <span className="ml-1.5 text-zinc-500">({picked.release_year})</span>
                )}
              </p>
              <button
                type="button"
                onClick={reset}
                className="rounded p-1 text-zinc-500 hover:text-zinc-300"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>

            {runId && validating && (
              <p className="mt-2 flex items-center gap-1.5 text-xs text-zinc-500">
                <Loader2 className="h-3 w-3 animate-spin" /> Checking this pick against the run's rules...
              </p>
            )}

            {isLockedDuplicate && (
              <p className="mt-2 flex items-center gap-1.5 text-xs font-medium text-red-400">
                <Lock className="h-3 w-3" /> Already in Run (Step {existingStepNumber})
              </p>
            )}

            {runId && !validating && !isLockedDuplicate && validation && (
              <div className="mt-2">
                {validation.valid ? (
                  <div className="text-xs text-emerald-400">
                    <LinkBonusBadges meta={validation.mechanic} className="mb-1" />
                    {validation.connections.length > 0
                      ? `Connects to Frontier via ${validation.connections
                          .map((c) =>
                            c.kind === "character"
                              ? `${c.actor_name} (same character)`
                              : c.kind === "craft" && c.role_in_to
                              ? roleBadgeText(c.role_in_to, c.actor_name, c.role_in_from)
                              : c.kind === "director"
                                ? `${c.actor_name} (director)`
                                : c.actor_name,
                          )
                          .join(", ")}`
                      : tailMovieId !== undefined
                        ? describeRuleFit(validation)
                        : "First step - nothing to validate yet."}
                  </div>
                ) : validation.blocked ? (
                  <div className="flex items-center gap-1.5 text-xs font-medium text-red-400">
                    <Lock className="h-3 w-3 shrink-0" />
                    {validation.reason ?? "This film isn't allowed in this run."}
                  </div>
                ) : wildcardsExhausted ? (
                  <div className="flex items-center gap-1.5 text-xs font-medium text-red-400">
                    <Lock className="h-3 w-3" /> Rule broken & 0 {pricing.plural} remaining
                  </div>
                ) : (
                  <div className="flex items-center gap-1.5 text-xs font-medium text-amber-400">
                    <AlertTriangle className="h-3 w-3" />
                    {validation.reason ?? "No shared cast found"} - using this will consume 1 of{" "}
                    {wildcardsRemaining === -1 ? "unlimited" : wildcardsRemaining} remaining {pricing.plural}.
                  </div>
                )}

                <div className="mt-3 flex items-center gap-4 text-xs text-zinc-400">
                  <label className="flex items-center gap-1.5">
                    <input
                      type="radio"
                      checked={watchStatus === "watched"}
                      onChange={() => setWatchStatus("watched")}
                      className="accent-accent"
                    />
                    I've watched this
                  </label>
                  <label className="flex items-center gap-1.5">
                    <input
                      type="radio"
                      checked={watchStatus === "planned"}
                      onChange={() => setWatchStatus("planned")}
                      className="accent-accent"
                    />
                    Plan for later / Up next
                  </label>
                </div>
                {watchStatus === "watched" && (
                  <input
                    type="date"
                    value={watchedDate}
                    max={new Date().toISOString().slice(0, 10)}
                    onChange={(e) => setWatchedDate(e.target.value)}
                    className="mt-2 rounded-md border border-app-border bg-app-bg px-2 py-1 text-xs text-zinc-200 focus:border-accent focus:outline-none"
                  />
                )}

                <div className="mt-2 flex flex-wrap gap-2">
                  {validation.valid ? (
                    <LogButton pending={createStep.isPending} onClick={() => handleLog(false)}>
                      Log this movie
                    </LogButton>
                  ) : (
                    !validation.blocked &&
                    !wildcardsExhausted && (
                      <LogButton pending={createStep.isPending} onClick={() => handleLog(true)}>
                        {pricing.confirmLabel}
                      </LogButton>
                    )
                  )}
                  {!validation.valid && !validation.blocked && tailMovieId !== undefined && (
                    <button
                      type="button"
                      onClick={() => navigate(`/tools/bridge?from=${tailMovieId}&to=${picked.tmdb_id}`)}
                      className="flex items-center gap-1 rounded-md border border-app-border px-3 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover"
                    >
                      <GitBranch className="h-3 w-3" /> Build Bridge to Here
                    </button>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="relative">
      <div className="flex items-center gap-2 rounded-md border border-app-border bg-app-bg px-3 py-2">
        <Search className="h-4 w-4 text-zinc-500" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={placeholder}
          className="w-full bg-transparent text-sm text-zinc-100 placeholder:text-zinc-600 focus:outline-none"
        />
        {isFetching && <Loader2 className="h-4 w-4 animate-spin text-zinc-600" />}
      </div>

      {debouncedQuery.trim().length > 1 && (
        <div className="absolute z-20 mt-1 max-h-80 w-full overflow-y-auto rounded-md border border-app-border bg-app-surface shadow-xl">
          {data?.results.length === 0 && (
            <p className="px-3 py-3 text-sm text-zinc-500">No films found.</p>
          )}
          {data?.results.map((movie) => (
            <button
              key={movie.tmdb_id}
              type="button"
              onClick={() => handlePick(movie)}
              className="flex w-full items-center gap-3 px-3 py-2 text-left transition-colors hover:bg-app-surface-hover"
            >
              <MoviePoster path={movie.poster_path} title={movie.title} className="w-9" />
              <span className="min-w-0 truncate text-sm text-zinc-200">
                {movie.title}
                {movie.release_year && (
                  <span className="ml-1.5 text-zinc-500">({movie.release_year})</span>
                )}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function LogButton({
  onClick,
  pending,
  children,
}: {
  onClick: () => void;
  pending: boolean;
  children: string;
}) {
  return (
    <button
      type="button"
      disabled={pending}
      onClick={onClick}
      className="flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:opacity-60"
    >
      {pending && <Loader2 className="h-3 w-3 animate-spin" />}
      {children}
    </button>
  );
}

/** "Fits this run's rule" plus whatever evidence the engine measured for a cast-free hop. */
function describeRuleFit(validation: ValidationResult): string {
  const mechanic = validation.mechanic as { year_delta?: number } | null | undefined;
  const evidence: string[] = [];
  if (mechanic?.year_delta !== undefined) {
    const years = Math.abs(mechanic.year_delta);
    evidence.push(`${years} year${years === 1 ? "" : "s"} ${mechanic.year_delta > 0 ? "later" : "earlier"}`);
  }
  if (validation.similarity != null) {
    evidence.push(`${Math.round(Math.max(0, validation.similarity) * 100)}% plot match`);
  }
  if (validation.color_distance != null) {
    evidence.push(`colour distance ${Math.round(validation.color_distance)}`);
  }
  return evidence.length > 0
    ? `Fits this run's rule (${evidence.join(", ")}).`
    : "Fits this run's rule.";
}
