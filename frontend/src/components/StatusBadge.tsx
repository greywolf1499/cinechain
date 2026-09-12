import { cn } from "../lib/cn";
import type { RunStatus } from "../types/api";

const STYLES: Record<RunStatus, string> = {
  active: "bg-emerald-950 text-emerald-400",
  completed: "bg-app-surface-hover text-accent",
  abandoned: "bg-app-surface-hover text-zinc-500",
};

export default function StatusBadge({ status }: { status: RunStatus }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium capitalize",
        STYLES[status],
      )}
    >
      {status}
    </span>
  );
}
