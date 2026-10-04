import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  ArrowDown,
  ArrowDownAZ,
  ArrowLeft,
  ArrowUp,
  ArrowUpAZ,
  Check,
  Clapperboard,
  GitBranch,
  Loader2,
  Lock,
  Search,
  User,
  X,
} from "lucide-react";
import Modal from "./Modal";
import ColorSwatch, { SemanticMatchBadge } from "./ColorSwatch";
import ModifierChips from "./ModifierChips";
import PitchButton from "./PitchButton";
import MoviePoster from "./MoviePoster";
import AcquisitionControl from "./AcquisitionControl";
import MovieTagline from "./MovieTagline";
import OnServerBadge, { onServerCardClass } from "./OnServerBadge";
import RatingBadges from "./RatingBadges";
import { CanonBadgeList } from "./CanonBadge";
import { api } from "../lib/api";
import { cn } from "../lib/cn";
import { profileUrl } from "../lib/tmdbImage";
import { isoToFlagEmoji, parseOriginCountries } from "../lib/countries";
import { countryName } from "../lib/countryNames";
import { gameModeStyle, usesCastLinks } from "../lib/gameModes";
import { connectionMetadata } from "../lib/connections";
import { allowsMovieRepeats, findExistingStepNumber } from "../lib/rules";
import { SIDE_LABELS } from "../lib/tunnel";
import {
  useCanonBadgesBulk,
  useCreateStep,
  useOfferFork,
  useDiscoverCandidates,
  useMovieDetail,
  useRunConstraint,
} from "../lib/queries";
import type {
  TunnelSide,
  CastMember,
  DiscoveryCandidate,
  DiscoveryConnection,
  GenreOut,
  JellyfinItemSummary,
  MovieRatings,
  MovieSummary,
  RulesConfig,
  RunStep,
  ValidationResult,
} from "../types/api";

type CoStarMode = "or" | "and";
type SortBy = "match" | "year" | "popularity" | "imdb" | "rt";
type SortDir = "asc" | "desc";

/** Navigation stack for the in-dialog drill-down (grid -> movie -> actor ->
 * movie -> ...), eliminating stacked modal dialogs entirely. */
type Screen =
  | { kind: "grid"; label: "Pick Next" }
  | {
      kind: "movie";
      label: string;
      movieId: number;
      posterPath: string | null;
      /** True when this film is already KNOWN to connect to the frontier
       * (reached directly from the discovery grid, or via an actor who was
       * themselves guaranteed connected) - skips the wildcard guard entirely. */
      guaranteedConnected: boolean;
      /** Actor ids on THIS film already confirmed to also be in the frontier's
       * cast - drilling into one of them keeps guaranteedConnected=true one
       * level deeper, since that actor's presence alone proves the link. */
      safeActorIds: Set<number>;
      directConnection?: DiscoveryConnection;
    }
  | {
      kind: "actor";
      label: string;
      actorId: number;
      actorName: string;
      profilePath: string | null;
      characterName?: string | null;
      guaranteedConnected: boolean;
    };

