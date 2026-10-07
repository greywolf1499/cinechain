import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Download, Loader2, XCircle } from "lucide-react";
import Toast, { type ToastState } from "./Toast";
import SyncBadge from "./SyncBadge";
import { useCuratedLists } from "../lib/queries";
import { useTrackedTask } from "../lib/useTrackedTask";
import { describeProgress } from "../lib/tasks";
import TaskProgressBar from "./TaskProgressBar";
import type { CuratedListSummary } from "../types/api";

const linkButtonClass =
  "rounded-md border border-app-border px-3 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover";

const inputClass =
  "rounded-md border border-app-border bg-app-bg px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-accent focus:outline-none";

export default function CuratedCanonsCard() {
  const queryClient = useQueryClient();
  const { data: lists, isLoading } = useCuratedLists();
  const [toast, setToast] = useState<ToastState | null>(null);

  const [customUrl, setCustomUrl] = useState("");
  const [customPrefix, setCustomPrefix] = useState("");
  const [customColor, setCustomColor] = useState("#d9a441");

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
              <CanonSyncRow list={list} key={list.id} />
            ))}
          </div>
        )}

        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-app-border pt-3">
          <p className="text-xs text-zinc-500">
            Browse, search and enable lists from every curator on their own pages.
          </p>
          <div className="flex gap-2">
            <Link to="/lists" className={linkButtonClass}>
              Browse Lists
            </Link>
            <Link to="/curators" className={linkButtonClass}>
              Manage Curators
            </Link>
          </div>
        </div>

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

        <Toast toast={toast} onDismiss={() => setToast(null)} />
      </div>
    </div>
  );
}

function CanonSyncRow({ list }: { list: CuratedListSummary }) {
  const queryClient = useQueryClient();
  const sync = useTrackedTask<{ matched: number; total_films: number }>({
    dedupeKey: `curated_list_sync:${list.id}`,
    onFinished: () => { void queryClient.invalidateQueries({ queryKey: ["curated"] }); },
  });
  return <div
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
                  disabled={sync.busy}
                  onClick={() => void sync.start(`/curated/sync/${list.id}`)}
                  className="flex shrink-0 items-center gap-1.5 rounded-md border border-app-border px-3 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {sync.busy ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Download className="h-3.5 w-3.5" />
                  )}
                  Sync
                </button>
                {sync.busy && sync.task && <div className="w-full"><TaskProgressBar task={sync.task} /><p className="text-xs text-zinc-400">{describeProgress(sync.task)}</p></div>}
                {sync.error && <p role="alert" className="w-full text-xs text-red-300">{sync.error}</p>}
              </div>;
}
