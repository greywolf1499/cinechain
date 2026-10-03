import { Tv } from "lucide-react";
import { cn } from "../lib/cn";

/** Green border + tint for any movie card whose film is available on Jellyfin. */
export function onServerCardClass(onServer: boolean | null | undefined): string {
  return onServer
    ? "border-emerald-600/70 bg-emerald-950/30 ring-1 ring-emerald-600/30"
    : "border-app-border bg-app-surface";
}

export default function OnServerBadge({ onServer }: { onServer: boolean | null | undefined }) {
  if (onServer === null || onServer === undefined) return null;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold",
        onServer
          ? "bg-emerald-600 text-white shadow-sm shadow-emerald-950/60"
          : "bg-app-surface-hover font-medium text-zinc-500",
      )}
    >
      {onServer ? "✓" : <Tv className="h-3 w-3" />}
      {onServer ? "On Server" : "Not on Server"}
    </span>
  );
}
