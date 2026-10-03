import { cn } from "../lib/cn";
import type { SystemTask } from "../lib/tasks";

export default function TaskProgressBar({ task }: { task: SystemTask<unknown> }) {
  const progress = task.progress_data?.progress;
  const known = progress?.total ? Math.min(100, Math.round(((progress.current ?? 0) / progress.total) * 100)) : null;
  return (
    <div
      role="progressbar"
      aria-valuenow={known ?? undefined}
      aria-valuemin={0}
      aria-valuemax={100}
      className="h-1.5 w-full overflow-hidden rounded-full bg-app-surface-hover"
    >
      <div
        className={cn("h-full rounded-full bg-accent transition-[width] duration-500", known === null && "w-1/3 animate-pulse")}
        style={known === null ? undefined : { width: `${known}%` }}
      />
    </div>
  );
}
