import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2, Search, User, X } from "lucide-react";
import { api } from "../lib/api";
import { useDebouncedValue } from "../lib/useDebouncedValue";
import { profileUrl } from "../lib/tmdbImage";
import type { PersonSummary } from "../types/api";

/** Live TMDB person search: pick the person whose career the marathon follows - an actor by
 * default, or (`department="Directing"`) only directors. */
export default function ActorPicker({
  value,
  onChange,
  department,
  noun = "actor",
  accentClass = "border-fuchsia-400/40 bg-fuchsia-500/5",
}: {
  value: PersonSummary | null;
  onChange: (person: PersonSummary | null) => void;
  department?: string;
  noun?: string;
  accentClass?: string;
}) {
  const [query, setQuery] = useState("");
  const debounced = useDebouncedValue(query, 300);
  const { data, isFetching } = useQuery({
    queryKey: ["people", "search", debounced, department ?? null],
    queryFn: () =>
      api.get<PersonSummary[]>(
        `/people/search?q=${encodeURIComponent(debounced)}${
          department ? `&department=${encodeURIComponent(department)}` : ""
        }`,
      ),
    enabled: debounced.trim().length > 1 && !value,
  });

  if (value) {
    return (
      <div className={`flex items-center gap-3 rounded-lg border p-2.5 ${accentClass}`}>
        <ActorPhoto person={value} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-zinc-100">{value.name}</p>
          <p className="truncate text-[11px] text-zinc-500">{value.known_for.join(" · ")}</p>
        </div>
        <button
          type="button"
          aria-label={`Choose a different ${noun}`}
          onClick={() => onChange(null)}
          className="rounded-md p-1.5 text-zinc-500 hover:bg-app-surface-hover hover:text-zinc-200"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2 rounded-md border border-app-border bg-app-bg px-3 py-2 focus-within:border-accent">
        <Search className="h-4 w-4 shrink-0 text-zinc-500" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={`Search for ${noun === "actor" ? "an" : "a"} ${noun}...`}
          aria-label={`Search for ${noun === "actor" ? "an" : "a"} ${noun}`}
          className="w-full bg-transparent text-sm text-zinc-100 placeholder:text-zinc-600 focus:outline-none"
        />
        {isFetching && <Loader2 className="h-4 w-4 animate-spin text-zinc-500" />}
      </div>
      {query.trim().length > 1 && data && data.length === 0 && !isFetching && (
        <p className="px-1 text-[11px] text-zinc-500">No {noun}s found for “{query.trim()}”.</p>
      )}
      {query.trim().length > 1 && data && data.length > 0 && (
        <ul className="max-h-64 overflow-y-auto rounded-md border border-app-border bg-app-surface">
          {data.map((person) => (
            <li key={person.person_id}>
              <button
                type="button"
                onClick={() => {
                  onChange(person);
                  setQuery("");
                }}
                className="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-app-surface-hover"
              >
                <ActorPhoto person={person} small />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm text-zinc-100">{person.name}</span>
                  <span className="block truncate text-[11px] text-zinc-500">
                    {[person.known_for_department, ...person.known_for].filter(Boolean).join(" · ")}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ActorPhoto({ person, small = false }: { person: PersonSummary; small?: boolean }) {
  const size = small ? "h-8 w-8" : "h-11 w-11";
  const url = person.profile_path ? profileUrl(person.profile_path) : null;
  return url ? (
    <img src={url} alt="" className={`${size} shrink-0 rounded-full object-cover`} />
  ) : (
    <span className={`${size} flex shrink-0 items-center justify-center rounded-full bg-app-surface-hover text-zinc-500`}>
      <User className="h-4 w-4" />
    </span>
  );
}
