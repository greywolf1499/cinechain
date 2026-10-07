import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import { actingFields, notifyTableLog, syncTableRun } from "./tableMode";
import type {
	AcquisitionStatus,
	CacheStats,
	CanonBadge,
	IntegrationStatus,
	AccountKind,
	AccountSort,
	CuratedAccount,
	CuratedAccountLists,
	CuratedListSummary,
	CuratedSlices,
	SeedOptions,
	ListSort,
	ListState,
	Page,
	Passport,
	DiscoveryCandidate,
	Suggestion,
	EngineMeta,
	RunRulebook,
	GoldenVetoResult,
	MovieDetail,
	NarrativeEra,
	TropeExtraction,
	DailyConvertResult,
	DailyForfeitResult,
	DailyHopResult,
	DailyPuzzle,
	Run,
	RunDetail,
	RunStats,
	ConstraintInfo,
	LlmStatus,
	TunnelSide,
	TunnelState,
	TunnelHintResponse,
	RawRulesConfig,
	RunStatus,
	RulesConfig,
	RequestConfig,
	RouterResult,
	RouterWeights,
	RunStep,
	SplitPool,
	StepStatus,
	UserSummary,
	JellyfinItemSummary,
	WatchlistStatus,
} from "../types/api";

export const queryKeys = {
	users: ["users"] as const,
	runs: (status?: RunStatus) => ["runs", status ?? "all"] as const,
	run: (id: string) => ["runs", id] as const,
	runStats: (id: string) => ["runs", id, "stats"] as const,
	cacheStats: ["system", "cache-stats"] as const,
	curatedLists: ["curated", "lists"] as const,
	watchlistStatus: ["curated", "watchlist", "status"] as const,
	integrationsStatus: ["integrations", "status"] as const,
	curatedAccounts: ["curated", "accounts"] as const,
	discover: (runId: string, frontierMovieId: number, mode: "or" | "and") =>
		["runs", runId, "discover", frontierMovieId, mode] as const,
};

export function useUsers() {
	return useQuery({
		queryKey: queryKeys.users,
		queryFn: () => api.get<UserSummary[]>("/users"),
	});
}

export function useRuns(status?: RunStatus) {
	return useQuery({
		queryKey: queryKeys.runs(status),
		queryFn: () => {
			const suffix = status ? `?status=${status}` : "";
			return api.get<Run[]>(`/runs${suffix}`);
		},
	});
}

export function useRun(runId: string | undefined) {
	return useQuery({
		queryKey: queryKeys.run(runId ?? ""),
		queryFn: async () => {
			const run = await api.get<RunDetail>(`/runs/${runId}`);
			syncTableRun(run);
			return run;
		},
		enabled: !!runId,
	});
}

export function useEngines() {
	return useQuery({
		queryKey: ["engines"],
		queryFn: () => api.get<EngineMeta[]>("/engines"),
		staleTime: 5 * 60_000,
	});
}

export function useRunRulebook(runId: string | undefined) {
	return useQuery({
		queryKey: ["runs", runId ?? "", "rulebook"],
		queryFn: () => api.get<RunRulebook>(`/runs/${runId}/rulebook`),
		enabled: !!runId,
	});
}

export function useRunCoach(runId: string | undefined) {
	return useQuery({
		queryKey: ["runs", runId ?? "", "coach"],
		queryFn: () => api.get<{ line: string | null }>(`/runs/${runId}/coach`),
		enabled: !!runId,
	});
}

export function useRunStats(runId: string | undefined) {
	return useQuery({
		queryKey: queryKeys.runStats(runId ?? ""),
		queryFn: () => api.get<RunStats>(`/runs/${runId}/stats`),
		enabled: !!runId,
	});
}

/** Meet in the Middle: both frontiers and their cached distance. */
export function useTunnelState(runId: string | undefined, enabled = true) {
	return useQuery({
		queryKey: [...queryKeys.run(runId ?? ""), "tunnel"],
		queryFn: () => api.get<TunnelState>(`/runs/${runId}/tunnel`),
		enabled: !!runId && enabled,
		staleTime: 60_000,
	});
}

export function useTunnelHint(runId: string) {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: (payload: { side: "head" | "tail"; level: "actor" | "film" }) =>
			api.post<TunnelHintResponse>(`/runs/${runId}/tunnel/hint`, payload),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) });
		},
	});
}

