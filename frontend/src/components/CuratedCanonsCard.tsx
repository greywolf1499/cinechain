import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Download, Loader2, XCircle } from "lucide-react";
import Toast, { type ToastState } from "./Toast";
import CuratorBrowser from "./CuratorBrowser";
import SyncBadge from "./SyncBadge";
import { useCuratedLists } from "../lib/queries";
import { postSse } from "../lib/sse";
import type { CuratedListSummary } from "../types/api";

const inputClass =
  "rounded-md border border-app-border bg-app-bg px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-accent focus:outline-none";

export default function CuratedCanonsCard() {
  const queryClient = useQueryClient();
  const { data: lists, isLoading } = useCuratedLists();
  const [syncingId, setSyncingId] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState | null>(null);

  const [customUrl, setCustomUrl] = useState("");
  const [customPrefix, setCustomPrefix] = useState("");
  const [customColor, setCustomColor] = useState("#d9a441");

  const [watchlistUsername, setWatchlistUsername] = useState("");
  const [watchlistSyncedAt, setWatchlistSyncedAt] = useState<string | null>(null);
  const [watchlistWarning, setWatchlistWarning] = useState<string | null>(null);
  const syncWatchlist = useMutation({
    mutationFn: () => postSse("/curated/watchlist/sync", { letterboxd_username: watchlistUsername }),
    onMutate: () => setWatchlistWarning(null),
    onSuccess: (events) => {
      const result = events.find((e) => e.event === "result")?.data as
        | { matched: number; total_films: number }
        | undefined;
      const errorEvent = events.find((e) => e.event === "error");
      if (errorEvent) {
        const error = errorEvent.data as { code?: string; message?: string } | null;
        if (error?.code === "watchlist_not_found") {
          setWatchlistWarning(
            "Watchlist not found or private. Check the username, and make sure the watchlist is public on Letterboxd.",
          );
        } else {
          const detail = error?.message;
          setToast({ type: "error", message: `Watchlist sync failed${detail ? `: ${detail}` : "."}` });
        }
      } else {
        setWatchlistSyncedAt(new Date().toISOString());
        setToast({
          type: "success",
          message: result
            ? `Synced ${result.matched}/${result.total_films} watchlist films.`
            : "Watchlist synced.",
        });
      }
    },
    onError: () => setToast({ type: "error", message: "Watchlist sync failed." }),
  });

  const createCustom = useMutation({
    mutationFn: () =>
      fetch("/api/curated/custom", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: customUrl,
          badge_prefix: customPrefix || undefined,
          badge_color: customColor,
        }),
      }).then((r) => {
        if (!r.ok) throw new Error(`Request failed with ${r.status}`);
        return r.json() as Promise<CuratedListSummary>;
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["curated", "lists"] });
      setCustomUrl("");
      setCustomPrefix("");
      setToast({ type: "success", message: "Custom list added - click Sync to ingest it." });
    },
    onError: () => setToast({ type: "error", message: "Failed to add custom list." }),
  });

  async function handleSync(list: CuratedListSummary) {
    setSyncingId(list.id);
    try {
      const events = await postSse(`/curated/sync/${list.id}`);
      const result = events.find((e) => e.event === "result")?.data as
        | { matched: number; total_films: number }
        | undefined;
      const errorEvent = events.find((e) => e.event === "error");
      if (errorEvent) {
        setToast({ type: "error", message: "Sync failed - see server logs." });
      } else {
        setToast({
          type: "success",
          message: result
            ? `Synced ${result.matched}/${result.total_films} films for ${list.title}.`
            : "Synced.",
        });
      }
      queryClient.invalidateQueries({ queryKey: ["curated", "lists"] });
    } catch {
      setToast({ type: "error", message: "Sync failed." });
    } finally {
      setSyncingId(null);
    }
  }

  return (
    <div className="rounded-xl border border-app-border bg-app-surface">
      <div className="border-b border-app-border px-5 py-3 text-sm font-medium text-zinc-300">
        Curated Canons
      </div>
      <div className="flex flex-col gap-4 px-5 py-4">
        <p className="text-xs text-zinc-600">
          Sync preset Letterboxd canon lists to display laurel badges (🏆 SS22, 🎖️ LB250, ...) on
          movies across the app, and surface "The Cinephile Route" in the Bridge Solver.
        </p>

        {isLoading && <div className="text-sm text-zinc-500">Loading...</div>}

        {lists && (
          <div className="flex flex-col gap-2">
            {lists.map((list) => (
              <div
                key={list.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-app-border bg-app-bg px-3 py-2.5"
              >
                <div className="min-w-0">
                  <p className="flex flex-wrap items-center gap-2 text-sm font-medium text-zinc-200">
                    <span className="truncate">
                      {list.badge_prefix} - {list.title}
                    </span>
                    <SyncBadge syncedAt={list.last_synced_at} />
                  </p>
                  <p className="text-[11px] text-zinc-500">
                    {list.total_items > 0
                      ? `${list.total_items} films synced`
                      : "No films synced yet"}
                  </p>
                  {list.last_sync_error && (
                    <p className="flex items-center gap-1 text-[11px] text-red-400">
                      <XCircle className="h-3 w-3" /> {list.last_sync_error}
                    </p>
                  )}
                </div>
                <button
                  type="button"
                  disabled={syncingId === list.id}
                  onClick={() => handleSync(list)}
                  className="flex shrink-0 items-center gap-1.5 rounded-md border border-app-border px-3 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {syncingId === list.id ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Download className="h-3.5 w-3.5" />
                  )}
                  Sync
                </button>
              </div>
            ))}
          </div>
        )}

        <CuratorBrowser onToast={setToast} />

        <div className="border-t border-app-border pt-3">
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-zinc-500">
            Custom List Importer
          </p>
          <div className="flex flex-wrap gap-2">
            <input
              value={customUrl}
              onChange={(e) => setCustomUrl(e.target.value)}
              placeholder="https://letterboxd.com/.../list/.../"
              className={`${inputClass} min-w-[220px] flex-1`}
            />
            <input
              value={customPrefix}
              onChange={(e) => setCustomPrefix(e.target.value)}
              placeholder="Badge prefix (optional)"
              className={`${inputClass} w-44`}
            />
            <input
              type="color"
              value={customColor}
              onChange={(e) => setCustomColor(e.target.value)}
              className="h-10 w-12 rounded-md border border-app-border bg-app-bg"
            />
            <button
              type="button"
              disabled={!customUrl.trim() || createCustom.isPending}
              onClick={() => createCustom.mutate()}
              className="flex items-center gap-1.5 rounded-md bg-accent px-3.5 py-2 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-60"
            >
              {createCustom.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              Add List
            </button>
          </div>
        </div>

        <div className="border-t border-app-border pt-3">
          <p className="mb-2 flex flex-wrap items-center gap-2 text-xs font-medium uppercase tracking-wide text-zinc-500">
            Sync My Letterboxd Watchlist
            <SyncBadge syncedAt={watchlistSyncedAt} />
          </p>
          <div className="flex flex-wrap gap-2">
            <input
              value={watchlistUsername}
              onChange={(e) => setWatchlistUsername(e.target.value)}
              placeholder="Letterboxd username"
              className={`${inputClass} min-w-[200px] flex-1`}
            />
            <button
              type="button"
              disabled={!watchlistUsername.trim() || syncWatchlist.isPending}
              onClick={() => syncWatchlist.mutate()}
              className="flex items-center gap-1.5 rounded-md border border-app-border px-3.5 py-2 text-sm font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover disabled:cursor-not-allowed disabled:opacity-60"
            >
              {syncWatchlist.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <CheckCircle2 className="h-3.5 w-3.5" />
              )}
              Sync Watchlist
            </button>
          </div>
          {watchlistWarning && (
            <p
              role="alert"
              className="mt-2 flex items-start gap-1.5 rounded-md border border-amber-900/50 bg-amber-950/20 px-3 py-2 text-xs font-medium text-amber-300"
            >
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              {watchlistWarning}
            </p>
          )}
        </div>

        <Toast toast={toast} onDismiss={() => setToast(null)} />
      </div>
    </div>
  );
}
