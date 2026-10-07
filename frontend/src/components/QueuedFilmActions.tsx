import { Check, Loader2, X } from "lucide-react";
import { useLogFilm } from "../lib/useLogFilm";

/** Stage 2 for a queued film on a board: "Queued" is never the end of the line, so the card
 * offers logging it watched and taking it back off the queue. */
export default function QueuedFilmActions({
  runId,
  stepId,
  disabled = false,
  onError,
}: {
  runId: string;
  stepId: string;
  disabled?: boolean;
  onError?: (message: string | null) => void;
}) {
  const logFilm = useLogFilm(runId);
  const busy = logFilm.isPending;
  const fail = (err: unknown) => onError?.(err instanceof Error ? err.message : "Could not update that film.");

  return (
    <div className="flex flex-wrap gap-1.5">
      <button
        type="button"
        disabled={disabled || busy}
        onClick={() => {
          onError?.(null);
          void logFilm.unqueue({ id: stepId }).catch(fail);
        }}
        className="flex items-center gap-1 rounded-md border border-app-border px-2.5 py-1.5 text-[11px] font-medium text-zinc-300 hover:bg-app-surface-hover disabled:opacity-50"
      >
        {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <X className="h-3 w-3" />}
        Unqueue
      </button>
      <button
        type="button"
        disabled={disabled || busy}
        onClick={() => {
          onError?.(null);
          void logFilm.markWatched({ id: stepId }).catch(fail);
        }}
        className="flex items-center gap-1 rounded-md bg-accent px-2.5 py-1.5 text-[11px] font-semibold text-zinc-950 hover:bg-accent-strong disabled:opacity-50"
      >
        {busy ? (
          <Loader2 className="h-3 w-3 animate-spin" />
        ) : (
          <Check className="h-3 w-3" />
        )}
        Log watched
      </button>
    </div>
  );
}
