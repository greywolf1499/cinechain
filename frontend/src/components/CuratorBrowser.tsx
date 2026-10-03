import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  BadgeCheck,
  ChevronRight,
  Compass,
  Loader2,
  Pin,
  PinOff,
  RefreshCw,
  ScanSearch,
  Search,
  Users,
} from "lucide-react";
import Modal from "./Modal";
import SyncBadge from "./SyncBadge";
import type { ToastState } from "./Toast";
import { ApiError, api } from "../lib/api";
import { useCuratedAccounts } from "../lib/queries";
import { postSse } from "../lib/sse";
import type { CuratedAccount, CuratedAccountLists, CuratedListSummary } from "../types/api";

const buttonClass =
  "flex shrink-0 items-center gap-1.5 rounded-md border border-app-border px-3 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover disabled:cursor-not-allowed disabled:opacity-60";

const PINNED_STORAGE_KEY = "cinechain.pinnedCurators";
const ACCOUNT_PAGE_SIZE = 50;

type Screen = { kind: "accounts" } | { kind: "lists"; username: string };

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError || err instanceof Error ? err.message : fallback;
}

function accountName(account: Pick<CuratedAccount, "display_name" | "username">): string {
  return account.display_name?.trim() || account.username;
}

function usePinnedCurators() {
  const [pinned, setPinned] = useState<string[]>(() => {
    try {
      const raw = localStorage.getItem(PINNED_STORAGE_KEY);
      const parsed: unknown = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === "string") : [];
    } catch {
      return [];
    }
  });

  function toggle(username: string) {
    setPinned((current) => {
      const next = current.includes(username)
        ? current.filter((u) => u !== username)
        : [...current, username];
      localStorage.setItem(PINNED_STORAGE_KEY, JSON.stringify(next));
      return next;
    });
  }

  return { pinned, toggle };
}

function useInspectAccount(onToast: (toast: ToastState) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (username: string) =>
      api.post<CuratedAccount>(`/curated/accounts/${username}/inspect`),
    onSuccess: (account) => {
      onToast({ type: "success", message: `Inspected ${accountName(account)}.` });
      queryClient.invalidateQueries({ queryKey: ["curated"] });
    },
    onError: (err) => onToast({ type: "error", message: errorMessage(err, "Inspect failed.") }),
  });
}

function AccountAvatar({ account }: { account: CuratedAccount }) {
  return account.avatar_url ? (
    <img
      src={account.avatar_url}
      alt=""
      referrerPolicy="no-referrer"
      className="h-8 w-8 shrink-0 rounded-full object-cover"
    />
  ) : (
    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-app-surface-hover text-zinc-500">
      <Users className="h-4 w-4" />
    </div>
  );
}

