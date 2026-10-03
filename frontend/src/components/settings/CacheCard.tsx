import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Zap } from "lucide-react";
import { MiniStat, SettingsCard, formatBytes } from "./shared";
import Toast, { type ToastState } from "../Toast";
import { ApiError, api } from "../../lib/api";
import { useCacheStats } from "../../lib/queries";
import { useAuthStore } from "../../store/authStore";
import type { CacheFlushResult } from "../../types/api";

const STALE_DAYS = 7;

export default function CacheCard() {
  const queryClient = useQueryClient();
  const isAdmin = !!useAuthStore((s) => s.user?.is_admin);
  const { data: stats, isLoading } = useCacheStats();
  const [toast, setToast] = useState<ToastState | null>(null);
  const [result, setResult] = useState<CacheFlushResult | null>(null);

  const flush = useMutation({
    mutationFn: () => api.post<CacheFlushResult>(`/system/cache/flush?max_age_days=${STALE_DAYS}`),
    onSuccess: (data) => {
      setResult(data);
      queryClient.invalidateQueries({ queryKey: ["system"] });
      setToast({ type: "success", message: "Stale cache flushed." });
    },
    onError: (err) =>
      setToast({ type: "error", message: err instanceof ApiError ? err.message : "Cache flush failed." }),
  });

  return (
    <SettingsCard title="Cache">
      {isLoading && <div className="px-5 py-4 text-sm text-zinc-500">Loading...</div>}
      {stats && (
        <div className="grid grid-cols-2 gap-4 px-5 py-4 sm:grid-cols-4">
          <MiniStat label="Cached Movies" value={stats.cached_movies} />
          <MiniStat label="Cached Actors" value={stats.cached_actors} />
          <MiniStat label="Cast Edges" value={stats.cached_cast_edges} />
          <MiniStat
            label="DB Size on Disk"
            value={stats.db_size_bytes != null ? formatBytes(stats.db_size_bytes) : "-"}
          />
        </div>
      )}
      {isAdmin && (
        <div className="flex flex-col gap-3 border-t border-app-border px-5 py-4">
          <p className="text-xs text-zinc-500">
            Drops ratings and cast/credit data fetched more than {STALE_DAYS} days ago and compacts the
            database. Nothing is lost for good: it is re-fetched from TMDB/OMDb the next time it is needed.
          </p>
          <button
            type="button"
            disabled={flush.isPending}
            onClick={() => flush.mutate()}
            className="flex w-fit items-center gap-1.5 rounded-md border border-app-border px-3.5 py-2 text-sm font-medium text-zinc-200 transition-colors hover:bg-app-surface-hover disabled:cursor-not-allowed disabled:opacity-60"
          >
            {flush.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Zap className="h-4 w-4 text-accent" />}
            Flush &amp; Refresh Stale Cache
          </button>
          {result && (
            <p className="text-xs text-zinc-400" role="status">
              Removed {result.ratings_removed} ratings, {result.cast_edges_removed} cast edges and{" "}
              {result.actors_removed} actors; {result.actors_marked_stale + result.movies_cast_reset} entries
              will refresh on next use.
              {result.db_size_before != null && result.db_size_after != null && (
                <>
                  {" "}
                  Database: {formatBytes(result.db_size_before)} &rarr; {formatBytes(result.db_size_after)}
                  {result.vacuumed ? "" : " (not compacted)"}.
                </>
              )}
            </p>
          )}
        </div>
      )}
      <Toast toast={toast} onDismiss={() => setToast(null)} />
    </SettingsCard>
  );
}
