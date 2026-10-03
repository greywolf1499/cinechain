import { ApiError, api } from "./api";

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
	created_at: string;
	updated_at: string;
}

const POLL_INTERVAL_MS = 1000;
const MAX_CONSECUTIVE_POLL_FAILURES = 5;

const isFinished = (task: { status: TaskStatus }) =>
	task.status === "completed" || task.status === "failed";

/** Polls a background task until it completes or fails. A failed task is
 * *returned* (inspect `progress_data.error`), not thrown - only transport
 * problems throw. */
export async function waitForTask<R = Record<string, unknown>>(
	taskId: string,
	onUpdate?: (task: SystemTask<R>) => void,
): Promise<SystemTask<R>> {
	let failures = 0;
	for (;;) {
		try {
			const task = await api.get<SystemTask<R>>(`/tasks/${taskId}`);
			failures = 0;
			onUpdate?.(task);
			if (isFinished(task)) return task;
		} catch (err) {
			// A pruned/foreign task or a lost session will never recover; a blip might.
			const permanent = err instanceof ApiError && err.status < 500;
			if (permanent || ++failures >= MAX_CONSECUTIVE_POLL_FAILURES) throw err;
		}
		await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
	}
}

/** POSTs an endpoint that returns 202 + a SystemTask, then waits for it. */
export async function runTask<R = Record<string, unknown>>(
	path: string,
	body?: unknown,
	onUpdate?: (task: SystemTask<R>) => void,
): Promise<SystemTask<R>> {
	const started = await api.post<SystemTask<R>>(path, body);
	onUpdate?.(started);
	return isFinished(started)
		? started
		: waitForTask<R>(started.id, onUpdate);
}

/** "120/400 films" style progress text for a running task. */
export function describeProgress(task: SystemTask): string {
	if (task.status === "pending") return "Queued...";
	const progress = task.progress_data?.progress;
	if (progress?.current != null) {
		return progress.total
			? `${progress.current}/${progress.total} films`
			: `${progress.current} films`;
	}
	return progress?.message ?? "Working...";
}
