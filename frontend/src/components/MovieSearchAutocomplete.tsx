import { useId, useRef, useState } from "react";
import { leapText, narrativeSettingText } from "../lib/historicalEra";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { AlertTriangle, GitBranch, Loader2, Lock, Search, X } from "lucide-react";
import { api } from "../lib/api";
import { useDebouncedValue } from "../lib/useDebouncedValue";
import { allowsMovieRepeats, findExistingStepNumber, forcePricing } from "../lib/rules";
import { connectionMetadata } from "../lib/connections";
import { roleBadgeText } from "../lib/crewRoles";
import { tugNextTeam } from "../lib/tugOfWar";
import LinkBonusBadges from "./LinkBonusBadges";
import { useLogFilm } from "../lib/useLogFilm";
import MoviePoster from "./MoviePoster";
import Popover from "./ui/Popover";
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
  allowedMovieIds?: number[] | null;
  excludedMovieIds?: number[];
}

export default function MovieSearchAutocomplete({
  onSelect,
  placeholder = "Search for a film...",
  runId,
  tailMovieId,
  rulesConfig,
  steps = [],
  onLogged,
  allowedMovieIds,
  excludedMovieIds = [],
}: MovieSearchAutocompleteProps) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebouncedValue(query, 300);
  const [activeResultIndex, setActiveResultIndex] = useState(-1);
  const [resultsDismissed, setResultsDismissed] = useState(false);
  const [picked, setPicked] = useState<MovieSummary | null>(null);
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [validating, setValidating] = useState(false);
  const [watchStatus, setWatchStatus] = useState<"watched" | "planned">("watched");
  const [watchedDate, setWatchedDate] = useState(() => new Date().toISOString().slice(0, 10));
  // Underdog B-Sides: least popular matches first, dead entries hidden.
  const [underdog, setUnderdog] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const resultsId = useId();

  const createStep = useLogFilm(runId ?? "");

  const { data, isFetching } = useQuery({
    queryKey: ["movies", "search", debouncedQuery, underdog],
    queryFn: () =>
      api.get<{ results: MovieSummary[] }>(
        `/movies/search?q=${encodeURIComponent(debouncedQuery)}${underdog ? "&sort_by=underdog" : ""}`,
      ),
    enabled: debouncedQuery.trim().length > 1 && !picked,
  });
  const results = (data?.results ?? []).filter(
    (movie) =>
      (allowedMovieIds == null || allowedMovieIds.includes(movie.tmdb_id)) &&
      !excludedMovieIds.includes(movie.tmdb_id),
  );
  const resultsOpen = debouncedQuery.trim().length > 1 && !picked && !resultsDismissed;

  async function handlePick(movie: MovieSummary) {
    setQuery("");
    setResultsDismissed(true);
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
      if (result.overlay_skippable?.length) setWatchStatus("watched");
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
      ...(validation?.overlay_skippable?.length ? { skip_overlays: validation.overlay_skippable } : {}),
      status: watchStatus,
      watched_at: watchStatus === "watched" ? new Date(watchedDate).toISOString() : null,
      ...([2, 3].includes(rulesConfig?.tug_rules_version ?? 1) && rulesConfig
        ? { tug_team: tugNextTeam(rulesConfig) } : {}),
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
    const skips = validation?.overlay_skippable ?? [];
    const pricing = forcePricing(skips.length ? { wildcards_budget: rulesConfig?.wildcards_budget ?? 0 }
      : rulesConfig ?? { wildcards_budget: -1 });
    const wildcardsRemaining = pricing.remaining;
    const wildcardsExhausted = pricing.exhausted || (wildcardsRemaining !== -1 && wildcardsRemaining < skips.length);

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
                    {validation.reason ?? "No shared cast found"} - using this will consume {skips.length || 1} of{" "}
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
                    Log watched
                  </label>
                  <label className="flex items-center gap-1.5">
                    <input
                      type="radio"
                      disabled={skips.length > 0}
                      checked={watchStatus === "planned"}
                      onChange={() => setWatchStatus("planned")}
                      className="accent-accent"
                    />
                    Queue
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
                      {watchStatus === "planned" ? "Queue" : "Log watched"}
                    </LogButton>
                  ) : (
                    !validation.blocked &&
                    !wildcardsExhausted && (skips.length === 0 || watchStatus === "watched") && (
                      <LogButton pending={createStep.isPending} onClick={() => handleLog(true)}>
                        {skips.length ? `Spend ${skips.length} wildcard${skips.length === 1 ? "" : "s"} & log watched` : pricing.confirmLabel}
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
          ref={inputRef}
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={resultsOpen}
          aria-controls={resultsOpen ? resultsId : undefined}
          aria-activedescendant={
            activeResultIndex >= 0 ? `${resultsId}-option-${activeResultIndex}` : undefined
          }
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setResultsDismissed(false);
            setActiveResultIndex(-1);
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape" && resultsOpen) {
              event.preventDefault();
              event.stopPropagation();
              setResultsDismissed(true);
            } else if (event.key === "ArrowDown" && resultsOpen && results.length > 0) {
              event.preventDefault();
              setActiveResultIndex((index) => (index + 1) % results.length);
            } else if (event.key === "ArrowUp" && resultsOpen && results.length > 0) {
              event.preventDefault();
              setActiveResultIndex((index) => (index <= 0 ? results.length - 1 : index - 1));
            } else if (event.key === "Enter" && resultsOpen && activeResultIndex >= 0) {
              event.preventDefault();
              const activeMovie = results[activeResultIndex];
              if (activeMovie) void handlePick(activeMovie);
            }
          }}
          placeholder={placeholder}
          className="w-full bg-transparent text-sm text-zinc-100 placeholder:text-zinc-600 focus:outline-none"
        />
        {isFetching && <Loader2 className="h-4 w-4 animate-spin text-zinc-600" />}
        <button
          type="button"
          aria-pressed={underdog}
          onClick={() => setUnderdog((v) => !v)}
          title="Least popular matches first: surface hidden gems"
          className={`flex shrink-0 items-center gap-1 rounded-md border px-2 py-1 text-[11px] font-medium transition-colors ${
            underdog
              ? "border-emerald-400 bg-emerald-500/15 text-emerald-200"
              : "border-app-border text-zinc-400 hover:bg-app-surface-hover"
          }`}
        >
          <span aria-hidden>💎</span>
          Underdog B-Sides
        </button>
      </div>

      <Popover
        anchorRef={inputRef}
        open={resultsOpen}
        onClose={() => setResultsDismissed(true)}
        label="Movie search results"
        placement="bottom"
      >
        <div
          id={resultsId}
          role="listbox"
          aria-label="Movie search results"
          aria-busy={isFetching}
          className="max-h-80 overflow-y-auto"
        >
          {results.length === 0 && !isFetching && (
            <p className="px-3 py-3 text-sm text-zinc-500">No films found.</p>
          )}
          {results.map((movie, index) => (
            <button
              key={movie.tmdb_id}
              id={`${resultsId}-option-${index}`}
              type="button"
              role="option"
              tabIndex={-1}
              aria-selected={index === activeResultIndex}
              onMouseEnter={() => setActiveResultIndex(index)}
              onClick={() => void handlePick(movie)}
              className={`flex w-full items-center gap-3 px-3 py-2 text-left transition-colors hover:bg-app-surface-hover ${
                index === activeResultIndex ? "bg-app-surface-hover" : ""
              }`}
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
      </Popover>
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
  const mechanic = validation.mechanic as
    | { year_delta?: number; narrative_delta?: number; narrative_year?: number; narrative_era_label?: string }
    | null
    | undefined;
  const evidence: string[] = [];
  if (mechanic?.narrative_delta !== undefined && mechanic.narrative_year !== undefined) {
    evidence.push(
      `${narrativeSettingText(mechanic.narrative_year, mechanic.narrative_era_label)} (${leapText(mechanic.narrative_delta)})`,
    );
  }
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
