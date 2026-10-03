import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { BadgeCheck, Compass, Loader2, Users } from "lucide-react";
import Breadcrumbs from "../components/Breadcrumbs";
import CuratorAvatar from "../components/CuratorAvatar";
import EmptyState from "../components/EmptyState";
import { SearchBox, SelectField } from "../components/ListControls";
import PageHeading from "../components/PageHeading";
import Pagination from "../components/Pagination";
import Toast, { type ToastState } from "../components/Toast";
import { ApiError } from "../lib/api";
import { useBrowseAccounts } from "../lib/queries";
import { postSse } from "../lib/sse";
import { useDebouncedValue } from "../lib/useDebouncedValue";
import { useAuthStore } from "../store/authStore";
import type { AccountKind, AccountSort } from "../types/api";

const SORT_OPTIONS: { value: AccountSort; label: string }[] = [
  { value: "name", label: "Name (A-Z)" },
  { value: "lists", label: "Most lists" },
  { value: "enabled", label: "Most enabled" },
];
const KIND_OPTIONS: { value: AccountKind; label: string }[] = [
  { value: "all", label: "All" },
  { value: "hq", label: "HQ accounts" },
  { value: "other", label: "Other" },
];

export default function CuratorsPage() {
  const queryClient = useQueryClient();
  const isAdmin = !!useAuthStore((s) => s.user?.is_admin);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<AccountSort>("name");
  const [kind, setKind] = useState<AccountKind>("all");
  const [page, setPage] = useState(1);
  const [toast, setToast] = useState<ToastState | null>(null);
  const q = useDebouncedValue(search.trim(), 300);

  useEffect(() => setPage(1), [q, sort, kind]);

  const { data, isLoading, error } = useBrowseAccounts({ q, sort, kind, page });

  const discoverHq = useMutation({
    mutationFn: () => postSse("/curated/accounts/discover-hq?max_pages=5"),
    onSuccess: (events) => {
      const result = events.find((e) => e.event === "result")?.data as
        | { discovered: number; new: number; partial: boolean }
        | undefined;
      if (events.some((e) => e.event === "error") && !result) {
        setToast({ type: "error", message: "HQ discovery failed - see server logs." });
        return;
      }
      setToast({
        type: "success",
        message: result
          ? `Found ${result.discovered} HQ accounts (${result.new} new)${result.partial ? " - partial result" : ""}.`
          : "HQ discovery finished.",
      });
      queryClient.invalidateQueries({ queryKey: ["curated"] });
    },
    onError: (err) =>
      setToast({ type: "error", message: err instanceof ApiError ? err.message : "HQ discovery failed." }),
  });

  return (
    <div>
      <Breadcrumbs items={[{ label: "Lists", to: "/lists" }, { label: "Curators" }]} />
      <PageHeading title="Curators" subtitle="Letterboxd accounts whose published lists you can browse and enable." />

      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-3">
          <SearchBox value={search} onChange={setSearch} placeholder="Search curators by name or bio..." />
          <SelectField label="Sort" value={sort} options={SORT_OPTIONS} onChange={setSort} />
          <SelectField label="Show" value={kind} options={KIND_OPTIONS} onChange={setKind} />
          {isAdmin && (
            <button
              type="button"
              disabled={discoverHq.isPending}
              onClick={() => discoverHq.mutate()}
              className="flex items-center gap-1.5 rounded-md border border-app-border px-3 py-2 text-xs font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover disabled:cursor-not-allowed disabled:opacity-60"
            >
              {discoverHq.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Compass className="h-3.5 w-3.5" />}
              Discover HQ Accounts
            </button>
          )}
        </div>

        {isLoading && <p className="text-sm text-zinc-500">Loading curators...</p>}
        {error && <p className="text-sm text-red-400">Could not load curators.</p>}
        {data && data.items.length === 0 && (
          <EmptyState icon={Users} title="No curators found" description="Try a different search or filter." />
        )}

        {data && data.items.length > 0 && (
          <>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {data.items.map((account) => (
                <Link
                  key={account.username}
                  to={`/curators/${account.username}`}
                  className="flex items-start gap-3 rounded-xl border border-app-border bg-app-surface p-4 transition-colors hover:bg-app-surface-hover"
                >
                  <CuratorAvatar avatarUrl={account.avatar_url} />
                  <div className="min-w-0 flex-1">
                    <p className="flex items-center gap-1.5 text-sm font-semibold text-zinc-100">
                      <span className="break-words">{account.display_name?.trim() || account.username}</span>
                      {account.is_hq && <BadgeCheck className="h-3.5 w-3.5 shrink-0 text-accent" aria-label="HQ" />}
                    </p>
                    <p className="text-[11px] text-zinc-500">
                      {account.discovered_lists > 0
                        ? `${account.discovered_lists} lists · ${account.enabled_lists} enabled`
                        : "Lists not browsed yet"}
                    </p>
                    {account.bio && <p className="mt-1.5 line-clamp-2 break-words text-xs text-zinc-400">{account.bio}</p>}
                  </div>
                </Link>
              ))}
            </div>
            <Pagination page={data.page} pages={data.pages} total={data.total} noun="curators" onPageChange={setPage} />
          </>
        )}
      </div>
      <Toast toast={toast} onDismiss={() => setToast(null)} />
    </div>
  );
}
