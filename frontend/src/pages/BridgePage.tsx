import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  AlertTriangle,
  Clock,
  Flag,
  GitBranch,
  Link2,
  Loader2,
  RotateCcw,
  Search,
  Sparkles,
  XCircle,
} from "lucide-react";
import Breadcrumbs from "../components/Breadcrumbs";
import PageHeading from "../components/PageHeading";
import EmptyState from "../components/EmptyState";
import MoviePoster from "../components/MoviePoster";
import MovieSearchAutocomplete from "../components/MovieSearchAutocomplete";
import MoviePreviewModal from "../components/MoviePreviewModal";
import BridgePathView, { PathTagChips } from "../components/BridgePathView";
import BridgeSwapPanel, { type SwapState } from "../components/BridgeSwapPanel";
import { ApiError, api } from "../lib/api";
import { cn } from "../lib/cn";
import { connectionMetadata } from "../lib/connections";
import { useCreateStep, useEngines, useRun, useRuns } from "../lib/queries";
import type {
  BridgeResult,
  BridgeRoute,
  JellyfinItemSummary,
  MovieSummary,
  PathTagsResult,
  SwapCandidate,
  SwapNodeResult,
} from "../types/api";

interface ProgressEvent {
  depth: number;
  frontier_forward: number;
  frontier_backward: number;
  tmdb_calls: number;
  cache_hits: number;
  rate_limit_pauses?: number;
  elapsed_ms: number;
}

interface RateLimitedEvent {
  wait_seconds: number;
  rate_limit_pauses: number;
}

interface TimeoutEvent {
  depth_reached: number;
  tmdb_calls: number;
  elapsed_ms: number;
  message?: string;
}

interface ExhaustedEvent {
  reason: "budget_exceeded" | "max_depth_reached" | "constraint_impossible";
  message?: string;
  tmdb_calls: number;
  elapsed_ms: number;
}

type SolveStatus = "idle" | "streaming" | "solved" | "exhausted" | "timeout" | "error";

type DeeperState =
  | { status: "idle" }
  | { status: "streaming"; target: number; progress: ProgressEvent | null }
  | { status: "empty" | "timeout" | "error"; message: string };

function routesFromResult(result: BridgeResult, labelPrefix = ""): BridgeRoute[] {
  return [
    {
      label: `${labelPrefix}${result.label ?? "Shortest"}`,
      path: result.path,
      hops: result.hops,
      connections: result.connections,
      tags: result.tags ?? [],
    },
    ...(result.alternate_paths ?? []).map((alt) => ({
      label: `${labelPrefix}${alt.label}`,
      path: alt.path,
      hops: alt.hops,
      connections: alt.connections,
      tags: alt.tags ?? [],
    })),
  ];
}

const pathKey = (path: { movie_id: number }[]) => path.map((node) => node.movie_id).join(",");

const MIN_DEPTH = 2;
const MAX_DEPTH = 5;
const DEFAULT_DEPTH = 4;
// Mirrors the backend's DEEP_SEARCH_MAX_HOPS.
const DEEP_SEARCH_MAX_HOPS = 8;

