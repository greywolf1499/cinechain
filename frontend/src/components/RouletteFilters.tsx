import { RotateCcw } from "lucide-react";
import DualRangeSlider from "./DualRangeSlider";
import { cn } from "../lib/cn";
import type { GenreOut } from "../types/api";

export const RUNTIME_RANGE = { min: 0, max: 240, step: 5 };
export const RATING_RANGE = { min: 0, max: 10, step: 0.1 };

export type GenreOperator = "AND" | "OR";

/** Slider ends mean "no limit": a runtime of 240 is "240+", a rating of 0 / 10 is unbounded. */
export interface RouletteFilterState {
  runtime: [number, number];
  rating: [number, number];
  genreIds: number[];
  genreOperator: GenreOperator;
}

export const EMPTY_FILTERS: RouletteFilterState = {
  runtime: [RUNTIME_RANGE.min, RUNTIME_RANGE.max],
  rating: [RATING_RANGE.min, RATING_RANGE.max],
  genreIds: [],
  genreOperator: "OR",
};

const COMEDY = 35;
const ANIMATION = 16;

export interface RoulettePreset {
  id: string;
  label: string;
  emoji: string;
  detail: string;
  filters: RouletteFilterState;
}

export const ROULETTE_PRESETS: RoulettePreset[] = [
  {
    id: "bad-movie-night",
    label: "Bad Movie Night",
    emoji: "🍅",
    detail: "Under 5.2 IMDb, over 100 min",
    filters: { ...EMPTY_FILTERS, runtime: [100, RUNTIME_RANGE.max], rating: [RATING_RANGE.min, 5.2] },
  },
  {
    id: "palate-cleanser",
    label: "Palate Cleanser",
    emoji: "🍋",
    detail: "Under 90 min, Comedy or Animation",
    filters: {
      ...EMPTY_FILTERS,
      runtime: [RUNTIME_RANGE.min, 90],
      genreIds: [COMEDY, ANIMATION],
      genreOperator: "OR",
    },
  },
  {
    id: "epic-masterpiece",
    label: "Epic Masterpiece",
    emoji: "🏛️",
    detail: "Over 150 min, over 8.0 IMDb",
    filters: { ...EMPTY_FILTERS, runtime: [150, RUNTIME_RANGE.max], rating: [8, RATING_RANGE.max] },
  },
];

function sameRange(a: [number, number], b: [number, number]): boolean {
  return a[0] === b[0] && a[1] === b[1];
}

export function filtersEqual(a: RouletteFilterState, b: RouletteFilterState): boolean {
  return (
    sameRange(a.runtime, b.runtime) &&
    sameRange(a.rating, b.rating) &&
    a.genreOperator === b.genreOperator &&
    a.genreIds.length === b.genreIds.length &&
    a.genreIds.every((id) => b.genreIds.includes(id))
  );
}

export function hasActiveFilters(filters: RouletteFilterState): boolean {
  return !filtersEqual(filters, EMPTY_FILTERS);
}

/** Query string for `GET /engine/roulette/spin`: slider ends and an unset genre list add nothing. */
export function filtersToParams(filters: RouletteFilterState, params = new URLSearchParams()) {
  const [minRuntime, maxRuntime] = filters.runtime;
  const [minRating, maxRating] = filters.rating;
  if (minRuntime > RUNTIME_RANGE.min) params.set("min_runtime", String(minRuntime));
  if (maxRuntime < RUNTIME_RANGE.max) params.set("max_runtime", String(maxRuntime));
  if (minRating > RATING_RANGE.min) params.set("min_rating", minRating.toFixed(1));
  if (maxRating < RATING_RANGE.max) params.set("max_rating", maxRating.toFixed(1));
  filters.genreIds.forEach((id) => params.append("genre_ids", String(id)));
  if (filters.genreIds.length > 1) params.set("genre_operator", filters.genreOperator);
  return params;
}

function runtimeLabel([low, high]: [number, number]): string {
  if (low === RUNTIME_RANGE.min && high === RUNTIME_RANGE.max) return "Any length";
  if (low === RUNTIME_RANGE.min) return `up to ${high} min`;
  if (high === RUNTIME_RANGE.max) return `${low}+ min`;
  return `${low}-${high} min`;
}

function ratingLabel([low, high]: [number, number]): string {
  if (low === RATING_RANGE.min && high === RATING_RANGE.max) return "Any rating";
  if (low === RATING_RANGE.min) return `up to ${high.toFixed(1)}`;
  if (high === RATING_RANGE.max) return `${low.toFixed(1)}+`;
  return `${low.toFixed(1)}-${high.toFixed(1)}`;
}

