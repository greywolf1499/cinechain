import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { useLiveTasks, type SystemTask } from "./tasks";
import { useAuthStore } from "../store/authStore";

const isFinished = (task: SystemTask<unknown>) => task.status === "completed" || task.status === "failed";

/** Follow the global feed; remounts resume only the requested key (or the caller's named job). */
export function useTrackedTask<R>(options: {
	onFinished?: (task: SystemTask<R>) => void;
	resumeNames?: string[];
	dedupeKey?: string;
}) {
	const { data: tasks, track, streamError } = useLiveTasks();
	const userId = useAuthStore((state) => state.user?.id);
	const [taskId, setTaskId] = useState<string | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [submitting, setSubmitting] = useState(false);
	const delivered = useRef(new Set<string>());
	const onFinished = useRef(options.onFinished);
	onFinished.current = options.onFinished;
	const task = (tasks?.find((item) =>
		!isFinished(item) && (options.dedupeKey ? item.dedupe_key === options.dedupeKey :
			item.user_id === userId && options.resumeNames?.includes(item.name))) ??
		tasks?.find((item) => item.id === taskId)) as SystemTask<R> | undefined;
	useEffect(() => {
		if (!task) return;
		setTaskId(task.id);
		if (isFinished(task) && !delivered.current.has(task.id)) {
			delivered.current.add(task.id);
			onFinished.current?.(task);
		}
	}, [task]);
	const start = useCallback(async (path: string, body?: unknown) => {
		setSubmitting(true);
		setError(null);
		try {
			const started = await api.post<SystemTask<R>>(path, body);
			setTaskId(started.id);
			track(started as SystemTask);
			return started;
		} catch (error) {
			setError(error instanceof Error ? error.message : "Request failed.");
		} finally {
			setSubmitting(false);
		}
	}, [track]);
	return { task: task ?? null, error: error ?? streamError,
		busy: submitting || !!task && !isFinished(task), start };
}