/** Are the opt-in generative features (pitches, teasers) switched on? */
export function useLlmStatus() {
	return useQuery({
		queryKey: ["engine", "llm-status"],
		queryFn: () => api.get<LlmStatus>("/engine/llm/status"),
		staleTime: 60_000,
	});
}

/** The rule shaping the run's next hop (e.g. "must be a Director"), or null. */
export function useRunConstraint(runId: string | undefined) {
	return useQuery({
		// Under the run's key prefix, so every step mutation refreshes it.
		queryKey: [...queryKeys.run(runId ?? ""), "constraint"],
		queryFn: () => api.get<ConstraintInfo | null>(`/runs/${runId}/constraint`),
		enabled: !!runId,
	});
}

export function useJellyfinLookup(tmdbIds: number[]) {
	return useQuery({
		queryKey: ["jellyfin", "lookup", tmdbIds],
		queryFn: () =>
			api.post<Record<string, JellyfinItemSummary>>("/integrations/jellyfin/lookup", {
				tmdb_ids: tmdbIds,
			}),
		enabled: tmdbIds.length > 0,
	});
}

export function useCacheStats() {
	return useQuery({
		queryKey: queryKeys.cacheStats,
		queryFn: () => api.get<CacheStats>("/system/cache/stats"),
	});
}

export function useCuratedLists() {
	return useQuery({
		queryKey: queryKeys.curatedLists,
		queryFn: () => api.get<CuratedListSummary[]>("/curated/lists"),
	});
}

export function useCuratedSlices(listId: string | undefined) {
	return useQuery({
		queryKey: ["curated", "slices", listId],
		queryFn: () => api.get<CuratedSlices>(`/curated-lists/${listId}/slices`),
		enabled: !!listId,
		refetchInterval: (query) => query.state.data?.indexing ? 2000 : false,
	});
}

export function useSeedOptions(
	gameType: string,
	rules: RulesConfig | RawRulesConfig,
	enabled: boolean,
	indexing = false,
) {
	return useQuery({
		queryKey: ["movies", "seed-options", gameType, rules],
		queryFn: () => api.post<SeedOptions>("/movies/seed-options", {
			game_type: gameType,
			rules_config: rules,
		}),
		enabled,
		retry: false,
		refetchInterval: indexing ? 2000 : false,
	});
}

export function useWatchlistStatus() {
	return useQuery({
		queryKey: queryKeys.watchlistStatus,
		queryFn: () => api.get<WatchlistStatus>("/curated/watchlist/status"),
		staleTime: 30_000,
	});
}

export function useIntegrationStatus() {
	return useQuery({
		queryKey: queryKeys.integrationsStatus,
		queryFn: () => api.get<IntegrationStatus>("/integrations/status"),
		staleTime: 30_000,
	});
}

export interface ListBrowseParams {
	q: string;
	sort: ListSort;
	state: ListState;
	account?: string;
	page: number;
	pageSize?: number;
}

export function useBrowseLists(params: ListBrowseParams) {
	const query = new URLSearchParams({
		q: params.q,
		sort: params.sort,
		state: params.state,
		page: String(params.page),
		page_size: String(params.pageSize ?? 20),
	});
	if (params.account) query.set("account", params.account);
	return useQuery({
		queryKey: ["curated", "lists", "browse", query.toString()],
		queryFn: () => api.get<Page<CuratedListSummary>>(`/curated/lists/browse?${query}`),
		placeholderData: (previous) => previous,
	});
}

export interface AccountBrowseParams {
	q: string;
	sort: AccountSort;
	kind: AccountKind;
	page: number;
	pageSize?: number;
}

export function useBrowseAccounts(params: AccountBrowseParams) {
	const query = new URLSearchParams({
		q: params.q,
		sort: params.sort,
		kind: params.kind,
		page: String(params.page),
		page_size: String(params.pageSize ?? 24),
	});
	return useQuery({
		queryKey: ["curated", "accounts", "browse", query.toString()],
		queryFn: () => api.get<Page<CuratedAccount>>(`/curated/accounts/browse?${query}`),
		placeholderData: (previous) => previous,
	});
}

export function useCuratorProfile(username: string) {
	return useQuery({
		queryKey: ["curated", "accounts", "profile", username],
		queryFn: () => api.get<CuratedAccountLists>(`/curated/accounts/${username}/lists`),
		retry: false,
	});
}

export function useCuratedAccounts() {
	return useQuery({
		queryKey: queryKeys.curatedAccounts,
		queryFn: () => api.get<CuratedAccount[]>("/curated/accounts"),
	});
}