type GuardStatus =
  | { state: "checking" }
  | { state: "no-connect"; result: ValidationResult };

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
  steps,
  gameType = "cinechain",
  tunnelSide,
  forkMode = false,
}: {
  open: boolean;
  onClose: () => void;
  runId: string;
  frontierStep: RunStep;
  rulesConfig: RulesConfig;
  steps: RunStep[];
  gameType?: string;
  /** Meet in the Middle: the end of the tunnel the picked film will extend. */
  tunnelSide?: TunnelSide;
  /** Blind Fork: select three films to offer the partner instead of logging one. */
  forkMode?: boolean;
}) {
  const castLinked = usesCastLinks(gameType, rulesConfig);
  const [stack, setStack] = useState<Screen[]>([{ kind: "grid", label: "Pick Next" }]);
  const activeScreen = stack[stack.length - 1];

  function pushScreen(screen: Screen) {
    setStack((prev) => [...prev, screen]);
  }
  function popScreen() {
    setStack((prev) => (prev.length > 1 ? prev.slice(0, -1) : prev));
  }
  function jumpTo(index: number) {
    setStack((prev) => prev.slice(0, index + 1));
  }
  function handleClose() {
    setStack([{ kind: "grid", label: "Pick Next" }]);
    onClose();
  }

  return (
    <Modal
      open={open}
      onClose={handleClose}
      title={
        forkMode
          ? "Blind Fork - offer three films"
          : tunnelSide
            ? `Pick Next Movie - extend ${SIDE_LABELS[tunnelSide]}'s side`
            : "Pick Next Movie"
      }
      widthClassName="max-w-5xl"
    >
      <div className="flex flex-col gap-4">
        <div className="flex items-center gap-2 border-b border-app-border pb-3 text-xs">
          {stack.length > 1 && (
            <button
              type="button"
              onClick={popScreen}
              className="flex shrink-0 items-center gap-1 rounded-md border border-app-border px-2 py-1 font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              Back
            </button>
          )}
          <div className="flex min-w-0 flex-wrap items-center gap-1 text-zinc-500">
            {stack.map((screen, index) => (
              <span key={index} className="flex items-center gap-1">
                {index > 0 && <span>&gt;</span>}
                <button
                  type="button"
                  onClick={() => jumpTo(index)}
                  disabled={index === stack.length - 1}
                  className={cn(
                    "truncate rounded px-1 transition-colors",
                    index === stack.length - 1
                      ? "font-semibold text-zinc-200"
                      : "hover:text-zinc-200",
                  )}
                >
                  {screen.label}
                </button>
              </span>
            ))}
          </div>
        </div>

        {activeScreen.kind === "grid" && (
          <DiscoveryGrid
            runId={runId}
            frontierStep={frontierStep}
            rulesConfig={rulesConfig}
            gameType={gameType}
            castLinked={castLinked}
            tunnelSide={tunnelSide}
            forkMode={forkMode}
            onOpenMovie={(screen) => pushScreen(screen)}
            onClose={handleClose}
          />
        )}

        {activeScreen.kind === "movie" && (
          <MovieScreenView
            key={activeScreen.movieId}
            screen={activeScreen}
            runId={runId}
            frontierMovieId={frontierStep.movie_id}
            frontierMovieTitle={frontierStep.movie_title}
            rulesConfig={rulesConfig}
            steps={steps}
            castLinked={castLinked}
            tunnelSide={tunnelSide}
            onOpenActor={(screen) => pushScreen(screen)}
            onClose={handleClose}
          />
        )}

        {activeScreen.kind === "actor" && (
          <ActorScreenView
            key={activeScreen.actorId}
            screen={activeScreen}
            onOpenMovie={(screen) => pushScreen(screen)}
          />
        )}
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Screen 1: the existing discovery grid (actor filters, AND/OR, sort, cards)
// ---------------------------------------------------------------------------
function DiscoveryGrid({
  runId,
  frontierStep,
  rulesConfig,
  gameType,
  castLinked,
  tunnelSide,
  forkMode,
  onOpenMovie,
  onClose,
}: {
  runId: string;
  frontierStep: RunStep;
  rulesConfig: RulesConfig;
  gameType: string;
  /** False for standalone modes: no actor network, the card shows the mode's own mechanic. */
  castLinked: boolean;
  tunnelSide?: TunnelSide;
  forkMode?: boolean;
  onOpenMovie: (screen: Screen & { kind: "movie" }) => void;
  onClose: () => void;
}) {
  const [offered, setOffered] = useState<DiscoveryCandidate[]>([]);
  const offerFork = useOfferFork(runId);
  const [mode, setMode] = useState<CoStarMode>("or");
  const [selectedActorIds, setSelectedActorIds] = useState<Set<number>>(new Set());
  const [search, setSearch] = useState("");
  const [genreId, setGenreId] = useState<number | null>(null);
  const [decadeKey, setDecadeKey] = useState("all");
  const defaultSort: SortBy = castLinked ? "year" : "match";
  const [sortBy, setSortBy] = useState<SortBy>(defaultSort);
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [pendingMovieId, setPendingMovieId] = useState<number | null>(null);

  function clearFilters() {
    setSelectedActorIds(new Set());
    setSearch("");
    setGenreId(null);
    setDecadeKey("all");
    setSortBy(defaultSort);
    setSortDir("desc");
  }

  const hasActiveFilters =
    selectedActorIds.size > 0 || search.trim() !== "" || genreId !== null || decadeKey !== "all";

  const { data: cast } = useQuery({
    queryKey: ["movies", frontierStep.movie_id, "cast"],
    queryFn: () => api.get<CastMember[]>(`/movies/${frontierStep.movie_id}/cast`),
    enabled: castLinked,
  });
  const { data: constraint } = useRunConstraint(runId);
  const { data: genres } = useQuery({
    queryKey: ["movies", "genres"],
    queryFn: () => api.get<GenreOut[]>("/movies/genres"),
  });
  const { data: candidates, isLoading } = useDiscoverCandidates(
    runId,
    frontierStep.movie_id,
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
  const { data: badgesMap } = useCanonBadgesBulk(tmdbIds);

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

    if (sortBy === "match") return sortDir === "desc" ? list : [...list].reverse();
    const direction = sortDir === "asc" ? -1 : 1;
    return [...list].sort((a, b) => {
      if (sortBy === "year") return direction * ((b.release_year ?? 0) - (a.release_year ?? 0));
      if (sortBy === "popularity") return direction * ((b.popularity ?? 0) - (a.popularity ?? 0));
      const key = sortBy === "imdb" ? "imdb" : "rt";
      return direction * (ratingSortValue(b, key) - ratingSortValue(a, key));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidates, selectedActorIds, mode, genreId, decadeKey, search, sortBy, sortDir, ratingsMap]);

  function toggleActor(actorId: number) {
    setSelectedActorIds((prev) => {
      const next = new Set(prev);
      if (next.has(actorId)) next.delete(actorId);
      else next.add(actorId);
      return next;
    });
  }

  function toggleOffered(candidate: DiscoveryCandidate) {
    setOffered((prev) =>
      prev.some((c) => c.movie_id === candidate.movie_id)
        ? prev.filter((c) => c.movie_id !== candidate.movie_id)
        : prev.length < FORK_OFFER_SIZE
          ? [...prev, candidate]
          : prev,
    );
  }

  async function handleOffer() {
    const links: Record<number, Record<string, unknown>> = {};
    for (const candidate of offered) {
      const connection = candidate.connections[0];
      if (connection) {
        links[candidate.movie_id] = connectionMetadata(connection, {
          from: connection.character_in_frontier,
          to: connection.character_in_candidate,
        });
      }
    }
    await offerFork.mutateAsync({ movie_ids: offered.map((c) => c.movie_id), links });
    onClose();
  }

  async function handleAdd(candidate: DiscoveryCandidate, watched: boolean) {
    setPendingMovieId(candidate.movie_id);
    const connection = candidate.connections[0];
    try {
      await createStep.mutateAsync({
        movie_id: candidate.movie_id,
        force: true,
        tunnel_side: tunnelSide,
        status: watched ? "watched" : "planned",
        watched_at: watched ? new Date().toISOString() : null,
        transition_metadata: connection
          ? connectionMetadata(connection, {
              from: connection.character_in_frontier,
              to: connection.character_in_candidate,
            })
          : null,
      });
      onClose();
    } finally {
      setPendingMovieId(null);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <ModifierChips constraint={constraint} />

      {!castLinked && (
        <RuleBanner
          gameType={gameType}
          title={constraint?.title}
          detail={constraint?.detail}
          frontierTitle={frontierStep.movie_title}
        />
      )}

      {castLinked && cast && cast.length > 0 && (
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
        {castLinked && (
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
        )}

        <div className="flex min-w-[180px] flex-1 items-center gap-2 rounded-md border border-app-border bg-app-bg px-3 py-2">
          <Search className="h-4 w-4 shrink-0 text-zinc-500" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={castLinked ? "Search title or character..." : "Search title..."}
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
          {!castLinked && <option value="match">Sort: Best match</option>}
          <option value="year">Sort: Year</option>
          <option value="popularity">Sort: Popularity</option>
          <option value="imdb">Sort: IMDb Rating</option>
          <option value="rt">Sort: Rotten Tomatoes</option>
        </select>

        <button
          type="button"
          onClick={() => setSortDir((d) => (d === "asc" ? "desc" : "asc"))}
          title={sortDir === "asc" ? "Ascending" : "Descending"}
          className="flex items-center gap-1.5 rounded-md border border-app-border bg-app-bg px-2.5 py-2 text-sm font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover"
        >
          {sortDir === "asc" ? (
            <ArrowUpAZ className="h-4 w-4" />
          ) : (
            <ArrowDownAZ className="h-4 w-4" />
          )}
          {sortDir === "asc" ? "Asc" : "Desc"}
        </button>

        {hasActiveFilters && (
          <button
            type="button"
            onClick={clearFilters}
            className="flex items-center gap-1 rounded-md px-2.5 py-2 text-sm font-medium text-zinc-500 transition-colors hover:text-zinc-200"
          >
            <X className="h-3.5 w-3.5" />
            Clear Filters
          </button>
        )}
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
        <p className="py-10 text-center text-sm text-zinc-500">No films match these filters.</p>
      )}

      {!isLoading && filtered.length > 0 && (
        <div className="grid grid-cols-2 items-stretch gap-3 sm:grid-cols-3 md:grid-cols-4">
          {filtered.map((candidate) => (
            <CandidateCard
              key={candidate.movie_id}
              candidate={candidate}
              genres={genres}
              ratings={ratingsMap?.[String(candidate.movie_id)]}
              badges={badgesMap?.[String(candidate.movie_id)]}
              gameType={gameType}
              castLinked={castLinked}
              frontierMovieId={frontierStep.movie_id}
              allowRepeats={allowRepeats}
              onServer={jellyfinStatus?.[String(candidate.movie_id)]?.on_server}
              pending={pendingMovieId === candidate.movie_id && createStep.isPending}
              fork={
                forkMode
                  ? {
                      selected: offered.some((c) => c.movie_id === candidate.movie_id),
                      full: offered.length >= FORK_OFFER_SIZE,
                      onToggle: () => toggleOffered(candidate),
                    }
                  : undefined
              }
              onQueue={() => handleAdd(candidate, false)}
              onLogWatched={() => handleAdd(candidate, true)}
              onOpenDetails={() =>
                onOpenMovie({
                  kind: "movie",
                  label: candidate.title,
                  movieId: candidate.movie_id,
                  posterPath: candidate.poster_path,
                  guaranteedConnected: true,
                  safeActorIds: new Set(candidate.connections.map((c) => c.actor_id)),
                  directConnection: candidate.connections[0],
                })
              }
            />
          ))}
        </div>
      )}

      {forkMode && (
        <div className="sticky bottom-0 z-10 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-fuchsia-400/40 bg-app-surface/95 px-4 py-3 backdrop-blur">
          <div className="min-w-0 text-xs text-zinc-400">
            <p className="font-semibold text-fuchsia-200">
              {offered.length}/{FORK_OFFER_SIZE} films chosen
            </p>
            <p className="truncate">
              {offered.length > 0 ? offered.map((c) => c.title).join(" · ") : "Select three films your partner can choose from."}
            </p>
            {offerFork.isError && (
              <p role="alert" className="text-red-400">
                {offerFork.error instanceof Error ? offerFork.error.message : "Couldn't send the offer."}
              </p>
            )}
          </div>
          <button
            type="button"
            disabled={offered.length !== FORK_OFFER_SIZE || offerFork.isPending}
            onClick={handleOffer}
            className="flex items-center gap-1.5 rounded-md bg-fuchsia-400 px-4 py-2 text-sm font-semibold text-zinc-950 transition-colors hover:bg-fuchsia-300 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {offerFork.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Offer to Partner
          </button>
        </div>
      )}
    </div>
  );
}

const FORK_OFFER_SIZE = 3;

function CandidateCard({
  candidate,
  genres,
  ratings,
  badges,
  gameType,
  castLinked,
  frontierMovieId,
  allowRepeats,
  onServer,
  pending,
  fork,
  onQueue,
  onLogWatched,
  onOpenDetails,
}: {
  candidate: DiscoveryCandidate;
  genres: GenreOut[] | undefined;
  ratings: MovieRatings | null | undefined;
  badges: { badge_label: string; badge_color: string }[] | undefined;
  gameType: string;
  castLinked: boolean;
  /** The film this candidate would follow: the "Why this link?" pitch compares the two. */
  frontierMovieId: number;
  allowRepeats: boolean;
  onServer: boolean | null | undefined;
  pending: boolean;
  /** Blind Fork selection state; replaces the log buttons while offering. */
  fork?: { selected: boolean; full: boolean; onToggle: () => void };
  onQueue: () => void;
  onLogWatched: () => void;
  onOpenDetails: () => void;
}) {
  const genreNames = candidate.genre_ids
    .map((id) => genres?.find((g) => g.id === id)?.name)
    .filter((name): name is string => !!name);
  const isLockedDuplicate = candidate.already_in_run && !allowRepeats;

  return (
    <div
      className={cn(
        "flex h-full flex-col justify-between gap-2 rounded-lg border p-2.5",
        onServerCardClass(onServer),
      )}
    >
      <div className="flex flex-col gap-2">
        <button
          type="button"
          onClick={onOpenDetails}
          className="relative text-left transition-opacity hover:opacity-85"
        >
          <MoviePoster path={candidate.poster_path} title={candidate.title} className="w-full" />
          <div className="absolute left-1 top-1">
            <OnServerBadge onServer={onServer} />
          </div>
          {candidate.dominant_color && (
            <ColorSwatch color={candidate.dominant_color} className="absolute right-1 top-1" />
          )}
        </button>

        <div>
          <p className="line-clamp-2 text-xs font-medium text-zinc-100">{candidate.title}</p>
          <p className="text-[10px] text-zinc-500">{candidate.release_year ?? "—"}</p>
          <RatingBadges ratings={ratings} />
          <div className="mt-1">
            <CanonBadgeList badges={badges} />
          </div>
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

        {castLinked && <ConnectionBadge connections={candidate.connections} />}
        <MechanicBadge candidate={candidate} gameType={gameType} />
        <PitchButton
          previousMovieId={frontierMovieId}
          candidateMovieId={candidate.movie_id}
          linkLabel={candidate.connections[0]?.actor_name}
          className="w-fit"
        />
        {candidate.constraint_unverified && (
          <span
            title="This run's rule couldn't be checked for this film yet - logging will check it."
            className="w-fit rounded-full bg-amber-950 px-2 py-0.5 text-[9px] font-medium text-amber-400"
          >
            Rule unverified
          </span>
        )}
      </div>

      <div className="flex flex-col gap-1.5">
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

        {isLockedDuplicate ? null : fork ? (
          <button
            type="button"
            aria-pressed={fork.selected}
            disabled={!fork.selected && fork.full}
            onClick={fork.onToggle}
            className={cn(
              "rounded-md border px-2 py-1.5 text-[10px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40",
              fork.selected
                ? "border-fuchsia-400 bg-fuchsia-500/20 text-fuchsia-200"
                : "border-app-border text-zinc-300 hover:bg-app-surface-hover",
            )}
          >
            {fork.selected ? "✓ In the offer" : "Add to offer"}
          </button>
        ) : (
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
    </div>
  );
}

/** The game's own mechanic for a candidate: year jump, country, poster colour or plot match. */
function MechanicBadge({
  candidate,
  gameType,
}: {
  candidate: DiscoveryCandidate;
  gameType: string;
}) {
  if (gameType === "chrono_climb" && candidate.year_delta != null) {
    const up = candidate.year_delta > 0;
    const Icon = up ? ArrowUp : ArrowDown;
    return (
      <span
        title={`Released ${Math.abs(candidate.year_delta)} year(s) ${up ? "after" : "before"} the last film`}
        className="flex w-fit items-center gap-1 rounded-full bg-violet-950 px-2 py-0.5 text-[10px] font-semibold text-violet-300"
      >
        <Icon className="h-3 w-3" />
        {up ? "+" : "−"}
        {Math.abs(candidate.year_delta)} yr{Math.abs(candidate.year_delta) === 1 ? "" : "s"}
      </span>
    );
  }
  if (gameType === "world_passport") {
    const countries = parseOriginCountries(candidate.origin_country);
    if (countries.length === 0) return null;
    return (
      <span
        title={countries.map((c) => countryName(c, c)).join(", ")}
        className="flex w-fit items-center gap-1 rounded-full bg-teal-950 px-2 py-0.5 text-[10px] font-semibold text-teal-300"
      >
        <span className="text-sm leading-none">{isoToFlagEmoji(countries[0])}</span>
        <span className="truncate">{countryName(countries[0], countries[0])}</span>
      </span>
    );
  }
  if (gameType === "aesthetic_gradient" && candidate.dominant_color) {
    return (
      <span className="flex w-fit items-center gap-1.5 rounded-full bg-app-surface-hover px-2 py-0.5 text-[10px] font-semibold text-zinc-300">
        <ColorSwatch color={candidate.dominant_color} className="h-3 w-3 border" />
        {candidate.dominant_color}
      </span>
    );
  }
  return <SemanticMatchBadge score={candidate.semantic_score} />;
}

/** Explains what makes a film eligible, in place of the actor network. */
function RuleBanner({
  gameType,
  title,
  detail,
  frontierTitle,
}: {
  gameType: string;
  title: string | undefined;
  detail: string | null | undefined;
  frontierTitle: string;
}) {
  const style = gameModeStyle(gameType);
  const Icon = style.icon;
  return (
    <div role="note" className={cn("flex items-start gap-3 rounded-lg px-3.5 py-3", style.bubble)}>
      <Icon className="mt-0.5 h-5 w-5 shrink-0" />
      <div className="min-w-0">
        <p className="text-sm font-semibold">{title ?? "Films that fit this mode's rule"}</p>
        <p className="text-[11px] opacity-75">
          {detail ? `${detail} ` : ""}Showing films that follow {frontierTitle} - no shared cast needed.
        </p>
      </div>
    </div>
  );
}

function sharedLabel(connections: DiscoveryConnection[]): string {
  if (connections.every((c) => c.kind === "director")) return "Directors";
  return connections.some((c) => c.kind === "director") ? "Links" : "Actors";
}

function ConnectionBadge({ connections }: { connections: DiscoveryConnection[] }) {
  if (connections.length === 0) return null;

  if (connections.length === 1) {
    const connection = connections[0];
    const isDirector = connection.kind === "director";
    return (
      <div className="flex items-center gap-1.5 rounded-md bg-app-bg px-2 py-1">
        {isDirector ? (
          <Clapperboard className="h-4 w-4 shrink-0 text-accent" />
        ) : connection.profile_path ? (
          <img
            src={profileUrl(connection.profile_path) ?? undefined}
            alt={connection.actor_name}
            className="h-5 w-5 shrink-0 rounded-full object-cover"
          />
        ) : (
          <User className="h-4 w-4 shrink-0 text-zinc-500" />
        )}
        <div className="min-w-0">
          <p className="truncate text-[10px] font-medium text-zinc-300">
            {connection.actor_name}
            {isDirector && <span className="ml-1 text-accent">(Director)</span>}
          </p>
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
      {`${connections.length} Shared ${sharedLabel(connections)}: ${connections.map((c) => c.actor_name).join(" & ")}`}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Screen 2: full movie details (overview, genres, ratings, full cast) - with
// Queue/Log actions and the frontier-link guard for non-guaranteed films.
// ---------------------------------------------------------------------------
function MovieScreenView({
  screen,
  runId,
  frontierMovieId,
  frontierMovieTitle,
  rulesConfig,
  steps,
  castLinked,
  tunnelSide,
  onOpenActor,
  onClose,
}: {
  screen: Screen & { kind: "movie" };
  runId: string;
  frontierMovieId: number;
  frontierMovieTitle: string;
  rulesConfig: RulesConfig;
  steps: RunStep[];
  castLinked: boolean;
  tunnelSide?: TunnelSide;
  onOpenActor: (screen: Screen & { kind: "actor" }) => void;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const { movie, isHydrating } = useMovieDetail(screen.movieId);
  const { data: genres } = useQuery({
    queryKey: ["movies", "genres"],
    queryFn: () => api.get<GenreOut[]>("/movies/genres"),
  });
  const { data: cast } = useQuery({
    queryKey: ["movies", screen.movieId, "cast", 20],
    queryFn: () => api.get<CastMember[]>(`/movies/${screen.movieId}/cast?limit=20`),
  });
  const { data: jellyfinStatus } = useQuery({
    queryKey: ["jellyfin", "lookup", [screen.movieId]],
    queryFn: () =>
      api.post<Record<string, JellyfinItemSummary>>("/integrations/jellyfin/lookup", {
        tmdb_ids: [screen.movieId],
      }),
  });
  const { data: badgesMap } = useCanonBadgesBulk([screen.movieId]);

  const createStep = useCreateStep(runId);
  const [guard, setGuard] = useState<GuardStatus | null>(null);

  const allowRepeats = allowsMovieRepeats(rulesConfig);
  const wildcardsRemaining = rulesConfig.wildcards_budget;
  const wildcardsExhausted = wildcardsRemaining !== -1 && wildcardsRemaining <= 0;
  const existingStepNumber = findExistingStepNumber(steps, screen.movieId);
  const isLockedDuplicate = existingStepNumber !== null && !allowRepeats;

  const genreNames = (movie?.genre_ids ?? [])
    .map((id) => genres?.find((g) => g.id === id)?.name)
    .filter((name): name is string => !!name);

  async function handleAdd(watched: boolean, connection?: DiscoveryConnection | ValidationResult["connections"][number]) {
    await createStep.mutateAsync({
      movie_id: screen.movieId,
      force: true,
      tunnel_side: tunnelSide,
      status: watched ? "watched" : "planned",
      watched_at: watched ? new Date().toISOString() : null,
      transition_metadata: connection
        ? connectionMetadata(connection, {
            from:
              "character_in_frontier" in connection
                ? connection.character_in_frontier
                : connection.character_in_from,
            to:
              "character_in_candidate" in connection
                ? connection.character_in_candidate
                : connection.character_in_to,
          })
        : null,
    });
    onClose();
  }

  async function checkAndAdd(watched: boolean) {
    if (screen.guaranteedConnected) {
      await handleAdd(watched, screen.directConnection);
      return;
    }
    if (guard?.state === "no-connect") {
      await handleAdd(watched, guard.result.connections[0]);
      return;
    }
    setGuard({ state: "checking" });
    try {
      // Run-scoped, so the run's own engine and rules decide (not plain CineChain).
      const result = await api.post<ValidationResult>(`/runs/${runId}/validate`, {
        movie_id: screen.movieId,
        tunnel_side: tunnelSide,
      });
      if (result.valid) {
        await handleAdd(watched, result.connections[0]);
        return;
      }
      setGuard({ state: "no-connect", result });
    } catch {
      setGuard({
        state: "no-connect",
        result: { valid: false, reason: "Could not check this connection.", connections: [] },
      });
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex gap-4">
        <MoviePoster path={screen.posterPath ?? movie?.poster_path ?? null} title={screen.label} className="w-28 shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-zinc-100">{screen.label}</p>
          <p className="mt-0.5 text-xs text-zinc-400">
            {movie?.release_year ?? "—"}
            {movie?.runtime ? ` · ${movie.runtime} min` : ""}
          </p>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            <RatingBadges ratings={movie?.ratings} />
            <OnServerBadge onServer={jellyfinStatus?.[String(screen.movieId)]?.on_server} />
            <AcquisitionControl
              tmdbId={screen.movieId}
              title={screen.label}
              onServer={jellyfinStatus?.[String(screen.movieId)]?.on_server}
            />
          </div>
          <div className="mt-1.5">
            <CanonBadgeList badges={badgesMap?.[String(screen.movieId)]} />
          </div>
          {genreNames.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {genreNames.map((name) => (
                <span key={name} className="rounded-full bg-app-surface-hover px-2 py-0.5 text-xs text-zinc-300">
                  {name}
                </span>
              ))}
            </div>
          )}
          {movie && (
            <>
              <MovieTagline tagline={movie.tagline} />
              <p className="mt-2 line-clamp-3 text-xs text-zinc-500">
                {movie.overview || (isHydrating ? "Fetching description..." : "No overview available.")}
              </p>
            </>
          )}
        </div>
      </div>

      {isLockedDuplicate ? (
        <p className="flex items-center gap-1.5 rounded-md bg-red-950 px-3 py-2 text-xs font-medium text-red-300">
          <Lock className="h-3.5 w-3.5" /> Already in Run (Step {existingStepNumber})
        </p>
      ) : screen.guaranteedConnected ? (
        <div className="flex items-center gap-2 rounded-md bg-emerald-950 px-3 py-2 text-xs font-medium text-emerald-400">
          <Check className="h-3.5 w-3.5" />{" "}
          {castLinked ? "Connects to Frontier" : "Fits this mode's rule"}
          {castLinked && screen.directConnection && ` via ${screen.directConnection.actor_name}`}
        </div>
      ) : (
        <FrontierGuardPanel
          guard={guard}
          frontierMovieTitle={frontierMovieTitle}
          wildcardsRemaining={wildcardsRemaining}
          wildcardsExhausted={wildcardsExhausted}
          castLinked={castLinked}
          onBuildBridge={() => navigate(`/tools/bridge?from=${frontierMovieId}&to=${screen.movieId}`)}
        />
      )}

      {!isLockedDuplicate && (() => {
        const lockedByWildcards =
          !screen.guaranteedConnected &&
          guard?.state === "no-connect" &&
          (wildcardsExhausted || !!guard.result.blocked);
        const disabled = createStep.isPending || guard?.state === "checking" || lockedByWildcards;
        return (
          <div className="flex gap-2">
            <button
              type="button"
              disabled={disabled}
              onClick={() => checkAndAdd(false)}
              className="flex flex-1 items-center justify-center gap-1.5 rounded-md border border-app-border px-3 py-2 text-xs font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover disabled:cursor-not-allowed disabled:opacity-60"
            >
              {(createStep.isPending || guard?.state === "checking") && (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              )}
              Queue Up Next
            </button>
            <button
              type="button"
              disabled={disabled}
              onClick={() => checkAndAdd(true)}
              className="flex flex-1 items-center justify-center gap-1.5 rounded-md bg-accent px-3 py-2 text-xs font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-60"
            >
              {(createStep.isPending || guard?.state === "checking") && (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              )}
              Log Watched
            </button>
          </div>
        );
      })()}

      <div>
        <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500">Cast</p>
        <div className="flex flex-wrap gap-2">
          {cast?.map((member) => (
            <button
              key={member.actor_id}
              type="button"
              title={member.name}
              onClick={() =>
                onOpenActor({
                  kind: "actor",
                  label: member.name,
                  actorId: member.actor_id,
                  actorName: member.name,
                  profilePath: member.profile_path,
                  characterName: member.character_name,
                  guaranteedConnected: screen.guaranteedConnected && screen.safeActorIds.has(member.actor_id),
                })
              }
              className="flex w-14 shrink-0 flex-col items-center gap-1 rounded-md border border-app-border px-1 py-1.5 text-center transition-colors hover:border-accent"
            >
              {member.profile_path ? (
                <img
                  src={profileUrl(member.profile_path) ?? undefined}
                  alt={member.name}
                  className="h-9 w-9 rounded-full object-cover"
                />
              ) : (
                <div className="flex h-9 w-9 items-center justify-center rounded-full bg-app-surface-hover text-zinc-500">
                  <User className="h-4 w-4" />
                </div>
              )}
              <span className="line-clamp-2 text-[9px] leading-tight text-zinc-400">{member.name}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function FrontierGuardPanel({
  guard,
  frontierMovieTitle,
  wildcardsRemaining,
  wildcardsExhausted,
  castLinked,
  onBuildBridge,
}: {
  guard: GuardStatus | null;
  frontierMovieTitle: string;
  wildcardsRemaining: number;
  wildcardsExhausted: boolean;
  castLinked: boolean;
  onBuildBridge: () => void;
}) {
  if (guard?.state === "checking") {
    return (
      <div className="flex items-center gap-1.5 rounded-md bg-app-surface-hover px-3 py-2 text-xs text-zinc-400">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />{" "}
        {castLinked ? "Checking connection to frontier..." : "Checking this film against the rule..."}
      </div>
    );
  }
  if (guard?.state !== "no-connect") {
    return (
      <p className="rounded-md bg-app-surface-hover px-3 py-2 text-xs text-zinc-400">
        {castLinked
          ? "Connection to the frontier is unknown yet - click Queue Up Next or Log Watched to check."
          : "This film hasn't been checked against the rule yet - click Queue Up Next or Log Watched to check."}
      </p>
    );
  }
  if (guard.result.blocked) {
    return (
      <p className="flex items-center gap-1.5 rounded-md border border-red-900/50 bg-red-950/30 p-3 text-xs font-medium text-red-400">
        <Lock className="h-3.5 w-3.5 shrink-0" />
        {guard.result.reason ?? "This film isn't allowed in this run."}
      </p>
    );
  }

  return (
    <div
      className={cn(
        "rounded-md border p-3 text-xs",
        wildcardsExhausted ? "border-red-900/50 bg-red-950/30" : "border-amber-900/50 bg-amber-950/30",
      )}
    >
      {wildcardsExhausted ? (
        <p className="flex items-center gap-1.5 font-medium text-red-400">
          <Lock className="h-3.5 w-3.5" /> No link to frontier & 0 wildcards remaining
        </p>
      ) : (
        <p className="flex items-center gap-1.5 font-medium text-amber-400">
          <AlertTriangle className="h-3.5 w-3.5" />
          Does not connect to current frontier ({frontierMovieTitle}). Adding this will consume 1 of{" "}
          {wildcardsRemaining === -1 ? "unlimited" : wildcardsRemaining} remaining wildcards.
        </p>
      )}
      <button
        type="button"
        onClick={onBuildBridge}
        className="mt-2 flex items-center gap-1 rounded-md border border-app-border px-2.5 py-1.5 text-[11px] font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover"
      >
        <GitBranch className="h-3 w-3" /> Build Bridge to Here
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Screen 3: an actor's filmography - click a poster to drill into THAT movie.
// ---------------------------------------------------------------------------
function ActorScreenView({
  screen,
  onOpenMovie,
}: {
  screen: Screen & { kind: "actor" };
  onOpenMovie: (screen: Screen & { kind: "movie" }) => void;
}) {
  const { data: credits, isLoading } = useQuery({
    queryKey: ["people", screen.actorId, "credits", "all"],
    queryFn: () => api.get<MovieSummary[]>(`/people/${screen.actorId}/credits`),
  });
  const tmdbIds = credits?.map((m) => m.tmdb_id) ?? [];
  const { data: jellyfinStatus } = useQuery({
    queryKey: ["jellyfin", "lookup", tmdbIds],
    queryFn: () =>
      api.post<Record<string, JellyfinItemSummary>>("/integrations/jellyfin/lookup", {
        tmdb_ids: tmdbIds,
      }),
    enabled: tmdbIds.length > 0,
  });

  const sorted = useMemo(
    () => [...(credits ?? [])].sort((a, b) => (b.release_year ?? 0) - (a.release_year ?? 0)),
    [credits],
  );

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3">
        {screen.profilePath ? (
          <img
            src={profileUrl(screen.profilePath) ?? undefined}
            alt={screen.actorName}
            className="h-12 w-12 rounded-full object-cover"
          />
        ) : (
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-app-surface-hover text-zinc-500">
            <User className="h-5 w-5" />
          </div>
        )}
        <div>
          <p className="text-sm font-semibold text-zinc-100">{screen.actorName}</p>
          {screen.characterName && <p className="text-xs text-zinc-500">as {screen.characterName}</p>}
        </div>
      </div>

      {isLoading && (
        <div className="flex justify-center py-8">
          <Loader2 className="h-5 w-5 animate-spin text-zinc-600" />
        </div>
      )}

      {!isLoading && sorted.length === 0 && (
        <p className="py-6 text-center text-sm text-zinc-500">No filmography found.</p>
      )}

      {!isLoading && sorted.length > 0 && (
        <div className="grid grid-cols-3 gap-3 sm:grid-cols-4 md:grid-cols-5">
          {sorted.map((movie) => (
            <button
              key={movie.tmdb_id}
              type="button"
              onClick={() =>
                onOpenMovie({
                  kind: "movie",
                  label: movie.title,
                  movieId: movie.tmdb_id,
                  posterPath: movie.poster_path,
                  guaranteedConnected: screen.guaranteedConnected,
                  safeActorIds: screen.guaranteedConnected
                    ? new Set([screen.actorId])
                    : new Set<number>(),
                })
              }
              className={cn(
                "flex flex-col gap-1 rounded-lg border p-1 text-left transition-opacity hover:opacity-85",
                jellyfinStatus?.[String(movie.tmdb_id)]?.on_server
                  ? onServerCardClass(true)
                  : "border-transparent",
              )}
            >
              <div className="relative">
                <MoviePoster path={movie.poster_path} title={movie.title} className="w-full" />
                <div className="absolute left-1 top-1">
                  <OnServerBadge onServer={jellyfinStatus?.[String(movie.tmdb_id)]?.on_server} />
                </div>
              </div>
              <p className="line-clamp-2 text-[10px] font-medium text-zinc-200">{movie.title}</p>
              <p className="text-[9px] text-zinc-500">{movie.release_year ?? "—"}</p>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

