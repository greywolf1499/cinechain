import { Tv } from "lucide-react";
import { cn } from "../lib/cn";

export default function OnServerBadge({ onServer }: { onServer: boolean | null | undefined }) {
  if (onServer === null || onServer === undefined) return null;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium",
        onServer ? "bg-emerald-950 text-emerald-400" : "bg-app-surface-hover text-zinc-500",
      )}
    >
      <Tv className="h-3 w-3" />
      {onServer ? "On Server" : "Not on Server"}
    </span>
  );
}