export function useRequestConfig() {
	return useQuery({
		queryKey: ["integrations", "request-config"],
		queryFn: () => api.get<RequestConfig>("/integrations/request-config"),
		staleTime: 60_000,
	});
}

export const acquisitionKey = (tmdbId: number) =>
	["integrations", "acquisition", tmdbId] as const;

export function useAcquisitionStatus(tmdbId: number, enabled = true) {
	return useQuery({
		queryKey: acquisitionKey(tmdbId),
		queryFn: async (): Promise<AcquisitionStatus> => {
			const result = await api.post<Record<string, AcquisitionStatus>>(
				"/integrations/status/lookup",
				{ tmdb_ids: [tmdbId] },
			);
			return result[String(tmdbId)] ?? { state: "missing", source: null };
		},
		enabled,
		staleTime: 30_000,
	});
}

export const MIN_OVERVIEW_LENGTH = 20;

/** Movie detail with JIT hydration: bridge/actor-expansion payloads carry no
 * overview, so a missing/short one triggers a one-off forced TMDB refresh. */
export function useMovieDetail(movieId: number | undefined, enabled = true) {
	const base = useQuery({
		queryKey: ["movies", movieId],
		queryFn: () => api.get<MovieDetail>(`/movies/${movieId}`),
		enabled: enabled && movieId !== undefined,
	});
	const needsHydration =
		enabled &&
		!!base.data &&
		(base.data.overview?.trim().length ?? 0) < MIN_OVERVIEW_LENGTH;
	const hydrated = useQuery({
		queryKey: ["movies", movieId, "hydrated"],
		queryFn: () => api.get<MovieDetail>(`/movies/${movieId}?refresh=true`),
		enabled: needsHydration,
		staleTime: Number.POSITIVE_INFINITY,
		retry: false,
	});
	return {
		movie: hydrated.data ?? base.data,
		isHydrating: needsHydration && hydrated.isFetching,
		error: base.error ?? hydrated.error,
		refetch: () => needsHydration ? hydrated.refetch() : base.refetch(),
	};
}

export function useRefreshRatings() {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: (movieId: number) => api.get<MovieDetail>(`/movies/${movieId}?refresh_ratings=true`),
		onSuccess: (movie) => {
			queryClient.setQueryData(["movies", movie.tmdb_id], movie);
			queryClient.setQueryData(["movies", movie.tmdb_id, "hydrated"], movie);
			queryClient.invalidateQueries({ queryKey: ["movies", "ratings", "bulk"] });
			queryClient.invalidateQueries({
				queryKey: ["runs"],
				predicate: (query) => query.queryKey[2] === "split-pool",
			});
		},
	});
}

/** A film's extracted tropes. Reads the cached ones off the detail; when they are missing and the
 * LLM is on, asks the server to extract (and cache) them once. Empty = none / LLM off. */
export function useMovieTropes(movieId: number | undefined, enabled = true) {
	const { data: llm } = useLlmStatus();
	const { movie } = useMovieDetail(movieId, enabled);
	const cached = movie?.extracted_tropes ?? null;
	const extraction = useQuery({
		queryKey: ["movies", movieId, "tropes"],
		queryFn: () => api.post<TropeExtraction>(`/movies/${movieId}/tropes/extract`),
		enabled: enabled && movieId !== undefined && !!movie && cached === null && !!llm?.enabled,
		staleTime: Number.POSITIVE_INFINITY,
		retry: false,
	});
	return {
		tropes: cached ?? extraction.data?.tropes ?? [],
		isExtracting: extraction.isFetching,
	};
}

export function useCanonBadgesBulk(movieIds: number[]) {
	return useQuery({
		queryKey: ["curated", "badges", "bulk", movieIds],
		queryFn: () =>
			api.post<Record<string, CanonBadge[]>>("/curated/badges/bulk", {
				movie_ids: movieIds,
			}),
		enabled: movieIds.length > 0,
	});
}

export interface DiscoverOptions {
	/** The Chaser: only short (<= 95 min) Comedy / Animation palate cleansers. */
	chaser?: boolean;
	/** Underdog B-Sides: least popular first, dead entries (popularity < 1) dropped. */
	underdog?: boolean;
	includeOffTier?: boolean;
}