/** The Roulette filter dashboard: runtime + IMDb dual sliders, genre chips with AND/OR, quick presets. */
export default function RouletteFilters({
  value,
  onChange,
  genres,
}: {
  value: RouletteFilterState;
  onChange: (filters: RouletteFilterState) => void;
  genres: GenreOut[] | undefined;
}) {
  function toggleGenre(id: number) {
    onChange({
      ...value,
      genreIds: value.genreIds.includes(id)
        ? value.genreIds.filter((g) => g !== id)
        : [...value.genreIds, id],
    });
  }

  return (
    <div className="flex flex-col gap-3.5 rounded-lg border border-app-border bg-app-bg/60 p-3">
      <div>
        <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-zinc-500">Quick presets</p>
        <div className="flex flex-wrap gap-1.5">
          {ROULETTE_PRESETS.map((preset) => {
            const active = filtersEqual(value, preset.filters);
            return (
              <button
                key={preset.id}
                type="button"
                title={preset.detail}
                aria-pressed={active}
                onClick={() => onChange(active ? EMPTY_FILTERS : preset.filters)}
                className={cn(
                  "rounded-full border px-2.5 py-1 text-xs font-medium transition-colors",
                  active
                    ? "border-accent bg-accent/15 text-accent"
                    : "border-app-border text-zinc-300 hover:border-zinc-600 hover:bg-app-surface-hover",
                )}
              >
                {preset.emoji} {preset.label}
              </button>
            );
          })}
        </div>
      </div>

      <div className="flex flex-col gap-3">
        <SliderField label="Runtime" summary={runtimeLabel(value.runtime)}>
          <DualRangeSlider
            label="Runtime"
            {...RUNTIME_RANGE}
            value={value.runtime}
            onChange={(runtime) => onChange({ ...value, runtime })}
          />
        </SliderField>
        <SliderField label="IMDb rating" summary={ratingLabel(value.rating)}>
          <DualRangeSlider
            label="IMDb rating"
            {...RATING_RANGE}
            value={value.rating}
            onChange={(rating) => onChange({ ...value, rating })}
          />
        </SliderField>
      </div>

      <div>
        <div className="mb-1.5 flex items-center justify-between gap-2">
          <p className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">
            Genres{value.genreIds.length > 0 && ` (${value.genreIds.length})`}
          </p>
          <div
            role="group"
            aria-label="Genre match mode"
            className="inline-flex rounded-full border border-app-border bg-app-surface p-0.5 text-[10px] font-semibold"
          >
            {(["OR", "AND"] as const).map((operator) => (
              <button
                key={operator}
                type="button"
                aria-pressed={value.genreOperator === operator}
                onClick={() => onChange({ ...value, genreOperator: operator })}
                title={operator === "AND" ? "Must have every selected genre" : "Any of the selected genres"}
                className={cn(
                  "rounded-full px-2.5 py-0.5 transition-colors",
                  value.genreOperator === operator
                    ? "bg-accent text-zinc-950"
                    : "text-zinc-400 hover:text-zinc-200",
                )}
              >
                {operator === "OR" ? "Any (OR)" : "All (AND)"}
              </button>
            ))}
          </div>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {genres?.map((genre) => {
            const selected = value.genreIds.includes(genre.id);
            return (
              <button
                key={genre.id}
                type="button"
                aria-pressed={selected}
                onClick={() => toggleGenre(genre.id)}
                className={cn(
                  "rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors",
                  selected
                    ? "border-accent bg-accent/15 text-accent"
                    : "border-app-border text-zinc-400 hover:border-zinc-600 hover:text-zinc-200",
                )}
              >
                {genre.name}
              </button>
            );
          }) ?? <span className="text-[11px] text-zinc-600">Loading genres...</span>}
        </div>
      </div>

      {hasActiveFilters(value) && (
        <button
          type="button"
          onClick={() => onChange(EMPTY_FILTERS)}
          className="flex w-fit items-center gap-1 text-[11px] font-medium text-zinc-500 transition-colors hover:text-zinc-200"
        >
          <RotateCcw className="h-3 w-3" /> Reset filters
        </button>
      )}
    </div>
  );
}

function SliderField({
  label,
  summary,
  children,
}: {
  label: string;
  summary: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between text-[11px]">
        <span className="font-medium uppercase tracking-wide text-zinc-500">{label}</span>
        <span className="font-semibold text-accent">{summary}</span>
      </div>
      {children}
    </div>
  );
}
