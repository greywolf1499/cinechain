import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { BadgeCheck, ExternalLink, Loader2, RefreshCw, ScanSearch } from "lucide-react";
import Breadcrumbs from "../components/Breadcrumbs";
import CuratorAvatar from "../components/CuratorAvatar";
import ListBrowser from "../components/ListBrowser";
import Toast, { type ToastState } from "../components/Toast";
import { ApiError, api } from "../lib/api";
import { useCuratorProfile } from "../lib/queries";
import { useAuthStore } from "../store/authStore";
import type { CuratedAccount, CuratedAccountLists } from "../types/api";

const buttonClass =
  "flex items-center gap-1.5 rounded-md border border-app-border px-3 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover disabled:cursor-not-allowed disabled:opacity-60";

function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiError || err instanceof Error ? err.message : fallback;
}

export default function CuratorProfilePage() {
  const { username = "" } = useParams();
  const queryClient = useQueryClient();
  const isAdmin = !!useAuthStore((s) => s.user?.is_admin);
  const [toast, setToast] = useState<ToastState | null>(null);
  const { data, isLoading, error } = useCuratorProfile(username);

  const inspect = useMutation({
    mutationFn: () => api.post<CuratedAccount>(`/curated/accounts/${username}/inspect`),
    onSuccess: () => {
      setToast({ type: "success", message: "Profile refreshed from Letterboxd." });
      queryClient.invalidateQueries({ queryKey: ["curated"] });
    },
    onError: (err) => setToast({ type: "error", message: errorText(err, "Inspect failed.") }),
  });
  const rescan = useMutation({
    mutationFn: () => api.get<CuratedAccountLists>(`/curated/accounts/${username}/lists?refresh=true`),
    onSuccess: (fresh) => {
      setToast({ type: "success", message: `Found ${fresh.lists.length} lists.` });
      queryClient.invalidateQueries({ queryKey: ["curated"] });
    },
    onError: (err) => setToast({ type: "error", message: errorText(err, "Rescan failed.") }),
  });

  const account = data?.account;
  const name = account?.display_name?.trim() || account?.username || username;

  return (
    <div>
      <Breadcrumbs
        items={[{ label: "Lists", to: "/lists" }, { label: "Curators", to: "/curators" }, { label: name }]}
      />

      {isLoading && (
        <p className="flex items-center gap-2 text-sm text-zinc-500">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading curator - the first visit may scan Letterboxd and take a minute...
        </p>
      )}
      {error && (
        <div className="rounded-xl border border-red-900/50 bg-red-950/20 px-5 py-4 text-sm text-red-300">
          {errorText(error, "Could not load this curator.")}{" "}
          <Link to="/curators" className="underline">
            Back to curators
          </Link>
        </div>
      )}

      {account && (
        <div className="flex flex-col gap-6">
          <header className="flex flex-col gap-4 rounded-xl border border-app-border bg-app-surface p-5 sm:flex-row sm:items-start">
            <CuratorAvatar avatarUrl={account.avatar_url} size="lg" />
            <div className="min-w-0 flex-1">
              <h1 className="flex flex-wrap items-center gap-2 text-xl font-semibold tracking-tight text-zinc-100">
                <span className="break-words">{name}</span>
                {account.is_hq && <BadgeCheck className="h-5 w-5 shrink-0 text-accent" aria-label="HQ account" />}
                {account.account_tier && (
                  <span className="rounded-full border border-app-border px-2 py-0.5 text-[11px] font-medium text-zinc-400">
                    {account.account_tier}
                  </span>
                )}
              </h1>
              <a
                href={`https://letterboxd.com/${account.username}/`}
                target="_blank"
                rel="noreferrer noopener"
                className="mt-0.5 inline-flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-300"
              >
                @{account.username} <ExternalLink className="h-3 w-3" />
              </a>
              {account.bio ? (
                <p className="mt-3 whitespace-pre-line break-words text-sm leading-relaxed text-zinc-300">{account.bio}</p>
              ) : (
                <p className="mt-3 text-sm text-zinc-600">No bio yet{isAdmin ? " - use Refresh profile to fetch it." : "."}</p>
              )}
              <p className="mt-3 text-xs text-zinc-500">
                {account.discovered_lists} lists browsed · {account.enabled_lists} enabled
                {account.total_public_lists > 0 && ` · ${account.total_public_lists} public on Letterboxd`}
              </p>
              {data.partial && (
                <p className="mt-1 text-xs text-amber-400">Partial scan - Letterboxd rate-limited the crawl.</p>
              )}
              {!data.discovered && !isAdmin && (
                <p className="mt-1 text-xs text-zinc-500">Lists not browsed yet - ask an admin to scan this curator.</p>
              )}
            </div>
            {isAdmin && (
              <div className="flex shrink-0 flex-wrap gap-2 sm:flex-col">
                <button type="button" disabled={inspect.isPending} onClick={() => inspect.mutate()} className={buttonClass}>
                  {inspect.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ScanSearch className="h-3.5 w-3.5" />}
                  Refresh profile
                </button>
                <button type="button" disabled={rescan.isPending} onClick={() => rescan.mutate()} className={buttonClass}>
                  {rescan.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                  Rescan lists
                </button>
              </div>
            )}
          </header>

          <section>
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-400">Curator&apos;s Lists</h2>
            <ListBrowser account={account.username} isAdmin={isAdmin} onToast={setToast} />
          </section>
        </div>
      )}
      <Toast toast={toast} onDismiss={() => setToast(null)} />
    </div>
  );
}