export function useDiscoverCandidates(
	runId: string,
	frontierMovieId: number | undefined,
	mode: "or" | "and",
	options: DiscoverOptions = {},
) {
	const { chaser = false, underdog = false, includeOffTier = false } = options;
	return useQuery({
		queryKey: [...queryKeys.discover(runId, frontierMovieId ?? 0, mode), { chaser, underdog, includeOffTier }],
		queryFn: () =>
			api.get<DiscoveryCandidate[]>(
				`/runs/${runId}/discover?frontier_movie_id=${frontierMovieId}&mode=${mode}` +
					(chaser ? "&chaser=true" : "") +
					(includeOffTier ? "&include_off_tier=true" : "") +
					(underdog ? "&sort_by=underdog" : ""),
			),
		enabled: !!frontierMovieId,
	});
}

export function useRunSuggestions(runId: string) {
	return useMutation({
		mutationFn: (filters: { frontier_movie_id: number; country?: string; decade?: number; genre_id?: number; chaser?: boolean; sort_by?: "underdog" }) => {
			const params = new URLSearchParams();
			for (const [key, value] of Object.entries(filters)) {
				if (value !== undefined) params.set(key, String(value));
			}
			return api.get<Suggestion[]>(`/runs/${runId}/suggestions?${params}`);
		},
	});
}

/** The Chaos Button: roll (POST) or cancel (DELETE) the one-step handicap. */
export function useChaos(runId: string) {
	const queryClient = useQueryClient();
	const onSuccess = () => {
		queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) });
	};
	return {
		roll: useMutation({ mutationFn: () => api.post<RunDetail>(`/runs/${runId}/chaos`), onSuccess }),
		cancel: useMutation({ mutationFn: () => api.delete<RunDetail>(`/runs/${runId}/chaos`), onSuccess }),
	};
}

export function useCreateRun() {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: (payload: {
			name: string;
			game_type: string;
			participant_user_ids: string[];
			seed_movie_id?: number | null;
			/** Meet in the Middle: Partner B's starting film. */
			tail_seed_movie_id?: number | null;
			rules_config?: RulesConfig | RawRulesConfig;
		}) => api.post<RunDetail>("/runs", payload),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: ["runs"] });
		},
	});
}

export function useUpdateRun(runId: string) {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: (payload: { name?: string; status?: RunStatus }) =>
			api.patch<RunDetail>(`/runs/${runId}`, payload),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) });
			queryClient.invalidateQueries({ queryKey: ["runs"] });
		},
	});
}

export function useUpdateRunRules(runId: string) {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: (payload: Partial<RulesConfig>) =>
			api.patch<RunDetail>(`/runs/${runId}/rules`, payload),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) });
			queryClient.invalidateQueries({ queryKey: ["runs"] });
		},
	});
}

export function useWrapMarathon(runId: string) {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: () => api.post<RunDetail>(`/runs/${runId}/wrap`),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) });
			queryClient.invalidateQueries({ queryKey: ["runs"] });
		},
	});
}

/** Every Blind Fork / Golden Veto call changes the run (and the veto balance in /auth/me). */
function useRunMutation<TVariables, TResult>(
	runId: string,
	mutationFn: (variables: TVariables) => Promise<TResult>,
) {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn,
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) });
			queryClient.invalidateQueries({ queryKey: ["runs"] });
			queryClient.invalidateQueries({ queryKey: ["auth", "me"] });
			queryClient.invalidateQueries({ queryKey: queryKeys.users });
		},
	});
}

export function useOfferFork(runId: string) {
	return useRunMutation(runId, (payload: { movie_ids: number[]; links?: Record<number, Record<string, unknown>> }) =>
		api.post<RunDetail>(`/runs/${runId}/fork`, { ...payload, ...actingFields(runId) }),
	);
}

export function useWithdrawFork(runId: string) {
	return useRunMutation(runId, (_: void) => {
		const actor = actingFields(runId).acting_participant_id;
		return api.delete<RunDetail>(`/runs/${runId}/fork${actor ? `?acting_participant_id=${encodeURIComponent(actor)}` : ""}`);
	});
}

export function useVetoForkMovie(runId: string) {
	return useRunMutation(runId, (movieId: number) =>
		api.post<RunDetail>(`/runs/${runId}/fork/veto`, { movie_id: movieId, ...actingFields(runId) }),
	);
}

export function useAcceptForkMovie(runId: string) {
	return useRunMutation(runId, async (movieId: number) => {
		const step = await api.post<RunStep>(`/runs/${runId}/fork/accept`, { movie_id: movieId, ...actingFields(runId) });
		notifyTableLog(step);
		return step;
	});
}

