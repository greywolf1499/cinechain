import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import type {
	AcquisitionStatus,
	CacheStats,
	CanonBadge,
	AccountKind,
	AccountSort,
	CuratedAccount,
	CuratedAccountLists,
	CuratedListSummary,
	ListSort,
	ListState,
	Page,
	DiscoveryCandidate,
	MovieDetail,
	Run,
	RunDetail,
	RunStats,
	RunStatus,
	RulesConfig,
	RequestConfig,
	StepStatus,
	UserSummary,
} from "../types/api";

export const queryKeys = {
	users: ["users"] as const,
	runs: (status?: RunStatus) => ["runs", status ?? "all"] as const,
	run: (id: string) => ["runs", id] as const,
	runStats: (id: string) => ["runs", id, "stats"] as const,
	cacheStats: ["system", "cache-stats"] as const,
	curatedLists: ["curated", "lists"] as const,
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
		queryFn: () => api.get<RunDetail>(`/runs/${runId}`),
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

export function useDiscoverCandidates(
	runId: string,
	frontierMovieId: number | undefined,
	mode: "or" | "and",
) {
	return useQuery({
		queryKey: queryKeys.discover(runId, frontierMovieId ?? 0, mode),
		queryFn: () =>
			api.get<DiscoveryCandidate[]>(
				`/runs/${runId}/discover?frontier_movie_id=${frontierMovieId}&mode=${mode}`,
			),
		enabled: !!frontierMovieId,
	});
}

export function useCreateRun() {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: (payload: {
			name: string;
			game_type: string;
			participant_user_ids: string[];
			seed_movie_id?: number | null;
			rules_config?: RulesConfig;
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
		mutationFn: (payload: RulesConfig) =>
			api.patch<RunDetail>(`/runs/${runId}/rules`, payload),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) });
			queryClient.invalidateQueries({ queryKey: ["runs"] });
		},
	});
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
		}) => api.post(`/runs/${runId}/steps`, payload),
		onSuccess: () => {
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
		}) => api.patch(`/runs/${runId}/steps/${stepId}/mark-watched`, payload),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) });
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
		}) => api.patch(`/runs/${runId}/steps/${stepId}`, payload),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) });
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
