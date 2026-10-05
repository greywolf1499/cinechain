import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { AlertTriangle, GitBranch, Loader2, Lock } from "lucide-react";
import Modal from "./Modal";
import MoviePoster from "./MoviePoster";
import OnServerBadge, { onServerCardClass } from "./OnServerBadge";
import { api } from "../lib/api";
import { cn } from "../lib/cn";
import { allowsMovieRepeats, findExistingStepNumber, forcePricing } from "../lib/rules";
import { useCreateStep, useJellyfinLookup } from "../lib/queries";
import type {
  MovieSummary,
  RulesConfig,
  RunStep,
  ValidationResult,
} from "../types/api";

const GENRE_CHIPS = [
  { id: 28, name: "Action" },
  { id: 35, name: "Comedy" },
  { id: 18, name: "Drama" },
  { id: 27, name: "Horror" },
  { id: 10749, name: "Romance" },
  { id: 878, name: "Sci-Fi" },
];

type GuardStatus =
  | { state: "checking" }
  | { state: "no-connect"; result: ValidationResult };

export default function ForkInTheRoadModal({
  open,
  onClose,
  actorId,
  actorName,
  actorProfilePath = null,
  actorCharacterName = null,
  runId,
  frontierMovieId,
  frontierMovieTitle,
  isBrowsingFrontier,
  rulesConfig,
  steps,
  onLogged,
}: {
  open: boolean;
  onClose: () => void;
  actorId: number;
  actorName: string;
  actorProfilePath?: string | null;
  actorCharacterName?: string | null;
  runId: string;
  /** Always the run's true last-logged movie, regardless of which step's cast
   * `actorId` was clicked from - used to guard against accidental wildcard
   * burns when Fork-in-the-Road is opened from an OLDER (non-frontier) step. */
  frontierMovieId: number | undefined;
  frontierMovieTitle: string | undefined;
  /** True when `actorId` was clicked from the frontier's own cast strip - in
   * that case every one of the actor's films necessarily shares them with the
   * frontier, so the guard check can be skipped entirely (fast path). */
  isBrowsingFrontier: boolean;
  rulesConfig: RulesConfig;
  steps: RunStep[];
  onLogged?: () => void;
}) {
  const navigate = useNavigate();
  const [decade, setDecade] = useState<number | null>(null);
  const [genreId, setGenreId] = useState<number | null>(null);
  const [guardByMovieId, setGuardByMovieId] = useState<Record<number, GuardStatus>>({});
  const createStep = useCreateStep(runId);

  const allowRepeats = allowsMovieRepeats(rulesConfig);
  const pricing = forcePricing(rulesConfig);
  const wildcardsRemaining = pricing.remaining;
  const wildcardsExhausted = pricing.exhausted;

  const { data: allCredits } = useQuery({
    queryKey: ["people", actorId, "credits", "all"],
    queryFn: () => api.get<MovieSummary[]>(`/people/${actorId}/credits`),
    enabled: open,
  });

  const decades = useMemo(() => {
    const found = new Set<number>();
    for (const movie of allCredits ?? []) {
      if (movie.release_year) found.add(Math.floor(movie.release_year / 10) * 10);
    }
    return [...found].sort((a, b) => b - a);
  }, [allCredits]);

  const { data: credits, isLoading } = useQuery({
    queryKey: ["people", actorId, "credits", decade, genreId],
    queryFn: () => {
      const params = new URLSearchParams();
      if (decade) params.set("decade", String(decade));
      if (genreId) params.set("genre_id", String(genreId));
      const qs = params.toString();
      return api.get<MovieSummary[]>(`/people/${actorId}/credits${qs ? `?${qs}` : ""}`);
    },
    enabled: open,
  });

  const tmdbIds = credits?.map((m) => m.tmdb_id) ?? [];
  const { data: jellyfinStatus } = useJellyfinLookup(tmdbIds);

  async function handleLog(
    movie: MovieSummary,
    connection?: ValidationResult["connections"][number],
  ) {
    await createStep.mutateAsync({
      movie_id: movie.tmdb_id,
      force: true,
      transition_metadata: connection
        ? {
            actor_id: connection.actor_id,
            actor_name: connection.actor_name,
            profile_path: connection.profile_path,
            character_in_from: connection.character_in_from,
            character_in_to: connection.character_in_to,
          }
        : {
            actor_id: actorId,
            actor_name: actorName,
            profile_path: actorProfilePath,
            character_in_from: actorCharacterName,
          },
    });
    onLogged?.();
    onClose();
  }

  /** Only used when NOT browsing the frontier's own cast - checks the true
   * frontier link before allowing a log, so a connected pick logs straight
   * through and a disconnected one surfaces the wildcard-jump guard instead. */
  async function checkFrontierLink(movie: MovieSummary) {
    if (frontierMovieId === undefined) return;
    setGuardByMovieId((prev) => ({ ...prev, [movie.tmdb_id]: { state: "checking" } }));
    try {
      const result = await api.post<ValidationResult>("/engine/validate", {
        game_type: "cinechain",
        from_movie_id: frontierMovieId,
        to_movie_id: movie.tmdb_id,
      });
      if (result.valid) {
        await handleLog(movie, result.connections[0]);
        return;
      }
      setGuardByMovieId((prev) => ({ ...prev, [movie.tmdb_id]: { state: "no-connect", result } }));
    } catch {
      setGuardByMovieId((prev) => ({
        ...prev,
        [movie.tmdb_id]: {
          state: "no-connect",
          result: { valid: false, reason: "Could not check this connection.", connections: [] },
        },
      }));
    }
  }

  function goBuildBridge(movieId: number) {
    if (frontierMovieId === undefined) return;
    navigate(`/tools/bridge?from=${frontierMovieId}&to=${movieId}`);
  }

  return (
    <Modal open={open} onClose={onClose} title={`${actorName}'s filmography`} widthClassName="max-w-lg">
      <div className="mb-3 flex flex-wrap gap-1.5">
        <Chip active={decade === null} onClick={() => setDecade(null)}>
          All decades
        </Chip>
        {decades.map((d) => (
          <Chip key={d} active={decade === d} onClick={() => setDecade(decade === d ? null : d)}>
            {d}s
          </Chip>
        ))}
      </div>
      <div className="mb-4 flex flex-wrap gap-1.5">
        <Chip active={genreId === null} onClick={() => setGenreId(null)}>
          All genres
        </Chip>
        {GENRE_CHIPS.map((g) => (
          <Chip key={g.id} active={genreId === g.id} onClick={() => setGenreId(genreId === g.id ? null : g.id)}>
            {g.name}
          </Chip>
        ))}
      </div>

      {isLoading && (
        <div className="flex justify-center py-8">
          <Loader2 className="h-5 w-5 animate-spin text-zinc-600" />
        </div>
      )}

      <div className="flex flex-col gap-2">
        {credits?.length === 0 && !isLoading && (
          <p className="py-6 text-center text-sm text-zinc-500">No films match these filters.</p>
        )}
        {credits?.map((movie) => {
          const existingStepNumber = findExistingStepNumber(steps, movie.tmdb_id);
          const isLockedDuplicate = existingStepNumber !== null && !allowRepeats;
          const guard = guardByMovieId[movie.tmdb_id];

          return (
            <div
              key={movie.tmdb_id}
              className={cn(
                "flex flex-col gap-2 rounded-md border p-2",
                jellyfinStatus?.[String(movie.tmdb_id)]?.on_server
                  ? onServerCardClass(true)
                  : "border-app-border bg-app-bg",
              )}
            >
              <div className="flex items-center gap-3">
                <MoviePoster path={movie.poster_path} title={movie.title} className="w-10" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-zinc-100">
                    {movie.title}
                    {movie.release_year && (
                      <span className="ml-1.5 text-zinc-500">({movie.release_year})</span>
                    )}
                  </p>
                  <div className="mt-1">
                    <OnServerBadge onServer={jellyfinStatus?.[String(movie.tmdb_id)]?.on_server} />
                  </div>
                </div>

                {isLockedDuplicate ? null : isBrowsingFrontier ? (
                  <button
                    type="button"
                    disabled={createStep.isPending}
                    onClick={() => handleLog(movie)}
                    className="shrink-0 rounded-md bg-accent px-2.5 py-1.5 text-xs font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:opacity-60"
                  >
                    Log as next
                  </button>
                ) : !guard ? (
                  <button
                    type="button"
                    onClick={() => checkFrontierLink(movie)}
                    className="shrink-0 rounded-md bg-accent px-2.5 py-1.5 text-xs font-semibold text-zinc-950 transition-colors hover:bg-accent-strong"
                  >
                    Log as next
                  </button>
                ) : guard.state === "checking" ? (
                  <span className="flex shrink-0 items-center gap-1.5 text-xs text-zinc-500">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" /> Checking...
                  </span>
                ) : null}
              </div>

              {isLockedDuplicate && (
                <p className="flex items-center gap-1.5 text-[11px] font-medium text-red-400">
                  <Lock className="h-3 w-3" /> Already in Run (Step {existingStepNumber})
                </p>
              )}

              {guard?.state === "no-connect" && (
                <div
                  className={cn(
                    "rounded-md border p-2 text-[11px]",
                    wildcardsExhausted
                      ? "border-red-900/50 bg-red-950/30"
                      : "border-amber-900/50 bg-amber-950/30",
                  )}
                >
                  {wildcardsExhausted ? (
                    <p className="flex items-center gap-1.5 font-medium text-red-400">
                      <Lock className="h-3 w-3" /> No link to frontier & 0 {pricing.plural} remaining
                    </p>
                  ) : (
                    <p className="flex items-center gap-1.5 font-medium text-amber-400">
                      <AlertTriangle className="h-3 w-3" />
                      Does not connect to current frontier ({frontierMovieTitle ?? "unknown"}). Adding
                      this will consume 1 of{" "}
                      {wildcardsRemaining === -1 ? "unlimited" : wildcardsRemaining} remaining {pricing.plural}.
                    </p>
                  )}
                  <div className="mt-2 flex gap-2">
                    {!wildcardsExhausted && (
                      <button
                        type="button"
                        disabled={createStep.isPending}
                        onClick={() => handleLog(movie)}
                        className="rounded-md bg-accent px-2.5 py-1.5 text-[11px] font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:opacity-60"
                      >
                        {pricing.confirmLabel}
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => goBuildBridge(movie.tmdb_id)}
                      className="flex items-center gap-1 rounded-md border border-app-border px-2.5 py-1.5 text-[11px] font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover"
                    >
                      <GitBranch className="h-3 w-3" /> Build Bridge to Here
                    </button>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </Modal>
  );
}

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-full border px-2.5 py-1 text-xs font-medium transition-colors",
        active
          ? "border-accent bg-accent/10 text-accent"
          : "border-app-border text-zinc-500 hover:text-zinc-200",
      )}
    >
      {children}
    </button>
  );
}