export function useGoldenVeto(runId: string) {
	return useRunMutation(runId, (target: "fork" | "step") =>
		api.post<GoldenVetoResult>(`/runs/${runId}/veto`, { target, ...actingFields(runId) }),
	);
}

export function useDeleteRun() {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: (runId: string) => api.delete(`/runs/${runId}`),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: ["runs"] });
		},
	});
}

export function useCreateStep(runId: string) {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: (payload: {
			movie_id: number;
			transition_metadata?: Record<string, unknown> | null;
			user_notes?: string | null;
			force?: boolean;
			status?: StepStatus;
			watched_at?: string | null;
			/** Meet in the Middle: which end of the tunnel this film extends. */
			tunnel_side?: TunnelSide;
			/** Tug of War: team whose turn is being logged (shared-device support). */
			tug_team?: "team_a" | "team_b";
			/** Rotten Tomatoes Split: the household's joint rating (1-100). */
			household_score?: number;
			no_contest?: boolean;
			skip_overlays?: string[];
		}) => api.post<RunStep>(`/runs/${runId}/steps`, { ...payload, ...actingFields(runId) }),
		onSuccess: (step) => {
			notifyTableLog(step);
			queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) });
			// A logged step can end the run (win/fail), which changes the list badge.
			queryClient.invalidateQueries({ queryKey: ["runs"] });
		},
	});
}

/** March Madness: decide a matchup (the winner is logged as watched). */
export function useAdvanceBracket(runId: string) {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: (vars: { matchup_id: string; winning_movie_id: number }) =>
			api.post<RunDetail>(`/runs/${runId}/bracket/advance`, { ...vars, ...actingFields(runId) }),
		onSuccess: (run) => {
			syncTableRun(run);
			queryClient.setQueryData(queryKeys.run(runId), run);
			queryClient.invalidateQueries({ queryKey: ["runs"] });
		},
	});
}

/** March Madness: a partner's vote on a matchup (a majority resolves it). */
export function useBracketVote(runId: string) {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: (vars: { matchup_id: string; movie_id: number; acting_participant_id?: string }) =>
			api.post<RunDetail>(`/runs/${runId}/bracket/vote`, { ...actingFields(runId), ...vars }),
		onSuccess: (run) => {
			syncTableRun(run);
			queryClient.setQueryData(queryKeys.run(runId), run);
			queryClient.invalidateQueries({ queryKey: ["runs"] });
		},
	});
}

export interface MatchupCommentary {
	matchup_id: string;
	/** "" when the AI model is off. */
	commentary: string;
	enabled: boolean;
	cached: boolean;
}

/** March Madness: the AI announcer's "Tale of the Tape" for a matchup (generated once, then cached on the run). */
export function useMatchupCommentary(runId: string) {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: (matchupId: string) =>
			api.post<MatchupCommentary>(`/runs/${runId}/bracket/commentary`, { matchup_id: matchupId }),
		onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) }),
	});
}

/** Bounty Board: "✨ Roll Custom Bounty" - the AI writes a new bounty for the board. */
export function useRollCustomBounty(runId: string) {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: () => api.post<RunDetail>(`/runs/${runId}/bounties/custom`),
		onSuccess: (run) => queryClient.setQueryData(queryKeys.run(runId), run),
	});
}

export function useDiscardBounty(runId: string) {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: (bountyId: string) =>
			api.post<RunDetail>(`/runs/${runId}/bounties/${encodeURIComponent(bountyId)}/discard`),
		onSuccess: (run) => queryClient.setQueryData(queryKeys.run(runId), run),
	});
}

export function useRabbitHoleReroll(runId: string) {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: () => api.post<RunDetail>(`/runs/${runId}/rabbit-hole/reroll`),
		onSuccess: (run) => {
			queryClient.setQueryData(queryKeys.run(runId), run);
			queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) });
		},
	});
}

export function useRabbitHoleSkipCurse(runId: string) {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: () => api.post<RunDetail>(`/runs/${runId}/rabbit-hole/skip-curse`),
		onSuccess: (run) => {
			queryClient.setQueryData(queryKeys.run(runId), run);
			queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) });
		},
	});
}

