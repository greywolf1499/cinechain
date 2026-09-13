import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2, Search, User } from "lucide-react";
import Modal from "./Modal";
import MoviePoster from "./MoviePoster";
import OnServerBadge from "./OnServerBadge";
import RatingBadges from "./RatingBadges";
import { api } from "../lib/api";
import { cn } from "../lib/cn";
import { profileUrl } from "../lib/tmdbImage";
import { allowsMovieRepeats } from "../lib/rules";
import { useCreateStep, useDiscoverCandidates } from "../lib/queries";
import type {
  CastMember,
  DiscoveryCandidate,
  DiscoveryConnection,
  GenreOut,
  JellyfinItemSummary,
  MovieRatings,
  RulesConfig,
  RunStep,
} from "../types/api";

type CoStarMode = "or" | "and";
type SortBy = "year" | "popularity" | "imdb" | "rt";

const DECADE_PILLS: { key: string; label: string }[] = [
  { key: "all", label: "All" },
  { key: "early", label: "60s & earlier" },
  { key: "1970", label: "70s" },
  { key: "1980", label: "80s" },
  { key: "1990", label: "90s" },
  { key: "2000", label: "00s" },
  { key: "2010", label: "10s" },
  { key: "2020", label: "20s" },
];

function matchesDecade(year: number | null, key: string): boolean {
  if (key === "all") return true;
  if (year == null) return false;
  if (key === "early") return year < 1970;
  return Math.floor(year / 10) * 10 === Number(key);
}

