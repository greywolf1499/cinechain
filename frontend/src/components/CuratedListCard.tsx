import { useState } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Eye, Loader2, Pencil } from "lucide-react";
import EditListModal from "./EditListModal";
import ListCover from "./ListCover";
import SyncBadge from "./SyncBadge";
import ExpandableText from "./ui/ExpandableText";
import type { ToastState } from "./Toast";
import { ApiError, api } from "../lib/api";
import { useTrackedTask } from "../lib/useTrackedTask";
import { describeProgress } from "../lib/tasks";
import TaskProgressBar from "./TaskProgressBar";
import type { CuratedListSummary } from "../types/api";

const buttonClass =
  "flex shrink-0 items-center gap-1.5 rounded-md border border-app-border px-3 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover disabled:cursor-not-allowed disabled:opacity-60";
function Description({ text }: { text: string }) {
  return (
    <div className="mt-1.5">
      <ExpandableText
        text={text}
        lines={4}
        moreLabel="Read more"
        lessLabel="Show less"
        className="whitespace-pre-line text-xs leading-relaxed text-zinc-400"
      />
    </div>
  );
}

/** One curated list: full title + description (no truncation), badge preview,
 * and (for admins) enable/sync/customize controls. */
export default function CuratedListCard({
  list,
  isAdmin,
  showCurator,
  onToast,
}: {
  list: CuratedListSummary;
  isAdmin: boolean;
  showCurator: boolean;
  onToast: (toast: ToastState) => void;
}) {
  const queryClient = useQueryClient();
  const [updating, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const syncTask = useTrackedTask<{ matched: number; total_films: number }>({
    dedupeKey: `curated_list_sync:${list.id}`,
    onFinished: () => { void queryClient.invalidateQueries({ queryKey: ["curated"] }); },
  });
  const busy = updating || syncTask.busy;

  async function run(action: () => Promise<string>, failure: string) {
    setBusy(true);
    try {
      onToast({ type: "success", message: await action() });
      await queryClient.invalidateQueries({ queryKey: ["curated"] });
    } catch (err) {
      onToast({
        type: "error",
        message: err instanceof ApiError || err instanceof Error ? err.message : failure,
      });
    } finally {
      setBusy(false);
    }
  }

  const toggle = () =>
    run(async () => {
      if (list.is_enabled) {
        await api.patch(`/curated/lists/${list.id}`, { is_enabled: false });
        return `${list.title} disabled.`;
      }
      await api.patch(`/curated/lists/${list.id}`, { is_enabled: true });
      const started = await syncTask.start(`/curated/sync/${list.id}`);
      if (!started) throw new Error(`${list.title} enabled, but its sync could not start.`);
      return `${list.title} enabled; sync started.`;
    }, "Update failed.");

  return (
    <article className="flex flex-col gap-3 rounded-xl border border-app-border bg-app-surface p-4 sm:flex-row sm:items-start">
      <ListCover list={list} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="break-words text-sm font-semibold text-zinc-100">{list.title}</h3>
          {list.is_enabled ? (
            <SyncBadge syncedAt={list.last_synced_at} />
          ) : (
            <span className="rounded-full border border-zinc-700 bg-zinc-800 px-2.5 py-0.5 text-[11px] font-semibold text-zinc-400">
              Disabled
            </span>
          )}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-zinc-500">
          <span
            className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-semibold"
            style={{ backgroundColor: `${list.badge_color}26`, color: list.badge_color }}
          >
            {list.badge_emoji || "🏅"} {list.badge_prefix}
          </span>
          <span>{list.film_count.toLocaleString()} films</span>
          {list.is_enabled && list.total_items > 0 && <span>{list.total_items.toLocaleString()} matched</span>}
          {list.watched_count > 0 && (
            <span className="inline-flex items-center gap-1 text-emerald-400">
              <Eye className="h-3 w-3" /> {list.watched_count} watched
            </span>
          )}
          {list.slug && <span className="font-mono">/{list.slug}</span>}
          {showCurator && list.account_username && (
            <Link to={`/curators/${list.account_username}`} className="text-accent hover:underline">
              {list.account_display_name || list.account_username}
            </Link>
          )}
        </div>
        {list.description && <Description text={list.description} />}
        {list.last_sync_error && <p className="mt-1.5 break-words text-[11px] text-red-400">{list.last_sync_error}</p>}
        {syncTask.busy && syncTask.task && <div className="mt-2"><TaskProgressBar task={syncTask.task} /><p className="text-xs text-zinc-400">{describeProgress(syncTask.task)}</p></div>}
        {syncTask.error && <p role="alert" className="text-xs text-red-300">{syncTask.error}</p>}
      </div>
      {isAdmin && (
        <div className="flex shrink-0 flex-wrap gap-2 sm:flex-col">
          <button type="button" disabled={busy} onClick={toggle} className={buttonClass}>
            {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {list.is_enabled ? "Disable" : "Enable & Sync"}
          </button>
          {list.is_enabled && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void syncTask.start(`/curated/sync/${list.id}`)}
              className={buttonClass}
            >
              Re-sync
            </button>
          )}
          <button type="button" onClick={() => setEditing(true)} className={buttonClass}>
            <Pencil className="h-3.5 w-3.5" /> Customize
          </button>
        </div>
      )}
      {editing && <EditListModal list={list} onClose={() => setEditing(false)} onToast={onToast} />}
    </article>
  );
}
