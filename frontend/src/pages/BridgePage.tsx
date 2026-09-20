import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  AlertTriangle,
  Flag,
  GitBranch,
  Link2,
  Loader2,
  RotateCcw,
  Sparkles,
  XCircle,
} from "lucide-react";
import PageHeading from "../components/PageHeading";
import EmptyState from "../components/EmptyState";
import MoviePoster from "../components/MoviePoster";
import MovieSearchAutocomplete from "../components/MovieSearchAutocomplete";
import MoviePreviewModal from "../components/MoviePreviewModal";
import BridgePathView from "../components/BridgePathView";
import { ApiError, api } from "../lib/api";
import { cn } from "../lib/cn";
import { useCreateStep, useRun, useRuns } from "../lib/queries";
import type { BridgeResult, JellyfinItemSummary, MovieSummary } from "../types/api";

interface ProgressEvent {
  depth: number;
  frontier_forward: number;
  frontier_backward: number;
  tmdb_calls: number;
  cache_hits: number;
  elapsed_ms: number;
}

interface ExhaustedEvent {
  reason: "budget_exceeded" | "max_depth_reached";
  tmdb_calls: number;
  elapsed_ms: number;
}

type SolveStatus = "idle" | "streaming" | "solved" | "exhausted" | "error";

const MIN_DEPTH = 2;
const MAX_DEPTH = 5;
const DEFAULT_DEPTH = 4;

