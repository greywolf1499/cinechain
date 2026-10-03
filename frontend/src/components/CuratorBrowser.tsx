import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BadgeCheck, Compass, Loader2, RefreshCw, ScanSearch } from "lucide-react";
import type { ToastState } from "./Toast";
import { ApiError, api } from "../lib/api";
import { useCuratedAccounts } from "../lib/queries";
import { postSse } from "../lib/sse";
import type { CuratedAccount, CuratedAccountLists, CuratedListSummary } from "../types/api";

const buttonClass =
  "flex shrink-0 items-center gap-1.5 rounded-md border border-app-border px-3 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover disabled:cursor-not-allowed disabled:opacity-60";

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError || err instanceof Error ? err.message : fallback;
}

/** Tier 0/1: tracked curator accounts and the lists each one publishes. */
export default function CuratorBrowser({ onToast }: { onToast: (toast: ToastState) => void }) {
  const queryClient = useQueryClient();
  const { data: accounts, isLoading } = useCuratedAccounts();
  const [selected, setSelected] = useState<string | null>(null);

  const refreshAll = () => queryClient.invalidateQueries({ queryKey: ["curated"] });

  const discoverHq = useMutation({
    mutationFn: () => postSse("/curated/accounts/discover-hq?max_pages=5"),
    onSuccess: (events) => {
      const result = events.find((e) => e.event === "result")?.data as
        | { discovered: number; new: number; partial: boolean }
        | undefined;
      if (events.some((e) => e.event === "error") && !result) {
        onToast({ type: "error", message: "HQ discovery failed - see server logs." });
        return;
      }
      onToast({
        type: "success",
        message: result
          ? `Found ${result.discovered} HQ accounts (${result.new} new)${result.partial ? " - partial result" : ""}.`
          : "HQ discovery finished.",
      });
      refreshAll();
    },
    onError: (err) => onToast({ type: "error", message: errorMessage(err, "HQ discovery failed.") }),
  });

  const inspect = useMutation({
    mutationFn: (username: string) =>
      api.post<CuratedAccount>(`/curated/accounts/${username}/inspect`),
    onSuccess: (account) => {
      onToast({ type: "success", message: `Inspected ${account.display_name ?? account.username}.` });
      refreshAll();
    },
    onError: (err) => onToast({ type: "error", message: errorMessage(err, "Inspect failed.") }),
  });

  return (
    <div className="border-t border-app-border pt-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-medium uppercase tracking-wide text-zinc-500">
          Curator Directory
        </p>
        <button
          type="button"
          disabled={discoverHq.isPending}
          onClick={() => discoverHq.mutate()}
          className={buttonClass}
        >
          {discoverHq.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Compass className="h-3.5 w-3.5" />
          )}
          Discover HQ Accounts
        </button>
      </div>

      {isLoading && <div className="text-sm text-zinc-500">Loading...</div>}

      <div className="flex flex-col gap-2">
        {accounts?.map((account) => (
          <div key={account.username} className="rounded-md border border-app-border bg-app-bg">
            <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2.5">
              <div className="flex min-w-0 items-center gap-2.5">
                {account.avatar_url && (
                  <img
                    src={account.avatar_url}
                    alt=""
                    referrerPolicy="no-referrer"
                    className="h-8 w-8 rounded-full object-cover"
                  />
                )}
                <div className="min-w-0">
                  <p className="flex items-center gap-1.5 truncate text-sm font-medium text-zinc-200">
                    {account.display_name ?? account.username}
                    {account.is_hq && <BadgeCheck className="h-3.5 w-3.5 text-accent" aria-label="HQ" />}
                  </p>
                  <p className="text-[11px] text-zinc-500">
                    @{account.username} · {account.discovered_lists} lists browsed ·{" "}
                    {account.enabled_lists} enabled
                  </p>
                </div>
              </div>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={inspect.isPending && inspect.variables === account.username}
                  onClick={() => inspect.mutate(account.username)}
                  className={buttonClass}
                >
                  {inspect.isPending && inspect.variables === account.username ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <ScanSearch className="h-3.5 w-3.5" />
                  )}
                  Inspect
                </button>
                <button
                  type="button"
                  onClick={() => setSelected(selected === account.username ? null : account.username)}
                  className={buttonClass}
                >
                  {selected === account.username ? "Hide Lists" : "Browse Lists"}
                </button>
              </div>
            </div>
            {selected === account.username && (
              <AccountLists username={account.username} onToast={onToast} onChanged={refreshAll} />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function AccountLists({
  username,
  onToast,
  onChanged,
}: {
  username: string;
  onToast: (toast: ToastState) => void;
  onChanged: () => void;
}) {
  const queryClient = useQueryClient();
  const queryKey = ["curated", "accounts", username, "lists"];
  const { data, isLoading, error } = useQuery({
    queryKey,
    queryFn: () => api.get<CuratedAccountLists>(`/curated/accounts/${username}/lists`),
    retry: false,
  });
  const [busyId, setBusyId] = useState<string | null>(null);

  const rescan = useMutation({
    mutationFn: () => api.get<CuratedAccountLists>(`/curated/accounts/${username}/lists?refresh=true`),
    onSuccess: (fresh) => {
      queryClient.setQueryData(queryKey, fresh);
      onChanged();
      onToast({ type: "success", message: `Found ${fresh.lists.length} lists for @${username}.` });
    },
    onError: (err) => onToast({ type: "error", message: errorMessage(err, "Rescan failed.") }),
  });

  async function setEnabled(list: CuratedListSummary, enable: boolean) {
    setBusyId(list.id);
    try {
      await api.patch(`/curated/lists/${list.id}`, { is_enabled: enable });
      if (enable) {
        const events = await postSse(`/curated/sync/${list.id}`);
        if (events.some((e) => e.event === "error")) {
          onToast({ type: "error", message: `Enabled ${list.title}, but the sync failed.` });
        } else {
          onToast({ type: "success", message: `${list.title} enabled and synced.` });
        }
      } else {
        onToast({ type: "success", message: `${list.title} disabled.` });
      }
      await queryClient.invalidateQueries({ queryKey: ["curated"] });
    } catch (err) {
      onToast({ type: "error", message: errorMessage(err, "Update failed.") });
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="border-t border-app-border px-3 py-3">
      {isLoading && (
        <p className="flex items-center gap-2 text-xs text-zinc-500">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          Discovering lists from Letterboxd - this can take a minute on first visit...
        </p>
      )}
      {error && <p className="text-xs text-red-400">{errorMessage(error, "Could not load lists.")}</p>}

      {data && (
        <>
          <div className="mb-2 flex items-center justify-between gap-2">
            <p className="text-[11px] text-zinc-500">
              {data.discovered
                ? `${data.lists.length} lists`
                : "Not browsed yet - ask an admin to scan this account."}
              {data.partial && " · partial (Letterboxd rate-limited the scan)"}
            </p>
            <button
              type="button"
              disabled={rescan.isPending}
              onClick={() => rescan.mutate()}
              className={buttonClass}
            >
              {rescan.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" />
              )}
              Rescan
            </button>
          </div>

          <div className="flex flex-col gap-2">
            {data.lists.map((list) => (
              <div
                key={list.id}
                className="flex flex-wrap items-center justify-between gap-3 rounded-md bg-app-surface px-3 py-2"
              >
                <div className="flex min-w-0 flex-1 items-center gap-3">
                  <div className="flex shrink-0 -space-x-3">
                    {list.preview_posters.slice(0, 4).map((src) => (
                      <img
                        key={src}
                        src={src}
                        alt=""
                        referrerPolicy="no-referrer"
                        className="h-12 w-8 rounded-sm border border-app-border object-cover"
                      />
                    ))}
                  </div>
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-zinc-200">{list.title}</p>
                    <p className="text-[11px] text-zinc-500">
                      {list.film_count} films
                      {list.is_enabled && list.total_items > 0 && ` · ${list.total_items} matched`}
                      {" · "}
                      {list.badge_prefix}
                    </p>
                    {list.description && (
                      <p className="line-clamp-1 text-[11px] text-zinc-600">{list.description}</p>
                    )}
                  </div>
                </div>
                <button
                  type="button"
                  disabled={busyId === list.id}
                  onClick={() => setEnabled(list, !list.is_enabled)}
                  className={buttonClass}
                >
                  {busyId === list.id && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                  {list.is_enabled ? "Disable" : "Enable & Sync"}
                </button>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
