import { useEffect, useMemo, useState, type ReactNode } from "react";
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
import { HISTORICAL_TIME_TRAVEL, leapText, narrativeSettingText } from "../lib/historicalEra";
import ColorSwatch, { SemanticMatchBadge } from "./ColorSwatch";
import ModifierChips from "./ModifierChips";
import PitchButton from "./PitchButton";
import MoviePoster from "./MoviePoster";
import AcquisitionControl from "./AcquisitionControl";
import ChaosBanner from "./ChaosBanner";
import ChaosButton from "./ChaosButton";
import MovieTagline from "./MovieTagline";
import OnServerBadge, { onServerCardClass } from "./OnServerBadge";
import RatingBadges from "./RatingBadges";
import { CanonBadgeList } from "./CanonBadge";
import { api } from "../lib/api";
import { cn } from "../lib/cn";
import { profileUrl } from "../lib/tmdbImage";
import { parseOriginCountries } from "../lib/countries";
import CountryFlags from "./CountryFlags";
import { countryName } from "../lib/countryNames";
import { gameModeStyle, usesCastLinks } from "../lib/gameModes";
import { connectionMetadata } from "../lib/connections";
import { ROLE_STYLES, connectionRole } from "../lib/crewRoles";
import { TropeChips } from "./TropeChips";
import { tropeLabel } from "../lib/tropes";
import { RABBIT_HOLE } from "../lib/rabbitHole";
import RoleBadge from "./RoleBadge";
import { allowsMovieRepeats, findExistingStepNumber, forcePricing } from "../lib/rules";
import { SIDE_LABELS } from "../lib/tunnel";
import { tugBankMultiplier, tugEffectLabel, tugNextTeam } from "../lib/tugOfWar";
import { GlossaryChip } from "./HowToPlay";
import ModeFilterBar, { filterDataUnknown, filterDefaults, matchesModeFilters, visitedCountries } from "./pick-next/ModeFilterBar";
import type { FilterValues } from "./pick-next/ModeFilterBar";
import DirectorsPicks from "./pick-next/DirectorsPicks";
import { pendulumState } from "../lib/pendulum";
import { isoToFlagEmoji } from "../lib/countries";
import { effectiveCooldown } from "../lib/modifiers";
import ExpandableText from "./ui/ExpandableText";
import ClampedLabel from "./ui/ClampedLabel";
import {
  useCanonBadgesBulk,
  useCreateStep,
  useOfferFork,
  useDiscoverCandidates,
  useEngines,
  useRunSuggestions,
  useJellyfinLookup,
  useMovieDetail,
  useMovieTropes,
  useRunConstraint,
  useUpdateRun,
} from "../lib/queries";
import type {
  CraftRole,
  CrewMember,
  TunnelSide,
  CastMember,
  DiscoveryCandidate,
  DiscoveryConnection,
  GenreOut,
  MovieRatings,
  MovieSummary,
  RulesConfig,
  RunStep,
  ValidationResult,
} from "../types/api";

const CREW_CRAFT = "crew_craft";
const SEMANTIC_TROPE = "semantic_trope";
const DISCOVERY_PAGE_SIZE = 48;

