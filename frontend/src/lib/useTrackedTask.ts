import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "./api";
import { runTask, waitForTask, type SystemTask } from "./tasks";

const isFinished = (task: SystemTask<unknown>) => task.status === "completed" || task.status === "failed";

/** Starts a task-backed endpoint and follows it with the shared polling helper.
 * With `resumeNames`, a matching task still running from an earlier visit (or tab)
 * is picked up on mount, so a reload mid-import keeps its live progress bar. */
export function useTrackedTask<R>(options: {
	onFinished?: (task: SystemTask<R>) => void;
	resumeNames?: string[];
}) {
	const [task, setTask] = useState<SystemTask<R> | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [submitting, setSubmitting] = useState(false);
	const controller = useRef<AbortController | null>(null);
	const onFinished = useRef(options.onFinished);
	onFinished.current = options.onFinished;

	const follow = useCallback(async (run: (signal: AbortSignal) => Promise<SystemTask<R>>) => {
		controller.current?.abort();
		const abort = new AbortController();
		controller.current = abort;
		setError(null);
		try {
			const finished = await run(abort.signal);
			if (!abort.signal.aborted) onFinished.current?.(finished);
		} catch (err) {
			if (abort.signal.aborted) return;
			setError(err instanceof ApiError || err instanceof Error ? err.message : "Request failed.");
		} finally {
			if (!abort.signal.aborted) setSubmitting(false);
		}
	}, []);

	const start = useCallback(
		(path: string, body?: unknown) => {
			setTask(null);
			setSubmitting(true);
			return follow((signal) => runTask<R>(path, body, setTask, signal));
		},
		[follow],
	);

	const resumeKey = options.resumeNames?.join(",");
	useEffect(() => {
		if (!resumeKey) return;
		let cancelled = false;
		api.get<SystemTask<R>[]>("/tasks?active=true").then((active) => {
			const running = active.find((t) => resumeKey.split(",").includes(t.name));
			if (running && !cancelled) {
				setTask(running);
				setSubmitting(true);
				void follow((signal) => waitForTask<R>(running.id, setTask, signal));
			}
		}).catch(() => {});
		return () => {
			cancelled = true;
		};
	}, [resumeKey, follow]);

	useEffect(() => () => controller.current?.abort(), []);

	return { task, error, busy: submitting || (task !== null && !isFinished(task)), start };
}