export default function PickNextHub({
  open,
  onClose,
  runId,
  frontierStep,
  rulesConfig,
}: {
  open: boolean;
  onClose: () => void;
  runId: string;
  frontierStep: RunStep;
  rulesConfig: RulesConfig;
}) {
  const [mode, setMode] = useState<CoStarMode>("or");
  const [selectedActorIds, setSelectedActorIds] = useState<Set<number>>(new Set());
  const [search, setSearch] = useState("");
  const [genreId, setGenreId] = useState<number | null>(null);
  const [decadeKey, setDecadeKey] = useState("all");
  const [sortBy, setSortBy] = useState<SortBy>("year");
  const [pendingMovieId, setPendingMovieId] = useState<number | null>(null);

  const { data: cast } = useQuery({
    queryKey: ["movies", frontierStep.movie_id, "cast"],
    queryFn: () => api.get<CastMember[]>(`/movies/${frontierStep.movie_id}/cast`),
    enabled: open,
  });
  const { data: genres } = useQuery({
    queryKey: ["movies", "genres"],
    queryFn: () => api.get<GenreOut[]>("/movies/genres"),
    enabled: open,
  });
  const { data: candidates, isLoading } = useDiscoverCandidates(
    runId,
    open ? frontierStep.movie_id : undefined,
    mode,
  );

  const tmdbIds = candidates?.map((c) => c.movie_id) ?? [];
  const { data: jellyfinStatus } = useQuery({
    queryKey: ["jellyfin", "lookup", tmdbIds],
    queryFn: () =>
      api.post<Record<string, JellyfinItemSummary>>("/integrations/jellyfin/lookup", {
        tmdb_ids: tmdbIds,
      }),
    enabled: tmdbIds.length > 0,
  });
  const { data: ratingsMap } = useQuery({
    queryKey: ["movies", "ratings", "bulk", tmdbIds],
    queryFn: () =>
      api.post<Record<string, MovieRatings | null>>("/movies/ratings/bulk", {
        tmdb_ids: tmdbIds,
      }),
    enabled: tmdbIds.length > 0,
  });

  const createStep = useCreateStep(runId);
  const allowRepeats = allowsMovieRepeats(rulesConfig);

  function ratingSortValue(candidate: DiscoveryCandidate, key: "imdb" | "rt"): number {
    const ratings = ratingsMap?.[String(candidate.movie_id)];
    if (!ratings) return -1;
    const raw = key === "imdb" ? ratings.imdb_rating : ratings.rotten_tomatoes;
    if (!raw) return -1;
    const parsed = Number.parseFloat(raw.replace("%", ""));
    return Number.isNaN(parsed) ? -1 : parsed;
  }

  const filtered = useMemo(() => {
    let list = candidates ?? [];

    if (selectedActorIds.size > 0) {
      list = list.filter((candidate) => {
        const connectedIds = new Set(candidate.connections.map((c) => c.actor_id));
        return mode === "and"
          ? [...selectedActorIds].every((id) => connectedIds.has(id))
          : [...selectedActorIds].some((id) => connectedIds.has(id));
      });
    }
    if (genreId !== null) {
      list = list.filter((candidate) => candidate.genre_ids.includes(genreId));
    }
    if (decadeKey !== "all") {
      list = list.filter((candidate) => matchesDecade(candidate.release_year, decadeKey));
    }
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      list = list.filter(
        (candidate) =>
          candidate.title.toLowerCase().includes(q) ||
          candidate.connections.some(
            (c) =>
              c.character_in_candidate?.toLowerCase().includes(q) ||
              c.character_in_frontier?.toLowerCase().includes(q),
          ),
      );
    }

    return [...list].sort((a, b) => {
      if (sortBy === "year") return (b.release_year ?? 0) - (a.release_year ?? 0);
      if (sortBy === "popularity") return (b.popularity ?? 0) - (a.popularity ?? 0);
      return ratingSortValue(b, sortBy === "imdb" ? "imdb" : "rt") - ratingSortValue(a, sortBy === "imdb" ? "imdb" : "rt");
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidates, selectedActorIds, mode, genreId, decadeKey, search, sortBy, ratingsMap]);

  function toggleActor(actorId: number) {
    setSelectedActorIds((prev) => {
      const next = new Set(prev);
      if (next.has(actorId)) next.delete(actorId);
      else next.add(actorId);
      return next;
    });
  }

  async function handleAdd(candidate: DiscoveryCandidate, watched: boolean) {
    setPendingMovieId(candidate.movie_id);
    const connection = candidate.connections[0];
    try {
      await createStep.mutateAsync({
        movie_id: candidate.movie_id,
        force: true,
        status: watched ? "watched" : "planned",
        watched_at: watched ? new Date().toISOString() : null,
        transition_metadata: connection
          ? {
              actor_id: connection.actor_id,
              actor_name: connection.actor_name,
              profile_path: connection.profile_path,
              character_in_from: connection.character_in_frontier,
              character_in_to: connection.character_in_candidate,
            }
          : null,
      });
      onClose();
    } finally {
      setPendingMovieId(null);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Pick Next Movie" widthClassName="max-w-5xl">
      <div className="flex flex-col gap-4">
        {cast && cast.length > 0 && (
          <div className="flex gap-2 overflow-x-auto pb-1">
            {cast.map((member) => (
              <button
                key={member.actor_id}
                type="button"
                onClick={() => toggleActor(member.actor_id)}
                title={member.name}
                className={cn(
                  "flex w-16 shrink-0 flex-col items-center gap-1 rounded-lg border px-1.5 py-1.5 text-center transition-colors",
                  selectedActorIds.has(member.actor_id)
                    ? "border-accent bg-accent/10"
                    : "border-app-border hover:border-zinc-600",
                )}
              >
                {member.profile_path ? (
                  <img
                    src={profileUrl(member.profile_path) ?? undefined}
                    alt={member.name}
                    className="h-10 w-10 rounded-full object-cover"
                  />
                ) : (
                  <div className="flex h-10 w-10 items-center justify-center rounded-full bg-app-surface-hover text-zinc-500">
                    <User className="h-4 w-4" />
                  </div>
                )}
                <span className="line-clamp-2 text-[10px] leading-tight text-zinc-300">
                  {member.name}
                </span>
              </button>
            ))}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <div className="inline-flex rounded-full border border-app-border bg-app-surface p-1 text-xs font-medium">
            <button
              type="button"
              onClick={() => setMode("or")}
              className={cn(
                "rounded-full px-3 py-1.5 transition-colors",
                mode === "or" ? "bg-accent text-zinc-950" : "text-zinc-400 hover:text-zinc-200",
              )}
            >
              OR - Any shared actor
            </button>
            <button
              type="button"
              onClick={() => setMode("and")}
              className={cn(
                "rounded-full px-3 py-1.5 transition-colors",
                mode === "and" ? "bg-accent text-zinc-950" : "text-zinc-400 hover:text-zinc-200",
              )}
            >
              AND - Co-stars reunite
            </button>
          </div>

          <div className="flex min-w-[180px] flex-1 items-center gap-2 rounded-md border border-app-border bg-app-bg px-3 py-2">
            <Search className="h-4 w-4 shrink-0 text-zinc-500" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search title or character..."
              className="w-full bg-transparent text-sm text-zinc-100 placeholder:text-zinc-600 focus:outline-none"
            />
          </div>

          <select
            value={genreId ?? ""}
            onChange={(e) => setGenreId(e.target.value ? Number(e.target.value) : null)}
            className="rounded-md border border-app-border bg-app-bg px-2.5 py-2 text-sm text-zinc-200 focus:border-accent focus:outline-none"
          >
            <option value="">All Genres</option>
            {genres?.map((genre) => (
              <option key={genre.id} value={genre.id}>
                {genre.name}
              </option>
            ))}
          </select>

          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as SortBy)}
            className="rounded-md border border-app-border bg-app-bg px-2.5 py-2 text-sm text-zinc-200 focus:border-accent focus:outline-none"
          >
            <option value="year">Sort: Newest First</option>
            <option value="popularity">Sort: Popularity</option>
            <option value="imdb">Sort: IMDb Rating (Highest First)</option>
            <option value="rt">Sort: Rotten Tomatoes (Highest First)</option>
          </select>
        </div>

        <div className="flex flex-wrap gap-1.5">
          {DECADE_PILLS.map((pill) => (
            <button
              key={pill.key}
              type="button"
              onClick={() => setDecadeKey(pill.key)}
              className={cn(
                "rounded-full border px-2.5 py-1 text-xs font-medium transition-colors",
                decadeKey === pill.key
                  ? "border-accent bg-accent/10 text-accent"
                  : "border-app-border text-zinc-500 hover:text-zinc-200",
              )}
            >
              {pill.label}
            </button>
          ))}
        </div>

        {isLoading && (
          <div className="flex justify-center py-12">
            <Loader2 className="h-6 w-6 animate-spin text-zinc-600" />
          </div>
        )}

        {!isLoading && filtered.length === 0 && (
          <p className="py-10 text-center text-sm text-zinc-500">
            No films match these filters.
          </p>
        )}

        {!isLoading && filtered.length > 0 && (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4">
            {filtered.map((candidate) => (
              <CandidateCard
                key={candidate.movie_id}
                candidate={candidate}
                genres={genres}
                ratings={ratingsMap?.[String(candidate.movie_id)]}
                allowRepeats={allowRepeats}
                onServer={jellyfinStatus?.[String(candidate.movie_id)]?.on_server}
                pending={pendingMovieId === candidate.movie_id && createStep.isPending}
                onQueue={() => handleAdd(candidate, false)}
                onLogWatched={() => handleAdd(candidate, true)}
              />
            ))}
          </div>
        )}
      </div>
    </Modal>
  );
}

function CandidateCard({
  candidate,
  genres,
  ratings,
  allowRepeats,
  onServer,
  pending,
  onQueue,
  onLogWatched,
}: {
  candidate: DiscoveryCandidate;
  genres: GenreOut[] | undefined;
  ratings: MovieRatings | null | undefined;
  allowRepeats: boolean;
  onServer: boolean | null | undefined;
  pending: boolean;
  onQueue: () => void;
  onLogWatched: () => void;
}) {
  const genreNames = candidate.genre_ids
    .map((id) => genres?.find((g) => g.id === id)?.name)
    .filter((name): name is string => !!name);
  const isLockedDuplicate = candidate.already_in_run && !allowRepeats;

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-app-border bg-app-surface p-2.5">
      <div className="relative">
        <MoviePoster path={candidate.poster_path} title={candidate.title} className="w-full" />
        <div className="absolute left-1 top-1">
          <OnServerBadge onServer={onServer} />
        </div>
      </div>

      <div>
        <p className="line-clamp-2 text-xs font-medium text-zinc-100">{candidate.title}</p>
        <p className="text-[10px] text-zinc-500">{candidate.release_year ?? "—"}</p>
        <RatingBadges ratings={ratings} />
        {genreNames.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1">
            {genreNames.slice(0, 2).map((name) => (
              <span
                key={name}
                className="rounded-full bg-app-surface-hover px-1.5 py-0.5 text-[9px] text-zinc-400"
              >
                {name}
              </span>
            ))}
          </div>
        )}
      </div>

      <ConnectionBadge connections={candidate.connections} />

      {candidate.already_in_run && (
        <span
          className={cn(
            "rounded-md px-2 py-1 text-center text-[10px] font-medium",
            isLockedDuplicate ? "bg-red-950 text-red-300" : "bg-app-surface-hover text-zinc-500",
          )}
        >
          {isLockedDuplicate ? "Locked: " : ""}Already in Run (Step {candidate.existing_step_number})
        </span>
      )}

      {isLockedDuplicate ? null : (
        <div className="flex gap-1.5">
          <button
            type="button"
            disabled={pending}
            onClick={onQueue}
            className="flex flex-1 items-center justify-center gap-1 rounded-md border border-app-border px-2 py-1.5 text-[10px] font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover disabled:opacity-60"
          >
            {pending && <Loader2 className="h-3 w-3 animate-spin" />}
            Queue Up Next
          </button>
          <button
            type="button"
            disabled={pending}
            onClick={onLogWatched}
            className="flex flex-1 items-center justify-center gap-1 rounded-md bg-accent px-2 py-1.5 text-[10px] font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:opacity-60"
          >
            {pending && <Loader2 className="h-3 w-3 animate-spin" />}
            Log Watched
          </button>
        </div>
      )}
    </div>
  );
}

function ConnectionBadge({ connections }: { connections: DiscoveryConnection[] }) {
  if (connections.length === 0) return null;

  if (connections.length === 1) {
    const connection = connections[0];
    return (
      <div className="flex items-center gap-1.5 rounded-md bg-app-bg px-2 py-1">
        {connection.profile_path ? (
          <img
            src={profileUrl(connection.profile_path) ?? undefined}
            alt={connection.actor_name}
            className="h-5 w-5 shrink-0 rounded-full object-cover"
          />
        ) : (
          <User className="h-4 w-4 shrink-0 text-zinc-500" />
        )}
        <div className="min-w-0">
          <p className="truncate text-[10px] font-medium text-zinc-300">{connection.actor_name}</p>
          {connection.character_in_candidate && (
            <p className="truncate text-[9px] text-zinc-500">as {connection.character_in_candidate}</p>
          )}
        </div>
      </div>
    );
  }

  return (
    <div
      className="rounded-md bg-accent/10 px-2 py-1 text-[10px] font-medium text-accent"
      title={connections.map((c) => c.actor_name).join(", ")}
    >
      {connections.length} Shared Actors: {connections.map((c) => c.actor_name).join(" & ")}
    </div>
  );
}
