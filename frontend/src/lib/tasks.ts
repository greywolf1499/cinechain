import { createContext, createElement, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import Toast, { type ToastState } from "../components/Toast";
import { useAuthStore } from "../store/authStore";

export type TaskStatus = "pending" | "running" | "completed" | "failed";

export interface TaskProgress {
	stage?: string;
	current?: number | null;
	total?: number | null;
	message?: string;
}

export interface TaskErrorInfo {
	code: string;
	message: string;
	status?: number;
	username?: string;
}

export interface SystemTask<R = Record<string, unknown>> {
	id: string;
	name: string;
	status: TaskStatus;
	progress_data: {
		label?: string;
		progress?: TaskProgress;
		result?: R;
		error?: TaskErrorInfo;
	} | null;
	error: string | null;
	user_id: string | null;
	dedupe_key: string | null;
	cancel_requested: boolean;
	link: string | null;
	created_at: string;
	updated_at: string;
}

const isFinished = (task: { status: TaskStatus }) =>
	task.status === "completed" || task.status === "failed";

/** "120/400 films" style progress text for a running task. */
export function describeProgress(task: SystemTask<unknown>, unit = "films"): string {
	if (task.status === "pending") return "Queued...";
	const progress = task.progress_data?.progress;
	if (progress?.current != null) {
		return progress.total
			? `${progress.current}/${progress.total} ${unit}`
			: `${progress.current} ${unit}`;
	}
	return progress?.message ?? "Working...";
}

export const TASKS_KEY = ["tasks"] as const;
const HISTORY_LIMIT = 100;

function mergeTask(tasks: SystemTask[], incoming: SystemTask): SystemTask[] {
	const previous = tasks.find((task) => task.id === incoming.id);
	if (previous && previous.updated_at > incoming.updated_at) return tasks;
	const rest = tasks.filter((t) => t.id !== incoming.id);
	return [incoming, ...rest]
		.sort((a, b) => b.created_at.localeCompare(a.created_at))
		.slice(0, HISTORY_LIMIT);
}

/** Task list kept live by `GET /api/tasks/stream` (SSE). A slow poll stays on as
 * a safety net, and `live` tells the UI whether the stream is currently open. */
const TaskStreamContext = createContext<{
	live: boolean;
	streamError: string | null;
	track: (task: SystemTask) => void;
} | null>(null);

export function TaskStreamProvider({ children }: { children: ReactNode }) {
	const queryClient = useQueryClient();
	const [live, setLive] = useState(false);
	const [streamError, setStreamError] = useState<string | null>(null);
	const [toasts, setToasts] = useState<ToastState[]>([]);
	const seen = useRef(new Map<string, TaskStatus>());
	const ingest = useCallback((task: SystemTask, started = false) => {
		const cached = queryClient.getQueryData<SystemTask[]>(TASKS_KEY)?.find((item) => item.id === task.id);
		if (cached && cached.updated_at > task.updated_at) return;
		const previous = seen.current.get(task.id);
		if (isFinished(task) && (previous === "pending" || previous === "running" || started && !previous)) {
			const title = task.progress_data?.label ?? TASK_TITLES[task.name] ?? task.name;
			setToasts((queue) => [...queue, {
				type: task.status === "completed" ? "success" : "error",
				message: task.status === "completed" ? `${title} completed.` :
					`${title}: ${task.progress_data?.error?.message ?? task.error ?? "Failed."}`,
			}]);
			void queryClient.invalidateQueries({ queryKey: ["curated"] });
			void queryClient.invalidateQueries({ queryKey: ["passport"] });
		}
		seen.current.set(task.id, task.status);
		queryClient.setQueryData<SystemTask[]>(TASKS_KEY, (old) => mergeTask(old ?? [], task));
	}, [queryClient]);
	const query = useQuery({
		queryKey: TASKS_KEY,
		queryFn: async () => {
			const tasks = await api.get<SystemTask[]>(`/tasks?limit=${HISTORY_LIMIT}`);
			tasks.forEach((task) => ingest(task));
			return queryClient.getQueryData<SystemTask[]>(TASKS_KEY) ?? tasks;
		},
		refetchInterval: live ? false : 15_000,
	});

	useEffect(() => {
		if (typeof EventSource === "undefined") return;
		const source = new EventSource("/api/tasks/stream", { withCredentials: true });
		source.onopen = () => setLive(true);
		source.onerror = () => setLive(false); // the browser reconnects on its own
		source.addEventListener("task", (event) => {
			try {
				const task = JSON.parse((event as MessageEvent).data) as SystemTask;
				ingest(task);
				setStreamError(null);
			} catch (error) {
				setStreamError(error instanceof Error ? error.message : "Invalid task update.");
				setLive(false);
			}
		});
		return () => source.close();
	}, [ingest]);

	const track = useCallback((task: SystemTask) => ingest(task, true), [ingest]);
	return createElement(TaskStreamContext.Provider, { value: {
		live, streamError: streamError ?? (query.error ? query.error.message : null), track,
	} }, children, createElement(Toast, {
		toast: toasts[0] ?? null,
		onDismiss: () => setToasts((queue) => queue.slice(1)),
	}));
}

export function useLiveTasks() {
	const stream = useContext(TaskStreamContext);
	const user = useAuthStore((state) => state.user);
	if (!stream) throw new Error("Tasks must be used inside TaskStreamProvider.");
	const query = useQuery<SystemTask[]>({ queryKey: TASKS_KEY, enabled: false });
	return { ...query, ...stream, data: query.data?.filter((task) => user?.is_admin || task.user_id === user?.id) };
}

export const TASK_TITLES: Record<string, string> = {
	watchlist_sync: "Letterboxd watchlist sync",
	curated_list_sync: "Curated list sync",
	discover_hq: "HQ account discovery",
	canon_hydrate: "Canon film indexing",
	diary_import_csv: "Diary CSV import",
	diary_import_rss: "Diary RSS import",
	passport_backfill_directors: "Director lookup",
	llm_model_download: "Local model download",
};
