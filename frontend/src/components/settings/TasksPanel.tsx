import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Clock, Loader2, Radio, XCircle } from "lucide-react";
import { SettingsCard } from "./shared";
import TaskProgressBar from "../TaskProgressBar";
import { cn } from "../../lib/cn";
import { useUsers } from "../../lib/queries";
import { TASK_TITLES, describeProgress, useLiveTasks, type SystemTask } from "../../lib/tasks";
import { useAuthStore } from "../../store/authStore";

const HISTORY_SHOWN = 50;

function useNow(active: boolean): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);
  return now;
}

function formatDuration(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return minutes < 60 ? `${minutes}m ${seconds % 60}s` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

const titleOf = (task: SystemTask) => TASK_TITLES[task.name] ?? task.name;
const isActive = (task: SystemTask) => task.status === "pending" || task.status === "running";

function StatusBadge({ status }: { status: SystemTask["status"] }) {
  const config = {
    pending: { label: "Queued", icon: Clock, className: "border-zinc-700 bg-zinc-800 text-zinc-300" },
    running: { label: "Running", icon: Loader2, className: "border-sky-800 bg-sky-950 text-sky-300" },
    completed: { label: "Completed", icon: CheckCircle2, className: "border-emerald-800 bg-emerald-950 text-emerald-300" },
    failed: { label: "Failed", icon: XCircle, className: "border-red-900 bg-red-950 text-red-300" },
  }[status];
  const Icon = config.icon;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold",
        config.className,
      )}
    >
      <Icon className={cn("h-3 w-3", status === "running" && "animate-spin")} />
      {config.label}
    </span>
  );
}

function resultSummary(task: SystemTask): string {
  const result = task.progress_data?.result;
  if (!result) return "-";
  const { matched, total_films: total, discovered, new: added } = result as Record<string, number>;
  if (matched !== undefined && total !== undefined) return `${matched}/${total} films matched`;
  if (discovered !== undefined) return `${discovered} accounts (${added ?? 0} new)`;
  return Object.entries(result).map(([key, value]) => `${key}: ${String(value)}`).join(", ");
}

export default function TasksPanel() {
  const { data: tasks, isLoading, live } = useLiveTasks();
  const isAdmin = !!useAuthStore((s) => s.user?.is_admin);
  const { data: users } = useUsers();
  const userName = (id: string | null) => users?.find((u) => u.id === id)?.display_name ?? "-";

  const active = (tasks ?? []).filter(isActive);
  const history = (tasks ?? []).filter((t) => !isActive(t)).slice(0, HISTORY_SHOWN);
  const now = useNow(active.length > 0);
  const since = (iso: string) => formatDuration(now - new Date(iso).getTime());

  return (
    <div className="flex flex-col gap-5">
      <SettingsCard title="Active Tasks">
        <div className="flex flex-col gap-3 px-5 py-4">
          <p className="flex items-center gap-1.5 text-xs text-zinc-500">
            <Radio className={cn("h-3.5 w-3.5", live ? "text-emerald-400" : "text-zinc-600")} />
            {live ? "Live updates connected" : "Live updates reconnecting - refreshing every 15s"}
          </p>
          {isLoading && <p className="text-sm text-zinc-500">Loading...</p>}
          {tasks && active.length === 0 && (
            <p className="rounded-md border border-dashed border-app-border px-3 py-6 text-center text-sm text-zinc-500">
              No tasks are running. Syncs and discovery jobs show up here while they work.
            </p>
          )}
          {active.map((task) => (
            <div key={task.id} className="flex flex-col gap-2 rounded-md border border-app-border bg-app-bg px-4 py-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <p className="break-words text-sm font-medium text-zinc-100">{titleOf(task)}</p>
                  {task.progress_data?.label && (
                    <p className="break-words text-[11px] text-zinc-500">{task.progress_data.label}</p>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-[11px] text-zinc-500">{since(task.created_at)}</span>
                  <StatusBadge status={task.status} />
                </div>
              </div>
              <TaskProgressBar task={task} />
              <p className="text-[11px] text-zinc-400">
                {describeProgress(task)}
                {task.progress_data?.progress?.message && task.status === "running" && (
                  <span className="text-zinc-600"> &middot; {task.progress_data.progress.message}</span>
                )}
              </p>
            </div>
          ))}
        </div>
      </SettingsCard>

      <SettingsCard title="History">
        {tasks && history.length === 0 ? (
          <p className="px-5 py-6 text-center text-sm text-zinc-500">Nothing has finished yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-xs">
              <thead className="border-b border-app-border text-[11px] uppercase tracking-wide text-zinc-500">
                <tr>
                  <th className="px-5 py-2.5 font-medium">Task</th>
                  <th className="px-3 py-2.5 font-medium">Status</th>
                  <th className="px-3 py-2.5 font-medium">Started</th>
                  <th className="px-3 py-2.5 font-medium">Took</th>
                  <th className="px-5 py-2.5 font-medium">Outcome</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-app-border">
                {history.map((task) => (
                  <tr key={task.id} className="align-top">
                    <td className="px-5 py-3">
                      <p className="font-medium text-zinc-200">{titleOf(task)}</p>
                      {task.progress_data?.label && (
                        <p className="break-words text-[11px] text-zinc-500">{task.progress_data.label}</p>
                      )}
                      {isAdmin && <p className="text-[11px] text-zinc-600">by {userName(task.user_id)}</p>}
                    </td>
                    <td className="px-3 py-3">
                      <StatusBadge status={task.status} />
                    </td>
                    <td className="whitespace-nowrap px-3 py-3 text-zinc-400" title={task.created_at}>
                      {new Date(task.created_at).toLocaleString()}
                    </td>
                    <td className="whitespace-nowrap px-3 py-3 text-zinc-400">
                      {formatDuration(new Date(task.updated_at).getTime() - new Date(task.created_at).getTime())}
                    </td>
                    <td className="px-5 py-3">
                      {task.status === "failed" ? (
                        <p className="flex items-start gap-1 break-words text-red-400">
                          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                          <span>
                            {task.progress_data?.error?.code && (
                              <code className="mr-1 text-red-300">{task.progress_data.error.code}</code>
                            )}
                            {task.error ?? "Failed"}
                          </span>
                        </p>
                      ) : (
                        <span className="text-zinc-300">{resultSummary(task)}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SettingsCard>
    </div>
  );
}
