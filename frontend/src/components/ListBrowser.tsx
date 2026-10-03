import { useEffect, useState } from "react";
import { ListX, Loader2 } from "lucide-react";
import CuratedListCard from "./CuratedListCard";
import EmptyState from "./EmptyState";
import { SearchBox, SelectField } from "./ListControls";
import Pagination from "./Pagination";
import type { ToastState } from "./Toast";
import { useBrowseLists } from "../lib/queries";
import { useDebouncedValue } from "../lib/useDebouncedValue";
import type { ListSort, ListState } from "../types/api";

const SORT_OPTIONS: { value: ListSort; label: string }[] = [
  { value: "film_count", label: "Movie count" },
  { value: "name", label: "Name (A-Z)" },
  { value: "popularity", label: "Popularity (most watched)" },
  { value: "synced", label: "Recently synced" },
];
const STATE_OPTIONS: { value: ListState; label: string }[] = [
  { value: "all", label: "All" },
  { value: "enabled", label: "Enabled" },
  { value: "disabled", label: "Disabled" },
];

/** Searchable, sortable, filterable, server-paginated lists - used by the Lists
 * page (all curators) and by a curator profile (scoped via `account`). */
export default function ListBrowser({
  account,
  isAdmin,
  onToast,
}: {
  account?: string;
  isAdmin: boolean;
  onToast: (toast: ToastState) => void;
}) {
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<ListSort>("film_count");
  const [state, setState] = useState<ListState>("all");
  const [page, setPage] = useState(1);
  const q = useDebouncedValue(search.trim(), 300);

  useEffect(() => setPage(1), [q, sort, state, account]);

  const { data, isLoading, isFetching, error } = useBrowseLists({ q, sort, state, account, page });

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <SearchBox value={search} onChange={setSearch} placeholder="Search titles, descriptions, badges, curators..." />
        <SelectField label="Sort" value={sort} options={SORT_OPTIONS} onChange={setSort} />
        <SelectField label="Show" value={state} options={STATE_OPTIONS} onChange={setState} />
        {isFetching && !isLoading && <Loader2 className="h-4 w-4 animate-spin text-zinc-500" />}
      </div>

      {isLoading && <p className="text-sm text-zinc-500">Loading lists...</p>}
      {error && <p className="text-sm text-red-400">Could not load lists.</p>}

      {data && data.items.length === 0 && (
        <EmptyState
          icon={ListX}
          title="No lists match"
          description="Try a different search, or switch the Enabled/Disabled filter."
        />
      )}

      {data && data.items.length > 0 && (
        <>
          <div className="flex flex-col gap-3">
            {data.items.map((list) => (
              <CuratedListCard key={list.id} list={list} isAdmin={isAdmin} showCurator={!account} onToast={onToast} />
            ))}
          </div>
          <Pagination page={data.page} pages={data.pages} total={data.total} noun="lists" onPageChange={setPage} />
        </>
      )}
    </div>
  );
}
