import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import type {
	CacheStats,
	Run,
	RunDetail,
	RunStats,
	RunStatus,
	RulesConfig,
	StepStatus,
	UserSummary,
} from "../types/api";

export const queryKeys = {
	users: ["users"] as const,
	runs: (status?: RunStatus) => ["runs", status ?? "all"] as const,
	run: (id: string) => ["runs", id] as const,
	runStats: (id: string) => ["runs", id, "stats"] as const,
	cacheStats: ["system", "cache-stats"] as const,
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
