import { cn } from "../lib/cn";

const FRESH_MS = 7 * 24 * 60 * 60 * 1000;

function timeAgo(ms: number): string {
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

/** Color-coded sync indicator: green = synced within a week, amber = stale, gray = never. */
export default function SyncBadge({ syncedAt }: { syncedAt: string | null | undefined }) {
  const age = syncedAt ? Date.now() - new Date(syncedAt).getTime() : null;
  const state = age === null ? "never" : age <= FRESH_MS ? "fresh" : "stale";
  const label =
    state === "never" ? "Never synced" : `${state === "stale" ? "Stale - synced" : "Synced"} ${timeAgo(age ?? 0)}`;

  return (
    <span
      title={syncedAt ? new Date(syncedAt).toLocaleString() : undefined}
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-semibold",
        state === "fresh" && "border-emerald-700 bg-emerald-950 text-emerald-300",
        state === "stale" && "border-amber-700 bg-amber-950 text-amber-300",
        state === "never" && "border-zinc-700 bg-zinc-800 text-zinc-400",
      )}
    >
      <span
        className={cn(
          "h-2 w-2 rounded-full",
          state === "fresh" && "bg-emerald-400",
          state === "stale" && "bg-amber-400",
          state === "never" && "bg-zinc-500",
        )}
      />
      {label}
    </span>
  );
}
