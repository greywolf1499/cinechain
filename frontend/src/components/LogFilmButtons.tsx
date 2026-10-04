import { Loader2 } from "lucide-react";

/** Queue / Log Watched, the pair every marathon board offers on a film still to be seen. */
export default function LogFilmButtons({
  busy,
  disabled,
  onQueue,
  onWatch,
}: {
  busy: boolean;
  disabled: boolean;
  onQueue: () => void;
  onWatch: () => void;
}) {
  return (
    <div className="flex shrink-0 gap-1.5">
      <button
        type="button"
        disabled={disabled}
        onClick={onQueue}
        className="rounded-md border border-app-border px-2.5 py-1.5 text-[11px] font-medium text-zinc-300 hover:bg-app-surface-hover disabled:opacity-50"
      >
        Queue
      </button>
      <button
        type="button"
        disabled={disabled}
        onClick={onWatch}
        className="flex items-center gap-1 rounded-md bg-accent px-2.5 py-1.5 text-[11px] font-semibold text-zinc-950 hover:bg-accent-strong disabled:opacity-50"
      >
        {busy && <Loader2 className="h-3 w-3 animate-spin" />}
        Log Watched
      </button>
    </div>
  );
}
