import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { api } from "../lib/api";
import { useDebouncedValue } from "../lib/useDebouncedValue";
import type { CuratedListEntry, CuratedListSummary, MovieSummary } from "../types/api";
import Modal from "./Modal";
import MoviePoster from "./MoviePoster";
import type { ToastState } from "./Toast";

const buttonClass = "rounded border border-app-border px-3 py-1.5 text-xs text-accent disabled:opacity-50";

export default function ListEntryReview({ list, onClose, onToast }: {
  list: CuratedListSummary;
  onClose: () => void;
  onToast: (toast: ToastState) => void;
}) {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState("needs_review");
  const [selected, setSelected] = useState<CuratedListEntry | null>(null);
  const [query, setQuery] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const debouncedQuery = useDebouncedValue(query, 300);
  const entries = useQuery({
    queryKey: ["curated", "entries", list.id, filter],
    queryFn: () => api.get<CuratedListEntry[]>(`/curated/lists/${list.id}/entries${
      filter === "all" || filter === "needs_review" ? "" : `?status=${filter}`}`),
  });
  const search = useQuery({
    queryKey: ["movies", "search", debouncedQuery],
    queryFn: () => api.get<{ results: MovieSummary[] }>(
      `/movies/search?q=${encodeURIComponent(debouncedQuery)}`),
    enabled: !!selected && debouncedQuery.trim().length > 1,
  });
  const visible = (entries.data ?? []).filter((entry) =>
    filter !== "needs_review" || entry.status !== "matched");

  async function match(movie: MovieSummary) {
    if (!selected) return;
    setSaving(true);
    setError(null);
    try {
      await api.patch(`/curated/lists/${list.id}/entries/${selected.position}`, {
        tmdb_id: movie.tmdb_id,
      });
      await queryClient.invalidateQueries({ queryKey: ["curated"] });
      onToast({ type: "success", message: `${selected.title} matched to ${movie.title}.` });
      setSelected(null);
      setQuery("");
    } catch (error) {
      setError(error instanceof Error ? error.message : "Could not save this match.");
    } finally {
      setSaving(false);
    }
  }

  return <Modal open onClose={onClose} title={`Review ${list.title}`} widthClassName="max-w-2xl"
    dismissible={!saving}>
    <p className="mb-3 text-xs text-zinc-400">Choose a TMDB movie for an unmatched or ambiguous title.
      Manual matches stay linked when the same Letterboxd slug appears on the next sync.</p>
    <label className="mb-3 block text-xs text-zinc-400">Entry status
      <select aria-label="Entry status" value={filter} disabled={saving} onChange={(event) => {
        setFilter(event.target.value); setSelected(null); setError(null);
      }} className="ml-2 rounded border border-app-border bg-app-bg px-2 py-1">
        <option value="needs_review">Needs review</option>
        <option value="unmatched">Unmatched</option>
        <option value="ambiguous">Ambiguous</option>
        <option value="tv_title">TV titles</option>
        <option value="matched">Matched</option>
        <option value="all">All entries</option>
      </select>
    </label>
    {entries.isPending && <p role="status" className="text-sm text-zinc-400">Loading entries...</p>}
    {entries.error && <p role="alert" className="text-sm text-red-300">{entries.error.message}
      <button className={buttonClass} onClick={() => void entries.refetch()}>Retry</button></p>}
    {error && <p role="alert" className="mb-2 text-sm text-red-300">{error}</p>}
    {selected ? <section className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm text-zinc-100">Match #{selected.position}: {selected.title}
          {selected.year ? ` (${selected.year})` : ""}</h3>
        <button disabled={saving} className={buttonClass} onClick={() => setSelected(null)}>Back</button>
      </div>
      <label className="block text-xs text-zinc-400">Search TMDB movies
        <input autoFocus value={query} disabled={saving} onChange={(event) => setQuery(event.target.value)}
          placeholder="Film title" className="mt-1 w-full rounded border border-app-border bg-app-bg px-3 py-2 text-sm text-zinc-100" />
      </label>
      {search.isFetching && <p role="status" className="text-xs text-zinc-400">Searching...</p>}
      {search.error && <p role="alert" className="text-xs text-red-300">{search.error.message}</p>}
      {!search.isFetching && !search.error && debouncedQuery.length > 1 && !search.data?.results.length &&
        <p className="text-xs text-zinc-400">No movies found. Try another title.</p>}
      {search.data?.results.map((movie) => <div key={movie.tmdb_id} className="flex items-center gap-3 rounded border border-app-border p-2">
        <MoviePoster movieId={movie.tmdb_id} path={movie.poster_path} title={movie.title} className="w-10 shrink-0" />
        <span className="min-w-0 flex-1 break-words text-sm text-zinc-200">{movie.title}
          {movie.release_year ? ` (${movie.release_year})` : ""} <span className="text-xs text-zinc-500">TMDB #{movie.tmdb_id}</span></span>
        <button disabled={saving} className={buttonClass} onClick={() => void match(movie)}>
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Use this movie"}
        </button>
      </div>)}
    </section> : <ol className="space-y-2">
      {visible.map((entry) => <li key={entry.position} className="flex items-start justify-between gap-3 rounded border border-app-border p-3">
        <div className="min-w-0">
          <p className="break-words text-sm text-zinc-200">#{entry.position} {entry.title}
            {entry.year ? ` (${entry.year})` : ""}</p>
          <p className="text-xs text-zinc-400">{entry.status === "tv_title" ? "TV title" : entry.status}
            {entry.match_tier ? ` - ${entry.match_tier}` : ""}{entry.tmdb_id ? ` - TMDB #${entry.tmdb_id}` : ""}</p>
          {entry.reason && <p className="mt-1 break-words text-xs text-zinc-500">{entry.reason}</p>}
        </div>
        <button className={`${buttonClass} shrink-0`} onClick={() => {
          setSelected(entry); setQuery(entry.title); setError(null);
        }}>Match...</button>
      </li>)}
      {entries.isSuccess && !visible.length && <p className="text-sm text-zinc-400">No entries in this view.</p>}
    </ol>}
  </Modal>;
}