export default function BridgePage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { data: activeRuns } = useRuns("active");
  const [selectedRunId, setSelectedRunId] = useState("");
  const { data: selectedRun } = useRun(selectedRunId || undefined);
  const createStep = useCreateStep(selectedRunId);

  const [startMovie, setStartMovie] = useState<MovieSummary | null>(null);
  const [targetMovie, setTargetMovie] = useState<MovieSummary | null>(null);
  const [maxDepth, setMaxDepth] = useState(DEFAULT_DEPTH);

  const [status, setStatus] = useState<SolveStatus>("idle");
  const [progress, setProgress] = useState<ProgressEvent | null>(null);
  const [result, setResult] = useState<BridgeResult | null>(null);
  const [activePathIndex, setActivePathIndex] = useState(0);
  const [previewMovieId, setPreviewMovieId] = useState<number | null>(null);
  const [exhausted, setExhausted] = useState<ExhaustedEvent | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [onServerMap, setOnServerMap] = useState<Record<number, JellyfinItemSummary>>({});

  const [queueing, setQueueing] = useState(false);
  const [queueError, setQueueError] = useState<string | null>(null);

  const sourceRef = useRef<EventSource | null>(null);

  const runIdFromQuery = searchParams.get("run_id");

  // A ?run_id= query param scopes the whole solve to that run - takes over
  // the run picker entirely (no free choice of "queue to a different run").
  useEffect(() => {
    if (runIdFromQuery) setSelectedRunId(runIdFromQuery);
  }, [runIdFromQuery]);

  // Default the run picker to the most recently created active run - skipped
  // when a ?run_id= query param already pinned it.
  useEffect(() => {
    if (runIdFromQuery) return;
    if (!selectedRunId && activeRuns && activeRuns.length > 0) {
      setSelectedRunId(activeRuns[0].id);
    }
  }, [activeRuns, selectedRunId, runIdFromQuery]);

  const tailStep = selectedRun?.steps[selectedRun.steps.length - 1];

  // "Build Bridge to Here" shortcut (from Fork-in-the-Road / search guards)
  // deep-links here with ?from=&to= - takes priority over the tail-step default.
  const prefilledFromQuery = useRef(false);
  useEffect(() => {
    const fromId = searchParams.get("from");
    const toId = searchParams.get("to");
    if (!fromId && !toId) return;
    prefilledFromQuery.current = true;
    if (fromId) {
      api.get<MovieSummary>(`/movies/${fromId}`).then(setStartMovie).catch(() => {});
    }
    if (toId) {
      api.get<MovieSummary>(`/movies/${toId}`).then(setTargetMovie).catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Default the starting movie to the selected run's last logged film -
  // skipped when a ?from= query param already set it explicitly.
  useEffect(() => {
    if (prefilledFromQuery.current) return;
    if (tailStep) {
      setStartMovie({
        tmdb_id: tailStep.movie_id,
        title: tailStep.movie_title,
        poster_path: tailStep.movie_poster_path,
        release_year: tailStep.movie_release_year,
        origin_country: tailStep.movie_origin_country,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tailStep?.id]);

  useEffect(() => {
    return () => {
      sourceRef.current?.close();
    };
  }, []);

  // Fetch Jellyfin "on server" badges once a path is solved (non-fatal on failure).
  // Covers every path option's movies, not just the primary, so switching tabs
  // still shows correct badges without a refetch.
  useEffect(() => {
    if (!result) return;
    const allMovieIds = [
      ...result.path.map((node) => node.movie_id),
      ...(result.alternate_paths ?? []).flatMap((alt) => alt.path.map((node) => node.movie_id)),
    ];
    let cancelled = false;
    api
      .post<Record<string, JellyfinItemSummary>>("/integrations/jellyfin/lookup", {
        tmdb_ids: allMovieIds,
      })
      .then((data) => {
        if (cancelled) return;
        const numeric: Record<number, JellyfinItemSummary> = {};
        for (const [key, value] of Object.entries(data)) {
          numeric[Number(key)] = value;
        }
        setOnServerMap(numeric);
      })
      .catch(() => {
        /* badges are a nice-to-have - skip silently */
      });
    return () => {
      cancelled = true;
    };
  }, [result]);

  function closeSource() {
    sourceRef.current?.close();
    sourceRef.current = null;
  }

  function startSolve(depthOverride?: number) {
    if (!startMovie || !targetMovie) return;
    closeSource();
    setStatus("streaming");
    setProgress(null);
    setResult(null);
    setActivePathIndex(0);
    setExhausted(null);
    setErrorMessage(null);
    setOnServerMap({});
    setQueueError(null);

    const depth = depthOverride ?? maxDepth;
    const params = new URLSearchParams({
      from_movie_id: String(startMovie.tmdb_id),
      to_movie_id: String(targetMovie.tmdb_id),
      game_type: "cinechain",
      max_depth: String(depth),
    });
    if (runIdFromQuery) params.set("run_id", runIdFromQuery);
    const source = new EventSource(`/api/engine/bridge/stream?${params.toString()}`, {
      withCredentials: true,
    });
    sourceRef.current = source;

    source.addEventListener("progress", (event) => {
      setProgress(JSON.parse((event as MessageEvent).data));
    });
    source.addEventListener("result", (event) => {
      setResult(JSON.parse((event as MessageEvent).data));
      setActivePathIndex(0);
      setStatus("solved");
      closeSource();
    });
    source.addEventListener("exhausted", (event) => {
      setExhausted(JSON.parse((event as MessageEvent).data));
      setStatus("exhausted");
      closeSource();
    });
    source.addEventListener("error", (event) => {
      // The server sends a named "error" event on solve failure, but the
      // browser also fires a plain (dataless) "error" event on connection
      // loss - only the former carries a MessageEvent.data payload.
      const messageEvent = event as MessageEvent;
      if (messageEvent.data) {
        try {
          const parsed = JSON.parse(messageEvent.data);
          setErrorMessage(parsed.message ?? "Bridge solve failed.");
        } catch {
          setErrorMessage("Bridge solve failed.");
        }
      } else {
        setErrorMessage("Connection to the solver was lost.");
      }
      setStatus("error");
      closeSource();
    });
    source.addEventListener("done", () => {
      closeSource();
    });
  }

  function cancelSolve() {
    closeSource();
    setStatus("idle");
    setProgress(null);
  }

  function retryWithHigherDepth() {
    const nextDepth = Math.min(maxDepth + 1, MAX_DEPTH);
    setMaxDepth(nextDepth);
    startSolve(nextDepth);
  }

  const canQueue =
    !!selectedRun && !!result && !!tailStep && startMovie?.tmdb_id === tailStep.movie_id;

  // Path 1 (Shortest) + up to 2 alternates returned by the collision-layer
  // multi-path search - the active tab drives both the rendered path and queueing.
  const pathOptions: BridgeResult[] = result
    ? [
        { label: result.label ?? "Shortest", path: result.path, hops: result.hops, connections: result.connections },
        ...(result.alternate_paths ?? []),
      ]
    : [];
  const activePath = pathOptions[activePathIndex] ?? pathOptions[0];

  const targetAlreadyInRun =
    !!selectedRun && !!targetMovie && selectedRun.steps.some((s) => s.movie_id === targetMovie.tmdb_id);

  async function handleQueue() {
    if (!canQueue || !activePath || !selectedRun) return;
    setQueueing(true);
    setQueueError(null);
    try {
      for (let i = 1; i < activePath.path.length; i++) {
        const node = activePath.path[i];
        const connection = activePath.connections[i - 1];
        await createStep.mutateAsync({
          movie_id: node.movie_id,
          status: "planned",
          force: true,
          transition_metadata: connection
            ? {
                actor_id: connection.actor_id,
                actor_name: connection.actor_name,
                profile_path: connection.profile_path,
              }
            : null,
        });
      }
      navigate(`/runs/${selectedRun.id}`);
    } catch (err) {
      setQueueError(err instanceof ApiError ? err.message : "Failed to queue the bridge path.");
    } finally {
      setQueueing(false);
    }
  }

  const canSolve = !!startMovie && !!targetMovie && status !== "streaming";

  return (
    <div>
      <PageHeading title="Bridge Solver" subtitle="Find a path between any two films" />

      <div className="flex flex-col gap-5">
        {runIdFromQuery && selectedRun && (
          <div className="flex items-center gap-2.5 rounded-xl border border-accent/40 bg-accent/10 px-5 py-3 text-sm text-accent">
            <Link2 className="h-4 w-4 shrink-0" />
            Solving within context of <strong>{selectedRun.name}</strong> - excluding{" "}
            {selectedRun.steps.length} already watched movie{selectedRun.steps.length === 1 ? "" : "s"}.
          </div>
        )}

        <div className="rounded-xl border border-app-border bg-app-surface p-5">
          {activeRuns && activeRuns.length > 0 && !runIdFromQuery && (
            <div className="mb-4">
              <label className="mb-1.5 block text-xs font-medium uppercase tracking-wide text-zinc-500">
                Queue results to
              </label>
              <select
                value={selectedRunId}
                onChange={(e) => setSelectedRunId(e.target.value)}
                className="w-full rounded-md border border-app-border bg-app-bg px-3 py-2 text-sm text-zinc-200 focus:border-accent focus:outline-none sm:w-72"
              >
                {activeRuns.map((run) => (
                  <option key={run.id} value={run.id}>
                    {run.name}
                  </option>
                ))}
              </select>
            </div>
          )}

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <MovieSlot label="Starting film" movie={startMovie} onPick={setStartMovie} />
            <div>
              <MovieSlot label="Target film" movie={targetMovie} onPick={setTargetMovie} />
              {targetAlreadyInRun && (
                <p className="mt-1.5 flex items-center gap-1.5 text-xs font-medium text-amber-400">
                  <AlertTriangle className="h-3 w-3 shrink-0" />
                  This film is already logged in {selectedRun?.name}.
                </p>
              )}
            </div>
          </div>

          <div className="mt-4">
            <label className="mb-1.5 flex items-center justify-between text-xs font-medium uppercase tracking-wide text-zinc-500">
              <span>Max depth</span>
              <span className="text-zinc-300">{maxDepth} hops</span>
            </label>
            <input
              type="range"
              min={MIN_DEPTH}
              max={MAX_DEPTH}
              step={1}
              value={maxDepth}
              onChange={(e) => setMaxDepth(Number(e.target.value))}
              disabled={status === "streaming"}
              className="w-full accent-accent"
            />
          </div>

          <div className="mt-4 flex gap-2">
            {status === "streaming" ? (
              <button
                type="button"
                onClick={cancelSolve}
                className="flex items-center gap-1.5 rounded-md border border-app-border px-3.5 py-2 text-sm font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover"
              >
                <XCircle className="h-4 w-4" />
                Cancel
              </button>
            ) : (
              <button
                type="button"
                disabled={!canSolve}
                onClick={() => startSolve()}
                className="flex items-center gap-1.5 rounded-md bg-accent px-3.5 py-2 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-50"
              >
                <GitBranch className="h-4 w-4" />
                Solve Bridge
              </button>
            )}
          </div>
        </div>

        {status === "streaming" && (
          <div className="flex items-center gap-3 rounded-xl border border-app-border bg-app-surface px-5 py-4">
            <Loader2 className="h-5 w-5 shrink-0 animate-spin text-accent" />
            <div className="text-sm text-zinc-300">
              {progress ? (
                <>
                  Searching depth {progress.depth} from start and target (up to {maxDepth} hops
                  total) &middot; {progress.tmdb_calls} TMDB calls &middot; {progress.cache_hits}{" "}
                  cache hits &middot; {(progress.elapsed_ms / 1000).toFixed(1)}s
                  <p className="mt-0.5 text-xs text-zinc-500">
                    Frontier: {progress.frontier_forward} forward / {progress.frontier_backward}{" "}
                    backward
                  </p>
                </>
              ) : (
                "Starting search..."
              )}
            </div>
          </div>
        )}

        {status === "exhausted" && exhausted && (
          <div className="flex flex-col gap-3 rounded-xl border border-amber-900/50 bg-amber-950/20 px-5 py-4">
            <div className="flex items-center gap-2.5 text-sm text-amber-300">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              {exhausted.reason === "max_depth_reached"
                ? `No path found within ${maxDepth} hops.`
                : "TMDB call budget exhausted before a path was found."}
            </div>
            {maxDepth < MAX_DEPTH ? (
              <button
                type="button"
                onClick={retryWithHigherDepth}
                className="flex w-fit items-center gap-1.5 rounded-md border border-amber-800 px-3 py-1.5 text-sm font-medium text-amber-300 transition-colors hover:bg-amber-900/30"
              >
                <RotateCcw className="h-3.5 w-3.5" />
                Retry with Higher Depth
              </button>
            ) : (
              <p className="text-xs text-amber-400/80">Already at the maximum depth of {MAX_DEPTH}.</p>
            )}
          </div>
        )}

        {status === "error" && errorMessage && (
          <div className="flex items-center gap-2.5 rounded-xl border border-red-900/50 bg-red-950/20 px-5 py-4 text-sm text-red-300">
            <XCircle className="h-4 w-4 shrink-0" />
            {errorMessage}
          </div>
        )}

        {status === "solved" && result && (
          <div className="rounded-xl border border-app-border bg-app-surface p-5">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-2 text-sm font-medium text-zinc-200">
                <Sparkles className="h-4 w-4 text-accent" />
                Solved in {activePath?.hops ?? result.hops} hop
                {(activePath?.hops ?? result.hops) === 1 ? "" : "s"}
              </div>
              <button
                type="button"
                disabled={!canQueue || queueing}
                onClick={handleQueue}
                className="flex items-center gap-1.5 rounded-md bg-accent px-3.5 py-2 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-50"
                title={
                  canQueue
                    ? undefined
                    : "Select a run whose last logged film matches the starting film to queue this path."
                }
              >
                {queueing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Flag className="h-4 w-4" />}
                Queue Bridge to Active Run
              </button>
            </div>

            {pathOptions.length > 1 && (
              <div className="mb-4 flex flex-wrap gap-1.5">
                {pathOptions.map((option, index) => (
                  <button
                    key={`${option.label}-${index}`}
                    type="button"
                    onClick={() => setActivePathIndex(index)}
                    className={cn(
                      "rounded-full border px-3 py-1.5 text-xs font-medium transition-colors",
                      index === activePathIndex
                        ? "border-accent bg-accent/10 text-accent"
                        : "border-app-border text-zinc-500 hover:text-zinc-200",
                    )}
                  >
                    Path {index + 1} ({option.label})
                  </button>
                ))}
              </div>
            )}

            <div className="overflow-x-auto pb-2">
              <BridgePathView
                path={activePath?.path ?? result.path}
                connections={activePath?.connections ?? result.connections}
                onServerMap={onServerMap}
                onMovieClick={setPreviewMovieId}
              />
            </div>

            {queueError && <p className="mt-3 text-xs text-red-400">{queueError}</p>}
          </div>
        )}

        {previewMovieId !== null && (
          <MoviePreviewModal
            open={previewMovieId !== null}
            onClose={() => setPreviewMovieId(null)}
            movieId={previewMovieId}
          />
        )}

        {status === "idle" && !result && (
          <EmptyState
            icon={GitBranch}
            title="Pick a starting and target film"
            description="The solver searches a bidirectional cast graph and streams live progress as it works."
          />
        )}
      </div>
    </div>
  );
}

function MovieSlot({
  label,
  movie,
  onPick,
}: {
  label: string;
  movie: MovieSummary | null;
  onPick: (movie: MovieSummary) => void;
}) {
  const [editing, setEditing] = useState(false);

  return (
    <div>
      <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500">{label}</p>
      {movie && !editing ? (
        <div className="flex items-center gap-3 rounded-lg border border-app-border bg-app-bg p-3">
          <MoviePoster path={movie.poster_path} title={movie.title} className="w-10" />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-zinc-100">
              {movie.title}
              {movie.release_year && <span className="ml-1.5 text-zinc-500">({movie.release_year})</span>}
            </p>
          </div>
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="shrink-0 text-xs font-medium text-accent hover:underline"
          >
            Change
          </button>
        </div>
      ) : (
        <MovieSearchAutocomplete
          onSelect={(m) => {
            onPick(m);
            setEditing(false);
          }}
          placeholder={`Search for the ${label.toLowerCase()}...`}
        />
      )}
    </div>
  );
}