/** Tier 0/1: enabled/pinned curators on the main screen; the full directory lives in a modal. */
export default function CuratorBrowser({ onToast }: { onToast: (toast: ToastState) => void }) {
  const queryClient = useQueryClient();
  const { data: accounts, isLoading } = useCuratedAccounts();
  const { pinned, toggle } = usePinnedCurators();
  const inspect = useInspectAccount(onToast);
  const [modalScreen, setModalScreen] = useState<Screen | null>(null);

  const visibleAccounts = useMemo(
    () => (accounts ?? []).filter((a) => a.enabled_lists > 0 || pinned.includes(a.username)),
    [accounts, pinned],
  );

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
      queryClient.invalidateQueries({ queryKey: ["curated"] });
    },
    onError: (err) => onToast({ type: "error", message: errorMessage(err, "HQ discovery failed.") }),
  });

  return (
    <div className="border-t border-app-border pt-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-medium uppercase tracking-wide text-zinc-500">
          Enabled &amp; Pinned Curators
        </p>
        <div className="flex flex-wrap gap-2">
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
          <button
            type="button"
            onClick={() => setModalScreen({ kind: "accounts" })}
            className={buttonClass}
          >
            <Users className="h-3.5 w-3.5" />
            Manage Curators{accounts ? ` (${accounts.length})` : ""}
          </button>
        </div>
      </div>

      {isLoading && <div className="text-sm text-zinc-500">Loading...</div>}

      {!isLoading && visibleAccounts.length === 0 && (
        <p className="rounded-md border border-dashed border-app-border px-3 py-4 text-center text-xs text-zinc-500">
          No enabled or pinned curators yet. Open Manage Curators to browse accounts and enable lists.
        </p>
      )}

      <div className="flex flex-col gap-2">
        {visibleAccounts.map((account) => (
          <div
            key={account.username}
            className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-app-border bg-app-bg px-3 py-2.5"
          >
            <div className="flex min-w-0 items-center gap-2.5">
              <AccountAvatar account={account} />
              <div className="min-w-0">
                <p className="flex items-center gap-1.5 truncate text-sm font-medium text-zinc-200">
                  {accountName(account)}
                  {account.is_hq && <BadgeCheck className="h-3.5 w-3.5 text-accent" aria-label="HQ" />}
                </p>
                <p className="text-[11px] text-zinc-500">
                  {account.discovered_lists} lists browsed · {account.enabled_lists} enabled
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
                onClick={() => setModalScreen({ kind: "lists", username: account.username })}
                className={buttonClass}
              >
                Browse Lists
              </button>
            </div>
          </div>
        ))}
      </div>

      <ManageCuratorsModal
        initialScreen={modalScreen}
        accounts={accounts ?? []}
        pinned={pinned}
        onTogglePin={toggle}
        onClose={() => setModalScreen(null)}
        onToast={onToast}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Manage Curators: in-dialog navigation stack (Account List -> Account's Lists)
// ---------------------------------------------------------------------------
function ManageCuratorsModal({
  initialScreen,
  accounts,
  pinned,
  onTogglePin,
  onClose,
  onToast,
}: {
  initialScreen: Screen | null;
  accounts: CuratedAccount[];
  pinned: string[];
  onTogglePin: (username: string) => void;
  onClose: () => void;
  onToast: (toast: ToastState) => void;
}) {
  const [stack, setStack] = useState<Screen[]>([{ kind: "accounts" }]);
  const [search, setSearch] = useState("");
  const [visibleCount, setVisibleCount] = useState(ACCOUNT_PAGE_SIZE);
  const inspect = useInspectAccount(onToast);

  // Reset the stack each time the modal is opened (optionally deep-linked to one account).
  useEffect(() => {
    if (!initialScreen) return;
    setStack(
      initialScreen.kind === "lists" ? [{ kind: "accounts" }, initialScreen] : [{ kind: "accounts" }],
    );
    setSearch("");
    setVisibleCount(ACCOUNT_PAGE_SIZE);
  }, [initialScreen]);

  const current = stack[stack.length - 1];
  const currentAccount =
    current.kind === "lists" ? accounts.find((a) => a.username === current.username) : undefined;

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const matches = needle
      ? accounts.filter(
          (a) =>
            a.username.toLowerCase().includes(needle) ||
            (a.display_name ?? "").toLowerCase().includes(needle),
        )
      : accounts;
    // Pinned + HQ first, then alphabetical by display name.
    return [...matches].sort(
      (a, b) =>
        Number(pinned.includes(b.username)) - Number(pinned.includes(a.username)) ||
        Number(b.is_hq) - Number(a.is_hq) ||
        accountName(a).localeCompare(accountName(b)),
    );
  }, [accounts, search, pinned]);

  const title =
    current.kind === "lists"
      ? currentAccount
        ? accountName(currentAccount)
        : current.username
      : "Manage Curators";

  return (
    <Modal open={initialScreen !== null} onClose={onClose} title={title} widthClassName="max-w-2xl">
      <div className="flex flex-col gap-3">
        {stack.length > 1 && (
          <nav className="flex items-center gap-1.5 text-xs text-zinc-500">
            <button
              type="button"
              onClick={() => setStack((s) => s.slice(0, -1))}
              className="flex items-center gap-1 rounded-md px-1.5 py-1 font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover"
            >
              <ArrowLeft className="h-3.5 w-3.5" /> Back
            </button>
            <button
              type="button"
              onClick={() => setStack([{ kind: "accounts" }])}
              className="hover:text-zinc-300"
            >
              All Curators
            </button>
            <ChevronRight className="h-3 w-3" />
            <span className="truncate text-zinc-300">{title}</span>
            {current.kind === "lists" && (
              <button
                type="button"
                disabled={inspect.isPending}
                onClick={() => inspect.mutate(current.username)}
                className={`${buttonClass} ml-auto`}
              >
                {inspect.isPending ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <ScanSearch className="h-3.5 w-3.5" />
                )}
                Inspect
              </button>
            )}
          </nav>
        )}

        {current.kind === "accounts" && (
          <>
            <div className="flex items-center gap-2 rounded-md border border-app-border bg-app-bg px-3 py-2">
              <Search className="h-4 w-4 shrink-0 text-zinc-500" />
              <input
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value);
                  setVisibleCount(ACCOUNT_PAGE_SIZE);
                }}
                placeholder={`Search ${accounts.length} curators...`}
                className="w-full bg-transparent text-sm text-zinc-100 placeholder:text-zinc-600 focus:outline-none"
              />
            </div>

            {filtered.length === 0 && (
              <p className="py-6 text-center text-sm text-zinc-500">No curators found.</p>
            )}

            <div className="flex flex-col gap-1.5">
              {filtered.slice(0, visibleCount).map((account) => {
                const isPinned = pinned.includes(account.username);
                return (
                  <div
                    key={account.username}
                    className="flex items-center gap-2 rounded-md border border-app-border bg-app-bg pr-2"
                  >
                    <button
                      type="button"
                      onClick={() => setStack((s) => [...s, { kind: "lists", username: account.username }])}
                      className="flex min-w-0 flex-1 items-center gap-2.5 px-3 py-2.5 text-left transition-colors hover:bg-app-surface-hover"
                    >
                      <AccountAvatar account={account} />
                      <div className="min-w-0 flex-1">
                        <p className="flex items-center gap-1.5 truncate text-sm font-medium text-zinc-200">
                          {accountName(account)}
                          {account.is_hq && (
                            <BadgeCheck className="h-3.5 w-3.5 shrink-0 text-accent" aria-label="HQ" />
                          )}
                        </p>
                        <p className="truncate text-[11px] text-zinc-500">
                          {account.discovered_lists > 0
                            ? `${account.discovered_lists} lists · ${account.enabled_lists} enabled`
                            : "Lists not browsed yet"}
                        </p>
                      </div>
                      <ChevronRight className="h-4 w-4 shrink-0 text-zinc-600" />
                    </button>
                    <button
                      type="button"
                      onClick={() => onTogglePin(account.username)}
                      title={isPinned ? "Unpin from main screen" : "Pin to main screen"}
                      className="rounded-md p-1.5 text-zinc-500 transition-colors hover:bg-app-surface-hover hover:text-accent"
                    >
                      {isPinned ? <PinOff className="h-4 w-4 text-accent" /> : <Pin className="h-4 w-4" />}
                    </button>
                  </div>
                );
              })}
            </div>

            {filtered.length > visibleCount && (
              <button
                type="button"
                onClick={() => setVisibleCount((n) => n + ACCOUNT_PAGE_SIZE)}
                className={`${buttonClass} self-center`}
              >
                Show more ({filtered.length - visibleCount} remaining)
              </button>
            )}
          </>
        )}

        {current.kind === "lists" && (
          <AccountLists username={current.username} onToast={onToast} />
        )}
      </div>
    </Modal>
  );
}

function AccountLists({
  username,
  onToast,
}: {
  username: string;
  onToast: (toast: ToastState) => void;
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
      queryClient.invalidateQueries({ queryKey: ["curated"] });
      onToast({
        type: "success",
        message: `Found ${fresh.lists.length} lists for ${accountName(fresh.account)}.`,
      });
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
    <div className="flex flex-col gap-2">
      {isLoading && (
        <p className="flex items-center gap-2 text-xs text-zinc-500">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          Discovering lists from Letterboxd - this can take a minute on first visit...
        </p>
      )}
      {error && <p className="text-xs text-red-400">{errorMessage(error, "Could not load lists.")}</p>}

      {data && (
        <>
          <div className="flex items-center justify-between gap-2">
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

          {data.lists.map((list) => (
            <div
              key={list.id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-app-border bg-app-bg px-3 py-2"
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
                  <p className="flex flex-wrap items-center gap-2 text-sm font-medium text-zinc-200">
                    <span className="truncate">{list.title}</span>
                    {list.is_enabled && <SyncBadge syncedAt={list.last_synced_at} />}
                  </p>
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
        </>
      )}
    </div>
  );
}