export default function BridgePage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { data: activeRuns } = useRuns("active");
  const { data: engines } = useEngines();
  const [selectedRunId, setSelectedRunId] = useState("");
  const { data: selectedRun } = useRun(selectedRunId || undefined);
  const createStep = useCreateStep(selectedRunId);
  const runIdFromQuery = searchParams.get("run_id");
  // A run-scoped solve (?run_id=) follows that run's own rules: Chrono Climb, World
  // Passport and Auteur Relay constrain the search. Engines without a Bridge Solver
  // (and free-form solves) use the classic one.
  const runEngine = runIdFromQuery ? engines?.find((e) => e.game_type === selectedRun?.game_type) : undefined;
  const solveGameType = runEngine?.capabilities.includes("solve_bridge") ? runEngine.game_type : "cinechain";
  const solveEngine = engines?.find((e) => e.game_type === solveGameType);

  const [startMovie, setStartMovie] = useState<MovieSummary | null>(null);
  const [targetMovie, setTargetMovie] = useState<MovieSummary | null>(null);
  const [maxDepth, setMaxDepth] = useState(DEFAULT_DEPTH);

  const [status, setStatus] = useState<SolveStatus>("idle");
  const [progress, setProgress] = useState<ProgressEvent | null>(null);
  const [result, setResult] = useState<BridgeResult | null>(null);
  // Every displayable route (primary, alternates, deeper finds), edited in place by swaps.
  const [options, setOptions] = useState<BridgeRoute[]>([]);
  const [swap, setSwap] = useState<SwapState | null>(null);
  const [applyingSwap, setApplyingSwap] = useState(false);
  const [deeper, setDeeper] = useState<DeeperState>({ status: "idle" });
  // Highest "Search Deeper" hop target that already came back empty.
  const [deeperFloor, setDeeperFloor] = useState(0);
  const [activePathIndex, setActivePathIndex] = useState(0);
  const [previewMovieId, setPreviewMovieId] = useState<number | null>(null);
  const [exhausted, setExhausted] = useState<ExhaustedEvent | null>(null);
  const [timedOut, setTimedOut] = useState<TimeoutEvent | null>(null);
  const [rateLimitNotice, setRateLimitNotice] = useState<RateLimitedEvent | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [onServerMap, setOnServerMap] = useState<Record<number, JellyfinItemSummary>>({});

  const [queueing, setQueueing] = useState(false);
  const [queueError, setQueueError] = useState<string | null>(null);

  const sourceRef = useRef<EventSource | null>(null);
  const deeperSourceRef = useRef<EventSource | null>(null);
  const swapRequestRef = useRef(0);
  const lookedUpRef = useRef<Set<number>>(new Set());


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
      deeperSourceRef.current?.close();
    };
  }, []);

  // The pause banner only lives for the length of the backend's sleep.
  useEffect(() => {
    if (!rateLimitNotice) return;
    const timer = window.setTimeout(() => setRateLimitNotice(null), rateLimitNotice.wait_seconds * 1000 + 500);
    return () => window.clearTimeout(timer);
  }, [rateLimitNotice]);

  // Jellyfin "on server" badges (non-fatal on failure). Looks up only films not
  // seen yet, so switching tabs or swapping a node never refetches the rest.
  async function lookupOnServer(movieIds: number[]) {
    const missing = [...new Set(movieIds)].filter((id) => !lookedUpRef.current.has(id));
    if (missing.length === 0) return;
    missing.forEach((id) => lookedUpRef.current.add(id));
    try {
      const data = await api.post<Record<string, JellyfinItemSummary>>("/integrations/jellyfin/lookup", {
        tmdb_ids: missing,
      });
      setOnServerMap((prev) => {
        const next = { ...prev };
        for (const [key, value] of Object.entries(data)) next[Number(key)] = value;
        return next;
      });
    } catch {
      missing.forEach((id) => lookedUpRef.current.delete(id));
    }
  }

  useEffect(() => {
    void lookupOnServer(options.flatMap((option) => option.path.map((node) => node.movie_id)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [options]);

  function closeSource() {
    sourceRef.current?.close();
    sourceRef.current = null;
  }

  function closeDeeperSource() {
    deeperSourceRef.current?.close();
    deeperSourceRef.current = null;
  }

  function startSolve(depthOverride?: number) {
    if (!startMovie || !targetMovie) return;
    closeSource();
    closeDeeperSource();
    setStatus("streaming");
    setProgress(null);
    setResult(null);
    setOptions([]);
    setSwap(null);
    setDeeper({ status: "idle" });
    setDeeperFloor(0);
    lookedUpRef.current = new Set();
    setActivePathIndex(0);
    setExhausted(null);
    setTimedOut(null);
    setRateLimitNotice(null);
    setErrorMessage(null);
    setOnServerMap({});
    setQueueError(null);

    const depth = depthOverride ?? maxDepth;
    const params = new URLSearchParams({
      from_movie_id: String(startMovie.tmdb_id),
      to_movie_id: String(targetMovie.tmdb_id),
      game_type: solveGameType,
      max_depth: String(depth),
    });
    if (runIdFromQuery) params.set("run_id", runIdFromQuery);
    const source = new EventSource(`/api/engine/bridge/stream?${params.toString()}`, {
      withCredentials: true,
    });
    sourceRef.current = source;
    let finished = false;

    source.addEventListener("progress", (event) => {
      setProgress(JSON.parse((event as MessageEvent).data));
    });
    source.addEventListener("rate_limited", (event) => {
      setRateLimitNotice(JSON.parse((event as MessageEvent).data));
    });
    source.addEventListener("timeout", (event) => {
      finished = true;
      setTimedOut(JSON.parse((event as MessageEvent).data));
      setStatus("timeout");
      closeSource();
    });
    source.addEventListener("result", (event) => {
      finished = true;
      const parsed: BridgeResult = JSON.parse((event as MessageEvent).data);
      setResult(parsed);
      setOptions(routesFromResult(parsed));
      setActivePathIndex(0);
      setStatus("solved");
      closeSource();
    });
    source.addEventListener("exhausted", (event) => {
      finished = true;
      setExhausted(JSON.parse((event as MessageEvent).data));
      setStatus("exhausted");
      closeSource();
    });
    source.addEventListener("error", (event) => {
      finished = true;
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
      // Safety net: a stream that ends without a result/exhausted/timeout/error
      // event must never leave the spinner running forever.
      if (!finished) {
        setErrorMessage("The solver stopped without returning a result.");
        setStatus("error");
      }
    });
  }

  function cancelSolve() {
    closeSource();
    setStatus("idle");
    setProgress(null);
    setRateLimitNotice(null);
  }

  function retryWithHigherDepth() {
    const nextDepth = Math.min(maxDepth + 1, MAX_DEPTH);
    setMaxDepth(nextDepth);
    startSolve(nextDepth);
  }

  // Path 1 (Shortest) + up to 2 alternates returned by the collision-layer
  // multi-path search, plus any "Search Deeper" finds - the active tab drives
  // both the rendered path and queueing.
  const activePath: BridgeRoute | undefined = options[activePathIndex] ?? options[0];

  const canQueue =
    !!selectedRun && !!activePath && !!tailStep && startMovie?.tmdb_id === tailStep.movie_id;

  function selectPath(index: number) {
    swapRequestRef.current += 1; // drop any in-flight swap lookup for the old tab
    setSwap(null);
    setActivePathIndex(index);
  }

  async function handleSwap(index: number) {
    if (!activePath) return;
    if (swap?.index === index) {
      setSwap(null);
      return;
    }
    const connectionIn = activePath.connections[index - 1];
    const connectionOut = activePath.connections[index];
    if (!connectionIn || !connectionOut) return;

    const requestId = ++swapRequestRef.current;
    setSwap({ index, status: "loading" });
    const params = new URLSearchParams({
      movie_id: String(activePath.path[index].movie_id),
      from_movie_id: String(activePath.path[index - 1].movie_id),
      to_movie_id: String(activePath.path[index + 1].movie_id),
      actor_in_id: String(connectionIn.actor_id),
      actor_out_id: String(connectionOut.actor_id),
      exclude_movie_ids: activePath.path.map((node) => node.movie_id).join(","),
      game_type: solveGameType,
    });
    if (runIdFromQuery) params.set("run_id", runIdFromQuery);
    try {
      const data = await api.get<SwapNodeResult>(`/engine/bridge/swap-node?${params.toString()}`);
      if (requestId !== swapRequestRef.current) return;
      setSwap({ index, status: "ready", candidates: data.candidates, total: data.total });
      void lookupOnServer(data.candidates.map((candidate) => candidate.node.movie_id));
    } catch (err) {
      if (requestId !== swapRequestRef.current) return;
      setSwap({
        index,
        status: "error",
        message: err instanceof ApiError ? err.message : "Couldn't look up alternatives.",
      });
    }
  }

  async function applySwap(candidate: SwapCandidate) {
    if (!swap || !activePath) return;
    const { index } = swap;
    const routeIndex = activePathIndex;
    const newPath = activePath.path.map((node, i) => (i === index ? candidate.node : node));
    const newConnections = activePath.connections.map((connection, i) =>
      i === index - 1 ? candidate.connection_in : i === index ? candidate.connection_out : connection,
    );
    const key = pathKey(newPath);

    setSwap(null);
    // Tags describe the old path - drop them until the re-analysis returns.
    setOptions((prev) =>
      prev.map((route, i) =>
        i === routeIndex ? { ...route, path: newPath, connections: newConnections, tags: [] } : route,
      ),
    );

    setApplyingSwap(true);
    try {
      const analysis = await api.get<PathTagsResult>(
        `/engine/bridge/tags?movie_ids=${newPath.map((node) => node.movie_id).join(",")}`,
      );
      setOptions((prev) =>
        prev.map((route, i) =>
          i === routeIndex && pathKey(route.path) === key
            ? { ...route, path: analysis.nodes, tags: analysis.tags }
            : route,
        ),
      );
    } catch {
      /* tags are a nice-to-have - the swapped path itself is already applied */
    } finally {
      setApplyingSwap(false);
    }
  }

  const maxHopsFound = options.reduce((max, option) => Math.max(max, option.hops), 0);
  const nextDeeperHops = Math.max(maxHopsFound, deeperFloor) + 1;

  function searchDeeper() {
    if (!startMovie || !targetMovie || options.length === 0) return;
    if (nextDeeperHops > DEEP_SEARCH_MAX_HOPS) return;
    closeDeeperSource();
    const target = nextDeeperHops;
    setSwap(null);
    setDeeper({ status: "streaming", target, progress: null });

    // Same SSE endpoint, told to skip routes shallower than `target` hops. The
    // shallow levels are cache hits; every new level it reaches is cached.
    const params = new URLSearchParams({
      from_movie_id: String(startMovie.tmdb_id),
      to_movie_id: String(targetMovie.tmdb_id),
      game_type: solveGameType,
      max_depth: String(target),
      min_hops: String(target),
    });
    if (runIdFromQuery) params.set("run_id", runIdFromQuery);
    const source = new EventSource(`/api/engine/bridge/stream?${params.toString()}`, {
      withCredentials: true,
    });
    deeperSourceRef.current = source;
    let finished = false;
    const finish = (state: DeeperState) => {
      finished = true;
      setDeeper(state);
      closeDeeperSource();
    };

    source.addEventListener("progress", (event) => {
      const progressEvent: ProgressEvent = JSON.parse((event as MessageEvent).data);
      setDeeper((prev) => (prev.status === "streaming" ? { ...prev, progress: progressEvent } : prev));
    });
    source.addEventListener("result", (event) => {
      const parsed: BridgeResult = JSON.parse((event as MessageEvent).data);
      const known = new Set(options.map((option) => pathKey(option.path)));
      const fresh = routesFromResult(parsed, "Deeper - ").filter((route) => !known.has(pathKey(route.path)));
      if (fresh.length > 0) {
        setOptions((prev) => [...prev, ...fresh]);
        setActivePathIndex(options.length);
        finish({ status: "idle" });
      } else {
        setDeeperFloor(target);
        finish({ status: "empty", message: `No new ${target}+ hop routes turned up.` });
      }
    });
    source.addEventListener("exhausted", () => {
      setDeeperFloor(target);
      finish({
        status: "empty",
        message: `No routes of ${target}+ hops exist within this search depth. The levels it explored are now cached.`,
      });
    });
    source.addEventListener("timeout", (event) => {
      const timeout: TimeoutEvent = JSON.parse((event as MessageEvent).data);
      finish({
        status: "timeout",
        message: `Timed out at depth ${timeout.depth_reached} after ${(timeout.elapsed_ms / 1000).toFixed(0)}s. What it explored is cached, so searching again goes further.`,
      });
    });
    source.addEventListener("error", (event) => {
      const messageEvent = event as MessageEvent;
      let message = "Connection to the solver was lost.";
      if (messageEvent.data) {
        try {
          message = JSON.parse(messageEvent.data).message ?? "Deeper search failed.";
        } catch {
          message = "Deeper search failed.";
        }
      }
      finish({ status: "error", message });
    });
    source.addEventListener("done", () => {
      closeDeeperSource();
      if (!finished) setDeeper({ status: "error", message: "The solver stopped without returning a result." });
    });
  }

  function cancelDeeper() {
    closeDeeperSource();
    setDeeper({ status: "idle" });
  }

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
            ? connectionMetadata(connection, {
                from: connection.character_in_from,
                to: connection.character_in_to,
              })
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
      <Breadcrumbs items={[{ label: "Tools", to: "/tools" }, { label: "Bridge Solver" }]} />
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
          <div className="flex flex-col gap-3 rounded-xl border border-app-border bg-app-surface px-5 py-4">
            <div className="flex items-center gap-3">
              <Loader2 className="h-5 w-5 shrink-0 animate-spin text-accent" />
              <div className="min-w-0 text-sm text-zinc-300">
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
            {rateLimitNotice ? (
              <div
                role="status"
                className="flex items-center gap-2 rounded-md border border-amber-900/50 bg-amber-950/20 px-3 py-2 text-xs font-medium text-amber-300"
              >
                <Clock className="h-3.5 w-3.5 shrink-0" />
                API rate limit reached. Pausing search for a moment...
                <span className="ml-auto shrink-0 text-amber-400/70">
                  pause #{rateLimitNotice.rate_limit_pauses}
                </span>
              </div>
            ) : (
              !!progress?.rate_limit_pauses && (
                <p className="text-xs text-amber-400/70">
                  Paused {progress.rate_limit_pauses}x so far to stay under TMDB&apos;s rate limit.
                </p>
              )
            )}
          </div>
        )}

        {status === "timeout" && timedOut && (
          <div className="flex flex-col gap-3 rounded-xl border border-amber-900/50 bg-amber-950/20 px-5 py-4">
            <div className="flex items-center gap-2.5 text-sm font-medium text-amber-300">
              <Clock className="h-4 w-4 shrink-0" />
              Search timed out before a path could be found.
            </div>
            <p className="text-xs text-amber-400/80">
              Reached depth {timedOut.depth_reached} after {(timedOut.elapsed_ms / 1000).toFixed(0)}s and{" "}
              {timedOut.tmdb_calls} TMDB calls. Results so far are cached, so trying again resumes faster. You
              can also raise the solver timeout in Settings.
            </p>
            <button
              type="button"
              onClick={() => startSolve()}
              className="flex w-fit items-center gap-1.5 rounded-md border border-amber-800 px-3 py-1.5 text-sm font-medium text-amber-300 transition-colors hover:bg-amber-900/30"
            >
              <RotateCcw className="h-3.5 w-3.5" />
              Try Again
            </button>
          </div>
        )}

        {status === "exhausted" && exhausted && (
          <div className="flex flex-col gap-3 rounded-xl border border-amber-900/50 bg-amber-950/20 px-5 py-4">
            <div className="flex items-center gap-2.5 text-sm text-amber-300">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              {exhausted.reason === "constraint_impossible"
                ? (exhausted.message ?? "This run's rules make that bridge impossible.")
                : exhausted.reason === "max_depth_reached"
                  ? `No path found within ${maxDepth} hops.`
                  : "TMDB call budget exhausted before a path was found."}
            </div>
            {exhausted.reason === "constraint_impossible" ? null : maxDepth < MAX_DEPTH ? (
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

            {options.length > 1 && (
              <div className="mb-4 flex flex-wrap gap-1.5">
                {options.map((option, index) => (
                  <button
                    key={`${option.label}-${index}`}
                    type="button"
                    onClick={() => selectPath(index)}
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

            {activePath && activePath.tags.length > 0 && (
              <div className="mb-3">
                <PathTagChips tags={activePath.tags} />
              </div>
            )}

            <div className="overflow-x-auto pb-2">
              <BridgePathView
                path={activePath?.path ?? result.path}
                connections={activePath?.connections ?? result.connections}
                onServerMap={onServerMap}
                onMovieClick={setPreviewMovieId}
                onSwapNode={solveEngine?.capabilities.includes("bridge_swap") ? handleSwap : undefined}
                swapIndex={swap?.index ?? null}
              />
            </div>

            {swap && activePath && (
              <BridgeSwapPanel
                movieTitle={activePath.path[swap.index].title}
                actorNames={[
                  activePath.connections[swap.index - 1]?.actor_name ?? "the first actor",
                  activePath.connections[swap.index]?.actor_name ?? "the second actor",
                ]}
                state={swap}
                onServerMap={onServerMap}
                applying={applyingSwap}
                onPick={applySwap}
                onClose={() => {
                  swapRequestRef.current += 1;
                  setSwap(null);
                }}
              />
            )}

            {queueError && <p className="mt-3 text-xs text-red-400">{queueError}</p>}

            <div className="mt-5 border-t border-app-border pt-4">
              {deeper.status === "streaming" ? (
                <div className="flex flex-wrap items-center gap-3">
                  <Loader2 className="h-4 w-4 shrink-0 animate-spin text-accent" />
                  <p className="min-w-0 flex-1 text-sm text-zinc-300">
                    Searching for {deeper.target}+ hop routes
                    {deeper.progress && (
                      <span className="text-zinc-500">
                        {" "}
                        &middot; depth {deeper.progress.depth} &middot; {deeper.progress.tmdb_calls} TMDB calls
                        &middot; {deeper.progress.cache_hits} cache hits &middot;{" "}
                        {(deeper.progress.elapsed_ms / 1000).toFixed(1)}s
                      </span>
                    )}
                  </p>
                  <button
                    type="button"
                    onClick={cancelDeeper}
                    className="flex items-center gap-1.5 rounded-md border border-app-border px-3 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover"
                  >
                    <XCircle className="h-3.5 w-3.5" />
                    Cancel
                  </button>
                </div>
              ) : (
                <div className="flex flex-wrap items-center gap-3">
                  <button
                    type="button"
                    onClick={searchDeeper}
                    disabled={nextDeeperHops > DEEP_SEARCH_MAX_HOPS}
                    className="flex items-center gap-1.5 rounded-md border border-accent/50 px-3.5 py-2 text-sm font-medium text-accent transition-colors hover:bg-accent/10 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <Search className="h-4 w-4" />
                    Search Deeper
                  </button>
                  <p className="min-w-0 flex-1 text-xs text-zinc-500">
                    {nextDeeperHops > DEEP_SEARCH_MAX_HOPS
                      ? `Already searched to the ${DEEP_SEARCH_MAX_HOPS}-hop limit.`
                      : `Hunt for weirder routes of ${nextDeeperHops}+ hops. Everything it explores is cached, so repeat searches get faster.`}
                  </p>
                </div>
              )}
              {(deeper.status === "empty" || deeper.status === "timeout") && (
                <p className="mt-2 flex items-center gap-1.5 text-xs text-amber-400">
                  {deeper.status === "timeout" ? (
                    <Clock className="h-3.5 w-3.5 shrink-0" />
                  ) : (
                    <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                  )}
                  {deeper.message}
                </p>
              )}
              {deeper.status === "error" && <p className="mt-2 text-xs text-red-400">{deeper.message}</p>}
            </div>
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