type CoStarMode = "or" | "and";
type SortBy = "match" | "year" | "popularity" | "imdb" | "rt" | "tug";
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
  if (year == null) return true;
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
  initialChaser = false,
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
  /** Open with The Chaser on: only short, lighthearted palate cleansers. */
  initialChaser?: boolean;
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
      onEscape={() => {
        if (stack.length <= 1) return false;
        popScreen();
        return true;
      }}
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

        <div hidden={activeScreen.kind !== "grid"}>
          <DiscoveryGrid
            runId={runId}
            frontierStep={frontierStep}
            rulesConfig={rulesConfig}
            gameType={gameType}
            castLinked={castLinked}
            steps={steps}
            tunnelSide={tunnelSide}
            forkMode={forkMode}
            initialChaser={initialChaser}
            onOpenMovie={(screen) => pushScreen(screen)}
            onClose={handleClose}
          />
        </div>

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
  steps,
  tunnelSide,
  forkMode,
  initialChaser = false,
  onOpenMovie,
  onClose,
}: {
  runId: string;
  frontierStep: RunStep;
  rulesConfig: RulesConfig;
  gameType: string;
  /** False for standalone modes: no actor network, the card shows the mode's own mechanic. */
  castLinked: boolean;
  steps: RunStep[];
  tunnelSide?: TunnelSide;
  forkMode?: boolean;
  initialChaser?: boolean;
  onOpenMovie: (screen: Screen & { kind: "movie" }) => void;
  onClose: () => void;
}) {
  const [offered, setOffered] = useState<DiscoveryCandidate[]>([]);
  const offerFork = useOfferFork(runId);
  const [mode, setMode] = useState<CoStarMode>("or");
  const [selectedActorIds, setSelectedActorIds] = useState<Set<number>>(new Set());
  const [search, setSearch] = useState("");
  const [genreId, setGenreId] = useState<number | null>(null);
  const nextTeam = tugNextTeam(rulesConfig);
  const [decadeKey, setDecadeKey] = useState("all");
  const [sortBy, setSortBy] = useState<SortBy>("match");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [pendingMovieId, setPendingMovieId] = useState<number | null>(null);
  const [tropeFilter, setTropeFilter] = useState<string | null>(null);
  // The Chaser (palate cleansers) and the Underdog B-Sides flip are server-side pool options.
  const [chaser, setChaser] = useState(initialChaser);
  const [underdog, setUnderdog] = useState(false);
  // Tagline Roulette masks every poster and title behind its tagline; only a page is shown at a time.
  const [roulette, setRoulette] = useState(false);
  const [visibleCount, setVisibleCount] = useState(DISCOVERY_PAGE_SIZE);
  const { data: engines, isError: enginesError, refetch: retryEngines } = useEngines();
  const engine = engines?.find((entry) => entry.game_type === gameType);
  const specs = useMemo(() => (engine?.discovery_filters ?? []).filter((spec) =>
    spec.source !== "tug_effect" || [2, 3].includes(rulesConfig.tug_rules_version ?? 1)), [engine, rulesConfig.tug_rules_version]);
  const visited = useMemo(() => visitedCountries(steps), [steps]);
  const [modeOverrides, setModeOverrides] = useState<FilterValues>({});
  const modeValues = useMemo<FilterValues>(() => ({ ...filterDefaults(specs, visited),
    ...(rulesConfig.tug_rules_version === 3 ? { tug_effect: "" } : {}),
    ...modeOverrides }), [specs, visited, modeOverrides, rulesConfig.tug_rules_version]);
  const includeOffTier = specs.some((spec) => spec.server_param === "include_off_tier" && modeValues[spec.key] === true);
  const suggestions = useRunSuggestions(runId);
  const [suggestionContext, setSuggestionContext] = useState<string | null>(null);
  const [furtherPool, setFurtherPool] = useState<{ context: string; candidates: DiscoveryCandidate[] } | null>(null);
  const filterContext = JSON.stringify({ frontier: frontierStep.movie_id, mode, chaser, underdog, modeValues, decadeKey, genreId, tropeFilter, search, actors: [...selectedActorIds] });

  useEffect(() => {
    setVisibleCount(DISCOVERY_PAGE_SIZE);
  }, [
    chaser,
    decadeKey,
    genreId,
    mode,
    search,
    selectedActorIds,
    sortBy,
    sortDir,
    tropeFilter,
    underdog,
    modeValues,
    furtherPool,
  ]);

  function clearFilters() {
    setChaser(false);
    setUnderdog(false);
    setTropeFilter(null);
    setSelectedActorIds(new Set());
    setSearch("");
    setGenreId(null);
    setDecadeKey("all");
    setSortBy("match");
    setSortDir("desc");
    setModeOverrides(Object.fromEntries(specs.map((spec) => [spec.key, spec.kind === "toggle" ? false : spec.kind === "range" ? [null, null] : ""])));
    setFurtherPool(null);
    suggestions.reset();
  }

  const hasActiveFilters =
    chaser ||
    underdog ||
    selectedActorIds.size > 0 ||
    search.trim() !== "" ||
    genreId !== null ||
    decadeKey !== "all" ||
    tropeFilter !== null ||
    Object.values(modeValues).some((value) => Array.isArray(value) ? value.some((bound) => bound != null) : Boolean(value));

  const { data: cast } = useQuery({
    queryKey: ["movies", frontierStep.movie_id, "cast"],
    queryFn: () => api.get<CastMember[]>(`/movies/${frontierStep.movie_id}/cast`),
    enabled: castLinked,
  });
  // Crew & Craft Trail: the frontier's key crew sit beside its cast as filter chips.
  const hasCrew = engine?.capabilities.includes("crew_craft") ?? gameType === CREW_CRAFT;
  const { data: crew } = useQuery({
    queryKey: ["movies", frontierStep.movie_id, "crew"],
    queryFn: () => api.get<CrewMember[]>(`/movies/${frontierStep.movie_id}/crew`),
    enabled: castLinked && hasCrew,
  });
  const people = useMemo(() => mergeFilterPeople(hasCrew ? (crew ?? []) : [], cast ?? []), [hasCrew, crew, cast]);
  // Semantic Trope Web: the frontier's extracted tropes are filter chips over the pool.
  const tropeMode = gameType === SEMANTIC_TROPE;
  const { tropes: frontierTropes, isExtracting: extractingTropes } = useMovieTropes(
    frontierStep.movie_id,
    tropeMode,
  );
  const { data: constraint } = useRunConstraint(runId);
  const surrender = useUpdateRun(runId);
  const { data: genres } = useQuery({
    queryKey: ["movies", "genres"],
    queryFn: () => api.get<GenreOut[]>("/movies/genres"),
  });
  const { data: candidates, isLoading: poolLoading, isError: poolError, error: poolFailure, refetch: retryPool } = useDiscoverCandidates(
    runId,
    frontierStep.movie_id,
    mode,
    { chaser, underdog, includeOffTier },
  );
  const isLoading = poolLoading || !engine && !enginesError;
  const pool = useMemo(() => {
    const byId = new Map((candidates ?? []).map((candidate) => [candidate.movie_id, candidate]));
    if (furtherPool?.context === filterContext) {
      for (const candidate of furtherPool.candidates) if (!byId.has(candidate.movie_id)) byId.set(candidate.movie_id, candidate);
    }
    return [...byId.values()];
  }, [candidates, furtherPool, filterContext]);
  const targetGenreId = genres?.find((genre) => genre.name === pendulumState(rulesConfig, steps.length).target)?.id;
  const cooldown = useMemo(() => {
    const window = effectiveCooldown(gameType, rulesConfig, engine);
    return new Map((constraint?.cooldown_countries ?? []).map((code) => {
      const age = [...steps].reverse().findIndex((step) => (step.movie_origin_countries ?? parseOriginCountries(step.movie_origin_country))[0] === code);
      return [code, Math.max(1, window - Math.max(0, age))];
    }));
  }, [constraint, steps, gameType, rulesConfig, engine]);
  const selectedCountry = specs.find((spec) => spec.source === "origin_country");
  const countryFilter = selectedCountry && typeof modeValues[selectedCountry.key] === "string" ? modeValues[selectedCountry.key] as string : undefined;
  const furtherDecade = decadeKey !== "all" && decadeKey !== "early" ? Number(decadeKey) : undefined;
  const canSearchFurther = Boolean(countryFilter || furtherDecade != null);
  const datedCandidate = pool.find((candidate) => candidate.narrative_year != null && candidate.narrative_delta != null);
  const frontierNarrativeYear = frontierStep.movie_narrative_year ??
    (datedCandidate?.narrative_year != null && datedCandidate.narrative_delta != null
      ? datedCandidate.narrative_year - datedCandidate.narrative_delta : null);
  const searchLabel = [
    countryFilter ? `${isoToFlagEmoji(countryFilter)} ${countryName(countryFilter, countryFilter)}` : "",
    furtherDecade != null ? `${furtherDecade}s` : "",
  ].filter(Boolean).join(" / ");
  function searchFurther() {
    setSuggestionContext(filterContext);
    suggestions.mutate({
      frontier_movie_id: frontierStep.movie_id,
      country: countryFilter || undefined,
      decade: furtherDecade,
      genre_id: genreId ?? undefined,
      chaser,
      sort_by: underdog ? "underdog" : undefined,
    }, { onSuccess: (results) => setFurtherPool({ context: filterContext, candidates: results }) });
  }

  // Rabbit Hole: with every life spent and nothing left that obeys the tier, the descent is over.
  const deadEnd =
    gameType === RABBIT_HOLE &&
    rulesConfig.lives_remaining === 0 &&
    !isLoading &&
    !includeOffTier &&
    !poolError &&
    pool.length === 0;

  const tmdbIds = pool.map((c) => c.movie_id);
  const { data: jellyfinStatus } = useJellyfinLookup(tmdbIds);
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
    let list = pool.filter((candidate) => matchesModeFilters(candidate, specs, modeValues, visited, targetGenreId));

    if (selectedActorIds.size > 0) {
      list = list.filter((candidate) => {
        const connectedIds = new Set(candidate.connections.map((c) => c.actor_id));
        return mode === "and"
          ? [...selectedActorIds].every((id) => connectedIds.has(id))
          : [...selectedActorIds].some((id) => connectedIds.has(id));
      });
    }
    if (tropeFilter !== null) {
      list = list.filter((candidate) => candidate.tropes?.includes(tropeFilter));
    }
    if (genreId !== null) {
      list = list.filter((candidate) => candidate.genre_ids.length === 0 || candidate.genre_ids.includes(genreId));
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

    if (underdog) return list; // the server already ordered it least popular first
    if (sortBy === "match") return sortDir === "desc" ? list : [...list].reverse();
    const direction = sortDir === "asc" ? -1 : 1;
    return [...list].sort((a, b) => {
      if (sortBy === "year") return direction * ((b.release_year ?? 0) - (a.release_year ?? 0));
      if (sortBy === "tug") return direction * ((b.tug_points ?? 0) - (a.tug_points ?? 0));
      if (sortBy === "popularity") return direction * ((b.popularity ?? 0) - (a.popularity ?? 0));
      const key = sortBy === "imdb" ? "imdb" : "rt";
      return direction * (ratingSortValue(b, key) - ratingSortValue(a, key));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pool, specs, modeValues, visited, targetGenreId, selectedActorIds, mode, genreId, decadeKey, tropeFilter, search, sortBy, sortDir, ratingsMap, underdog]);

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
    if (Object.values(candidate.overlay_ok ?? {}).some((ok) => ok === false)) {
      openCandidate(candidate);
      return;
    }
    setPendingMovieId(candidate.movie_id);
    const connection = candidate.connections[0];
    try {
      await createStep.mutateAsync({
        movie_id: candidate.movie_id,
        force: true,
        ...(gameType === "tug_of_war" && [2, 3].includes(rulesConfig.tug_rules_version ?? 1) ? { tug_team: nextTeam } : {}),
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

  function openCandidate(candidate: DiscoveryCandidate) {
    onOpenMovie({
      kind: "movie", label: candidate.title, movieId: candidate.movie_id,
      posterPath: candidate.poster_path,
      guaranteedConnected: candidate.tier_compliant !== false && !candidate.constraint_unverified
        && Object.values(candidate.overlay_ok ?? {}).every((ok) => ok === true),
      safeActorIds: new Set(candidate.connections.map((connection) => connection.actor_id)),
      directConnection: candidate.connections[0],
    });
  }

  function renderCandidate(candidate: DiscoveryCandidate) {
    return <CandidateCard
      key={candidate.movie_id}
      roulette={roulette}
      candidate={candidate}
      genres={genres}
      ratings={ratingsMap?.[String(candidate.movie_id)]}
      badges={badgesMap?.[String(candidate.movie_id)]}
      gameType={gameType}
      tugMultiplier={rulesConfig.tug_rules_version === 3 ? undefined : tugBankMultiplier(rulesConfig)}
      castLinked={castLinked}
      tierLabel={gameType === RABBIT_HOLE && constraint?.rabbit_hole
        ? `Tier ${constraint.rabbit_hole.tier}: ${constraint.rabbit_hole.tier_rule}` : undefined}
      frontierTropes={frontierTropes}
      frontierMovieId={frontierStep.movie_id}
      allowRepeats={allowRepeats}
      onServer={jellyfinStatus?.[String(candidate.movie_id)]?.on_server}
      pending={pendingMovieId === candidate.movie_id && createStep.isPending}
      unknownFilterData={specs.some((spec) => filterDataUnknown(candidate, spec)) || genreId !== null && candidate.genre_ids.length === 0 || decadeKey !== "all" && candidate.release_year == null}
      fork={forkMode ? {
        selected: offered.some((film) => film.movie_id === candidate.movie_id),
        full: offered.length >= FORK_OFFER_SIZE,
        onToggle: () => toggleOffered(candidate),
      } : undefined}
      onQueue={() => handleAdd(candidate, false)}
      onLogWatched={() => handleAdd(candidate, true)}
      onOpenDetails={() => openCandidate(candidate)}
    />;
  }

  return (
    <div className="flex flex-col gap-4">
      <ModifierChips constraint={constraint} />
      {constraint?.overlay_progress?.filter((overlay) => overlay.can_skip).map((overlay) => (
        <p key={overlay.key} role="status" className="rounded border border-amber-800/50 p-3 text-xs text-amber-300">
          No {overlay.next} films within reach: spend a wildcard to skip {overlay.next}.
          Open a substitute film, check it, then log watched to confirm. Queueing cannot spend a skip.
        </p>
      ))}

      {constraint?.rabbit_hole?.upcoming_tier_warning && (
        <div
          role="alert"
          className="rounded-lg border border-amber-500/60 bg-amber-500/10 px-3.5 py-2.5 font-mono text-xs font-semibold text-amber-200"
        >
          {constraint.rabbit_hole.upcoming_tier_warning}
        </div>
      )}

      {!castLinked && (
        <RuleBanner
          gameType={gameType}
          title={constraint?.title}
          detail={constraint?.detail}
          frontierTitle={frontierStep.movie_title}
        />
      )}

      {tropeMode && (frontierTropes.length > 0 || extractingTropes) && (
        <div className="flex flex-wrap items-center gap-1.5" aria-label="Filter by trope">
          <span className="text-[10px] font-medium uppercase tracking-wide text-zinc-500">
            {frontierStep.movie_title} tropes
          </span>
          {frontierTropes.map((trope) => (
            <button
              key={trope}
              type="button"
              onClick={() => setTropeFilter((current) => (current === trope ? null : trope))}
              aria-pressed={tropeFilter === trope}
              title={`Only films that share the trope "${tropeLabel(trope)}"`}
              className={cn(
                "rounded-full border px-2.5 py-1 text-xs font-medium transition-colors",
                tropeFilter === trope
                  ? "border-rose-400 bg-rose-500/20 text-rose-100"
                  : "border-app-border text-zinc-400 hover:border-zinc-600 hover:text-zinc-200",
              )}
            >
              🏷️ {trope}
            </button>
          ))}
          {extractingTropes && (
            <span className="flex items-center gap-1 text-[10px] text-zinc-600">
              <Loader2 className="h-3 w-3 animate-spin" /> Extracting tropes...
            </span>
          )}
        </div>
      )}

      {castLinked && people.length > 0 && (
        <div className="flex gap-2 overflow-x-auto pb-1" aria-label="Filter by person">
          {people.map((person) => (
            <button
              key={person.id}
              type="button"
              onClick={() => toggleActor(person.id)}
              aria-pressed={selectedActorIds.has(person.id)}
              title={person.roles.map((role) => `${ROLE_STYLES[role].label}: ${person.name}`).join(" / ")}
              className={cn(
                "flex w-20 shrink-0 flex-col items-center gap-1 rounded-lg border px-1.5 py-1.5 text-center transition-colors",
                selectedActorIds.has(person.id)
                  ? "border-accent bg-accent/10"
                  : "border-app-border hover:border-zinc-600",
              )}
            >
              {person.profile_path ? (
                <img
                  src={profileUrl(person.profile_path) ?? undefined}
                  alt={person.name}
                  loading="lazy"
                  decoding="async"
                  className="h-10 w-10 rounded-full object-cover"
                />
              ) : (
                <div
                  className={cn(
                    "flex h-10 w-10 items-center justify-center rounded-full bg-app-surface-hover text-zinc-500",
                    person.roles[0] !== "actor" && ROLE_STYLES[person.roles[0]].className,
                  )}
                >
                  {person.roles[0] === "actor" ? (
                    <User className="h-4 w-4" />
                  ) : (
                    <span className="text-lg" aria-hidden>
                      {ROLE_STYLES[person.roles[0]].emoji}
                    </span>
                  )}
                </div>
              )}
              <ClampedLabel
                text={person.name}
                lines={2}
                as="span"
                className="text-[10px] leading-tight text-zinc-300"
              />
              {hasCrew && (
                <span className="text-[11px] leading-none" aria-hidden>
                  {person.roles.map((role) => ROLE_STYLES[role].emoji).join("")}
                </span>
              )}
            </button>
          ))}
        </div>
      )}

      {rulesConfig.active_chaos && <ChaosBanner runId={runId} chaos={rulesConfig.active_chaos} />}

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
            OR - Any shared {hasCrew ? "person" : "actor"}
          </button>
          <button
            type="button"
            onClick={() => setMode("and")}
            className={cn(
              "rounded-full px-3 py-1.5 transition-colors",
              mode === "and" ? "bg-accent text-zinc-950" : "text-zinc-400 hover:text-zinc-200",
            )}
          >
            AND - {hasCrew ? "Reunite several people" : "Co-stars reunite"}
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
          <option value="match">Sort: Best match</option>
          <option value="year">Sort: Year</option>
          {specs.some((spec) => spec.source === "tug_effect") && <option value="tug">Sort: Tug points</option>}
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

        {!forkMode && <ChaosButton runId={runId} active={!!rulesConfig.active_chaos} />}

        <button
          type="button"
          aria-pressed={underdog}
          onClick={() => setUnderdog((v) => !v)}
          title="Least popular first: surface hidden gems"
          className={cn(
            "flex items-center gap-1.5 rounded-md border px-2.5 py-2 text-sm font-medium transition-colors",
            underdog
              ? "border-emerald-400 bg-emerald-500/15 text-emerald-200"
              : "border-app-border bg-app-bg text-zinc-300 hover:bg-app-surface-hover",
          )}
        >
          <span aria-hidden>💎</span>
          Underdog B-Sides
        </button>

        <button
          type="button"
          aria-pressed={roulette}
          onClick={() => {
            setRoulette((v) => !v);
            setVisibleCount(DISCOVERY_PAGE_SIZE);
          }}
          title="Hide the posters and titles: pick on the tagline alone"
          className={cn(
            "flex items-center gap-1.5 rounded-md border px-2.5 py-2 text-sm font-medium transition-colors",
            roulette
              ? "border-fuchsia-400 bg-fuchsia-500/15 text-fuchsia-200"
              : "border-app-border bg-app-bg text-zinc-300 hover:bg-app-surface-hover",
          )}
        >
          <span aria-hidden>🎭</span>
          Tagline Roulette
        </button>

        {chaser && (
          <button
            type="button"
            onClick={() => setChaser(false)}
            title="Back to every candidate"
            className="flex items-center gap-1.5 rounded-md border border-amber-400 bg-amber-400/15 px-2.5 py-2 text-sm font-medium text-amber-200"
          >
            <span aria-hidden>🍺</span>
            Chaser: ≤ 95 min, Comedy / Animation
            <X className="h-3.5 w-3.5" />
          </button>
        )}

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

      {enginesError && <p role="alert" className="text-sm text-red-300">
        Couldn't load mode filters. <button type="button" onClick={() => retryEngines()} className="underline">Retry filters</button>
      </p>}
      <ModeFilterBar specs={specs} pool={pool} values={modeValues}
        onChange={(key, value) => setModeOverrides((current) => ({ ...current, [key]: value }))}
        cooldown={cooldown} frontierYear={frontierStep.movie_release_year}
        frontierNarrativeYear={frontierNarrativeYear}
        descending={(specs.some((spec) => spec.source === "narrative_year") ? rulesConfig.direction : rulesConfig.chrono_direction ?? rulesConfig.direction) === "descent"}
      />

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

      {poolError && <p role="alert" className="text-sm text-red-300">
        {poolFailure instanceof Error ? poolFailure.message : "Couldn't load candidates."}{" "}
        <button type="button" onClick={() => retryPool()} className="underline">Retry candidates</button>
      </p>}
      {createStep.isError && <p role="alert" className="text-sm text-red-300">
        {createStep.error instanceof Error ? createStep.error.message : "Couldn't log this film."}
      </p>}
      {!isLoading && !poolError && filtered.length === 0 && (
        deadEnd ? (
          <div className="flex flex-col items-center gap-3 rounded-xl border border-red-900/60 bg-red-950/20 px-4 py-8 text-center font-mono">
            <p className="text-sm font-semibold text-red-300">
              💀 Dead end - no lives left and no film obeys the tier rule.
            </p>
            <button
              type="button"
              disabled={surrender.isPending}
              onClick={() => surrender.mutate({ status: "forfeited" }, { onSuccess: onClose })}
              className="rounded-md bg-red-600 px-4 py-2 text-xs font-semibold text-white transition-colors hover:bg-red-500 disabled:opacity-60"
            >
              Accept your fate
            </button>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-3 py-6 text-center text-sm text-zinc-500">
            <p>No films match these filters.</p>
            {canSearchFurther && <button type="button" disabled={suggestions.isPending} onClick={searchFurther}
              className="rounded-md border border-accent px-3 py-2 text-accent disabled:opacity-50">
              {suggestions.isPending ? "Searching..." : `Search further in ${searchLabel}`}
            </button>}
            {suggestions.isError && suggestionContext === filterContext && <p role="alert" className="text-red-300">
              {suggestions.error instanceof Error ? suggestions.error.message : "Search failed. Try again."}
            </p>}
            {furtherPool?.context === filterContext && <p role="status">
              {furtherPool.candidates.length === 0 ? "No further legal films found. Try another country or decade." : "Further results were found, but none match every active filter. Try loosening a filter."}
            </p>}
          </div>
        )
      )}

      {!isLoading && !poolError && filtered.length > 0 && (
        <>
          <DirectorsPicks candidates={filtered} matchOrder={pool.map((candidate) => candidate.movie_id)}
            visited={visited} specs={specs} allowRepeats={allowRepeats} renderCard={renderCandidate} onPick={openCandidate}
            slot={gameType === "tug_of_war" && rulesConfig.tug_rules_version === 3
              ? <TugDecisionTriad candidates={filtered} runId={runId} rules={rulesConfig}
                  allowRepeats={allowRepeats} renderCard={renderCandidate} /> : undefined} />
          <p className="text-xs text-zinc-500" role="status">
            Showing {Math.min(visibleCount, filtered.length)} of {filtered.length} best matches · narrow with a filter
          </p>
        </>
      )}

      {!isLoading && filtered.length > 0 && (
        <div className="grid grid-cols-2 items-stretch gap-3 sm:grid-cols-3 md:grid-cols-4">
          {filtered.slice(0, visibleCount).map(renderCandidate)}
        </div>
      )}

      {!isLoading && visibleCount < filtered.length && (
        <button
          type="button"
          onClick={() => setVisibleCount((count) => Math.min(count + DISCOVERY_PAGE_SIZE, filtered.length))}
          className={cn(
            "self-center rounded-md border px-3 py-1.5 text-xs font-semibold",
            roulette
              ? "border-fuchsia-400/50 text-fuchsia-200 hover:bg-fuchsia-500/10"
              : "border-app-border text-zinc-300 hover:bg-app-surface-hover",
          )}
        >
          {roulette ? "Spin" : "Show"} {Math.min(DISCOVERY_PAGE_SIZE, filtered.length - visibleCount)} more
          {" "}({filtered.length - visibleCount} remaining)
        </button>
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
function TugDecisionTriad({ candidates, runId, rules, allowRepeats, renderCard }: {
  candidates: DiscoveryCandidate[];
  runId: string;
  rules: RulesConfig;
  allowRepeats: boolean;
  renderCard: (candidate: DiscoveryCandidate) => ReactNode;
}) {
  const [lookaheadRequested, setLookaheadRequested] = useState(false);
  const eligible = candidates.filter((candidate) => allowRepeats || !candidate.already_in_run);
  const best = (effect: DiscoveryCandidate["tug_effect"]) => eligible
    .filter((candidate) => candidate.tug_effect === effect)
    .sort((a, b) => (b.tug_points ?? 0) - (a.tug_points ?? 0) || b.connections.length - a.connections.length)[0];
  const next = tugNextTeam(rules);
  const defenderStreak = rules.tug_momentum?.streaks?.[next === "team_a" ? "team_b" : "team_a"] ?? 0;
  const picks = [
    { label: "Best Build", candidate: best("home") },
    { label: `Best Raid${defenderStreak ? ` (breaks 🔥${defenderStreak})` : ""}`, candidate: best("invasion") },
    { label: "Bank", candidate: best("neutral") },
  ];
  const ids = picks.flatMap(({ candidate }) => candidate ? [candidate.movie_id] : []).join(",");
  const lookahead = useQuery({
    queryKey: ["tug-lookahead", runId, ids, rules],
    queryFn: () => api.get<{ movies: Record<string, { scoring: number; neutral: number; partial: boolean }>; partial: boolean }>(
      `/runs/${runId}/tug/lookahead?movie_ids=${ids}`,
    ),
    enabled: lookaheadRequested && ids.length > 0,
    staleTime: 30_000,
    retry: false,
  });
  return <div className="grid gap-3 sm:grid-cols-3" aria-label="Tug Decision Triad">
    {picks.map(({ label, candidate }) => {
      const counts = candidate && lookahead.data?.movies[String(candidate.movie_id)];
      return <div key={label} className="flex min-w-0 flex-col gap-1"
        onMouseEnter={() => setLookaheadRequested(true)} onFocus={() => setLookaheadRequested(true)}>
        <p className="text-xs font-semibold text-accent">{label}{candidate ? ` · +${candidate.tug_points ?? 0}` : ""}</p>
        {candidate ? <>
          {renderCard(candidate)}
          <button type="button" onClick={() => setLookaheadRequested(true)}
            title="Cached reachability estimate; logging still validates all rules."
            className="text-left text-xs text-zinc-400">
            {counts ? `Leaves them: ${counts.scoring} scoring · ${counts.neutral} neutral${counts.partial || lookahead.data?.partial ? " · partial cache" : ""}`
              : lookahead.isFetching ? "Checking cached replies…" : "Hover or tap to check their replies"}
          </button>
        </> : <p className="text-xs text-zinc-500">No {label.toLowerCase()} matches these filters.</p>}
      </div>;
    })}
    {lookahead.isError && <p role="alert" className="text-xs text-red-300 sm:col-span-3">
      Could not load cached replies. <button type="button" onClick={() => void lookahead.refetch()} className="underline">Retry</button>
    </p>}
  </div>;
}

function CandidateCard({
  roulette = false,
  candidate,
  genres,
  ratings,
  badges,
  gameType,
  tugMultiplier,
  castLinked,
  tierLabel,
  frontierTropes,
  frontierMovieId,
  allowRepeats,
  onServer,
  pending,
  unknownFilterData = false,
  fork,
  onQueue,
  onLogWatched,
  onOpenDetails,
}: {
  /** Tagline Roulette: poster and title stay masked behind the tagline until revealed. */
  roulette?: boolean;
  candidate: DiscoveryCandidate;
  genres: GenreOut[] | undefined;
  ratings: MovieRatings | null | undefined;
  badges: { badge_label: string; badge_color: string }[] | undefined;
  gameType: string;
  tugMultiplier?: number;
  castLinked: boolean;
  /** The Rabbit Hole's active tier ("Tier 3: Non-English"): shown as a check when the film complies. */
  tierLabel?: string;
  /** The frontier's tropes: the ones this candidate shares are highlighted. */
  frontierTropes: string[];
  /** The film this candidate would follow: the "Why this link?" pitch compares the two. */
  frontierMovieId: number;
  allowRepeats: boolean;
  onServer: boolean | null | undefined;
  pending: boolean;
  unknownFilterData?: boolean;
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
  const [revealed, setRevealed] = useState(false);
  const masked = roulette && !revealed;
  // The tagline (or, failing that, the plot) is fetched lazily - only while roulette is on.
  const { movie: detail } = useMovieDetail(candidate.movie_id, roulette);
  const teaser = detail?.tagline?.trim() || firstSentence(detail?.overview) || null;

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
          onClick={masked ? () => setRevealed(true) : onOpenDetails}
          aria-label={masked ? "Reveal this film" : `Open ${candidate.title}`}
          className="relative overflow-hidden rounded-md text-left transition-opacity hover:opacity-85"
        >
          <div className={cn(masked && "scale-110 backdrop-blur-md filter blur-md")}>
            <MoviePoster
              path={candidate.poster_path}
              title={masked ? "Hidden film" : candidate.title}
              className="w-full"
            />
          </div>
          {!masked && (
            <div className="absolute left-1 top-1">
              <OnServerBadge onServer={onServer} />
            </div>
          )}
          {candidate.dominant_color && !masked && (
            <ColorSwatch color={candidate.dominant_color} className="absolute right-1 top-1" />
          )}
        </button>

        {roulette && (
          <div className="flex flex-col items-start gap-1.5">
            <p className="font-serif text-base font-semibold italic leading-snug text-fuchsia-100">
              {teaser ? `“${teaser}”` : detail ? "No tagline on file." : "Fetching the tagline..."}
            </p>
            {masked && (
              <button
                type="button"
                onClick={() => setRevealed(true)}
                className="rounded-md border border-fuchsia-400/50 px-2 py-1 text-[10px] font-semibold text-fuchsia-200 hover:bg-fuchsia-500/10"
              >
                🎭 Reveal
              </button>
            )}
          </div>
        )}

        <div>
          {masked ? (
            <p className="text-xs font-medium tracking-widest text-zinc-600">? ? ?</p>
          ) : (
            <ClampedLabel
              text={candidate.title}
              lines={2}
              as="p"
              className="text-xs font-medium text-zinc-100"
            />
          )}
          <p className="text-[10px] text-zinc-500">{candidate.release_year ?? "—"}</p>
          <RatingBadges ratings={ratings} movieId={candidate.movie_id} />
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
        {Object.entries(candidate.overlay_ok ?? {}).map(([key, ok]) => (
          <span key={key} className={cn("w-fit rounded-full px-2 py-0.5 text-[10px]",
            ok === true ? "bg-emerald-950 text-emerald-300" : "bg-amber-950 text-amber-300")}>
            {ok === true ? "✓" : "!"} {key.replaceAll("_", " ")}{ok === false ? " · Needs unreachable-rule skip" : ""}
          </span>
        ))}
        {gameType === "tug_of_war" && candidate.tug_effect && candidate.tug_points != null && (
          <GlossaryChip
            term={candidate.tug_effect === "invasion" ? "raid" : candidate.tug_effect === "neutral" ? "bank" : candidate.tug_effect === "home" ? "build" : "sudden_death"}
            className={cn(
              "w-fit rounded-full px-2 py-0.5 text-[10px] font-semibold",
              candidate.tug_effect === "invasion"
                ? "bg-orange-950 text-orange-200"
                : candidate.tug_effect === "neutral" || candidate.tug_effect === "sudden_neutral"
                  ? "bg-amber-950 text-amber-200"
                  : "bg-lime-950 text-lime-200",
            )}
          >
            {tugEffectLabel(candidate.tug_effect, candidate.tug_points, tugMultiplier)}
            {candidate.tug_breaks_streak ? " · breaks their streak" : ""}
          </GlossaryChip>
        )}
        {tierLabel && candidate.tier_compliant !== undefined && candidate.tier_compliant !== null && (
          <span
            title={candidate.tier_compliant ? "Satisfies the active tier's rule" : "Breaks the active tier's rule"}
            className={cn(
              "flex w-fit items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold",
              candidate.tier_compliant ? "bg-emerald-950 text-emerald-300" : "bg-red-950 text-red-300",
            )}
          >
            {candidate.tier_compliant ? <Check className="h-3 w-3" /> : <X className="h-3 w-3" />}
            {candidate.tier_compliant ? tierLabel : "−1 ❤️ · Off-tier"}
          </span>
        )}
        <TropeChips tropes={candidate.tropes} highlight={frontierTropes} max={4} />
        {!masked && (
          <PitchButton
            previousMovieId={frontierMovieId}
            candidateMovieId={candidate.movie_id}
            linkLabel={candidate.connections[0]?.actor_name}
            className="w-fit"
          />
        )}
        {candidate.constraint_unverified && (
          <span
            title="This run's rule couldn't be checked for this film yet - logging will check it."
            className="w-fit rounded-full bg-amber-950 px-2 py-0.5 text-[9px] font-medium text-amber-400"
          >
            ? Rule unverified
          </span>
        )}
        {unknownFilterData && !candidate.constraint_unverified && (
          <span className="w-fit rounded-full bg-amber-950 px-2 py-0.5 text-[9px] text-amber-400"
            title="Missing filter data is kept visible; logging re-checks the rules.">
            ? Filter data unverified
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

/** The opening sentence of a plot, used as the tagline stand-in. */
function firstSentence(text: string | null | undefined): string | null {
  const trimmed = text?.trim();
  if (!trimmed) return null;
  const match = trimmed.match(/^.+?[.!?](\s|$)/);
  return (match ? match[0] : trimmed).trim();
}

/** The game's own mechanic for a candidate: year jump, country, poster colour or plot match. */
function MechanicBadge({
  candidate,
  gameType,
}: {
  candidate: DiscoveryCandidate;
  gameType: string;
}) {
  if (gameType === HISTORICAL_TIME_TRAVEL && candidate.narrative_year != null) {
    return (
      <div className="flex flex-col items-start gap-1">
        <span
          title="The year the story is set in (not the release year)"
          className="w-fit rounded-full bg-violet-950 px-2 py-0.5 text-[10px] font-semibold text-violet-200"
        >
          {narrativeSettingText(candidate.narrative_year, candidate.narrative_era_label)}
        </span>
        {candidate.narrative_delta != null && (
          <span className="flex w-fit items-center gap-1 rounded-full bg-app-surface-hover px-2 py-0.5 text-[10px] font-semibold text-violet-300">
            ⏳ {leapText(candidate.narrative_delta)}
          </span>
        )}
        {candidate.constraint_unverified && (
          <span
            title="This film's setting year couldn't be worked out yet; logging checks it again"
            className="w-fit text-[9px] text-amber-400"
          >
            unverified
          </span>
        )}
      </div>
    );
  }
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
    const countries = candidate.origin_countries ?? parseOriginCountries(candidate.origin_country);
    if (countries.length === 0) return null;
    return (
      <span
        title={countries.map((c) => countryName(c, c)).join(", ")}
        className="flex w-fit items-center gap-1 rounded-full bg-teal-950 px-2 py-0.5 text-[10px] font-semibold text-teal-300"
      >
        <CountryFlags codes={countries} />
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

/** One filter chip per person: crew first (director, composer, DP, writers), then top-billed cast;
 * someone who is both (a director who acts) is a single chip carrying both roles. */
interface FilterPerson {
  id: number;
  name: string;
  profile_path: string | null;
  roles: CraftRole[];
}

function mergeFilterPeople(crew: CrewMember[], cast: CastMember[]): FilterPerson[] {
  const order: CraftRole[] = ["director", "composer", "cinematographer", "writer", "actor"];
  const byId = new Map<number, FilterPerson>();
  const add = (id: number, name: string, profile: string | null, role: CraftRole) => {
    const person = byId.get(id) ?? { id, name, profile_path: profile, roles: [] };
    if (!person.roles.includes(role)) person.roles.push(role);
    person.profile_path = person.profile_path ?? profile;
    byId.set(id, person);
  };
  for (const member of [...crew].sort((a, b) => order.indexOf(a.role) - order.indexOf(b.role))) {
    add(member.person_id, member.name, member.profile_path, member.role);
  }
  for (const member of cast) add(member.actor_id, member.name, member.profile_path, "actor");
  return [...byId.values()].map((p) => ({ ...p, roles: p.roles.sort((a, b) => order.indexOf(a) - order.indexOf(b)) }));
}

function sharedLabel(connections: DiscoveryConnection[]): string {
  if (connections.some((c) => c.kind === "craft")) return "Credits";
  if (connections.every((c) => c.kind === "director")) return "Directors";
  return connections.some((c) => c.kind === "director") ? "Links" : "Actors";
}

function ConnectionBadge({ connections }: { connections: DiscoveryConnection[] }) {
  if (connections.length === 0) return null;

  // Crew & Craft Trail: one role badge per connecting person ("🎼 Composer: Hans Zimmer").
  if (connections.every((c) => connectionRole(c))) {
    return (
      <div className="flex flex-col items-start gap-1">
        {connections.slice(0, 3).map((connection) => (
          <RoleBadge
            key={`${connection.actor_id}-${connection.role_in_candidate}`}
            role={connectionRole(connection) as CraftRole}
            name={connection.actor_name}
            fromRole={connection.role_in_frontier}
          />
        ))}
        {connections.length > 3 && (
          <span className="text-[10px] text-zinc-500">+{connections.length - 3} more links</span>
        )}
      </div>
    );
  }

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
            loading="lazy"
            decoding="async"
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
  const { data: jellyfinStatus } = useJellyfinLookup([screen.movieId]);
  const { data: badgesMap } = useCanonBadgesBulk([screen.movieId]);

  const createStep = useCreateStep(runId);
  const [guard, setGuard] = useState<GuardStatus | null>(null);

  const allowRepeats = allowsMovieRepeats(rulesConfig);
  const skips = guard?.state === "no-connect" ? guard.result.overlay_skippable ?? [] : [];
  const pricing = forcePricing(skips.length ? { ...rulesConfig, lives_remaining: undefined } : rulesConfig);
  const wildcardsRemaining = pricing.remaining;
  const wildcardsExhausted = pricing.exhausted;
  const existingStepNumber = findExistingStepNumber(steps, screen.movieId);
  const isLockedDuplicate = existingStepNumber !== null && !allowRepeats;

  const genreNames = (movie?.genre_ids ?? [])
    .map((id) => genres?.find((g) => g.id === id)?.name)
    .filter((name): name is string => !!name);

  async function handleAdd(watched: boolean, connection?: DiscoveryConnection | ValidationResult["connections"][number]) {
    await createStep.mutateAsync({
      movie_id: screen.movieId,
      force: true,
      ...(skips.length ? { skip_overlays: skips } : {}),
      ...([2, 3].includes(rulesConfig.tug_rules_version ?? 1)
        ? { tug_team: tugNextTeam(rulesConfig) }
        : {}),
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
            <RatingBadges ratings={movie?.ratings} movieId={screen.movieId} />
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
              <ExpandableText
                text={movie.overview}
                fallback={isHydrating ? "Fetching description..." : "No overview available."}
                lines={4}
                className="mt-2 text-xs text-zinc-500"
              />
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
          pluralNoun={pricing.plural}
          castLinked={castLinked}
          onBuildBridge={() => navigate(`/tools/bridge?from=${frontierMovieId}&to=${screen.movieId}`)}
        />
      )}

      {!isLockedDuplicate && (() => {
        const lockedByWildcards =
          !screen.guaranteedConnected &&
          guard?.state === "no-connect" &&
          (wildcardsExhausted || !!guard.result.blocked);
        const disabled = createStep.isPending || guard?.state === "checking" || lockedByWildcards
          || (skips.length > 0 && wildcardsRemaining !== -1 && wildcardsRemaining < skips.length);
        return (
          <div className="flex gap-2">
            <button
              type="button"
              disabled={disabled || skips.length > 0}
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
              {skips.length ? `Spend ${skips.length} wildcard${skips.length === 1 ? "" : "s"} & log watched` : "Log Watched"}
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
                  loading="lazy"
                  decoding="async"
                  className="h-9 w-9 rounded-full object-cover"
                />
              ) : (
                <div className="flex h-9 w-9 items-center justify-center rounded-full bg-app-surface-hover text-zinc-500">
                  <User className="h-4 w-4" />
                </div>
              )}
              <ClampedLabel
                text={member.name}
                lines={2}
                as="span"
                className="text-[9px] leading-tight text-zinc-400"
              />
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
  pluralNoun,
  castLinked,
  onBuildBridge,
}: {
  guard: GuardStatus | null;
  frontierMovieTitle: string;
  wildcardsRemaining: number;
  wildcardsExhausted: boolean;
  /** What a forced step spends: "wildcards" (or "lives" in the Rabbit Hole). */
  pluralNoun: string;
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
          <Lock className="h-3.5 w-3.5" /> Rule broken & 0 {pluralNoun} remaining
        </p>
      ) : (
        <p className="flex items-center gap-1.5 font-medium text-amber-400">
          <AlertTriangle className="h-3.5 w-3.5" />
          {guard.result.reason ?? `Does not connect to current frontier (${frontierMovieTitle}).`} Adding
          this will consume 1 of {wildcardsRemaining === -1 ? "unlimited" : wildcardsRemaining} remaining{" "}
          {pluralNoun}.
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
  const { data: jellyfinStatus } = useJellyfinLookup(tmdbIds);

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
            loading="lazy"
            decoding="async"
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
              <ClampedLabel
                text={movie.title}
                lines={2}
                as="p"
                className="text-[10px] font-medium text-zinc-200"
              />
              <p className="text-[9px] text-zinc-500">{movie.release_year ?? "—"}</p>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
