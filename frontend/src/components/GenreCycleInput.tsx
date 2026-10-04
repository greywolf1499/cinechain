import { useState } from "react";
import { X } from "lucide-react";
import {
  DEFAULT_GENRE_CYCLE,
  DEFAULT_SWING_FREQUENCY,
  MAX_CYCLE_LENGTH,
  TMDB_GENRE_NAMES,
  canonicalGenre,
} from "../lib/pendulum";

/** Tag input for the Genre Pendulum's cycle (type a genre and press Enter or comma; Backspace removes
 * the last), plus the steps-per-genre slider. Only TMDB genres are accepted. */
export default function GenreCycleInput({
  cycle,
  onCycleChange,
  frequency,
  onFrequencyChange,
}: {
  cycle: string[];
  onCycleChange: (next: string[]) => void;
  frequency: number;
  onFrequencyChange: (next: number) => void;
}) {
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);

  function commit(raw: string) {
    const value = raw.trim();
    if (!value) return;
    const genre = canonicalGenre(value);
    if (!genre) {
      setError(`"${value}" isn't a TMDB genre. Try one of the suggestions below.`);
      return;
    }
    if (cycle.length >= MAX_CYCLE_LENGTH) {
      setError(`A cycle can hold at most ${MAX_CYCLE_LENGTH} genres.`);
      return;
    }
    setError(null);
    setDraft("");
    onCycleChange([...cycle, genre]);
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      commit(draft);
    } else if (event.key === "Backspace" && draft === "" && cycle.length > 0) {
      onCycleChange(cycle.slice(0, -1));
    }
  }

  const suggestions = TMDB_GENRE_NAMES.filter(
    (name) => !draft || name.toLowerCase().includes(draft.trim().toLowerCase()),
  ).slice(0, 8);

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-red-400/30 bg-red-500/5 p-3">
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium text-zinc-400">Genre cycle (in order, repeating)</span>
          <button
            type="button"
            onClick={() => {
              setError(null);
              onCycleChange([...DEFAULT_GENRE_CYCLE]);
            }}
            className="text-[11px] font-medium text-accent hover:underline"
          >
            Reset to default
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 rounded-md border border-app-border bg-app-bg p-2 focus-within:border-accent">
          {cycle.map((genre, index) => (
            <span
              key={`${genre}-${index}`}
              className="flex items-center gap-1 rounded-full bg-red-500/15 py-0.5 pl-2.5 pr-1 text-xs font-medium text-red-200"
            >
              <span className="text-[10px] text-red-300/70">{index + 1}</span>
              {genre}
              <button
                type="button"
                aria-label={`Remove ${genre}`}
                onClick={() => onCycleChange(cycle.filter((_, i) => i !== index))}
                className="rounded-full p-0.5 text-red-300 hover:bg-red-500/20"
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
          <input
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              setError(null);
            }}
            onKeyDown={handleKeyDown}
            onBlur={() => draft.trim() && commit(draft)}
            placeholder={cycle.length === 0 ? "Add a genre..." : "Add another..."}
            aria-label="Add a genre to the cycle"
            className="min-w-[8rem] flex-1 bg-transparent px-1 py-0.5 text-sm text-zinc-100 placeholder:text-zinc-600 focus:outline-none"
          />
        </div>
        <div className="flex flex-wrap gap-1">
          {suggestions.map((name) => (
            <button
              key={name}
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => commit(name)}
              className="rounded-full border border-app-border px-2 py-0.5 text-[10px] text-zinc-400 transition-colors hover:border-red-400/60 hover:text-red-200"
            >
              + {name}
            </button>
          ))}
        </div>
        {error && (
          <p role="alert" className="text-xs text-amber-400">
            {error}
          </p>
        )}
        {cycle.length === 0 && (
          <p className="text-[11px] text-zinc-500">Empty: the default cycle ({DEFAULT_GENRE_CYCLE.join(", ")}) is used.</p>
        )}
      </div>

      <label className="flex flex-col gap-1.5 text-xs font-medium text-zinc-400">
        Swing frequency: {frequency} step{frequency === 1 ? "" : "s"} per genre
        <input
          type="range"
          min={1}
          max={6}
          value={frequency}
          onChange={(e) => onFrequencyChange(Number(e.target.value))}
          aria-label="Swing frequency"
          className="w-full accent-accent"
        />
      </label>
      <p className="text-[11px] text-zinc-500">
        Every film must share a genre with the last one and carry the current genre. The default is{" "}
        {DEFAULT_SWING_FREQUENCY} steps per genre.
      </p>
    </div>
  );
}
