import { useEffect, useState } from "react";
import { CheckCircle2, Loader2 } from "lucide-react";
import SyncBadge from "../SyncBadge";
import { useTrackedTask } from "../../lib/useTrackedTask";
import { describeProgress } from "../../lib/tasks";
import { queryKeys, useWatchlistStatus } from "../../lib/queries";
import { useQueryClient } from "@tanstack/react-query";

const inputClass =
  "rounded-md border border-app-border bg-app-bg px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-accent focus:outline-none";
const WATCHLIST_NOT_FOUND =
  "Watchlist not found or private. Check the username, and make sure the watchlist is public on Letterboxd.";

export default function WatchlistSyncCard() {
  const queryClient = useQueryClient();
  const { data: status } = useWatchlistStatus();
  const [username, setUsername] = useState("");
  const [usernameEdited, setUsernameEdited] = useState(false);
  const [hideStoredError, setHideStoredError] = useState(false);

  useEffect(() => {
    if (!usernameEdited && status?.letterboxd_username) {
      setUsername(status.letterboxd_username);
    }
  }, [status?.letterboxd_username, usernameEdited]);

  const sync = useTrackedTask<{ matched: number; total_films: number }>({
    resumeNames: ["watchlist_sync"],
    onFinished: (task) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.watchlistStatus });
      if (task.status === "failed") {
        const error = task.progress_data?.error;
        if (error?.code === "watchlist_not_found") {
          setHideStoredError(false);
          return;
        }
        setHideStoredError(true);
        return;
      }
      setHideStoredError(false);
    },
  });

  const storedError =
    status?.last_error && !hideStoredError && !sync.busy ? WATCHLIST_NOT_FOUND : null;
  const progress = sync.task ? describeProgress(sync.task) : null;

  function startSync() {
    setHideStoredError(true);
    void sync.start("/curated/watchlist/sync", { letterboxd_username: username });
  }

  return (
    <section
      id="watchlist"
      className="rounded-xl border border-app-border bg-app-surface"
      aria-labelledby="watchlist-sync-heading"
    >
      <div className="border-b border-app-border px-5 py-3 text-sm font-medium text-zinc-300">
        <h2 id="watchlist-sync-heading">Sync My Letterboxd Watchlist</h2>
      </div>
      <div className="flex flex-col gap-3 px-5 py-4">
        <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-500">
          <SyncBadge syncedAt={status?.synced_at} />
          <span>· {status?.total_items ?? 0} films</span>
        </div>
        <div className="flex flex-wrap gap-2">
          <label htmlFor="letterboxd-username" className="sr-only">
            Letterboxd username
          </label>
          <input
            id="letterboxd-username"
            value={username}
            onChange={(event) => {
              setUsername(event.target.value);
              setUsernameEdited(true);
            }}
            placeholder="Letterboxd username"
            pattern="[A-Za-z0-9_]{1,40}"
            title="Use 1–40 letters, numbers, or underscores."
            className={`${inputClass} min-w-[200px] flex-1`}
          />
          <button
            type="button"
            disabled={!username.trim() || sync.busy}
            onClick={startSync}
            className="flex items-center gap-1.5 rounded-md border border-app-border px-3.5 py-2 text-sm font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover disabled:cursor-not-allowed disabled:opacity-60"
          >
            {sync.busy ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <CheckCircle2 className="h-3.5 w-3.5" />
            )}
            {sync.busy ? `Syncing... ${progress ?? "Queued..."}` : "Sync Watchlist"}
          </button>
        </div>
        <p className="text-[11px] text-zinc-500">Letters, numbers and _ only</p>
        {sync.error && <p role="alert" className="text-xs text-red-300">{sync.error}</p>}
        {storedError && (
          <p role="alert" className="rounded-md border border-amber-900/50 bg-amber-950/20 px-3 py-2 text-xs font-medium text-amber-300">
            {storedError}
          </p>
        )}
        {sync.task?.status === "failed" && sync.task.progress_data?.error?.code !== "watchlist_not_found" &&
          <p role="alert" className="text-xs text-red-300">{sync.task.error ?? "Watchlist sync failed."}</p>}
      </div>
    </section>
  );
}