export function useMarkStepWatched(runId: string) {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: ({
			stepId,
			...payload
		}: {
			stepId: string;
			watched_at?: string | null;
			user_notes?: string | null;
			household_score?: number;
			no_contest?: boolean;
		}) => api.patch<RunStep>(`/runs/${runId}/steps/${stepId}/mark-watched`, { ...payload, ...actingFields(runId) }),
		onSuccess: (step) => {
			notifyTableLog(step);
			queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) });
			// A logged step can end the run (win/fail), which changes the list badge.
			queryClient.invalidateQueries({ queryKey: ["runs"] });
		},
	});
}

export function useUpdateStep(runId: string) {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: ({
			stepId,
			...payload
		}: {
			stepId: string;
			user_notes?: string | null;
			watched_at?: string | null;
		}) => api.patch(`/runs/${runId}/steps/${stepId}`, { ...payload, ...actingFields(runId) }),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) });
			// A logged step can end the run (win/fail), which changes the list badge.
			queryClient.invalidateQueries({ queryKey: ["runs"] });
		},
	});
}

export function useDeleteStep(runId: string) {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: (stepId: string) =>
			api.delete(`/runs/${runId}/steps/${stepId}`),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) });
		},
	});
}

export const PASSPORT_KEY = ["passport", "me"] as const;

export function usePassport() {
	return useQuery({
		queryKey: PASSPORT_KEY,
		queryFn: () => api.get<Passport>("/passport/me"),
	});
}

// --- The Daily Bridge ---

const DAILY_KEY = ["puzzles", "daily"] as const;

export function useDailyPuzzle() {
	return useQuery({
		queryKey: DAILY_KEY,
		queryFn: () => api.get<DailyPuzzle>("/puzzles/daily"),
		staleTime: 60_000,
		retry: false,
	});
}

/** Checks (and, when it continues the chain, records) a hop; a solve or move refreshes the board. */
export function useValidateDailyHop() {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: (vars: { current_movie_id: number; next_movie_id: number }) =>
			api.post<DailyHopResult>("/puzzles/daily/validate-hop", vars),
		onSuccess: (result) => {
			if (result.recorded) void queryClient.invalidateQueries({ queryKey: DAILY_KEY });
		},
	});
}

export function useUndoDailyHop() {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: () => api.post<DailyPuzzle>("/puzzles/daily/undo"),
		onSuccess: (puzzle) => queryClient.setQueryData(DAILY_KEY, puzzle),
	});
}

export function useForfeitDaily() {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: () => api.post<DailyForfeitResult>("/puzzles/daily/forfeit"),
		onSuccess: () => queryClient.invalidateQueries({ queryKey: DAILY_KEY }),
	});
}

export function useConvertDailyToRun() {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: () => api.post<DailyConvertResult>("/puzzles/daily/convert-to-run"),
		onSuccess: () => {
			void queryClient.invalidateQueries({ queryKey: DAILY_KEY });
			void queryClient.invalidateQueries({ queryKey: ["runs"] });
		},
	});
}

/** The Rotten Tomatoes Split: cached films critics and audiences disagree about. `scan` first
 * rates up to that many more cached films through OMDb. */
export function useSplitPool(runId: string, scan = 0) {
	return useQuery({
		queryKey: [...queryKeys.run(runId), "split-pool", scan],
		queryFn: () => api.get<SplitPool>(`/runs/${runId}/split-pool?scan=${scan}`),
		staleTime: 30_000,
	});
}

/** The Perfect Marathon Router: the smoothest order for these films. */
export function useOptimizeMarathon() {
	return useMutation({
		mutationFn: (payload: { movie_ids: number[] } & RouterWeights) =>
			api.post<RouterResult>("/tools/router/optimize", payload),
	});
}

/** Queues a router marathon as an active run of planned steps, in the order given. */
export function useConvertMarathonToRun() {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: (payload: { run_name: string; movie_ids: number[] }) =>
			api.post<RunDetail>("/tools/router/convert-to-run", payload),
		onSuccess: () => {
			void queryClient.invalidateQueries({ queryKey: ["runs"] });
		},
	});
}

/** Historical Time-Travel: corrects (or, with no body, re-resolves) a film's setting year. */
export function useUpdateNarrativeEra(runId?: string) {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: ({
			movieId,
			...payload
		}: {
			movieId: number;
			narrative_year?: number;
			narrative_era_label?: string;
		}) => api.post<NarrativeEra>(`/movies/${movieId}/narrative-era`, payload),
		onSuccess: () => {
			if (runId) void queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) });
			void queryClient.invalidateQueries({ queryKey: ["runs"] });
		},
	});
}
